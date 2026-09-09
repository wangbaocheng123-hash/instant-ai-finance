from __future__ import annotations

import gzip
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
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
            self.assertIs(wire_comment["author_liked"], True)

            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE comments SET collected_at=? WHERE video_id=?",
                    ("2026-09-03T09:05:30+08:00", "778899"),
                )
                connection.execute(
                    """
                    UPDATE videos
                    SET comments_collected_at=?, updated_at=?
                    WHERE video_id=?
                    """,
                    (
                        "2026-09-03T09:05:30+08:00",
                        "2026-09-03T09:05:30+08:00",
                        "778899",
                    ),
                )
                connection.commit()

            heartbeat_only = bridge.scan_once()
            self.assertEqual(heartbeat_only["unchanged"], 1)
            self.assertEqual(len(outbox.list_recent(limit=10)), 1)

            # Douyin can add/remove the creator-like marker without changing
            # the comment count or the parent video row. The bridge must still
            # emit a new revision so Instant AI does not retain stale state.
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE comments SET is_author_digged=? WHERE video_id=?",
                    (0, "778899"),
                )
                connection.commit()

            author_like_changed = bridge.scan_once()
            author_like_revisions = outbox.list_recent(limit=10)
            author_like_latest = outbox.get(author_like_revisions[0]["transfer_id"])
            author_like_artifact = next(
                item
                for item in outbox.artifacts_for(author_like_revisions[0]["transfer_id"])
                if item["artifact_kind"] == "comment_bundle"
            )
            author_like_wire_comment = json.loads(
                gzip.decompress(Path(author_like_artifact["local_path"]).read_bytes())
                .decode("utf-8")
                .splitlines()[0]
            )

            self.assertEqual(author_like_changed["enqueued"], 1)
            self.assertEqual(len(author_like_revisions), 2)
            self.assertEqual(author_like_latest["manifest"]["work"]["revision"], 2)
            self.assertIs(author_like_wire_comment["author_liked"], False)

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
            self.assertEqual(len(revisions), 3)
            self.assertEqual(latest["manifest"]["work"]["revision"], 3)

            # A first start after the fingerprint upgrade must not resend the
            # complete historical library merely because the algorithm
            # changed. Migrate a proven-current v1 state in place.
            with bridge._connect() as connection:
                legacy_row = dict(
                    connection.execute(
                        """
                        SELECT video_id, creator, title, source_url, published_at,
                               discovered_at, downloaded_at, file_path, file_size,
                               duration_seconds, download_status,
                               comments_collected_at, comment_count, updated_at
                        FROM videos WHERE video_id=?
                        """,
                        ("778899",),
                    ).fetchone()
                )
            migration_state = root / "migration-state.json"
            migration_state.write_text(
                json.dumps(
                    {
                        "778899": {
                            "signature": bridge._legacy_signature(legacy_row),
                            "transfer_id": "old-transfer",
                            "queued_at": "2026-09-03T10:00:00+08:00",
                        }
                    }
                ),
                encoding="utf-8",
            )
            migration_bridge = ModelDownloaderBridge(
                outbox=outbox,
                artifact_dir=artifact_root,
                collector_node_id="beijing-1",
                collector_key_id="key-1",
                collector_version="test",
                database_path=database,
                media_root=media_root,
                state_path=migration_state,
            )

            migrated = migration_bridge.scan_once()
            migrated_state = json.loads(migration_state.read_text(encoding="utf-8"))
            self.assertEqual(migrated["unchanged"], 1)
            self.assertEqual(len(outbox.list_recent(limit=10)), 3)
            self.assertEqual(migrated_state["778899"]["signature_version"], 2)
            self.assertTrue(
                bridge._requires_signature_upgrade_transfer(
                    legacy_row,
                    now=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
                )
            )
            self.assertFalse(
                bridge._requires_signature_upgrade_transfer(
                    legacy_row,
                    now=datetime(2026, 9, 6, 2, 0, tzinfo=UTC),
                )
            )


if __name__ == "__main__":
    unittest.main()
