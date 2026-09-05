from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mx_agent.storage import Storage


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LibraryLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.storage = Storage(self.root / "library.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_video(self) -> int:
        video_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "work-1",
                "author": "模型先生",
                "title": "科技股分化刚刚开始",
                "description": "",
            }
        )
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "video",
                "storage_mode": "source_file",
                "original_name": "202607291620.mp4",
                "local_path": str(self.root / "202607291620.mp4"),
                "mime_type": "video/mp4",
                "size_bytes": 1234,
                "sha256": "asset-sha",
                "source": "test",
                "status": "stored",
                "raw_json": {},
            }
        )
        return video_id

    def test_video_list_contains_lightweight_card_summary(self) -> None:
        video_id = self.create_video()
        self.storage.save_note(video_id, "video_text", "科技股开始分化。")
        self.storage.save_note(
            video_id,
            "ai_keywords",
            json.dumps(
                {
                    "keywords": ["科技股", "分化"],
                    "items": [],
                    "model": "test-model",
                    "source_hash": "source-hash",
                },
                ensure_ascii=False,
            ),
        )

        items = self.storage.list_videos(limit=10, account="模型先生")

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["primary_asset_id"], 1)
        self.assertEqual(item["primary_asset_mime"], "video/mp4")
        self.assertEqual(item["primary_asset_name"], "202607291620.mp4")
        self.assertEqual(item["has_video_text"], 1)
        self.assertNotIn("科技股开始分化", json.dumps(item, ensure_ascii=False))
        self.assertIn("科技股", item["ai_keywords_json"])

    def test_detail_comments_omit_ocr_debug_payload(self) -> None:
        video_id = self.create_video()
        self.storage.upsert_comment(
            {
                "video_id": video_id,
                "source": "test",
                "source_comment_id": "comment-1",
                "author": "测试用户",
                "text": "请问科技股会提前反弹吗？",
                "raw_json": {
                    "kind": "fan_comment",
                    "display_order": 1,
                    "parent_source_comment_id": "",
                    "source_image": "large-debug-image.png",
                    "ocr_lines": ["很多", "调试", "识别", "内容"],
                    "recognition_model": "test-ocr",
                },
            }
        )

        detail = self.storage.get_video_detail(video_id)

        self.assertIsNotNone(detail)
        raw = detail["comments"][0]["raw_json"]
        self.assertEqual(raw["kind"], "fan_comment")
        self.assertEqual(raw["display_order"], 1)
        self.assertNotIn("ocr_lines", raw)
        self.assertNotIn("source_image", raw)
        self.assertNotIn("recognition_model", raw)

    def test_retired_mainline_ui_is_not_loaded(self) -> None:
        self.assertFalse((PROJECT_ROOT / "web").exists())
        collector_html = (PROJECT_ROOT / "collector_web" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("investmentBoard", collector_html)
        self.assertNotIn("投资主线", collector_html)


if __name__ == "__main__":
    unittest.main()
