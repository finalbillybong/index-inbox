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
        created=self.client.post("/webhook/index",json={"transcription":"Create Project four two."},headers=headers)
        self.assertEqual(created.status_code,201)
        self.assertTrue(created.json["groupCreated"])
        first=self.client.post("/webhook/index",json={"transcription":"Note PROJECT42 first site observation"},headers=headers)
        explicit=self.client.post("/webhook/index",json={"transcription":"Add to project42: follow-up observation"},headers=headers)
        mention=self.client.post("/webhook/index",json={"transcription":"Ask whether PROJECT42 is complete"},headers=headers)
        self.assertEqual(first.json["group"],"PROJECT42")
        self.assertEqual(explicit.json["group"],"PROJECT42")
        self.assertIsNone(mention.json["group"])
        with self.module.app.app_context():
            rows=self.module.db().execute("SELECT transcription,group_name FROM entries WHERE id IN (?,?) ORDER BY transcription",(first.json["id"],explicit.json["id"])).fetchall()
        self.assertEqual([(row["transcription"],row["group_name"]) for row in rows],[('first site observation','PROJECT42'),('follow-up observation','PROJECT42')])

        spoken=self.client.post("/webhook/index",json={"transcription":"Project forty two another observation"},headers=headers)
        self.assertEqual(spoken.json["group"],"PROJECT42")

    def test_group_command_is_idempotent(self):
        headers={"X-Webhook-Secret": "test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create PW155"},headers=headers)
        repeated=self.client.post("/webhook/index",json={"transcription":"create pw155"},headers=headers)
        self.assertEqual(repeated.status_code,200)
        self.assertFalse(repeated.json["groupCreated"])

    def test_natural_spoken_number_group_aliases(self):
        headers={"X-Webhook-Secret": "test-webhook-secret"}
        created=self.client.post("/webhook/index",json={"transcription":"Create Example sixty 5."},headers=headers)
        self.assertEqual(created.json["group"],"EXAMPLE65")
        spoken=self.client.post("/webhook/index",json={"transcription":"Example sixty five first observation"},headers=headers)
        digits=self.client.post("/webhook/index",json={"transcription":"Example 65 second observation"},headers=headers)
        self.assertEqual(spoken.json["group"],"EXAMPLE65")
        self.assertEqual(digits.json["group"],"EXAMPLE65")

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

    def test_group_rename_updates_entries_and_preserves_old_alias(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Rename12"},headers=webhook)
        entry=self.client.post("/webhook/index",json={"transcription":"Rename12 original entry"},headers=webhook)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        renamed=self.client.patch("/api/groups/RENAME12",json={"name":"Renamed12"},headers=headers)
        self.assertEqual(renamed.status_code,200)
        self.assertEqual(renamed.json["name"],"RENAMED12")
        old_alias=self.client.post("/webhook/index",json={"transcription":"Rename12 second entry"},headers=webhook)
        self.assertEqual(old_alias.json["group"],"RENAMED12")
        with self.module.app.app_context():
            row=self.module.db().execute("SELECT group_name FROM entries WHERE id=?",(entry.json["id"],)).fetchone()
            self.assertEqual(row["group_name"],"RENAMED12")

    def test_archive_stops_matching_and_reopen_restores_it(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Archive23"},headers=webhook)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        self.assertEqual(self.client.patch("/api/groups/ARCHIVE23",json={"archived":True},headers=headers).status_code,200)
        standalone=self.client.post("/webhook/index",json={"transcription":"Archive23 should remain standalone"},headers=webhook)
        self.assertIsNone(standalone.json["group"])
        self.assertEqual(self.client.patch("/api/groups/ARCHIVE23",json={"archived":False},headers=headers).status_code,200)
        grouped=self.client.post("/webhook/index",json={"transcription":"Archive23 should now group"},headers=webhook)
        self.assertEqual(grouped.json["group"],"ARCHIVE23")

    def test_alias_management_rejects_conflicts_and_canonical_removal(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Alias31"},headers=webhook)
        self.client.post("/webhook/index",json={"transcription":"Create Alias32"},headers=webhook)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        added=self.client.post("/api/groups/ALIAS31/aliases",json={"alias":"first project"},headers=headers)
        self.assertEqual(added.status_code,201)
        matched=self.client.post("/webhook/index",json={"transcription":"First project alias matching works"},headers=webhook)
        self.assertEqual(matched.json["group"],"ALIAS31")
        conflict=self.client.post("/api/groups/ALIAS32/aliases",json={"alias":"first project"},headers=headers)
        self.assertEqual(conflict.status_code,409)
        canonical=self.client.delete("/api/groups/ALIAS31/aliases",json={"alias":"alias31"},headers=headers)
        self.assertEqual(canonical.status_code,409)
        removed=self.client.delete("/api/groups/ALIAS31/aliases",json={"alias":"first project"},headers=headers)
        self.assertEqual(removed.status_code,200)

    def test_manual_group_assignment_move_and_unassign(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Move41"},headers=webhook)
        self.client.post("/webhook/index",json={"transcription":"Create Move42"},headers=webhook)
        entry=self.client.post("/webhook/index",json={"transcription":"standalone assignment test"},headers=webhook)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        self.assertEqual(self.client.patch(f"/api/entries/{entry.json['id']}",json={"group_name":"MOVE41"},headers=headers).status_code,200)
        self.assertEqual(self.client.patch(f"/api/entries/{entry.json['id']}",json={"group_name":"MOVE42"},headers=headers).status_code,200)
        self.client.patch("/api/groups/MOVE41",json={"archived":True},headers=headers)
        archived=self.client.patch(f"/api/entries/{entry.json['id']}",json={"group_name":"MOVE41"},headers=headers)
        self.assertEqual(archived.status_code,400)
        self.assertEqual(self.client.patch(f"/api/entries/{entry.json['id']}",json={"group_name":None},headers=headers).status_code,200)
        with self.module.app.app_context():
            row=self.module.db().execute("SELECT group_name FROM entries WHERE id=?",(entry.json["id"],)).fetchone()
            self.assertIsNone(row["group_name"])


if __name__ == "__main__":
    unittest.main()
