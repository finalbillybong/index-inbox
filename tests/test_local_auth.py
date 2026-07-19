import importlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
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
        self.assertEqual(kinds,["group_created","capture_grouped","capture_standalone"])
        messages=" ".join(event["message"] for event in feed.json["events"]).lower()
        self.assertNotIn("confidential",messages)
        self.assertGreater(feed.json["sequence"],initial)
        empty=self.client.get(f"/api/changes?since={feed.json['sequence']}")
        self.assertEqual(empty.json["events"],[])

    def test_change_feed_reports_repeated_and_unrecognized_group_commands(self):
        self.login(); initial=self.client.get("/api/changes").json["sequence"]
        webhook={"X-Webhook-Secret":"test-webhook-secret"}
        self.client.post("/webhook/index",json={"transcription":"Create Notice ninety nine"},headers=webhook)
        self.client.post("/webhook/index",json={"transcription":"Create Notice 99"},headers=webhook)
        unmatched=self.client.post("/webhook/index",json={"transcription":"Create a group without a number"},headers=webhook)
        self.assertEqual(unmatched.status_code,201)
        self.assertIsNone(unmatched.json["group"])
        events=self.client.get(f"/api/changes?since={initial}").json["events"]
        self.assertEqual([event["kind"] for event in events],["group_created","group_exists","group_unrecognized"])

    def test_change_feed_reports_rejected_webhook(self):
        self.login(); initial=self.client.get("/api/changes").json["sequence"]
        rejected=self.client.post("/webhook/index",json={"transcription":"private rejected text"})
        self.assertEqual(rejected.status_code,401)
        events=self.client.get(f"/api/changes?since={initial}").json["events"]
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["kind"],"webhook_rejected")
        self.assertNotIn("private",events[0]["message"].lower())

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

    def test_change_feed_rejects_invalid_sequence(self):
        self.login()
        response=self.client.get("/api/changes?since=invalid")
        self.assertEqual(response.status_code,400)

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

    def test_group_timeline_and_exports_reject_unknown_group(self):
        self.login()
        self.assertEqual(self.client.get("/api/groups/UNKNOWN999/timeline").status_code,404)
        self.assertEqual(self.client.get("/api/groups/UNKNOWN999/export/json").status_code,404)

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
