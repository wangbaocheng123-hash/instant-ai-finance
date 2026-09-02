from __future__ import annotations

import tempfile
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from instant_ai.auth import (
    MIN_OWNER_PASSWORD_LENGTH,
    OwnerAuth,
    SESSION_COOKIE_NAME,
    SESSION_SECONDS,
    configure_owner,
    generate_owner_password,
)
from instant_ai.server import InstantAIHandler


class OwnerAuthTests(unittest.TestCase):
    def test_owner_password_minimum_is_nine_characters(self) -> None:
        self.assertEqual(MIN_OWNER_PASSWORD_LENGTH, 9)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            with self.assertRaisesRegex(ValueError, "至少需要 9 个字符"):
                configure_owner("owner", "12345678", path)

            configure_owner("owner", "123456789", path)
            self.assertTrue(OwnerAuth(required=True, path=path).authenticate("owner", "123456789"))

    def test_generated_password_is_long_and_not_a_fixed_default(self) -> None:
        first = generate_owner_password()
        second = generate_owner_password()
        self.assertGreaterEqual(len(first), 24)
        self.assertNotEqual(first, second)

    def test_owner_password_is_hashed_and_session_lasts_exactly_thirty_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            configure_owner("owner", "Strong-password-2026", path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Strong-password-2026", text)

            auth = OwnerAuth(required=True, path=path)
            self.assertTrue(auth.authenticate("owner", "Strong-password-2026"))
            self.assertFalse(auth.authenticate("owner", "wrong-password"))
            token, issued = auth.create_session("owner", now=1_000)
            session = auth.session(f"{SESSION_COOKIE_NAME}={token}", now=1_001)

            self.assertIsNotNone(session)
            self.assertEqual(issued.expires_at - issued.issued_at, SESSION_SECONDS)
            self.assertIsNone(auth.session(f"{SESSION_COOKIE_NAME}={token}", now=issued.expires_at))

    def test_tampered_session_is_rejected_and_cookie_is_hardened(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            configure_owner("owner", "Another-strong-password", path)
            auth = OwnerAuth(required=True, path=path)
            token, _ = auth.create_session("owner", now=2_000)
            tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

            self.assertIsNone(auth.session(f"{SESSION_COOKIE_NAME}={tampered}", now=2_001))
            cookie = auth.session_cookie(token, secure=True)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertIn("Secure", cookie)
            self.assertIn(f"Max-Age={SESSION_SECONDS}", cookie)

    def test_five_failed_attempts_trigger_the_fifteen_minute_limit(self) -> None:
        auth = OwnerAuth(required=True, path=Path("missing-auth.json"))
        for attempt in range(5):
            self.assertTrue(auth.login_allowed("client", now=float(attempt)))
            auth.record_failed_login("client", now=float(attempt))
        self.assertFalse(auth.login_allowed("client", now=10.0))
        self.assertTrue(auth.login_allowed("client", now=901.0))

    def test_missing_configuration_stays_locked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            auth = OwnerAuth(required=True, path=Path(directory) / "missing.json")
            self.assertTrue(auth.setup_required)
            self.assertFalse(auth.status()["authenticated"])

    def test_http_login_sets_cookie_and_unlocks_auth_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            configure_owner("owner", "Cloud-owner-password", path)
            auth = OwnerAuth(required=True, path=path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("instant_ai.server.AUTH", auth):
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request(
                        "POST",
                        "/api/auth/login",
                        body=json.dumps({"username": "owner", "password": "Cloud-owner-password"}),
                        headers={"Content-Type": "application/json", "X-Instant-AI": "1"},
                    )
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 200)
                    cookie = response.getheader("Set-Cookie") or ""
                    self.assertIn("HttpOnly", cookie)
                    self.assertNotIn("Secure", cookie)

                    connection.request("GET", "/api/auth/status", headers={"Cookie": cookie.split(";", 1)[0]})
                    status_response = connection.getresponse()
                    status = json.loads(status_response.read())
                    self.assertTrue(status["authenticated"])
                    self.assertEqual(status["username"], "owner")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
