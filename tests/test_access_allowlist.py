import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import PHONE, SESSION, server


class AllowlistTests(unittest.TestCase):
    def setUp(self):
        self.base = Path("/synthetic/pulse")
        self.secret = Path("/etc/secrets/numbers.txt")
        self.local = self.base / "numbers.txt"
        self.files = {self.local: PHONE + "\n"}
        self.enterContext(patch.object(server, "BASE_DIR", self.base))
        self.enterContext(patch.dict(os.environ, ACCESS_NUMBERS_FILE=""))
        self.enterContext(patch.dict(server.app.config, TESTING=True))
        self.enterContext(patch.object(server, "ACTIVE_ACCESS_SESSIONS", {}))
        self.enterContext(patch.object(server, "initialize_live", side_effect=AssertionError(
            "Broker startup is forbidden in allowlist tests")))
        self.client = server.app.test_client()
        self.reader = self.enterContext(patch.object(
            Path, "read_text", autospec=True, side_effect=self.read_file))
        self.errors = self.enterContext(patch.object(server.log, "error"))

    def read_file(self, path, **kwargs):
        if path not in self.files:
            raise FileNotFoundError
        value = self.files[path]
        if isinstance(value, Exception):
            raise value
        return value

    def login(self, phone=PHONE):
        return self.client.post("/api/access/login", json={
            "phone": phone, "session_id": SESSION})

    def test_local_file_used_when_render_secret_is_absent(self):
        self.assertEqual(server.allowed_access_numbers(), {PHONE})
        self.assertEqual([call.args[0] for call in self.reader.call_args_list],
                         [self.secret, self.local])
        self.reader.assert_called_with(self.local, encoding="utf-8-sig")

    def test_render_secret_takes_precedence_over_bundled_list(self):
        self.files[self.secret] = "0000000001\n"
        self.assertEqual(server.allowed_access_numbers(), {"0000000001"})
        self.reader.assert_called_once_with(self.secret, encoding="utf-8-sig")

    def test_explicit_absolute_path_is_authoritative(self):
        path = Path("/synthetic/private/approved.txt")
        os.environ["ACCESS_NUMBERS_FILE"] = str(path)
        self.files[path] = PHONE
        self.assertEqual(server.allowed_access_numbers(), {PHONE})
        self.reader.assert_called_once_with(path, encoding="utf-8-sig")

    def test_relative_path_is_resolved_against_application_directory(self):
        os.environ["ACCESS_NUMBERS_FILE"] = "private/approved.txt"
        path = self.base / "private/approved.txt"
        self.files[path] = PHONE
        self.assertEqual(server.allowed_access_numbers(), {PHONE})
        self.reader.assert_called_once_with(path, encoding="utf-8-sig")

    def test_explicit_missing_path_never_falls_back_to_bundled_list(self):
        path = Path("/synthetic/missing.txt")
        os.environ["ACCESS_NUMBERS_FILE"] = str(path)
        self.assertEqual(server.allowed_access_numbers(), set())
        self.reader.assert_called_once_with(path, encoding="utf-8-sig")
        self.errors.assert_called_once()

    def test_unreadable_secret_does_not_fall_back(self):
        self.files[self.secret] = PermissionError("private")
        self.assertEqual(server.allowed_access_numbers(), set())
        self.reader.assert_called_once_with(self.secret, encoding="utf-8-sig")
        self.errors.assert_called_once()

    def test_invalid_file_encoding_disables_access_without_500(self):
        self.files[self.secret] = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"], "access_not_configured")
        self.assertEqual(server.ACTIVE_ACCESS_SESSIONS, {})

    def test_empty_secret_does_not_fall_back_to_bundled_list(self):
        self.files[self.secret] = " \n# no entries\n"
        self.assertEqual(server.allowed_access_numbers(), set())
        self.reader.assert_called_once_with(self.secret, encoding="utf-8-sig")

    def test_country_prefix_blank_lines_bom_and_comments(self):
        self.files[self.local] = "\ufeff+91 " + PHONE + " # member 2026\n\n# 0000000001\n"
        self.assertEqual(server.allowed_access_numbers(), {PHONE})

    def test_invalid_records_are_not_authorized(self):
        self.files[self.local] = "123\n1234567890123\n0000000000,0000000001\nno numbers\n"
        self.assertEqual(server.allowed_access_numbers(), set())

    def test_duplicate_entries_are_normalized_and_deduplicated(self):
        self.files[self.local] = PHONE + "\n+91 " + PHONE + "\n"
        self.assertEqual(server.allowed_access_numbers(), {PHONE})

    def test_missing_list_returns_configuration_error_not_unapproved_number(self):
        self.files.clear()
        response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"ok": False, "error": "access_not_configured"})
        self.assertEqual(server.ACTIVE_ACCESS_SESSIONS, {})

    def test_valid_secret_authorizes_normalized_login(self):
        self.files[self.secret] = "+91 " + PHONE
        self.assertEqual(self.login("+91 " + PHONE).status_code, 200)
        self.assertIn(PHONE, server.ACTIVE_ACCESS_SESSIONS)

    def test_unlisted_phone_still_denied_with_a_valid_file(self):
        self.files[self.secret] = PHONE
        response = self.login("0000000001")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "not_allowed")
        self.assertEqual(server.ACTIVE_ACCESS_SESSIONS, {})

    def test_updates_are_read_on_next_login(self):
        self.assertEqual(self.login().status_code, 200)
        self.files[self.local] = "0000000001"
        self.assertEqual(self.login().status_code, 403)

    def test_configuration_error_does_not_disclose_private_data(self):
        self.files[self.secret] = PermissionError("private contents " + PHONE)
        response = self.login()
        self.assertNotIn(PHONE, response.get_data(as_text=True))
        self.assertNotIn("/etc/secrets", response.get_data(as_text=True))
        self.assertNotIn(PHONE, str(self.errors.call_args_list))

    def test_public_health_does_not_read_or_disclose_allowlist(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.get_json()["version"], "premium-2.1-access")
        self.reader.assert_not_called()
        self.assertNotIn(PHONE, response.get_data(as_text=True))
