from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mx_agent.importer import DownloadImportService
from mx_agent.knowledge import KnowledgeReader
from mx_agent.storage import Storage
from mx_agent.transcriber import VideoTranscriber


class SingleSourceMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.storage = Storage(self.root / "data" / "test.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_import_references_original_media_without_copying_it(self) -> None:
        source = self.root / "模型视频" / "202607291200.mp4"
        source.parent.mkdir()
        source.write_bytes(b"single canonical video")

        result = DownloadImportService(self.storage).import_file_as_video(
            source,
            source="model-video-drop",
            author="模型先生",
        )

        asset = self.storage.list_assets(result["video_id"])[0]
        self.assertEqual(Path(asset["local_path"]).resolve(), source.resolve())
        self.assertEqual(asset["storage_mode"], "source_file")
        self.assertFalse((self.root / "data" / "archive").exists())

    def test_new_dated_video_is_immediately_searchable_as_latest(self) -> None:
        old_id, _ = self.storage.upsert_video(
            {
                "source": "model-video-drop",
                "source_video_id": "old-video",
                "author": "模型先生",
                "title": "202607301556",
                "published_at": "2026-07-30T07:56:00+00:00",
            }
        )
        self.storage.save_note(old_id, "video_text", "七月三十日的旧观点")

        source = self.root / "模型视频" / "202607311230_9999999999999999999.mp4"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(b"future integration fixture")
        importer = DownloadImportService(self.storage)
        with patch.object(importer, "_recognize_cover_title", return_value={"skipped": True}):
            result = importer.import_file_as_video(
                source,
                source="model-video-drop",
                author="模型先生",
            )
        new_id = int(result["video_id"])
        self.storage.save_note(new_id, "video_text", "七月三十一日新视频对科技股的判断")

        latest = KnowledgeReader(self.storage.path).search("模型先生最新观点", limit=3)
        dated = KnowledgeReader(self.storage.path).search(
            "模型先生2026年7月31日有什么观点",
            limit=3,
        )
        with self.storage.connect() as conn:
            dirty_count = conn.execute("SELECT COUNT(*) FROM knowledge_dirty").fetchone()[0]

        self.assertEqual(
            self.storage.get_video(new_id)["published_at"],
            "2026-07-31T04:30:00+00:00",
        )
        self.assertEqual(latest["items"][0]["record_id"], f"video:{new_id}")
        self.assertEqual(
            [item["record_id"] for item in dated["items"]],
            [f"video:{new_id}"],
        )
        self.assertEqual(dirty_count, 0)

    def test_successful_local_recognition_cleanup_removes_work_directory(self) -> None:
        work_dir = self.root / "runtime" / "transcribe" / "video_7"
        frames = work_dir / "subtitle_frames"
        frames.mkdir(parents=True)
        (frames / "frame_001.png").write_bytes(b"frame")
        (work_dir / "audio.wav").write_bytes(b"audio")

        result = VideoTranscriber._cleanup_successful_work_dir(work_dir)

        self.assertTrue(result["removed"])
        self.assertEqual(result["files"], 2)
        self.assertFalse(work_dir.exists())

if __name__ == "__main__":
    unittest.main()
