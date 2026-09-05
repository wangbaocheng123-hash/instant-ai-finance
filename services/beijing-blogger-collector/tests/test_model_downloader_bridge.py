from __future__ import annotations

import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mx_agent.model_downloader_bridge import (
    MODEL_MR_TRANSFER_CREATOR_ID,
    ModelDownloaderBridge,
)
from mx_agent.transfer_outbox import TransferOutbox


class ModelDownloaderBridgeTests(unittest.TestCase):
    def test_unreadable_default_database_disables_bridge_without_breaking_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "outbox" / "artifacts"
            artifact_root.mkdir(parents=True)
            outbox = TransferOutbox(
                root / "outbox" / "transfer.sqlite3",
                allowed_artifact_roots=(artifact_root,),
            )

            with patch(
                "mx_agent.model_downloader_bridge.Path.is_file",
                side_effect=PermissionError("production database is not readable in tests"),
            ):
                bridge = ModelDownloaderBridge.from_environment(
                    outbox=outbox,
                    artifact_dir=artifact_root,
                    collector_node_id="beijing-1",
                    collector_key_id="key-1",
                    collector_version="test",
                )
                status = bridge.status()

            self.assertFalse(bridge.enabled)
            self.assertFalse(status["database_available"])

    def test_downloaded_video_is_enqueued_once_for_reserved_model_mr_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media_root = root / "videos"
            artifact_root = root / "outbox" / "artifacts"
            media_root.mkdir()
            artifact_root.mkdir(parents=True)
            video = media_root / "work.mp4"
            video.write_bytes(b"model-mr-video")
            database = root / "library.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE videos(
                        video_id TEXT, creator TEXT, title TEXT, source_url TEXT,
                        published_at TEXT, discovered_at TEXT, downloaded_at TEXT,
                        file_path TEXT, file_size INTEGER, duration_seconds REAL,
                        download_status TEXT, comments_collected_at TEXT,
                        comment_count INTEGER, updated_at TEXT
                    );
                    CREATE TABLE comments(
                        video_id TEXT, comment_id TEXT, parent_comment_id TEXT,
                        author_name TEXT, text TEXT, created_at TEXT,
                        digg_count INTEGER, reply_count INTEGER, ip_label TEXT,
                        is_creator INTEGER, is_author_digged INTEGER,
                        reply_to_comment_id TEXT, reply_to_user_name TEXT,
                        label_text TEXT, collected_at TEXT
                    );
                    """
                )
                connection.execute(
                    "INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "778899", "模型先生", "新作品",
                        "https://www.douyin.com/video/778899",
                        "2026-09-03T09:00:00+08:00", "", "",
                        str(video), video.stat().st_size, 10.0, "downloaded",
                        "2026-09-03T09:05:00+08:00", 1,
                        "2026-09-03T09:05:00+08:00",
                    ),
                )
                connection.execute(
                    "INSERT INTO comments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "778899", "c1", "", "读者\n甲", "测试\r\n评论\x00\t结束\x7f",
                        "2026-09-03T09:01:00+08:00", 3, 0, "北京", 0, 1,
                        "", "", "", "2026-09-03T09:05:00+08:00",
                    ),
                )
                connection.commit()
            connection.close()

            outbox = TransferOutbox(
                root / "outbox" / "transfer.sqlite3",
                allowed_artifact_roots=(artifact_root,),
            )
            bridge = ModelDownloaderBridge(
                outbox=outbox,
                artifact_dir=artifact_root,
                collector_node_id="beijing-1",
                collector_key_id="key-1",
                collector_version="test",
                database_path=database,
                media_root=media_root,
                state_path=root / "state.json",
            )

            first = bridge.scan_once()
            second = bridge.scan_once()
            rows = outbox.list_recent(limit=10)
            queued = outbox.get(rows[0]["transfer_id"])

            self.assertEqual(first["enqueued"], 1)
            self.assertEqual(second["unchanged"], 1)
            self.assertEqual(len(rows), 1)
            self.assertIsNotNone(queued)
            self.assertEqual(queued["manifest"]["creator"]["creator_id"], MODEL_MR_TRANSFER_CREATOR_ID)
            self.assertEqual(queued["manifest"]["work"]["source_work_id"], "778899")
            self.assertEqual(queued["manifest"]["comment_snapshot"]["captured_count"], 1)
            comment_artifact = next(
                item
                for item in outbox.artifacts_for(rows[0]["transfer_id"])
                if item["artifact_kind"] == "comment_bundle"
            )
            wire_comment = json.loads(
                gzip.decompress(Path(comment_artifact["local_path"]).read_bytes())
                .decode("utf-8")
                .splitlines()[0]
            )
            self.assertEqual(wire_comment["author"], "读者 甲")
            self.assertEqual(wire_comment["text"], "测试 评论 结束")

            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE comments SET text=? WHERE video_id=?",
                    ("更新后的评论", "778899"),
                )
                connection.execute(
                    """
                    UPDATE videos
                    SET comments_collected_at=?, updated_at=?
                    WHERE video_id=?
                    """,
                    (
                        "2026-09-03T09:06:00+08:00",
                        "2026-09-03T09:06:00+08:00",
                        "778899",
                    ),
                )
                connection.commit()
            connection.close()

            changed = bridge.scan_once()
            revisions = outbox.list_recent(limit=10)
            latest = outbox.get(revisions[0]["transfer_id"])
            self.assertEqual(changed["enqueued"], 1)
            self.assertEqual(len(revisions), 2)
            self.assertEqual(latest["manifest"]["work"]["revision"], 2)


if __name__ == "__main__":
    unittest.main()
