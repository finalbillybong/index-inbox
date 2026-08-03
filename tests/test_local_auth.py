import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


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
            self.module.db().execute("DELETE FROM local_device_tokens")
            self.module.db().execute("DELETE FROM login_attempts")
            self.module.db().execute("DELETE FROM app_settings")
            self.module.db().commit()
            self.module.set_setting_bool("automatic_execution",True)
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

    def device_login(self, device_name="Test phone"):
        return self.client.post(
            "/auth/device/login",
            json={"username": "owner", "password": "correct horse battery staple", "deviceName": device_name},
        )

    def test_device_login_bearer_access_and_logout(self):
        login=self.device_login()
        self.assertEqual(login.status_code,201)
        self.assertNotIn("Set-Cookie",login.headers)
        headers={"Authorization":f"Bearer {login.json['token']}"}
        session=self.client.get("/auth/device/session",headers=headers)
        self.assertEqual(session.status_code,200)
        self.assertEqual(session.json["deviceName"],"Test phone")
        capture=self.client.post("/api/manual",json={"transcription":"native capture"},headers=headers)
        self.assertEqual(capture.status_code,201)
        logout=self.client.post("/auth/device/logout",headers=headers)
        self.assertEqual(logout.status_code,200)
        self.assertEqual(self.client.get("/api/entries",headers=headers).status_code,401)

    def test_manual_audio_retry_with_same_id_is_idempotent(self):
        login=self.device_login(); headers={"Authorization":f"Bearer {login.json['token']}"}
        capture_id="android-retry-idempotency-test"
        with patch.object(self.module,"transcribe_upload",side_effect=[
            {"transcription":"first transcription"},
            {"transcription":"slightly different second transcription"},
        ]) as transcribe:
            first=self.client.post("/api/manual",data={
                "id":capture_id,
                "recordedAt":"1785355200000",
                "category":"note",
                "audio":(io.BytesIO(b"first audio payload"),"recording.m4a"),
            },headers=headers)
            retry=self.client.post("/api/manual",data={
                "id":capture_id,
                "recordedAt":"1785355200000",
                "category":"note",
                "audio":(io.BytesIO(b"first audio payload"),"recording.m4a"),
            },headers=headers)
        self.assertEqual(transcribe.call_count,1)
        self.assertEqual(first.status_code,201)
        self.assertEqual(retry.status_code,200)
        self.assertTrue(retry.json["duplicate"])
        self.assertEqual(retry.json["id"],first.json["id"])
        with self.module.app.app_context():
            rows=self.module.db().execute(
                "SELECT transcription FROM entries WHERE id=?",(first.json["id"],),
            ).fetchall()
        self.assertEqual([row["transcription"] for row in rows],["first transcription"])

    def test_reminder_parser_supports_relative_and_calendar_phrases(self):
        reference=datetime(2026,7,30,12,0,tzinfo=timezone.utc)
        relative=self.module.parse_reminder("Remind me to call Mum in 20 minutes",reference)
        tomorrow=self.module.parse_reminder("remind me send the invoice tomorrow at 9 am",reference)
        dated=self.module.parse_reminder("Remind me renew the certificate on 2026-08-04 at 14:30",reference)
        this_evening=self.module.parse_reminder("Remind me to have coffee at 9pm",reference)
        next_morning=self.module.parse_reminder("Remind me to make coffee at 9am",reference)
        time_first=self.module.parse_reminder("Remind me at 7.30 to have a coffee.",reference)
        dotted_meridiem=self.module.parse_reminder("Remind me at 7.30pm to have dinner",reference)
        self.assertEqual(relative,{"text":"call Mum","due_at":"2026-07-30T12:20:00+00:00"})
        self.assertEqual(tomorrow,{"text":"send the invoice","due_at":"2026-07-31T09:00:00+00:00"})
        self.assertEqual(dated,{"text":"renew the certificate","due_at":"2026-08-04T14:30:00+00:00"})
        self.assertEqual(this_evening,{"text":"have coffee","due_at":"2026-07-30T21:00:00+00:00"})
        self.assertEqual(next_morning,{"text":"make coffee","due_at":"2026-07-31T09:00:00+00:00"})
        self.assertEqual(time_first,{"text":"have a coffee","due_at":"2026-07-31T07:30:00+00:00"})
        self.assertEqual(dotted_meridiem,{"text":"have dinner","due_at":"2026-07-30T19:30:00+00:00"})
        self.assertIsNone(self.module.parse_reminder("Perhaps remind me about this sometime",reference))

    def test_manual_reminder_is_stored_and_available_in_reminder_feed(self):
        login=self.device_login();headers={"Authorization":f"Bearer {login.json['token']}"}
        with patch.object(self.module,"now",return_value="2026-07-30T12:00:00+00:00"),patch.object(
            self.module,"datetime",wraps=datetime,
        ) as mocked_datetime:
            mocked_datetime.now.return_value=datetime(2026,7,30,12,0,tzinfo=timezone.utc)
            response=self.client.post("/api/manual",json={
                "transcription":"Remind me at 7.30 to have a coffee.",
                "recordedAt":"2026-07-30T12:00:00+00:00",
                "id":"reminder-test",
            },headers=headers)
        self.assertEqual(response.status_code,201)
        reminders=self.client.get("/api/entries?view=reminders",headers=headers)
        self.assertEqual(reminders.status_code,200)
        item=next(row for row in reminders.json["items"] if row["id"]==response.json["id"])
        self.assertEqual(item["transcription"],"have a coffee")
        self.assertEqual(item["category"],"task")
        self.assertEqual(item["due_at"],"2026-07-31T07:30:00+00:00")
        completed=self.client.patch(f"/api/entries/{item['id']}",json={"reminder_completed":True},headers=headers)
        self.assertEqual(completed.status_code,200)

    def test_relative_reminder_uses_recording_time_and_persists_early_alert(self):
        login=self.device_login();headers={"Authorization":f"Bearer {login.json['token']}"}
        response=self.client.post("/api/manual",json={
            "transcription":"Remind me in two hours to check the oven with thirty minutes notice",
            "recordedAt":"2026-07-30T08:00:00+00:00",
            "id":"anchored-reminder-test",
        },headers=headers)
        self.assertEqual(response.status_code,201)
        item=next(row for row in self.client.get("/api/entries?view=reminders",headers=headers).json["items"] if row["id"]==response.json["id"])
        self.assertEqual(item["transcription"],"check the oven")
        self.assertEqual(item["due_at"],"2026-07-30T10:00:00+00:00")
        self.assertEqual(item["reminder_notify_before_minutes"],30)
        cleared=self.client.patch(f"/api/entries/{item['id']}",json={"reminder_notify_before_minutes":0},headers=headers)
        self.assertEqual(cleared.status_code,200)

    def test_device_token_is_hashed_and_wrong_token_is_rejected(self):
        login=self.device_login()
        with self.module.app.app_context():
            row=self.module.db().execute("SELECT token_hash FROM local_device_tokens").fetchone()
        self.assertNotEqual(row["token_hash"],login.json["token"])
        self.assertEqual(row["token_hash"],self.module.session_token_hash(login.json["token"]))
        self.assertEqual(self.client.get("/api/entries",headers={"Authorization":"Bearer wrong"}).status_code,401)

    def test_device_token_expiry_and_session_version_revoke_access(self):
        login=self.device_login(); headers={"Authorization":f"Bearer {login.json['token']}"}
        with self.module.app.app_context():
            self.module.db().execute("UPDATE local_device_tokens SET expires_at=?",(self.module.now(),)); self.module.db().commit()
        self.assertEqual(self.client.get("/api/entries",headers=headers).status_code,401)
        replacement=self.device_login(); replacement_headers={"Authorization":f"Bearer {replacement.json['token']}"}
        with self.module.app.app_context():
            self.module.db().execute("UPDATE local_users SET session_version=session_version+1 WHERE username='owner'"); self.module.db().commit()
        self.assertEqual(self.client.get("/api/entries",headers=replacement_headers).status_code,401)
        with self.module.app.app_context():
            self.module.db().execute("UPDATE local_users SET session_version=1 WHERE username='owner'"); self.module.db().commit()

    def test_device_login_requires_name_and_obeys_password_checks(self):
        missing=self.client.post("/auth/device/login",json={"username":"owner","password":"correct horse battery staple"})
        wrong=self.client.post("/auth/device/login",json={"username":"owner","password":"wrong","deviceName":"Phone"})
        self.assertEqual(missing.status_code,400)
        self.assertEqual(wrong.status_code,401)

    def test_native_device_can_list_and_revoke_other_devices(self):
        first=self.device_login("First phone"); second=self.device_login("Second phone")
        first_headers={"Authorization":f"Bearer {first.json['token']}"}
        second_headers={"Authorization":f"Bearer {second.json['token']}"}
        devices=self.client.get("/auth/devices",headers=first_headers)
        self.assertEqual(devices.status_code,200)
        self.assertEqual({item["deviceName"] for item in devices.json},{"First phone","Second phone"})
        self.assertEqual(sum(item["current"] for item in devices.json),1)
        revoked=self.client.post("/auth/devices/revoke-others",headers=first_headers)
        self.assertEqual(revoked.status_code,200)
        self.assertEqual(revoked.json["revoked"],1)
        self.assertEqual(self.client.get("/api/entries",headers=second_headers).status_code,401)
        self.assertEqual(self.client.get("/api/entries",headers=first_headers).status_code,200)

    def test_browser_session_cannot_manage_native_devices(self):
        self.login()
        self.assertEqual(self.client.get("/auth/devices").status_code,403)

    def test_authenticated_audio_can_be_transcribed(self):
        login = self.login()
        with patch.object(self.module, "transcribe_upload", return_value={"transcription": "locally transcribed note", "language": "en", "duration": 1.5}):
            response = self.client.post(
                "/api/transcribe",
                data={"audio": (io.BytesIO(b"browser-audio"), "recording.webm")},
                headers={"Origin": "http://localhost", "X-CSRF-Token": login.json["csrfToken"]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["transcription"], "locally transcribed note")

    def test_audio_only_manual_capture_uses_local_transcription(self):
        login = self.login()
        with patch.object(self.module, "transcribe_upload", return_value={"transcription": "audio only note", "language": "en", "duration": 1.0}):
            response = self.client.post(
                "/api/manual",
                data={"transcription": "", "category": "note", "recordedAt": "1784409957261", "audio": (io.BytesIO(b"browser-audio"), "recording.webm")},
                headers={"Origin": "http://localhost", "X-CSRF-Token": login.json["csrfToken"]},
            )
        self.assertEqual(response.status_code, 201)
        with self.module.app.app_context():
            row = self.module.db().execute("SELECT transcription,audio_path FROM entries WHERE id=?", (response.json["id"],)).fetchone()
        self.assertEqual(row["transcription"], "audio only note")
        self.assertTrue(row["audio_path"].endswith(".webm"))

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

    def test_trusted_cloudflare_peer_resolves_visitor_and_records_peer(self):
        previous_hops=self.module.TRUSTED_PROXY_HOPS; previous_networks=self.module.TRUSTED_PROXY_NETWORKS
        self.module.TRUSTED_PROXY_HOPS=1; self.module.TRUSTED_PROXY_NETWORKS=(self.module.ipaddress.ip_network("172.18.0.0/16"),)
        try:
            response=self.client.post("/auth/login",json={"username":"missing","password":"wrong"},headers={"Origin":"http://localhost","CF-Connecting-IP":"203.0.113.25"},environ_base={"REMOTE_ADDR":"172.18.0.4"})
            self.assertEqual(response.status_code,401)
            with self.module.app.app_context():attempt=self.module.db().execute("SELECT source_ip,peer_ip FROM login_attempts ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual((attempt["source_ip"],attempt["peer_ip"]),("203.0.113.25","172.18.0.4"))
        finally:self.module.TRUSTED_PROXY_HOPS=previous_hops; self.module.TRUSTED_PROXY_NETWORKS=previous_networks

    def test_forwarded_headers_from_untrusted_peer_are_ignored(self):
        previous_hops=self.module.TRUSTED_PROXY_HOPS; previous_networks=self.module.TRUSTED_PROXY_NETWORKS
        self.module.TRUSTED_PROXY_HOPS=1; self.module.TRUSTED_PROXY_NETWORKS=(self.module.ipaddress.ip_network("172.18.0.0/16"),)
        try:
            self.client.post("/auth/login",json={"username":"missing","password":"wrong"},headers={"Origin":"http://localhost","CF-Connecting-IP":"203.0.113.99","X-Forwarded-For":"203.0.113.99"},environ_base={"REMOTE_ADDR":"192.168.1.50"})
            with self.module.app.app_context():attempt=self.module.db().execute("SELECT source_ip,peer_ip FROM login_attempts ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual((attempt["source_ip"],attempt["peer_ip"]),("192.168.1.50","192.168.1.50"))
        finally:self.module.TRUSTED_PROXY_HOPS=previous_hops; self.module.TRUSTED_PROXY_NETWORKS=previous_networks

    def test_configured_forwarded_hop_is_used_for_throttling(self):
        previous_hops=self.module.TRUSTED_PROXY_HOPS; previous_networks=self.module.TRUSTED_PROXY_NETWORKS
        self.module.TRUSTED_PROXY_HOPS=1; self.module.TRUSTED_PROXY_NETWORKS=(self.module.ipaddress.ip_network("172.18.0.0/16"),)
        try:
            for _ in range(5):self.client.post("/auth/login",json={"username":"attacker","password":"wrong"},headers={"Origin":"http://localhost","CF-Connecting-IP":"203.0.113.40"},environ_base={"REMOTE_ADDR":"172.18.0.4"})
            limited=self.client.post("/auth/login",json={"username":"different","password":"wrong"},headers={"Origin":"http://localhost","CF-Connecting-IP":"203.0.113.40"},environ_base={"REMOTE_ADDR":"172.18.0.4"})
            other=self.client.post("/auth/login",json={"username":"owner","password":"correct horse battery staple"},headers={"Origin":"http://localhost","CF-Connecting-IP":"203.0.113.41"},environ_base={"REMOTE_ADDR":"172.18.0.4"})
            self.assertEqual(limited.status_code,429)
            self.assertEqual(other.status_code,200)
        finally:self.module.TRUSTED_PROXY_HOPS=previous_hops; self.module.TRUSTED_PROXY_NETWORKS=previous_networks

    def test_webhook_uses_its_own_secret(self):
        rejected = self.client.post("/webhook/index", json={"transcription": "no"})
        accepted = self.client.post(
            "/webhook/index",
            json={"transcription": "yes"},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(accepted.status_code, 201)

    def test_index_ring_secret_requires_password_and_rotation_invalidates_old_secret(self):
        login=self.login()
        headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        metadata=self.client.get("/api/integrations/index-ring")
        self.assertEqual(metadata.status_code,200)
        self.assertEqual(metadata.json["webhookPath"],"/webhook/index")
        self.assertTrue(metadata.json["requiresPassword"])
        self.assertNotIn("test-webhook-secret",json.dumps(metadata.json))
        wrong=self.client.post("/api/integrations/index-ring/reveal",json={"password":"wrong"},headers=headers)
        self.assertEqual(wrong.status_code,401)
        revealed=self.client.post("/api/integrations/index-ring/reveal",
          json={"password":"correct horse battery staple"},headers=headers)
        self.assertEqual(revealed.json["secret"],"test-webhook-secret")
        rotated=self.client.post("/api/integrations/index-ring/rotate",
          json={"password":"correct horse battery staple"},headers=headers)
        self.assertEqual(rotated.status_code,200)
        replacement=rotated.json["secret"]
        self.assertNotEqual(replacement,"test-webhook-secret")
        old=self.client.post("/webhook/index",json={"transcription":"old secret"},
          headers={"X-Webhook-Secret":"test-webhook-secret"})
        device=self.device_login()
        new=self.client.post("/webhook/index",json={"transcription":"new secret"},
          headers={"X-Webhook-Secret":replacement,"Authorization":f"Bearer {device.json['token']}"})
        self.assertEqual(old.status_code,401)
        self.assertEqual(new.status_code,201)

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

    def test_change_feed_reports_typed_capture_events_without_note_text(self):
        login=self.login()
        initial=self.client.get("/api/changes").json["sequence"]
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Event eighty eight"},headers=webhook)
        self.client.post("/webhook/index",json={"transcription":"Event 88 confidential grouped words"},headers=webhook)
        self.client.post("/webhook/index",json={"transcription":"confidential standalone words"},headers=webhook)
        feed=self.client.get(f"/api/changes?since={initial}")
        self.assertEqual(feed.status_code,200)
        kinds=[event["kind"] for event in feed.json["events"]]
        self.assertEqual(kinds,["interpreted_operation","interpreted_operation","capture_standalone"])
        capture_events=[event for event in feed.json["events"] if event["kind"].startswith("capture_")]
        self.assertTrue(all(event["details"] for event in capture_events))
        with self.module.app.app_context():
            self.assertTrue(all(self.module.db().execute("SELECT 1 FROM entries WHERE id=?",(event["details"],)).fetchone() for event in capture_events))
        messages=" ".join(event["message"] for event in feed.json["events"]).lower()
        self.assertNotIn("confidential",messages)
        self.assertGreater(feed.json["sequence"],initial)
        empty=self.client.get(f"/api/changes?since={feed.json['sequence']}")
        self.assertEqual(empty.json["events"],[])

    def test_authenticated_long_poll_returns_new_capture_immediately(self):
        login=self.device_login(); bearer={"Authorization":f"Bearer {login.json['token']}"}
        initial=self.client.get("/api/changes",headers=bearer).json["sequence"]
        self.client.post("/webhook/index",json={"transcription":"instant notification note"},headers={"X-Webhook-Secret":"test-webhook-secret"})
        response=self.client.get(f"/api/changes/wait?since={initial}&timeout=1",headers=bearer)
        self.assertEqual(response.status_code,200)
        self.assertEqual([event["kind"] for event in response.json["events"]],["capture_standalone"])
        self.assertGreater(response.json["sequence"],initial)

    def test_long_poll_validates_parameters(self):
        login=self.device_login(); bearer={"Authorization":f"Bearer {login.json['token']}"}
        response=self.client.get("/api/changes/wait?since=nope",headers=bearer)
        self.assertEqual(response.status_code,400)

    def test_authenticated_android_update_manifest_and_download(self):
        login=self.device_login(); bearer={"Authorization":f"Bearer {login.json['token']}"}
        previous=(self.module.ANDROID_UPDATE_VERSION_CODE,self.module.ANDROID_UPDATE_VERSION_NAME,self.module.ANDROID_UPDATE_APK)
        try:
            release=self.module.DATA_DIR/"test-index-inbox.apk";release.write_bytes(b"signed apk fixture")
            self.module.ANDROID_UPDATE_VERSION_CODE=19
            self.module.ANDROID_UPDATE_VERSION_NAME="1.0.0"
            self.module.ANDROID_UPDATE_APK=release
            manifest=self.client.get("/api/android-update",headers=bearer)
            self.assertEqual(manifest.status_code,200)
            self.assertEqual((manifest.json["versionCode"],manifest.json["versionName"]),(19,"1.0.0"))
            self.assertEqual(manifest.json["sha256"],self.module.hashlib.sha256(b"signed apk fixture").hexdigest())
            download=self.client.get("/api/android-update/apk",headers=bearer)
            self.assertEqual(download.data,b"signed apk fixture")
            download.close()
            self.assertEqual(self.client.get("/api/android-update").status_code,401)
        finally:
            self.module.ANDROID_UPDATE_VERSION_CODE,self.module.ANDROID_UPDATE_VERSION_NAME,self.module.ANDROID_UPDATE_APK=previous

    def test_change_feed_reports_repeated_and_unrecognized_group_commands(self):
        self.login(); initial=self.client.get("/api/changes").json["sequence"]
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Notice ninety nine"},headers=webhook)
        self.client.post("/webhook/index",json={"transcription":"Create Notice 99"},headers=webhook)
        unmatched=self.client.post("/webhook/index",json={"transcription":"Create !!!"},headers=webhook)
        self.assertEqual(unmatched.status_code,201)
        self.assertIsNone(unmatched.json["group"])
        events=self.client.get(f"/api/changes?since={initial}").json["events"]
        self.assertEqual([event["kind"] for event in events],["interpreted_operation","interpreted_operation","interpreted_operation"])

    def test_change_feed_reports_rejected_webhook(self):
        self.login(); initial=self.client.get("/api/changes").json["sequence"]
        rejected=self.client.post("/webhook/index",json={"transcription":"private rejected text"})
        self.assertEqual(rejected.status_code,401)
        events=self.client.get(f"/api/changes?since={initial}").json["events"]
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["kind"],"webhook_rejected")
        self.assertNotIn("private",events[0]["message"].lower())
        self.assertEqual(events[0]["details"],"")

    def test_change_feed_reports_ingestion_failure_without_exception_details(self):
        self.login(); initial=self.client.get("/api/changes").json["sequence"]
        with patch.object(self.module,"store_entry",side_effect=RuntimeError("sensitive internal failure")):
            failed=self.client.post("/webhook/index",json={"transcription":"private failed text"},headers={"X-Webhook-Secret":"test-webhook-secret"})
        self.assertEqual(failed.status_code,500)
        events=self.client.get(f"/api/changes?since={initial}").json["events"]
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["kind"],"ingest_error")
        self.assertNotIn("sensitive",events[0]["message"].lower())
        self.assertNotIn("private",events[0]["message"].lower())
        self.assertEqual(events[0]["details"],"")

    def test_change_feed_rejects_invalid_sequence(self):
        self.login()
        response=self.client.get("/api/changes?since=invalid")
        self.assertEqual(response.status_code,400)

    def test_interpretation_contract_covers_deterministic_operations_without_mutation(self):
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        with self.module.app.app_context():
            stamp=self.module.now()
            self.module.db().execute("INSERT OR IGNORE INTO note_groups(name,display_name,created_at) VALUES(?,?,?)",("INTERPRET42","INTERPRET42",stamp))
            self.module.db().execute("INSERT OR IGNORE INTO note_group_aliases(alias,group_name) VALUES(?,?)",("interpret 42","INTERPRET42"))
            self.module.db().execute("""INSERT INTO entries(id,created_at,transcription,payload_json,title,category)
              VALUES(?,?,?,?,?,?)""",("interpret-open-item",stamp,"buy oat milk","{}","Shopping milk","task"))
            self.module.db().commit()
            before=(self.module.db().execute("SELECT count(*) FROM entries").fetchone()[0],self.module.db().execute("SELECT count(*) FROM note_groups").fetchone()[0],self.module.db().execute("SELECT count(*) FROM activity").fetchone()[0])

        cases={
            "create_item": {"text":"A plain captured thought"},
            "create_collection": {"text":"Create Project ninety nine"},
            "add_to_collection": {"text":"Interpret 42 first checklist item"},
            "set_reminder": {"text":"Remind me tomorrow at 9am to call Mum","referenceAt":"2026-08-02T12:00:00+00:00"},
            "complete_item": {"text":"Complete Shopping milk"},
            "search_items": {"text":"Find oat milk"},
        }
        results={operation:self.client.post("/api/interpret",json=body,headers=headers) for operation,body in cases.items()}
        for operation,response in results.items():
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json["version"],"1.0")
            self.assertEqual(response.json["operation"],operation)
            self.assertIsInstance(response.json["arguments"],dict)
            self.assertIsInstance(response.json["confidence"],float)
            self.assertIsInstance(response.json["explanation"],str)
            self.assertIn("ambiguous",response.json)
            self.assertIn("requiresConfirmation",response.json)
        self.assertEqual(results["add_to_collection"].json["arguments"]["collectionName"],"INTERPRET42")
        self.assertEqual(results["complete_item"].json["arguments"]["itemId"],"interpret-open-item")
        self.assertTrue(results["complete_item"].json["requiresConfirmation"])
        with self.module.app.app_context():
            after=(self.module.db().execute("SELECT count(*) FROM entries").fetchone()[0],self.module.db().execute("SELECT count(*) FROM note_groups").fetchone()[0],self.module.db().execute("SELECT count(*) FROM activity").fetchone()[0])
        self.assertEqual(after,before)

    def test_interpretation_marks_invalid_and_ambiguous_requests_for_confirmation(self):
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        invalid=self.client.post("/api/interpret",json={"text":"Create !!!"},headers=headers)
        missing=self.client.post("/api/interpret",json={"text":"Complete something that does not exist"},headers=headers)
        empty=self.client.post("/api/interpret",json={"text":""},headers=headers)
        for response in (invalid,missing,empty):
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json["ambiguous"])
            self.assertTrue(response.json["requiresConfirmation"])

    def test_natural_shopping_list_commands_support_auto_and_accept(self):
        login=self.login();headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        with self.module.app.app_context():
            stamp=self.module.now()
            self.module.db().execute("INSERT INTO note_groups(name,display_name,created_at) VALUES(?,?,?)",("SHOPPING","SHOPPING",stamp))
            self.module.db().execute("INSERT INTO note_group_aliases(alias,group_name) VALUES(?,?)",("shopping","SHOPPING"));self.module.db().commit()
        auto=self.client.post("/api/manual",json={"transcription":"add milk to my shopping list","title":"","category":"note","recordedAt":"1785697828929","id":"f8c5b301-f19c-4c48-9dfc-3e9227c7ea3b","interpretationAction":"auto"},headers=headers)
        accepted=self.client.post("/api/manual",json={"transcription":"Add bread to my shopping list.","title":"","category":"note","recordedAt":"1785697918115","id":"e1d8335d-9ff2-4d89-b09e-52681aa15863","interpretationAction":"accept"},headers=headers)
        self.assertEqual((auto.status_code,accepted.status_code),(201,201))
        with self.module.app.app_context():
            rows=self.module.db().execute("SELECT transcription,group_name FROM entries WHERE id IN (?,?) ORDER BY transcription",(auto.json["id"],accepted.json["id"])).fetchall()
        self.assertEqual([(row["transcription"],row["group_name"]) for row in rows],[("bread","SHOPPING"),("milk","SHOPPING")])
        unknown=self.client.post("/api/interpret",json={"text":"Add eggs to my groceries list"},headers=headers)
        self.assertEqual(unknown.json["operation"],"add_to_collection");self.assertTrue(unknown.json["ambiguous"]);self.assertTrue(unknown.json["requiresConfirmation"])

    def test_interpretation_endpoint_requires_auth_and_valid_reference_time(self):
        self.assertEqual(self.client.post("/api/interpret",json={"text":"Find milk"}).status_code,401)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        self.assertEqual(self.client.post("/api/interpret",json={"text":"Find milk","referenceAt":"not-a-date"},headers=headers).status_code,400)

    def test_optional_self_hosted_model_is_validated_confirmed_and_falls_back_safely(self):
        login=self.login();headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        previous=(self.module.INTERPRETATION_MODEL_URL,self.module.INTERPRETATION_MODEL_NAME)
        self.module.INTERPRETATION_MODEL_URL="http://ollama:11434";self.module.INTERPRETATION_MODEL_NAME="test-model:4b"
        with self.module.app.app_context():
            self.module.db().execute("INSERT OR IGNORE INTO note_groups(name,display_name,created_at) VALUES('SHOPPING','SHOPPING',?)",(self.module.now(),));self.module.db().commit()
        valid=json.dumps({"message":{"content":json.dumps({"operation":"add_to_collection","collectionName":"SHOPPING","text":"milk","explanation":"The user asked to put milk on the shopping list."})}}).encode()
        tags=json.dumps({"models":[{"name":"test-model:4b"}]}).encode()
        def local_model(request_value,timeout=None):
            url=request_value.full_url if hasattr(request_value,"full_url") else str(request_value)
            return io.BytesIO(tags if url.endswith("/api/tags") else valid)
        try:
            self.assertTrue(self.client.patch("/api/model",json={"enabled":True},headers=headers).json["enabled"])
            with patch.object(self.module.urllib.request,"urlopen",side_effect=local_model):
                self.assertEqual(self.client.post("/api/model/test",headers=headers).status_code,200)
                proposal=self.client.post("/api/interpret",json={"text":"Could you put milk on my shopping list?"},headers=headers)
            self.assertEqual(proposal.json["interpretationSource"],"model",self.client.get("/api/model",headers=headers).json);self.assertEqual(proposal.json["operation"],"add_to_collection")
            self.assertTrue(proposal.json["requiresConfirmation"]);self.assertEqual(proposal.json["confidence"],0.7)
            no_match=io.BytesIO(json.dumps({"message":{"content":json.dumps({"operation":"no_match","explanation":"private raw model output"})}}).encode())
            with patch.object(self.module.urllib.request,"urlopen",return_value=no_match):
                fallback=self.client.post("/api/interpret",json={"text":"Could you put bread somewhere useful?"},headers=headers)
            self.assertEqual(fallback.json["interpretationSource"],"deterministic_fallback");self.assertEqual(fallback.json["operation"],"create_item")
            status=self.client.get("/api/model",headers=headers).json;self.assertEqual(status["state"],"unavailable");self.assertNotIn("private raw model output",status["message"])
        finally:self.module.INTERPRETATION_MODEL_URL,self.module.INTERPRETATION_MODEL_NAME=previous

    def test_manual_preview_requires_completion_confirmation_and_supports_plain_override(self):
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        with self.module.app.app_context():
            self.module.db().execute("""INSERT INTO entries(id,created_at,transcription,payload_json,title,category)
              VALUES(?,?,?,?,?,?)""",("preview-complete-item",self.module.now(),"buy coffee","{}","Coffee","task"))
            self.module.db().commit()
        proposed=self.client.post("/api/manual",json={"transcription":"Complete Coffee","interpretationAction":"accept"},headers=headers)
        self.assertEqual(proposed.status_code,409)
        self.assertEqual(proposed.json["interpretation"]["operation"],"complete_item")
        self.assertTrue(proposed.json["interpretation"]["requiresConfirmation"])
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute("SELECT completed FROM entries WHERE id='preview-complete-item'").fetchone()[0],0)
        confirmed=self.client.post("/api/manual",json={"transcription":"Complete Coffee","interpretationAction":"confirm"},headers=headers)
        self.assertEqual(confirmed.status_code,200)
        self.assertEqual(confirmed.json["operation"],"complete_item")
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute("SELECT completed FROM entries WHERE id='preview-complete-item'").fetchone()[0],1)

        plain=self.client.post("/api/manual",json={"transcription":"Create !!!","interpretationAction":"plain"},headers=headers)
        self.assertEqual(plain.status_code,201)
        with self.module.app.app_context():
            row=self.module.db().execute("SELECT transcription,group_name FROM entries WHERE id=?",(plain.json["id"],)).fetchone()
        self.assertEqual(row["transcription"],"Create !!!")
        self.assertIsNone(row["group_name"])

    def test_safe_automation_setting_policy_receipts_idempotency_and_undo(self):
        login=self.login();headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        with self.module.app.app_context():self.module.set_setting_bool("automatic_execution",False)
        initial=self.client.get("/api/automation",headers=headers)
        self.assertEqual(initial.status_code,200);self.assertFalse(initial.json["enabled"])
        self.assertEqual(initial.json["threshold"],0.95)
        self.assertEqual(set(initial.json["operations"]),{"create_collection","add_to_collection","set_reminder"})

        capture={"id":"phase-six-disabled-auto","transcription":"Create Safety sixty six","interpretationAction":"auto"}
        disabled=self.client.post("/api/manual",json=capture,headers=headers)
        self.assertEqual(disabled.status_code,201);self.assertEqual(disabled.json["operationOutcome"],"saved_plain_safely")
        receipt_id=disabled.json["operationReceiptId"]
        with self.module.app.app_context():
            self.assertIsNone(self.module.find_group("SAFETY66"))
            self.assertIsNotNone(self.module.db().execute("SELECT id FROM entries WHERE id=?",(disabled.json["id"],)).fetchone())

        duplicate=self.client.post("/api/manual",json=capture,headers=headers)
        self.assertEqual(duplicate.status_code,200);self.assertTrue(duplicate.json["duplicate"]);self.assertEqual(duplicate.json["operationReceiptId"],receipt_id)
        undone=self.client.post(f"/api/operations/{receipt_id}/undo",headers=headers)
        self.assertEqual(undone.status_code,200)
        self.assertEqual(self.client.post(f"/api/operations/{receipt_id}/undo",headers=headers).status_code,409)
        activity=self.client.get("/api/activity",headers=headers).json
        original=next(row for row in activity if receipt_id in row["details"])
        self.assertFalse(json.loads(original["details"])["reversible"])

        enabled=self.client.patch("/api/automation",json={"enabled":True},headers=headers)
        self.assertTrue(enabled.json["enabled"])
        command={"id":"phase-six-enabled-auto","transcription":"Create Safety sixty seven","interpretationAction":"auto"}
        executed=self.client.post("/api/manual",json=command,headers=headers)
        self.assertEqual(executed.status_code,201);self.assertEqual(executed.json["operationOutcome"],"executed")
        with self.module.app.app_context():self.assertIsNotNone(self.module.find_group("SAFETY67"))
        self.assertEqual(self.client.post(f"/api/operations/{executed.json['operationReceiptId']}/undo",headers=headers).status_code,200)
        with self.module.app.app_context():self.assertIsNone(self.module.find_group("SAFETY67"))

    def test_automation_never_executes_completion_or_ambiguous_commands(self):
        login=self.login();headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        self.client.patch("/api/automation",json={"enabled":True},headers=headers)
        with self.module.app.app_context():
            self.module.db().execute("INSERT INTO entries(id,created_at,transcription,payload_json,title,category) VALUES(?,?,?,?,?,?)",("never-auto-complete",self.module.now(),"buy tea","{}","Tea","task"));self.module.db().commit()
        completion=self.client.post("/api/manual",json={"id":"auto-completion","transcription":"Complete Tea","interpretationAction":"auto"},headers=headers)
        ambiguous=self.client.post("/api/manual",json={"id":"auto-ambiguous","transcription":"Create !!!","interpretationAction":"auto"},headers=headers)
        self.assertEqual(completion.json["operationOutcome"],"saved_plain_safely")
        self.assertEqual(ambiguous.json["operationOutcome"],"saved_plain_safely")
        with self.module.app.app_context():self.assertEqual(self.module.db().execute("SELECT completed FROM entries WHERE id='never-auto-complete'").fetchone()[0],0)

    def test_index_ring_uses_safe_policy_and_defers_completion_for_review(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret","X-Index-Trigger":"double-click-hold"}
        with self.module.app.app_context():
            self.module.db().execute("INSERT INTO entries(id,created_at,transcription,payload_json,title,category) VALUES(?,?,?,?,?,?)",("ring-target",self.module.now(),"prepare espresso phase seven","{}","Ring espresso phase seven","task"));self.module.db().commit()
        response=self.client.post("/webhook/index",data={"transcription":"Complete Ring espresso phase seven","recordedAt":"1785686400000","client":"ring"},headers=webhook)
        self.assertEqual(response.status_code,201);self.assertEqual(response.json["operationOutcome"],"awaiting_confirmation")
        with self.module.app.app_context():
            self.assertEqual(self.module.db().execute("SELECT completed FROM entries WHERE id='ring-target'").fetchone()[0],0)
            command=self.module.db().execute("SELECT payload_json FROM entries WHERE id=?",(response.json["id"],)).fetchone()
            self.assertEqual(json.loads(command["payload_json"])["indexTrigger"],"double-click-hold")
        login=self.login();activity=self.client.get("/api/activity").json
        receipt=next(row for row in activity if response.json["operationReceiptId"] in row["details"])
        details=json.loads(receipt["details"]);self.assertEqual(details["source"],"ring");self.assertEqual(details["targetId"],response.json["id"])
        self.assertTrue(details["confirmable"]);self.assertIn("needs confirmation",receipt["message"].lower())
        headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        confirmed=self.client.post(f"/api/operations/{response.json['operationReceiptId']}/confirm",headers=headers)
        self.assertEqual(confirmed.status_code,200);self.assertEqual(confirmed.json["targetId"],"ring-target")
        with self.module.app.app_context():
            target=self.module.db().execute("SELECT completed FROM entries WHERE id='ring-target'").fetchone();command=self.module.db().execute("SELECT archived FROM entries WHERE id=?",(response.json["id"],)).fetchone()
            self.assertEqual((target["completed"],command["archived"]),(1,1))
        self.assertEqual(self.client.post(f"/api/operations/{response.json['operationReceiptId']}/confirm",headers=headers).status_code,409)
        self.assertEqual(self.client.post(f"/api/operations/{response.json['operationReceiptId']}/undo",headers=headers).status_code,200)
        with self.module.app.app_context():self.assertEqual(self.module.db().execute("SELECT completed FROM entries WHERE id='ring-target'").fetchone()[0],0)

    def test_index_ring_audio_filename_is_a_stable_retry_key_and_payload_metadata_is_preserved(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret","X-Index-Trigger":"single-click-hold"}
        def send(content):
            return self.client.post("/webhook/index",data={"transcription":"Note retry-safe Ring audio","recordedAt":"1785686400000","client":"ring","audio":(io.BytesIO(content),"ring-recording-42.m4a","audio/mp4")},headers=webhook)
        first=send(b"first audio bytes");second=send(b"different retry bytes")
        self.assertEqual(first.status_code,201);self.assertEqual(second.status_code,200);self.assertTrue(second.json["duplicate"]);self.assertEqual(second.json["id"],first.json["id"])
        with self.module.app.app_context():
            rows=self.module.db().execute("SELECT payload_json FROM entries WHERE id=?",(first.json["id"],)).fetchall();self.assertEqual(len(rows),1)
            payload=json.loads(rows[0]["payload_json"]);self.assertEqual(payload["recordingId"],"ring-recording-42");self.assertEqual(payload["indexTrigger"],"single-click-hold")

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

    def test_collection_can_be_created_and_listed_via_canonical_api(self):
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        created=self.client.post("/api/collections",json={"name":"Shopping42"},headers=headers)
        self.assertEqual(created.status_code,201)
        self.assertEqual(created.json["name"],"SHOPPING42")
        self.assertEqual(created.json["entries"],0)
        self.assertEqual(self.client.post("/api/collections",json={"name":"shopping42"},headers=headers).status_code,409)
        collections=self.client.get("/api/collections").json
        self.assertIn("SHOPPING42",[collection["name"] for collection in collections])
        self.assertIn("SHOPPING42",[group["name"] for group in self.client.get("/api/groups").json])

    def test_multi_word_collection_commands_create_and_rename_consistently(self):
        login=self.login();headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        preview=self.client.post("/api/interpret",json={"text":"Create a collection called Books to Read"},headers=headers)
        self.assertEqual(preview.json["operation"],"create_collection")
        self.assertEqual(preview.json["arguments"]["name"],"BOOKS TO READ")
        created=self.client.post("/api/manual",json={"transcription":"Create a collection called Books to Read","interpretationAction":"accept"},headers=headers)
        self.assertEqual(created.status_code,201)
        renamed=self.client.patch("/api/collections/BOOKS%20TO%20READ",json={"name":"Reading List"},headers=headers)
        self.assertEqual(renamed.status_code,200)
        self.assertEqual(renamed.json["name"],"READING LIST")
        aliases=self.client.get("/api/collections/READING%20LIST/aliases",headers=headers).json["aliases"]
        self.assertIn("books to read",aliases)

    def test_accepted_search_command_is_never_stored_as_an_item(self):
        login=self.login();headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        response=self.client.post("/api/manual",json={"transcription":"Find The Hobbit","interpretationAction":"accept"},headers=headers)
        self.assertEqual(response.status_code,409)
        with self.module.app.app_context():
            self.assertIsNone(self.module.db().execute("SELECT id FROM entries WHERE transcription='Find The Hobbit'").fetchone())

    def test_deletion_emits_a_live_change_event(self):
        login=self.login();headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        created=self.client.post("/api/manual",json={"transcription":"delete live audit","interpretationAction":"plain"},headers=headers)
        baseline=self.client.get("/api/changes",headers=headers).json["sequence"]
        self.assertEqual(self.client.delete(f"/api/items/{created.json['id']}",headers=headers).status_code,200)
        feed=self.client.get(f"/api/changes?since={baseline}",headers=headers).json
        event=next(row for row in feed["events"] if row["kind"]=="item_deleted")
        self.assertEqual(event["details"],created.json["id"])

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

    def test_group_timeline_is_chronological_and_includes_archived_groups(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Timeline seventy seven"},headers=webhook)
        later=self.client.post("/webhook/index",json={"transcription":"Timeline 77 later observation","recordedAt":"2026-07-19T11:00:00Z"},headers=webhook)
        earlier=self.client.post("/webhook/index",json={"transcription":"Timeline 77 earlier observation","recordedAt":"2026-07-19T10:00:00Z"},headers=webhook)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        self.client.patch("/api/groups/TIMELINE77",json={"archived":True},headers=headers)
        response=self.client.get("/api/groups/TIMELINE77/timeline")
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json["group"]["archived"])
        self.assertEqual([item["id"] for item in response.json["items"]],[earlier.json["id"],later.json["id"]])

    def test_group_exports_are_scoped_and_zip_includes_audio_and_markdown(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Export sixty six"},headers=webhook)
        grouped=self.client.post("/webhook/index",json={"transcription":"Export 66 grouped export words"},headers=webhook)
        self.client.post("/webhook/index",json={"transcription":"unrelated export words"},headers=webhook)
        audio_name=f"{grouped.json['id']}.webm"
        with self.module.app.app_context():
            (self.module.AUDIO_DIR/audio_name).write_bytes(b"test-audio")
            self.module.db().execute("UPDATE entries SET audio_path=?,audio_mime=? WHERE id=?",(audio_name,"audio/webm",grouped.json["id"])); self.module.db().commit()
        self.login()
        json_response=self.client.get("/api/groups/EXPORT66/export/json")
        self.assertEqual(json_response.status_code,200)
        exported=json.loads(json_response.data)
        self.assertEqual(len(exported),1)
        self.assertEqual(exported[0]["group_name"],"EXPORT66")
        markdown=self.client.get("/api/groups/EXPORT66/export/markdown").text
        self.assertIn("# EXPORT66",markdown)
        self.assertIn("grouped export words",markdown)
        self.assertNotIn("unrelated export words",markdown)
        archive=self.client.get("/api/groups/EXPORT66/export/zip")
        with zipfile.ZipFile(io.BytesIO(archive.data)) as bundle:
            self.assertEqual(set(bundle.namelist()),{"entries.json","notes.md",f"audio/{audio_name}"})
            self.assertEqual(bundle.read(f"audio/{audio_name}"),b"test-audio")

    def test_unified_items_compatibility_fixture_preserves_legacy_content(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Compatibility 91"},headers=webhook)
        payload={
            "id":"ring-compatibility-91",
            "transcription":"Compatibility 91 preserve this reminder",
            "recordedAt":"2026-08-01T18:30:00Z",
            "dueAt":"2026-08-02T19:30:00Z",
            "reminderNotifyBeforeMinutes":15,
            "triggerType":"index-ring",
            "custom":{"firmware":"fixture"},
        }
        capture=self.client.post("/webhook/index",json=payload,headers=webhook)
        self.assertEqual(capture.status_code,201)
        entry_id=capture.json["id"]
        audio_name=f"{entry_id}.webm"
        with self.module.app.app_context():
            (self.module.AUDIO_DIR/audio_name).write_bytes(b"compatibility-audio")
            self.module.db().execute("""UPDATE entries SET audio_path=?,audio_mime=?,processed=1,starred=1
              WHERE id=?""",(audio_name,"audio/webm",entry_id))
            self.module.db().commit()

        self.login()
        row=next(item for item in self.client.get("/api/entries?group_name=COMPATIBILITY91").json["items"] if item["id"]==entry_id)
        self.assertEqual(row["recorded_at"],"2026-08-01T18:30:00Z")
        self.assertEqual(row["group_name"],"COMPATIBILITY91")
        self.assertEqual(row["due_at"],"2026-08-02T19:30:00Z")
        self.assertEqual(row["reminder_notify_before_minutes"],15)
        self.assertEqual((row["processed"],row["reminder_completed"]),(1,0))
        self.assertEqual(json.loads(row["payload_json"])["custom"],{"firmware":"fixture"})
        audio=self.client.get(f"/api/entries/{entry_id}/audio")
        self.assertEqual(audio.data,b"compatibility-audio")
        audio.close()

        exported=json.loads(self.client.get("/api/groups/COMPATIBILITY91/export/json").data)
        self.assertEqual([item["id"] for item in exported],[entry_id])
        archive=self.client.get("/api/groups/COMPATIBILITY91/export/zip")
        with zipfile.ZipFile(io.BytesIO(archive.data)) as bundle:
            self.assertEqual(bundle.read(f"audio/{audio_name}"),b"compatibility-audio")

        duplicate=self.client.post("/webhook/index",json={**payload,"transcription":"changed retry text"},headers=webhook)
        self.assertEqual(duplicate.status_code,200)
        self.assertTrue(duplicate.json["duplicate"])
        events=self.client.get("/api/activity").json
        self.assertTrue(any(event["kind"]=="interpreted_operation" and json.loads(event["details"]).get("targetId")==entry_id for event in events))

    def test_item_completion_and_collection_api_are_additive(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Additive 52"},headers=webhook)
        capture=self.client.post("/webhook/index",json={
            "id":"additive-item-52",
            "transcription":"standalone before assignment",
            "dueAt":"2026-08-05T09:00:00Z",
        },headers=webhook)
        login=self.device_login();headers={"Authorization":f"Bearer {login.json['token']}"}
        initial=self.client.get("/api/changes",headers=headers).json["sequence"]
        assigned=self.client.patch(f"/api/items/{capture.json['id']}",json={
            "collection_name":"ADDITIVE52",
            "completed":True,
        },headers=headers)
        self.assertEqual(assigned.status_code,200)

        item=next(row for row in self.client.get("/api/items?collection_name=ADDITIVE52&completed=1",headers=headers).json["items"] if row["id"]==capture.json["id"])
        self.assertEqual(item["collection_name"],"ADDITIVE52")
        self.assertEqual(item["group_name"],"ADDITIVE52")
        self.assertEqual((item["completed"],item["processed"],item["reminder_completed"]),(1,0,0))
        legacy=next(row for row in self.client.get("/api/entries?group_name=ADDITIVE52",headers=headers).json["items"] if row["id"]==capture.json["id"])
        self.assertEqual(legacy["group_name"],"ADDITIVE52")
        self.assertNotIn("collection_name",legacy)
        self.assertTrue(any(row["name"]=="ADDITIVE52" for row in self.client.get("/api/collections",headers=headers).json))

        changes=self.client.get(f"/api/changes?since={initial}",headers=headers).json["events"]
        self.assertEqual([event["kind"] for event in changes],["collection_changed","item_completed"])
        exported=json.loads(self.client.get("/api/collections/ADDITIVE52/export/json",headers=headers).data)
        self.assertEqual((exported[0]["collection_name"],exported[0]["completed"]),("ADDITIVE52",1))
        markdown=self.client.get("/api/collections/ADDITIVE52/export/markdown",headers=headers).text
        self.assertIn("Collection: ADDITIVE52",markdown)
        self.assertIn("Completed: yes",markdown)

    def test_phase_two_migration_preserves_legacy_database_and_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legacy.sqlite3"
            connection=self.module.sqlite3.connect(path)
            connection.executescript("""
              CREATE TABLE entries (id TEXT PRIMARY KEY,created_at TEXT NOT NULL,recorded_at TEXT,
                transcription TEXT NOT NULL DEFAULT '',trigger_type TEXT,audio_path TEXT,audio_mime TEXT,
                payload_json TEXT NOT NULL,starred INTEGER NOT NULL DEFAULT 0,processed INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT 'note',
                archived INTEGER NOT NULL DEFAULT 0,source_key TEXT,group_name TEXT,due_at TEXT,
                reminder_completed INTEGER NOT NULL DEFAULT 0,reminder_notify_before_minutes INTEGER);
              CREATE TABLE note_groups (name TEXT PRIMARY KEY COLLATE NOCASE,display_name TEXT NOT NULL,
                created_at TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE interpreted_operations (id TEXT PRIMARY KEY,created_at TEXT NOT NULL,source TEXT NOT NULL,
                source_key TEXT UNIQUE,operation TEXT NOT NULL,confidence REAL NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL,
                target_id TEXT,result_json TEXT NOT NULL,undo_kind TEXT,undo_payload TEXT,reversed_at TEXT);
            """)
            values=("legacy-1","2026-01-01T10:00:00Z","2026-01-01T09:59:00Z","legacy text","ring","legacy.webm","audio/webm",'{"legacy":true}',1,1,"old","Legacy","task",0,"stable-source","LEGACY7","2026-01-02T08:00:00Z",1,30)
            connection.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
            connection.execute("INSERT INTO note_groups VALUES('LEGACY7','LEGACY7','2025-12-01T00:00:00Z',0)")
            connection.commit();connection.close()

            self.module.init_db(path)
            self.module.init_db(path)
            migrated=self.module.sqlite3.connect(path);migrated.row_factory=self.module.sqlite3.Row
            row=migrated.execute("SELECT * FROM entries WHERE id='legacy-1'").fetchone()
            self.assertEqual(tuple(row[key] for key in ("id","created_at","recorded_at","audio_path","group_name","due_at","reminder_completed","processed")),("legacy-1","2026-01-01T10:00:00Z","2026-01-01T09:59:00Z","legacy.webm","LEGACY7","2026-01-02T08:00:00Z",1,1))
            self.assertEqual(row["completed"],0)
            self.assertIn("proposed_json",{column[1] for column in migrated.execute("PRAGMA table_info(interpreted_operations)")})
            self.assertEqual(migrated.execute("PRAGMA user_version").fetchone()[0],2)
            legacy_reader=migrated.execute("SELECT id,group_name,due_at,processed,reminder_completed FROM entries").fetchone()
            self.assertEqual(tuple(legacy_reader),("legacy-1","LEGACY7","2026-01-02T08:00:00Z",1,1))
            migrated.close()

    def test_group_timeline_and_exports_reject_unknown_group(self):
        self.login()
        self.assertEqual(self.client.get("/api/groups/UNKNOWN999/timeline").status_code,404)
        self.assertEqual(self.client.get("/api/groups/UNKNOWN999/export/json").status_code,404)

    def test_verified_backup_contains_database_audio_and_manifest(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        entry=self.client.post("/webhook/index",json={"transcription":"backup verification entry"},headers=webhook)
        audio_name=f"{entry.json['id']}.webm"
        with self.module.app.app_context():
            (self.module.AUDIO_DIR/audio_name).write_bytes(b"backup-audio")
            self.module.db().execute("UPDATE entries SET audio_path=?,audio_mime=? WHERE id=?",(audio_name,"audio/webm",entry.json["id"])); self.module.db().commit()
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]}
        response=self.client.post("/api/backups",headers=headers)
        self.assertEqual(response.status_code,201)
        archive_path=self.module.BACKUP_DIR/response.json["backup"]["archive_name"]
        verified=self.module.verify_backup_archive(archive_path)
        self.assertTrue(verified["ok"])
        self.assertGreaterEqual(verified["entries"],1)
        self.assertGreaterEqual(verified["audioEntries"],1)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIn("manifest.json",archive.namelist())
            self.assertIn("index-inbox.sqlite3",archive.namelist())
            self.assertIn(f"audio/{audio_name}",archive.namelist())
        latest=self.client.get("/api/backups/latest")
        self.assertEqual(latest.status_code,200)
        self.assertEqual(latest.data,archive_path.read_bytes())
        latest.close()

    def test_backup_failure_is_recorded_when_audio_is_missing(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        entry=self.client.post("/webhook/index",json={"transcription":"missing backup audio"},headers=webhook)
        with self.module.app.app_context():
            self.module.db().execute("UPDATE entries SET audio_path=?,audio_mime=? WHERE id=?",("missing-test.webm","audio/webm",entry.json["id"])); self.module.db().commit()
        login=self.login(); response=self.client.post("/api/backups",headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"]})
        self.assertEqual(response.status_code,500)
        with self.module.app.app_context():
            run=self.module.db().execute("SELECT status,error FROM backup_runs ORDER BY requested_at DESC LIMIT 1").fetchone()
            self.module.db().execute("UPDATE entries SET audio_path=NULL,audio_mime=NULL WHERE id=?",(entry.json["id"],)); self.module.db().commit()
        self.assertEqual(run["status"],"failed")
        self.assertIn("missing",run["error"].lower())

    def test_group_suggestion_requires_acceptance_and_does_not_learn_alias(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Suggestion eighty four"},headers=webhook)
        entry=self.client.post("/webhook/index",json={"transcription":"Sugestion 84 misplaced observation"},headers=webhook)
        self.assertIsNone(entry.json["group"])
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        suggestions=self.client.get("/api/group-suggestions").json
        suggestion=next(item for item in suggestions if item["entryId"]==entry.json["id"])
        self.assertEqual(suggestion["group"],"SUGGESTION84")
        with self.module.app.app_context():before=self.module.db().execute("SELECT count(*) FROM note_group_aliases WHERE group_name='SUGGESTION84'").fetchone()[0]
        accepted=self.client.post(f"/api/group-suggestions/{entry.json['id']}/accept",json={"group":"SUGGESTION84"},headers=headers)
        self.assertEqual(accepted.status_code,200)
        with self.module.app.app_context():
            stored=self.module.db().execute("SELECT group_name,transcription FROM entries WHERE id=?",(entry.json["id"],)).fetchone()
            after=self.module.db().execute("SELECT count(*) FROM note_group_aliases WHERE group_name='SUGGESTION84'").fetchone()[0]
        self.assertEqual((stored["group_name"],stored["transcription"]),("SUGGESTION84","misplaced observation"))
        self.assertEqual(after,before)

    def test_group_suggestion_dismissal_persists_and_number_must_match(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Review eighty five"},headers=webhook)
        dismissible=self.client.post("/webhook/index",json={"transcription":"Revew 85 dismiss this"},headers=webhook)
        different_number=self.client.post("/webhook/index",json={"transcription":"Revew 86 do not suggest"},headers=webhook)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        ids={item["entryId"] for item in self.client.get("/api/group-suggestions").json}
        self.assertIn(dismissible.json["id"],ids)
        self.assertNotIn(different_number.json["id"],ids)
        dismissed=self.client.post(f"/api/group-suggestions/{dismissible.json['id']}/dismiss",json={"group":"REVIEW85"},headers=headers)
        self.assertEqual(dismissed.status_code,200)
        ids={item["entryId"] for item in self.client.get("/api/group-suggestions").json}
        self.assertNotIn(dismissible.json["id"],ids)

    def test_archived_groups_are_not_suggested(self):
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Closed eighty seven"},headers=webhook)
        login=self.login(); headers={"Origin":"http://localhost","X-CSRF-Token":login.json["csrfToken"],"Content-Type":"application/json"}
        self.client.patch("/api/groups/CLOSED87",json={"archived":True},headers=headers)
        entry=self.client.post("/webhook/index",json={"transcription":"Clased 87 remain standalone"},headers=webhook)
        ids={item["entryId"] for item in self.client.get("/api/group-suggestions").json}
        self.assertNotIn(entry.json["id"],ids)


if __name__ == "__main__":
    unittest.main()
