from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from sunmao_community.app import create_http_server
from sunmao_community.store import CommunityMessageStore, validate_community_message


class CommunityMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_store_normalizes_text_and_never_returns_hidden_fields(self) -> None:
        store = CommunityMessageStore(self.root / "community.sqlite3")
        created = store.create_message("  木友\u200b 小李  ", "  想聊聊\t燕尾榫。\n\n\n谢谢！  ")
        self.assertEqual(created["nickname"], "木友 小李")
        self.assertEqual(created["body"], "想聊聊 燕尾榫。\n\n谢谢!")
        self.assertEqual(set(created), {"id", "nickname", "body", "created_at"})
        self.assertEqual(store.list_messages(), [created])

    def test_validation_rejects_blank_and_oversized_text(self) -> None:
        for nickname, body in (("", "内容"), ("木" * 21, "内容"), ("木友", ""), ("木友", "想" * 501)):
            with self.subTest(nickname=nickname[:4], body_length=len(body)):
                with self.assertRaises(ValueError):
                    validate_community_message(nickname, body)

    def test_http_api_requires_same_origin_and_rate_limits_posts(self) -> None:
        server = create_http_server(port=0, database_path=self.root / "community.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method: str, path: str, payload=None, *, origin: str = ""):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            headers = {}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if origin:
                headers["Origin"] = origin
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), json.loads(response.read())
            connection.close()
            return result

        try:
            local_origin = f"http://127.0.0.1:{server.server_port}"
            status, _headers, payload = request(
                "POST",
                "/api/messages",
                {"nickname": "木友", "body": "第一条公开交流"},
                origin="https://attacker.example",
            )
            self.assertEqual(status, 403)
            self.assertEqual(payload["error"], "same_origin_required")

            status, _headers, payload = request(
                "POST",
                "/api/messages",
                {"nickname": "木友", "body": "第一条公开交流"},
                origin=local_origin,
            )
            self.assertEqual(status, 201)
            self.assertEqual(payload["message"]["body"], "第一条公开交流")

            status, headers, payload = request(
                "POST",
                "/api/messages",
                {"nickname": "木友", "body": "发送太快"},
                origin=local_origin,
            )
            self.assertEqual(status, 429)
            self.assertIn("Retry-After", headers)
            self.assertEqual(payload["error"], "rate_limited")

            status, _headers, payload = request("GET", "/api/messages?limit=10")
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["messages"]), 1)
            self.assertEqual(payload["messages"][0]["nickname"], "木友")

            status, _headers, payload = request("GET", "/health/version")
            self.assertEqual(status, 200)
            self.assertEqual(payload["service"], "sunmao-ai-community")
            self.assertEqual(payload["version"], "0.2.0")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_server_refuses_non_loopback_binding(self) -> None:
        with self.assertRaises(ValueError):
            create_http_server(host="0.0.0.0", port=0, database_path=self.root / "bad.sqlite3")
