from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sunmao_community.analytics import CommunityAnalyticsStore
from sunmao_community.app import create_http_server
from sunmao_community.auth import AdminSessionManager, create_password_hash, verify_password


class CommunityAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "community.sqlite3"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_dashboard_counts_visitors_sessions_pageviews_and_dimensions(self) -> None:
        store = CommunityAnalyticsStore(self.database)
        now_epoch = int(datetime(2026, 10, 13, 4, 0, tzinfo=UTC).timestamp())
        store.record_pageview(
            visitor_token="visitor-token-one-1234567890",
            session_token="session-token-one-1234567890",
            view="exchange",
            referrer="https://www.baidu.com/s?wd=sunmao",
            user_agent="Mozilla/5.0 (iPhone; Mobile)",
            now_epoch=now_epoch,
        )
        store.record_pageview(
            visitor_token="visitor-token-one-1234567890",
            session_token="session-token-one-1234567890",
            view="joints",
            referrer="https://www.baidu.com/ignored-path",
            user_agent="Mozilla/5.0 (iPhone; Mobile)",
            now_epoch=now_epoch + 60,
        )
        store.record_pageview(
            visitor_token="visitor-token-one-1234567890",
            session_token="session-token-two-1234567890",
            view="sizes",
            user_agent="Mozilla/5.0 (iPhone; Mobile)",
            now_epoch=now_epoch + 1800,
        )
        store.record_pageview(
            visitor_token="visitor-token-two-1234567890",
            session_token="session-token-three-1234567",
            view="exchange",
            user_agent="Mozilla/5.0 (X11; Linux x86_64)",
            now_epoch=now_epoch + 1900,
        )

        dashboard = store.dashboard(days=7, now_epoch=now_epoch + 1900)
        self.assertEqual(dashboard["summary"]["total_visitors"], 2)
        self.assertEqual(dashboard["summary"]["total_visits"], 3)
        self.assertEqual(dashboard["summary"]["total_pageviews"], 4)
        self.assertEqual(dashboard["summary"]["today_visitors"], 2)
        self.assertEqual(sum(row["pageviews"] for row in dashboard["pages"]), 4)
        self.assertEqual(dashboard["sources"][0]["visits"], 2)
        self.assertEqual(len(dashboard["recent_visitors"]), 2)
        self.assertTrue(
            all(row["visitor_label"].startswith("木友-") for row in dashboard["recent_visitors"])
        )

    def test_analytics_schema_does_not_store_ip_or_raw_user_agent(self) -> None:
        owner_token = "owner-token-persistence-1234567890"
        store = CommunityAnalyticsStore(self.database)
        store.mark_owner(owner_token)
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {
                row[1]
                for table in (
                    "analytics_visitors",
                    "analytics_sessions",
                    "analytics_owner_visitors",
                )
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            owner_row = connection.execute(
                "SELECT visitor_hash, linked_at FROM analytics_owner_visitors"
            ).fetchone()
        self.assertNotIn("ip", columns)
        self.assertNotIn("ip_address", columns)
        self.assertNotIn("user_agent", columns)
        self.assertIsNotNone(owner_row)
        self.assertEqual(len(owner_row[0]), 64)
        self.assertNotIn(owner_token, owner_row)

        restarted_store = CommunityAnalyticsStore(self.database)
        restarted_store.record_pageview(
            visitor_token=owner_token,
            session_token="owner-session-persistence-1234567",
            view="sizes",
        )
        self.assertEqual(
            restarted_store.dashboard()["recent_visitors"][0]["visitor_label"],
            "主理人",
        )

    def test_password_hash_and_admin_session_expiry(self) -> None:
        encoded = create_password_hash(
            "correct horse battery staple", salt=b"0123456789abcdefghijkl"
        )
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("incorrect password", encoded))
        sessions = AdminSessionManager(
            encoded, username="owner-admin", session_seconds=300
        )
        self.assertIsNone(
            sessions.login("wrong-owner", "correct horse battery staple", now_epoch=1000)
        )
        token = sessions.login(
            "owner-admin", "correct horse battery staple", now_epoch=1000
        )
        self.assertIsNotNone(token)
        self.assertTrue(sessions.authenticated(token or "", now_epoch=1100))
        self.assertFalse(sessions.authenticated(token or "", now_epoch=1301))


class CommunityAnalyticsHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.username = "owner-admin"
        self.password = "a private admin password"
        self.server = create_http_server(
            port=0,
            database_path=self.root / "community.sqlite3",
            admin_username=self.username,
            admin_password_hash=create_password_hash(
                self.password, salt=b"abcdefghijklmnopqr"
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload=None, *, cookie: str = ""):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Origin"] = self.origin
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.getheaders(), json.loads(response.read())
        connection.close()
        return result

    @staticmethod
    def cookie_header(response_headers: list[tuple[str, str]]) -> str:
        return "; ".join(
            value.split(";", 1)[0]
            for name, value in response_headers
            if name.lower() == "set-cookie"
        )

    def test_pageviews_are_anonymous_and_admin_dashboard_requires_login(self) -> None:
        status, headers, payload = self.request(
            "POST",
            "/api/analytics/pageview",
            {"view": "exchange", "referrer": "https://www.baidu.com/search"},
        )
        self.assertEqual(status, 202)
        self.assertTrue(payload["accepted"])
        visitor_token = payload["visitor_token"]
        self.assertRegex(visitor_token, r"^[A-Za-z0-9_-]{24,96}$")
        visitor_cookies = self.cookie_header(headers)
        self.assertIn("sunmao_visitor=", visitor_cookies)
        self.assertIn("sunmao_visit_session=", visitor_cookies)

        status, _headers, _payload = self.request(
            "POST",
            "/api/analytics/pageview",
            {"view": "joints", "referrer": ""},
            cookie=visitor_cookies,
        )
        self.assertEqual(status, 202)

        # A browser-local fallback restores the same anonymous visitor even when
        # its cookies disappear between visits. A new visit session is expected.
        status, headers, payload = self.request(
            "POST",
            "/api/analytics/pageview",
            {"view": "sizes", "referrer": "", "visitor_token": visitor_token},
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["visitor_token"], visitor_token)
        self.assertIn("sunmao_visitor=", self.cookie_header(headers))

        status, _headers, payload = self.request("GET", "/api/admin/analytics")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "admin_auth_required")

        status, headers, payload = self.request(
            "POST",
            "/api/admin/login",
            {"username": self.username, "password": self.password},
            cookie=visitor_cookies,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["visitor_label"], "主理人")
        self.assertEqual(payload["visitor_token"], visitor_token)
        admin_cookie = self.cookie_header(headers)
        self.assertIn("sunmao_admin_session=", admin_cookie)
        self.assertIn("sunmao_visitor=", admin_cookie)

        status, _headers, payload = self.request(
            "GET", "/api/admin/analytics?days=7", cookie=admin_cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["summary"]["total_visitors"], 1)
        self.assertEqual(payload["summary"]["total_visits"], 2)
        self.assertEqual(payload["summary"]["total_pageviews"], 3)
        self.assertEqual(payload["recent_visitors"][0]["visitor_label"], "主理人")
        self.assertEqual(payload["recent_visitors"][0]["referrer_host"], "www.baidu.com")

    def test_admin_login_marks_future_browser_pageviews_as_owner(self) -> None:
        status, headers, payload = self.request(
            "POST",
            "/api/admin/login",
            {
                "username": self.username,
                "password": self.password,
                "visitor_token": "owner-browser-token-1234567890",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["visitor_label"], "主理人")
        owner_cookies = self.cookie_header(headers)

        status, _headers, _payload = self.request(
            "POST",
            "/api/analytics/pageview",
            {"view": "sizes", "referrer": ""},
            cookie=owner_cookies,
        )
        self.assertEqual(status, 202)
        status, _headers, dashboard = self.request(
            "GET", "/api/admin/analytics?days=7", cookie=owner_cookies
        )
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["recent_visitors"][0]["visitor_label"], "主理人")

    def test_pageview_rejects_invalid_browser_visitor_token(self) -> None:
        status, _headers, payload = self.request(
            "POST",
            "/api/analytics/pageview",
            {"view": "exchange", "referrer": "", "visitor_token": "too-short"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_analytics_event")

    def test_admin_login_rejects_another_username(self) -> None:
        self.server.analytics_store.record_pageview(
            visitor_token="anonymous-browser-token-1234567",
            session_token="anonymous-session-token-1234567",
            view="exchange",
        )
        status, _headers, payload = self.request(
            "POST",
            "/api/admin/login",
            {
                "username": "another-owner",
                "password": self.password,
                "visitor_token": "anonymous-browser-token-1234567",
            },
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "invalid_credentials")
        visitor = self.server.analytics_store.dashboard()["recent_visitors"][0]
        self.assertTrue(visitor["visitor_label"].startswith("木友-"))

    def test_admin_assets_are_not_indexable(self) -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request("GET", "/admin/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("X-Robots-Tag"), "noindex, nofollow")
        self.assertIn("社区后台", response.read().decode("utf-8"))
        connection.close()


if __name__ == "__main__":
    unittest.main()
