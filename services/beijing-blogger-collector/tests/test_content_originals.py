from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mx_agent.storage import Storage


class ContentOriginalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.storage = Storage(self.root / "content-originals.sqlite3")
        self.content_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "image-work-1",
                "author": "模型先生",
                "title": "20260705_0919_abcdef0123456789",
                "description": "",
                "published_at": "2026-07-05T01:19:00+00:00",
            }
        )
        self.storage.save_asset(
            {
                "video_id": self.content_id,
                "asset_type": "screenshot",
                "storage_mode": "source_file",
                "original_name": "20260705_0919_abcdef0123456789.png",
                "local_path": str(self.root / "20260705_0919_abcdef0123456789.png"),
                "mime_type": "image/png",
                "size_bytes": 1234,
                "sha256": "asset-sha",
                "source": "test",
                "status": "stored",
                "raw_json": {},
            }
        )
        self.storage.save_ocr_title(self.content_id, "国产算力进入质变阶段")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_and_read_content_original_catalog(self) -> None:
        text = "第一行原文。\n第二行原文。"

        saved = self.storage.save_content_original(self.content_id, text)
        catalog = self.storage.get_content_original_catalog(self.content_id)

        self.assertEqual(saved["original_text"], text)
        self.assertEqual(
            saved["text_sha256"],
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        self.assertIsNotNone(catalog)
        self.assertEqual(catalog["media_type"], "image")
        self.assertEqual(catalog["published_at"], "2026-07-05T01:19:00+00:00")
        self.assertEqual(catalog["canonical_name"], "20260705_0919_abcdef0123456789")
        self.assertEqual(catalog["active_title"], "国产算力进入质变阶段")
        self.assertEqual(catalog["original_text"], text)

    def test_update_replaces_current_original_without_time_fields(self) -> None:
        self.storage.save_content_original(self.content_id, "旧原文")

        updated = self.storage.save_content_original(self.content_id, "新原文")

        self.assertEqual(updated["original_text"], "新原文")
        self.assertNotIn("created_at", updated)
        self.assertNotIn("updated_at", updated)
        with self.storage.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM content_originals WHERE content_id = ?",
                (self.content_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_manual_save_archives_database_txt_and_search_index_together(self) -> None:
        text = "国产算力已经出现产业链质变。\n科技股仍需观察估值。"

        result = self.storage.save_official_original(self.content_id, text)

        target = (
            self.root
            / "博主数据"
            / "模型先生"
            / "视频原文"
            / "2026"
            / "07"
            / "20260705_0919_abcdef0123456789.txt"
        )
        self.assertEqual(target.read_bytes(), text.encode("utf-8"))
        self.assertEqual(result["txt_path"], str(target.resolve()))
        self.assertTrue(result["note"]["official"])
        self.assertEqual(result["note"]["text"], text)
        self.assertNotIn("created_at", result["note"])
        self.assertNotIn("updated_at", result["note"])
        self.assertEqual(self.storage.get_note(self.content_id, "video_text")["text"], text)
        self.assertEqual(
            self.storage.get_video_detail(self.content_id)["notes"]["video_text"]["text"],
            text,
        )
        with self.storage.connect() as conn:
            chunk = conn.execute(
                """
                SELECT source_table, source_id, content
                FROM knowledge_chunks
                WHERE video_id = ? AND source_type = 'video_original'
                """,
                (self.content_id,),
            ).fetchone()
            dirty = conn.execute("SELECT COUNT(*) FROM knowledge_dirty").fetchone()[0]
        self.assertEqual(chunk["source_table"], "content_originals")
        self.assertEqual(chunk["source_id"], self.content_id)
        self.assertEqual(chunk["content"], text)
        self.assertEqual(dirty, 0)

    def test_title_keyword_and_original_edits_update_current_catalog(self) -> None:
        self.storage.save_official_original(self.content_id, "旧原文")
        self.storage.save_manual_title(self.content_id, "国产算力与科技股估值")
        self.storage.save_note(
            self.content_id,
            "ai_keywords",
            json.dumps(
                {"keywords": ["国产算力", "科技股", "PE估值"]},
                ensure_ascii=False,
            ),
        )
        self.storage.save_official_original(self.content_id, "修改后的正式原文")

        catalog = self.storage.get_content_original_catalog(self.content_id)
        self.assertEqual(catalog["active_title"], "国产算力与科技股估值")
        self.assertEqual(
            json.loads(catalog["keywords_json"]),
            ["国产算力", "科技股", "PE估值"],
        )
        self.assertEqual(catalog["original_text"], "修改后的正式原文")
        target = Path(self.storage.save_official_original(
            self.content_id,
            "修改后的正式原文",
        )["txt_path"])
        self.assertEqual(target.read_text(encoding="utf-8"), "修改后的正式原文")


if __name__ == "__main__":
    unittest.main()
