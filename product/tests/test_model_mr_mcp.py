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
                            "title_source": "cover_ocr",
                            "title_confidence": 0.93,
                            "title_updated_at": "2026-08-30T08:30:00+08:00",
                            "description": "讨论宏观变量",
                            "url": "https://www.douyin.com/video/7",
                            "published_at": "2026-08-30T08:00:00+08:00",
                            "keywords": ["黄金", "利率"],
                            "keyword_info": {
                                "categories": {
                                    "宏观、政策与事件": ["实际利率"],
                                    "行业与板块": ["黄金"],
                                },
                                "keywords": ["黄金", "实际利率"],
                                "model": "doubao:test-model",
                                "schema_version": "test-keywords/v1",
                                "confirmed_at": "2026-08-30T09:05:00+08:00",
                            },
                            "comment_count": 3,
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
                    "thought_links": {"7": [1]},
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
                    "comments": [
                        {
                            "id": 1,
                            "author": "粉丝甲",
                            "text": "黄金大涨后还能追吗？",
                            "kind": "user_comment",
                            "reply_depth": 0,
                            "thread_key": "a1b2c3d4e5f6",
                            "like_count": 12,
                            "reply_count": 2,
                            "published_at": "2026-08-30T09:30:00+08:00",
                            "author_liked": True,
                            "profile_url": "https://private.example/fan",
                        },
                        {
                            "id": 2,
                            "author": "模型先生",
                            "text": "不要只看当天涨幅，要结合实际利率。",
                            "kind": "author_reply",
                            "reply_depth": 1,
                            "thread_key": "a1b2c3d4e5f6",
                            "like_count": 30,
                            "reply_count": 0,
                            "published_at": "2026-08-30T09:31:00+08:00",
                            "source_comment_id": "private-source-id",
                        },
                        {
                            "id": 3,
                            "author": "模型先生",
                            "text": "同名昵称但没有来源作者标记。",
                            "kind": "user_comment",
                            "reply_depth": 0,
                            "thread_key": "abcdefabcdef",
                        },
                    ],
                    "stock_mentions": {
                        "total_comments": 3,
                        "stock_count": 1,
                        "items": [
                            {
                                "rank": 1,
                                "name": "紫金矿业",
                                "code": "601899",
                                "comment_count": 1,
                                "mention_count": 1,
                                "fan_comment_count": 1,
                                "author_comment_count": 0,
                                "examples": ["紫金矿业怎么看？"],
                                "comment_ids": [1],
                                "private_path": "H:/private/master.json",
                            }
                        ],
                        "method": "local-security-master",
                    },
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

        comment_match = self.library.search_works_for_mcp("大涨后还能追吗", 10)
        self.assertEqual(comment_match["items"][0]["record_id"], "model-mr-work:7")
        self.assertIn("comments", comment_match["items"][0]["matched_in"])
        stock_match = self.library.search_works_for_mcp("紫金矿业", 10)
        self.assertIn("stock_mentions", stock_match["items"][0]["matched_in"])

        all_first = self.library.search_works_for_mcp("所有作品", 1, 0)
        self.assertEqual(all_first["query_mode"], "all")
        self.assertEqual(all_first["total"], 2)
        self.assertTrue(all_first["has_more"])
        all_second = self.library.search_works_for_mcp(
            "所有作品", 1, all_first["next_offset"]
        )
        self.assertFalse(all_second["has_more"])
        self.assertNotEqual(
            all_first["items"][0]["record_id"],
            all_second["items"][0]["record_id"],
        )

    def test_get_returns_complete_owner_facing_content_without_inline_comment_bodies(self) -> None:
        result = self.library.get_work_for_mcp("model-mr-work:7")
        self.assertTrue(result["found"])
        self.assertEqual(result["work"]["title_source"], "cover_ocr")
        self.assertEqual(result["work"]["title_confidence"], 0.93)
        self.assertEqual(result["video_original"]["status"], "official")
        self.assertTrue(result["video_original"]["verified"])
        self.assertIn("实际利率", result["video_original"]["text"])
        self.assertIn("实际利率", result["ai_keywords"]["categories"]["宏观、政策与事件"])
        self.assertEqual(result["ai_keywords"]["model"], "doubao:test-model")
        self.assertIn("降息预期", result["interpretation"]["text"])
        self.assertEqual(result["stock_mentions"]["items"][0]["name"], "紫金矿业")
        self.assertEqual(result["investment_thoughts"][0]["name"], "周期判断")
        self.assertEqual(result["comment_summary"]["total"], 3)
        self.assertEqual(result["comment_access"]["tool"], "get_model_mr_comments")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("黄金大涨后还能追吗", serialized)
        self.assertNotIn("粉丝甲", serialized)
        self.assertNotIn("sample.mp4", serialized)
        self.assertNotIn("H:/secret", serialized)
        self.assertNotIn("H:/private", serialized)

    def test_all_comments_are_pageable_with_roles_threads_filters_and_no_source_ids(self) -> None:
        first = self.library.get_comments_for_mcp("model-mr-work:7", 1, 0)
        self.assertTrue(first["found"])
        self.assertEqual(first["comment_total"], 3)
        self.assertEqual(first["matched_total"], 3)
        self.assertEqual(first["next_offset"], 1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["items"][0]["display_name"], "粉丝甲")
        self.assertEqual(first["items"][0]["role"], "fan")
        self.assertTrue(first["items"][0]["model_mr_liked"])

        rest = self.library.get_comments_for_mcp("model-mr-work:7", 2, first["next_offset"])
        self.assertFalse(rest["has_more"])
        self.assertEqual(rest["items"][0]["role"], "model_mr")
        self.assertEqual(rest["items"][0]["thread_id"], first["items"][0]["thread_id"])
        self.assertEqual(rest["items"][0]["root_comment_number"], 1)
        self.assertEqual(rest["items"][1]["display_name"], "模型先生")
        self.assertEqual(rest["items"][1]["role"], "fan")

        interactions = self.library.get_comments_for_mcp(
            "model-mr-work:7", 100, 0, "interactions"
        )
        self.assertEqual(interactions["matched_total"], 2)
        self.assertEqual({item["role"] for item in interactions["items"]}, {"fan", "model_mr"})
        author = self.library.get_comments_for_mcp("model-mr-work:7", 100, 0, "model_mr")
        self.assertEqual(author["matched_total"], 1)
        liked = self.library.get_comments_for_mcp("model-mr-work:7", 100, 0, "model_mr_liked")
        self.assertEqual(liked["items"][0]["text"], "黄金大涨后还能追吗？")
        queried = self.library.get_comments_for_mcp(
            "model-mr-work:7", 100, 0, "interactions", "实际利率"
        )
        self.assertEqual(queried["matched_total"], 2)

        serialized = json.dumps(first | {"rest": rest}, ensure_ascii=False)
        self.assertNotIn("profile_url", serialized)
        self.assertNotIn("private-source-id", serialized)
        self.assertNotIn("a1b2c3d4e5f6", serialized)
        self.assertNotIn("thread_key", serialized)
        self.assertNotIn("raw_json", serialized)

    def test_thoughts_are_queryable_as_a_sanitized_read_only_index(self) -> None:
        result = self.library.list_thoughts_for_mcp("周期", 10)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["name"], "周期判断")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private_path", serialized)
        self.assertNotIn("C:/private", serialized)

    def test_author_replies_are_separate_bounded_and_keep_question_context(self) -> None:
        result = self.library.get_author_replies_for_mcp("model-mr-work:7", 10, 0)
        self.assertTrue(result["found"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["question"]["text"], "黄金大涨后还能追吗？")
        self.assertEqual(
            result["items"][0]["author_messages"][0]["text"],
            "不要只看当天涨幅，要结合实际利率。",
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private user", serialized)
        self.assertNotIn("profile_url", serialized)
        self.assertNotIn("private-source-id", serialized)
        self.assertNotIn("同名昵称但没有来源作者标记", serialized)
        self.assertNotIn("thread_key", serialized)

        empty = self.library.get_author_replies_for_mcp("model-mr-work:8", 10, 0)
        self.assertEqual(empty["total"], 0)

    def test_projection_never_calls_sidecar_asr_ai_or_writes_library_files(self) -> None:
        paths = [self.snapshot, *sorted((self.snapshot.parent / "details").glob("*.json"))]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        with (
            patch("instant_ai.model_mr.urlopen") as live_sidecar,
            patch("instant_ai.model_mr.transcribe_video") as asr,
        ):
            self.library.search_works_for_mcp("黄金", 10)
            self.library.get_work_for_mcp("model-mr-work:7")
            self.library.get_comments_for_mcp("model-mr-work:7")
            self.library.get_author_replies_for_mcp("model-mr-work:7")
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
