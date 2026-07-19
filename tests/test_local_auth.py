import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


class LocalAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ.update({
            "AUTH_PROVIDER": "local",
            "AUTH_COOKIE_SECURE": "false",
            "AUTH_EXPECTED_ORIGIN": "http://localhost",
            "AUTH_ALLOWED_ORIGINS": "http://localhost,https://index.example.com",
            "LOCAL_SETUP_TOKEN": "test-setup-token",
            "DATA_DIR": cls.temp_dir.name,
            "WEBHOOK_SECRET": "test-webhook-secret",
        })
        sys.modules.pop("app", None)
        cls.module = importlib.import_module("app")
        cls.client = cls.module.app.test_client()
        with cls.module.app.app_context():
            stamp = cls.module.now()
            cls.module.db().execute(
                "INSERT INTO local_users(username,password_hash,created_at,password_changed_at) VALUES(?,?,?,?)",
                ("owner", cls.module.PASSWORD_HASHER.hash("correct horse battery staple"), stamp, stamp),
            )
            cls.module.db().commit()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        with self.module.app.app_context():
            self.module.db().execute("DELETE FROM local_sessions")
            self.module.db().execute("DELETE FROM login_attempts")
            self.module.db().commit()
        self.client.delete_cookie("index_session")

    def login(self):
        return self.client.post(
            "/auth/login",
            json={"username": "owner", "password": "correct horse battery staple"},
            headers={"Origin": "http://localhost"},
        )

    def test_login_session_and_logout(self):
        login = self.login()
        self.assertEqual(login.status_code, 200)
        self.assertIn("HttpOnly", login.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", login.headers["Set-Cookie"])
        session = self.client.get("/auth/session")
        self.assertTrue(session.json["authenticated"])
        logout = self.client.post(
            "/auth/logout",
            headers={"Origin": "http://localhost", "X-CSRF-Token": login.json["csrfToken"]},
        )
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/auth/session").status_code, 401)

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            "/auth/login",
            json={"username": "owner", "password": "wrong password"},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error"], "Invalid username or password")

    def test_mutation_requires_csrf(self):
        login = self.login()
        without_csrf = self.client.post("/api/manual", json={"transcription": "secret"})
        self.assertEqual(without_csrf.status_code, 403)
        with_csrf = self.client.post(
            "/api/manual",
            json={"transcription": "secret"},
            headers={"Origin": "http://localhost", "X-CSRF-Token": login.json["csrfToken"]},
        )
        self.assertEqual(with_csrf.status_code, 201)

    def test_wrong_origin_is_rejected(self):
        response = self.client.post(
            "/auth/login",
            json={"username": "owner", "password": "correct horse battery staple"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_second_allowed_origin_is_accepted(self):
        response = self.client.post(
            "/auth/login",
            json={"username": "owner", "password": "correct horse battery staple"},
            headers={"Origin": "https://index.example.com"},
        )
        self.assertEqual(response.status_code, 200)

    def test_first_run_setup_requires_token_and_only_runs_once(self):
        with self.module.app.app_context():
            self.module.db().execute("DELETE FROM local_sessions")
            self.module.db().execute("DELETE FROM local_users")
            self.module.db().commit()
        try:
            status = self.client.get("/auth/session")
            self.assertEqual(status.status_code, 401)
            self.assertTrue(status.json["setupRequired"])
            rejected = self.client.post(
                "/auth/setup",
                json={"setupToken": "wrong", "username": "first", "password": "a secure first password", "passwordConfirmation": "a secure first password"},
                headers={"Origin": "http://localhost"},
            )
            self.assertEqual(rejected.status_code, 401)
            created = self.client.post(
                "/auth/setup",
                json={"setupToken": "test-setup-token", "username": "first", "password": "a secure first password", "passwordConfirmation": "a secure first password"},
                headers={"Origin": "http://localhost"},
            )
            self.assertEqual(created.status_code, 201)
            second = self.client.post(
                "/auth/setup",
                json={"setupToken": "test-setup-token", "username": "other", "password": "another secure password", "passwordConfirmation": "another secure password"},
                headers={"Origin": "http://localhost"},
            )
            self.assertEqual(second.status_code, 409)
        finally:
            with self.module.app.app_context():
                self.module.db().execute("DELETE FROM local_sessions")
                self.module.db().execute("DELETE FROM local_users")
                stamp = self.module.now()
                self.module.db().execute(
                    "INSERT INTO local_users(username,password_hash,created_at,password_changed_at) VALUES(?,?,?,?)",
                    ("owner", self.module.PASSWORD_HASHER.hash("correct horse battery staple"), stamp, stamp),
                )
                self.module.db().commit()

    def test_repeated_failures_are_rate_limited(self):
        for _ in range(5):
            response = self.client.post(
                "/auth/login",
                json={"username": "owner", "password": "wrong"},
                headers={"Origin": "http://localhost"},
            )
            self.assertEqual(response.status_code, 401)
        limited = self.client.post(
            "/auth/login",
            json={"username": "owner", "password": "correct horse battery staple"},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(limited.status_code, 429)

    def test_webhook_uses_its_own_secret(self):
        rejected = self.client.post("/webhook/index", json={"transcription": "no"})
        accepted = self.client.post(
            "/webhook/index",
            json={"transcription": "yes"},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(accepted.status_code, 201)

    def test_explicit_group_creation_and_prefix_matching(self):
        headers={"X-Webhook-Secret": "test-webhook-secret"}
        created=self.client.post("/webhook/index",json={"transcription":"Create PW one five four."},headers=headers)
        self.assertEqual(created.status_code,201)
        self.assertTrue(created.json["groupCreated"])
        first=self.client.post("/webhook/index",json={"transcription":"Note PW154 Steph height is 700"},headers=headers)
        explicit=self.client.post("/webhook/index",json={"transcription":"Add to pw154: walkway length is 6154"},headers=headers)
        mention=self.client.post("/webhook/index",json={"transcription":"Ask whether PW154 is complete"},headers=headers)
        self.assertEqual(first.json["group"],"PW154")
        self.assertEqual(explicit.json["group"],"PW154")
        self.assertIsNone(mention.json["group"])
        with self.module.app.app_context():
            rows=self.module.db().execute("SELECT transcription,group_name FROM entries WHERE id IN (?,?) ORDER BY transcription",(first.json["id"],explicit.json["id"])).fetchall()
        self.assertEqual([(row["transcription"],row["group_name"]) for row in rows],[('Steph height is 700','PW154'),('walkway length is 6154','PW154')])

        spoken=self.client.post("/webhook/index",json={"transcription":"PW one five four another measurement is 22"},headers=headers)
        self.assertEqual(spoken.json["group"],"PW154")

    def test_group_command_is_idempotent(self):
        headers={"X-Webhook-Secret": "test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create PW155"},headers=headers)
        repeated=self.client.post("/webhook/index",json={"transcription":"create pw155"},headers=headers)
        self.assertEqual(repeated.status_code,200)
        self.assertFalse(repeated.json["groupCreated"])

    def test_natural_spoken_number_group_aliases(self):
        headers={"X-Webhook-Secret": "test-webhook-secret"}
        created=self.client.post("/webhook/index",json={"transcription":"Create Kingfisher sixty 5."},headers=headers)
        self.assertEqual(created.json["group"],"KINGFISHER65")
        spoken=self.client.post("/webhook/index",json={"transcription":"Kingfisher sixty five step height is seven hundred millimetres."},headers=headers)
        digits=self.client.post("/webhook/index",json={"transcription":"Kingfisher 65 walkway length is 6154"},headers=headers)
        self.assertEqual(spoken.json["group"],"KINGFISHER65")
        self.assertEqual(digits.json["group"],"KINGFISHER65")

    def test_removing_group_preserves_entries(self):
        webhook_headers={"X-Webhook-Secret": "test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Robin seventy two"},headers=webhook_headers)
        entry=self.client.post("/webhook/index",json={"transcription":"Robin 72 inspection complete"},headers=webhook_headers)
        login=self.login(); auth_headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        protected=self.client.delete("/api/groups/ROBIN72",headers=auth_headers)
        self.assertEqual(protected.status_code,409)
        removed=self.client.delete("/api/groups/ROBIN72?ungroup=true",headers=auth_headers)
        self.assertEqual(removed.status_code,200)
        self.assertEqual(removed.json["ungrouped"],1)
        with self.module.app.app_context():
            row=self.module.db().execute("SELECT group_name FROM entries WHERE id=?",(entry.json["id"],)).fetchone()
            self.assertIsNone(row["group_name"])


if __name__ == "__main__":
    unittest.main()
