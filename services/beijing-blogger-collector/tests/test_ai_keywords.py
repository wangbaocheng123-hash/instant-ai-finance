from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mx_agent.keyword_taxonomy import KEYWORD_CATEGORIES, KEYWORD_SCHEMA_VERSION
from mx_agent.keywords import KeywordExtractionService
from mx_agent.storage import Storage


def categorized_keywords() -> dict[str, list[str]]:
    payload = {category: [] for category in KEYWORD_CATEGORIES}
    payload["行业与板块"] = ["科技股"]
    payload["企业、个股与产业链"] = ["国产算力产业链"]
    payload["时间周期与走势状态"] = ["反弹"]
    payload["交易管理与风险控制"] = ["仓位控制"]
    return payload


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp-keywords",
            output_text=json.dumps(categorized_keywords(), ensure_ascii=False),
            output=[],
        )


class AIKeywordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp_dir.name) / "test.sqlite3")
        self.video_id, _ = self.storage.upsert_video(
            {
                "source": "manual",
                "source_video_id": "video-1",
                "author": "模型先生",
                "title": "原始文件名",
                "description": "",
                "discovered_at": "2026-07-23T08:00:00+00:00",
            }
        )
        self.storage.save_manual_title(self.video_id, "你想趁反弹解套吗")
        self.responses = FakeResponses()
        client = SimpleNamespace(responses=self.responses)
        settings = SimpleNamespace(openai_api_key="test-key")
        self.service = KeywordExtractionService(
            settings,
            self.storage,
            client_factory=lambda _key: client,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def save_original(self, text: str) -> None:
        self.storage.save_content_original(self.video_id, text)

    def test_requires_formal_video_original(self):
        with self.assertRaisesRegex(ValueError, "正式视频原文"):
            self.service.preview(self.video_id)
        self.assertEqual(self.responses.calls, [])

    def test_preview_only_returns_ten_categories_and_save_is_cached(self):
        self.save_original("科技股会出现一轮反弹，但不要一次性加满仓位。")
        with self.storage.connect() as conn:
            tables_before = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

        preview = self.service.preview(self.video_id)

        self.assertEqual(preview["categories"]["行业与板块"], ["科技股"])
        self.assertEqual(set(preview["categories"]), set(KEYWORD_CATEGORIES))
        self.assertNotIn("summary", preview)
        self.assertNotIn("core_points", preview)
        self.assertNotIn("items", preview)
        self.assertFalse(preview["cached"])
        self.assertEqual(len(self.responses.calls), 1)
        request = self.responses.calls[0]
        self.assertEqual(request["model"], "gpt-5.6-luna")
        self.assertEqual(request["reasoning"], {"effort": "low"})
        schema = request["text"]["format"]["schema"]
        self.assertEqual(set(schema["required"]), set(KEYWORD_CATEGORIES))
        self.assertNotIn("summary", schema["properties"])
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertFalse(request["store"])

        saved = self.service.save(
            self.video_id,
            categories=preview["categories"],
            source_hash=preview["source_hash"],
            model=preview["model"],
        )
        self.assertEqual(saved["keywords"], preview["keywords"])
        self.assertEqual(saved["schema_version"], KEYWORD_SCHEMA_VERSION)

        cached = self.service.preview(self.video_id)
        self.assertTrue(cached["cached"])
        self.assertEqual(len(self.responses.calls), 1)

        with self.storage.connect() as conn:
            row = conn.execute(
                "SELECT keywords_json FROM knowledge_records WHERE video_id = ?",
                (self.video_id,),
            ).fetchone()
            tables_after = {
                item["name"]
                for item in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            digest_count = conn.execute(
                """
                SELECT COUNT(*) FROM knowledge_chunks
                WHERE video_id = ? AND source_type = 'ai_digest'
                """,
                (self.video_id,),
            ).fetchone()[0]
            stored_count = conn.execute(
                "SELECT COUNT(*) FROM content_keywords WHERE content_id = ?",
                (self.video_id,),
            ).fetchone()[0]
            note_payload = json.loads(
                conn.execute(
                    """
                    SELECT text FROM video_notes
                    WHERE video_id = ? AND note_type = 'ai_keywords'
                    """,
                    (self.video_id,),
                ).fetchone()[0]
            )
        self.assertEqual(json.loads(row["keywords_json"]), preview["keywords"])
        self.assertEqual(stored_count, len(preview["keywords"]))
        self.assertEqual(digest_count, 0)
        self.assertNotIn("summary", note_payload)
        self.assertNotIn("core_points", note_payload)
        self.assertEqual(tables_after, tables_before)

    def test_source_change_marks_saved_keywords_stale(self):
        self.save_original("科技股会出现一轮反弹。")
        preview = self.service.preview(self.video_id)
        self.service.save(
            self.video_id,
            categories=preview["categories"],
            source_hash=preview["source_hash"],
            model=preview["model"],
        )
        self.assertFalse(self.service.status(self.video_id)["stale"])

        self.save_original("科技股反弹后还会进入震荡阶段。")

        self.assertTrue(self.service.status(self.video_id)["stale"])

    def test_rejects_stale_preview_on_save(self):
        self.save_original("科技股会出现一轮反弹。")
        preview = self.service.preview(self.video_id)
        self.save_original("视频文字已经被人工修改。")

        with self.assertRaisesRegex(ValueError, "已经变化"):
            self.service.save(
                self.video_id,
                categories=preview["categories"],
                source_hash=preview["source_hash"],
                model=preview["model"],
            )

    def test_save_limits_each_category_and_total(self):
        self.save_original("科技股反弹后进入震荡阶段。")
        preview = self.service.preview(self.video_id)
        values = {
            category: [f"{index:02d}{item:02d}关键词" for item in range(10)]
            for index, category in enumerate(KEYWORD_CATEGORIES)
        }

        saved = self.service.save(
            self.video_id,
            categories=values,
            source_hash=preview["source_hash"],
            model=preview["model"],
        )

        self.assertLessEqual(len(saved["keywords"]), 40)
        self.assertTrue(all(len(items) <= 8 for items in saved["categories"].values()))
        with self.storage.connect() as conn:
            indexed = conn.execute(
                "SELECT keywords_json FROM knowledge_records WHERE video_id = ?",
                (self.video_id,),
            ).fetchone()
        self.assertEqual(len(json.loads(indexed["keywords_json"])), 40)

    def test_all_empty_categories_are_valid_and_cached(self):
        self.save_original("大家好。")
        preview = self.service.preview(self.video_id)
        empty = {category: [] for category in KEYWORD_CATEGORIES}
        saved = self.service.save(
            self.video_id,
            categories=empty,
            source_hash=preview["source_hash"],
            model=preview["model"],
        )
        self.assertEqual(saved["keywords"], [])
        self.assertTrue(self.service.preview(self.video_id)["cached"])
        with self.storage.connect() as conn:
            indexed = json.loads(
                conn.execute(
                    "SELECT keywords_json FROM knowledge_records WHERE video_id = ?",
                    (self.video_id,),
                ).fetchone()[0]
            )
        self.assertEqual(indexed, [])

    def test_legacy_keyword_note_is_migrated_without_summary_content(self):
        self.save_original("紫金矿业属于有色资源行业。")
        self.storage.save_note(
            self.video_id,
            "ai_keywords",
            json.dumps(
                {
                    "summary": "旧摘要不应进入新索引。",
                    "core_points": ["旧核心要点。"],
                    "keywords": ["紫金矿业", "有色资源"],
                    "items": [
                        {"name": "紫金矿业", "category": "company"},
                        {"name": "有色资源", "category": "industry"},
                    ],
                    "source_hash": "legacy-hash",
                    "confirmed_at": "2026-07-01T00:00:00+00:00",
                },
                ensure_ascii=False,
            ),
        )

        migrated = Storage(self.storage.path).get_content_keyword_set(self.video_id)

        self.assertEqual(migrated["schema_version"], "legacy-v2-migrated")
        self.assertEqual(migrated["categories"]["企业、个股与产业链"], ["紫金矿业"])
        self.assertEqual(migrated["categories"]["行业与板块"], ["有色资源"])
        with self.storage.connect() as conn:
            migrated_note = json.loads(
                conn.execute(
                    """
                    SELECT text FROM video_notes
                    WHERE video_id = ? AND note_type = 'ai_keywords'
                    """,
                    (self.video_id,),
                ).fetchone()[0]
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE source_type = 'ai_digest'"
                ).fetchone()[0],
                0,
            )
        self.assertNotIn("summary", migrated_note)
        self.assertNotIn("core_points", migrated_note)


if __name__ == "__main__":
    unittest.main()
