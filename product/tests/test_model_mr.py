from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from instant_ai.model_mr import ModelMrClient


class ModelMrGatewayTests(unittest.TestCase):
    def test_work_summary_removes_local_paths_raw_payload_and_admin_fields(self) -> None:
        cleaned = ModelMrClient._clean_work(
            {
                "id": 12,
                "title": "raw filename",
                "active_title": "模型先生谈科技股",
                "description": "由 model-video-drop 自动导入的本地下载文件。",
                "url": "https://www.douyin.com/video/12",
                "published_at": "2026-08-30T08:00:00+08:00",
                "has_video_text": True,
                "has_interpretation": False,
                "raw_json": '{"source_path":"H:/private/video.mp4"}',
                "comment_count": 300,
                "primary_asset": {"file_url": "/api/assets/99/file"},
                "keyword_info": {"keywords": ["科技", "AI"]},
            }
        )
        self.assertEqual(cleaned["title"], "模型先生谈科技股")
        self.assertEqual(cleaned["description"], "")
        self.assertEqual(cleaned["keywords"], ["科技", "AI"])
        self.assertNotIn("raw_json", cleaned)
        self.assertNotIn("comment_count", cleaned)
        self.assertNotIn("primary_asset", cleaned)
        self.assertNotIn("source_path", str(cleaned))

    def test_unavailable_sidecar_returns_a_safe_module_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ModelMrClient("http://127.0.0.1:8787", Path(directory) / "missing.json")
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                status = client.status()
            self.assertFalse(status["available"])
            self.assertEqual(status["mode"], "independent-readonly")
            self.assertNotIn("127.0.0.1", status["message"])

    def test_sanitized_snapshot_works_without_the_private_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "public-snapshot.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "exported_at": 123,
                        "works": [
                            {
                                "id": 1,
                                "title": "黄金策略",
                                "url": "https://example.com/1",
                                "keywords": ["黄金"],
                                "private_path": "H:/secret.mp4",
                            }
                        ],
                        "thoughts": [{"id": 2, "name": "趋势", "level": 1}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:8787", path)
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                status = client.status()
                works = client.works(limit=10)
                thoughts = client.thoughts(limit=10)
                chat = client.chat_config()

            self.assertTrue(status["available"])
            self.assertEqual(status["mode"], "sanitized-snapshot")
            self.assertEqual(works["items"][0]["title"], "黄金策略")
            self.assertNotIn("private_path", works["items"][0])
            self.assertEqual(thoughts["categories"][0]["name"], "趋势")
            self.assertFalse(chat["enabled"])


if __name__ == "__main__":
    unittest.main()
