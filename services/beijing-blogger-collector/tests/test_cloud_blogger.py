from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from mx_agent.cloud_blogger import CloudBloggerReader, load_token, merge_cloud_search_result


class _CloudHandler(BaseHTTPRequestHandler):
    authorization = ""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        self.__class__.authorization = self.headers.get("Authorization", "")
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        if self.path.endswith("/search"):
            response = {
                "available": True,
                "query_mode": "latest",
                "count": 1,
                "items": [{
                    "record_id": "cloud-video:" + "a" * 64,
                    "title": "最新作品",
                    "published_at": "2026-09-02T05:14:18+08:00",
                }],
                "received_question": payload.get("question"),
            }
        else:
            response = {
                "found": True,
                "record_id": payload.get("record_id"),
                "video_original": {"text": "云端正式视频原文", "verified": True},
            }
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CloudBloggerReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CloudHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_search_and_get_use_dedicated_bearer_token(self) -> None:
        token = "b" * 64
        reader = CloudBloggerReader(
            f"http://127.0.0.1:{self.server.server_port}/api/mcp/blogger",
            token_loader=lambda: token,
        )
        search = reader.search("李艾琳最新视频", limit=1)
        self.assertTrue(search["available"])
        self.assertEqual(search["items"][0]["record_id"], "cloud-video:" + "a" * 64)
        self.assertEqual(_CloudHandler.authorization, f"Bearer {token}")

        complete = reader.get("cloud-video:" + "a" * 64)
        self.assertTrue(complete["found"])
        self.assertEqual(complete["video_original"]["text"], "云端正式视频原文")

    def test_missing_token_fails_closed_without_network_request(self) -> None:
        _CloudHandler.authorization = "not-called"
        reader = CloudBloggerReader(
            f"http://127.0.0.1:{self.server.server_port}/api/mcp/blogger",
            token_loader=lambda: "",
        )
        result = reader.search("任意问题")
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "cloud_blogger_not_configured")
        self.assertEqual(_CloudHandler.authorization, "not-called")

    def test_dpapi_token_file_is_loaded_without_plaintext_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cloud-token.bin"
            target.write_bytes(b"encrypted-test-payload")
            with patch.dict(
                os.environ,
                {
                    "INSTANT_AI_BLOGGER_MCP_TOKEN": "",
                    "BLOGGER_AGENT_CLOUD_TOKEN_FILE": str(target),
                },
                clear=False,
            ), patch("mx_agent.cloud_blogger._dpapi_unprotect", return_value=b"c" * 64):
                self.assertEqual(load_token(), "c" * 64)

    def test_cloud_latest_record_is_merged_ahead_of_older_local_history(self) -> None:
        local = {
            "query_mode": "latest",
            "count": 1,
            "items": [{
                "record_id": "video:58",
                "published_at": "2026-08-20T11:47:00+08:00",
                "relevance_score": 100,
            }],
            "retrieval": "local-index",
            "evidence_note": "本地证据。",
        }
        cloud = {
            "available": True,
            "query_mode": "latest",
            "items": [{
                "record_id": "cloud-video:" + "d" * 64,
                "published_at": "2026-09-02T05:14:18+08:00",
                "relevance_score": 50,
            }],
        }
        merged = merge_cloud_search_result(local, cloud, 10)
        self.assertEqual(merged["items"][0]["record_id"], "cloud-video:" + "d" * 64)
        self.assertTrue(merged["cloud_blogger"]["available"])
        self.assertIn("instant_ai_cloud_blogger", merged["retrieval"])


if __name__ == "__main__":
    unittest.main()
