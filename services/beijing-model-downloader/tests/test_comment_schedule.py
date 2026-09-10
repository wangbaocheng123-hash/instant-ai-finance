from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


COMPONENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPONENT))

from library_store import LibraryStore  # noqa: E402
from model_downloader_daemon import (  # noqa: E402
    COMMENT_MATURE_REFRESH_MINUTES,
    COMMENT_YOUNG_REFRESH_MINUTES,
    comment_refresh_interval_minutes,
)


CHINA = timezone(timedelta(hours=8))


class CommentScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        # Python 3.14 on Windows may keep SQLite handles alive until interpreter
        # shutdown; Linux release tests still remove the directory normally.
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.temporary.name)
        self.database = root / "library.sqlite3"
        self.store = LibraryStore(self.database, root / "comments")
        self.now = datetime(2026, 9, 10, 18, 0, tzinfo=CHINA)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_downloaded(self, video_id: str, published_at: datetime) -> None:
        self.store.upsert_video(
            video_id=video_id,
            creator="模型先生",
            title=video_id,
            source_url=f"https://www.douyin.com/video/{video_id}",
            published_at=published_at,
            now=self.now,
        )
        self.store.mark_downloaded(
            video_id=video_id,
            title=video_id,
            file_path=Path(f"/tmp/{video_id}.mp4"),
            file_size=1,
            duration_seconds=1.0,
            downloaded_at=self.now,
        )

    def set_last_success(self, video_id: str, value: datetime) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE videos SET comments_collected_at=? WHERE video_id=?",
                (value.isoformat(), video_id),
            )

    def due_ids(self) -> set[str]:
        return {
            str(row["video_id"])
            for row in self.store.comment_refresh_candidates(
                now=self.now,
                young_window_hours=24,
                young_refresh_minutes=COMMENT_YOUNG_REFRESH_MINUTES,
                mature_refresh_minutes=COMMENT_MATURE_REFRESH_MINUTES,
            )
        }

    def test_interval_switches_exactly_at_24_hours(self) -> None:
        self.assertEqual(
            COMMENT_YOUNG_REFRESH_MINUTES,
            comment_refresh_interval_minutes(
                self.now - timedelta(hours=23, minutes=59, seconds=59),
                self.now,
            ),
        )
        self.assertEqual(
            COMMENT_MATURE_REFRESH_MINUTES,
            comment_refresh_interval_minutes(
                self.now - timedelta(hours=24),
                self.now,
            ),
        )

    def test_young_video_is_due_at_15_minutes_not_14(self) -> None:
        self.add_downloaded("young", self.now - timedelta(hours=1))
        self.set_last_success("young", self.now - timedelta(minutes=14))
        self.assertNotIn("young", self.due_ids())
        self.set_last_success("young", self.now - timedelta(minutes=15))
        self.assertIn("young", self.due_ids())

    def test_mature_video_continues_hourly(self) -> None:
        self.add_downloaded("mature", self.now - timedelta(days=30))
        self.set_last_success("mature", self.now - timedelta(minutes=59))
        self.assertNotIn("mature", self.due_ids())
        self.set_last_success("mature", self.now - timedelta(minutes=60))
        self.assertIn("mature", self.due_ids())

    def test_manual_request_is_due_immediately(self) -> None:
        self.add_downloaded("manual", self.now - timedelta(days=3))
        self.set_last_success("manual", self.now)
        self.assertNotIn("manual", self.due_ids())
        self.store.mark_comment_refresh_requested("manual", self.now)
        self.assertIn("manual", self.due_ids())


if __name__ == "__main__":
    unittest.main()
