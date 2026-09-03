from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instant_ai.model_mr_mcp import ModelMrMcpLibrary, ModelMrMcpUnavailable


class ModelMrMcpLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        (root / "details").mkdir()
        self.snapshot = root / "public-snapshot.json"
        self.snapshot.write_text(
            json.dumps(
                {
                    "version": 2,
                    "works": [
                        {
                            "id": 7,
                            "title": "黄金、利率与美元",
                            "description": "讨论宏观变量",
                            "url": "https://www.douyin.com/video/7",
                            "published_at": "2026-08-30T08:00:00+08:00",
                            "keywords": ["黄金", "利率"],
                            "media_file": "private/sample.mp4",
                            "private_path": "H:/secret.mp4",
                        },
                        {
                            "id": 8,
                            "title": "AI 服务器产业链",
                            "url": "https://www.douyin.com/video/8",
                            "published_at": "2026-09-03T08:00:00+08:00",
                            "keywords": ["AI", "服务器"],
                        },
                    ],
                    "thoughts": [
                        {
                            "id": 1,
                            "name": "周期判断",
                            "description": "观察库存、利率和盈利周期。",
                            "level": 1,
                            "video_count": 12,
                            "private_path": "C:/private/thought.json",
                        },
                        {"id": 2, "name": "风险控制", "description": "控制仓位。", "level": 1},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "details" / "7.json").write_text(
            json.dumps(
                {
                    "work": {"id": 7, "media_file": "private/sample.mp4"},
                    "video_text": {
                        "text": "黄金通常会受到实际利率与美元方向共同影响。",
                        "official": True,
                        "source": "owner-mobile-edit",
                        "updated_at": "2026-08-30T09:00:00+08:00",
                    },
                    "interpretation": {
                        "text": "重点观察降息预期，而不是只看单日价格。",
                        "updated_at": "2026-08-30T10:00:00+08:00",
                    },
                    "comments": [{"author": "private user", "text": "不得通过 MCP 返回的评论"}],
                    "private_path": "H:/secret/detail.json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "details" / "8.json").write_text(
            json.dumps(
                {
                    "work": {"id": 8},
                    "transcripts": [
                        {
                            "text": "AI 服务器需求仍在增长。",
                            "source": "doubao-recording-asr-2.0",
                            "created_at": "2026-09-03T09:00:00+08:00",
                        }
                    ],
                    "comments": [{"text": "另一条私密评论"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.library = ModelMrMcpLibrary(self.snapshot)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_search_supports_latest_and_topic_without_private_fields(self) -> None:
        latest = self.library.search_works_for_mcp("模型先生最新一条视频原文", 1)
        self.assertEqual(latest["query_mode"], "latest")
        self.assertEqual(latest["items"][0]["record_id"], "model-mr-work:8")
        self.assertEqual(latest["items"][0]["original_status"], "transcript_unconfirmed")

        topical = self.library.search_works_for_mcp("模型先生怎么看黄金", 10)
        self.assertEqual(topical["query_mode"], "relevance")
        self.assertEqual(topical["items"][0]["record_id"], "model-mr-work:7")
        serialized = json.dumps(topical, ensure_ascii=False)
        self.assertNotIn("不得通过 MCP 返回的评论", serialized)
        self.assertNotIn("H:/secret", serialized)
        self.assertNotIn("media_file", serialized)

    def test_get_returns_only_work_text_and_saved_interpretation(self) -> None:
        result = self.library.get_work_for_mcp("model-mr-work:7")
        self.assertTrue(result["found"])
        self.assertEqual(result["video_original"]["status"], "official")
        self.assertTrue(result["video_original"]["verified"])
        self.assertIn("实际利率", result["video_original"]["text"])
        self.assertIn("降息预期", result["interpretation"]["text"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("comments", serialized)
        self.assertNotIn("private user", serialized)
        self.assertNotIn("sample.mp4", serialized)
        self.assertNotIn("H:/secret", serialized)

    def test_thoughts_are_queryable_as_a_sanitized_read_only_index(self) -> None:
        result = self.library.list_thoughts_for_mcp("周期", 10)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["name"], "周期判断")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private_path", serialized)
        self.assertNotIn("C:/private", serialized)

    def test_projection_never_calls_sidecar_asr_ai_or_writes_library_files(self) -> None:
        paths = [self.snapshot, *sorted((self.snapshot.parent / "details").glob("*.json"))]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        with (
            patch("instant_ai.model_mr.urlopen") as live_sidecar,
            patch("instant_ai.model_mr.transcribe_video") as asr,
        ):
            self.library.search_works_for_mcp("黄金", 10)
            self.library.get_work_for_mcp("model-mr-work:7")
            self.library.list_thoughts_for_mcp("", 100)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(after, before)
        live_sidecar.assert_not_called()
        asr.assert_not_called()

    def test_missing_snapshot_fails_closed(self) -> None:
        library = ModelMrMcpLibrary(Path(self.temporary.name) / "missing.json")
        with self.assertRaises(ModelMrMcpUnavailable):
            library.search_works_for_mcp("最新作品", 1)


if __name__ == "__main__":
    unittest.main()
