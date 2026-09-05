from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mx_agent.collector_export import (
    CollectorContentReadyAdapter,
    CollectorExportError,
)
from mx_agent.creators import CreatorRegistry
from mx_agent.storage import Storage
from mx_agent.transfer_contract import MAX_COMMENT_ITEMS
from mx_agent.transfer_outbox import TransferOutbox


class CollectorExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = Storage(self.root / "data" / "collector.sqlite3")
        self.registry = CreatorRegistry(
            SimpleNamespace(
                source_account_name="贵族之路",
                creator_profile_url="https://www.douyin.com/user/creator-sec-001",
                creator_sync_mode="count",
                creator_sync_enabled=False,
                creator_sync_interval_minutes=10,
                creator_sync_history_limit=500,
                creator_comments_enabled=True,
                creator_comment_limit=5000,
                creator_comment_refresh_minutes=60,
                creator_comment_tracking_hours=24,
            ),
            path=self.root / "runtime" / "creators.json",
        )
        self.outbox = TransferOutbox(
            self.root / "runtime" / "transfer-outbox.sqlite3",
            allowed_artifact_roots=[self.root],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def adapter(self, *, max_comments: int = MAX_COMMENT_ITEMS) -> CollectorContentReadyAdapter:
        return CollectorContentReadyAdapter(
            self.storage,
            self.registry,
            self.outbox,
            artifact_dir=self.root / "runtime" / "transfer-artifacts",
            collector_node_id="beijing-collector-1",
            collector_key_id="beijing-key-1",
            collector_version="0.10.0-test",
            max_comments=max_comments,
        )

    def add_video(self, source_work_id: str = "7000000000000000001") -> int:
        video_id, _created = self.storage.upsert_video(
            {
                "source": "douyin-auto",
                "source_video_id": source_work_id,
                "author": "贵族之路",
                "title": "一条测试作品",
                "description": "只发送采集事实，不发送本地路径。",
                "url": f"https://www.douyin.com/video/{source_work_id}",
                "cover_url": "https://example.invalid/cover.jpg",
                "published_at": "2026-08-30T09:00:00+08:00",
                "discovered_at": "2026-08-30T09:10:00+08:00",
                "raw_json": {
                    "douyin_aweme_id": source_work_id,
                    "source_path": str(self.root / "must-not-leak.mp4"),
                },
            }
        )
        return video_id

    def add_comment(
        self,
        video_id: int,
        source_comment_id: str,
        *,
        author: str,
        text: str,
        display_order: int,
        captured_at: str,
        parent_source_comment_id: str = "",
        root_source_comment_id: str = "",
        reply_count: int = 0,
        kind: str = "user_comment",
        section: str = "fan_comment",
    ) -> None:
        self.storage.upsert_comment(
            {
                "video_id": video_id,
                "source": "douyin-web",
                "source_comment_id": source_comment_id,
                "author": author,
                "text": text,
                "like_count": display_order,
                "reply_count": reply_count,
                "sentiment": "neutral",
                "risk_level": "normal",
                "published_at": "2026-08-30T09:15:00",
                "captured_at": captured_at,
                "raw_json": {
                    "kind": kind,
                    "section": section,
                    "parent_source_comment_id": parent_source_comment_id,
                    "root_source_comment_id": root_source_comment_id,
                    "author_uid": f"author-{source_comment_id}",
                    "sec_uid": f"sec-{source_comment_id}",
                    "user_id": f"user-{source_comment_id}",
                    "local_path": str(self.root / "private-comment.json"),
                    "cookie": "forbidden-comment-cookie",
                    "author_liked": source_comment_id == "comment-10",
                    "display_order": display_order,
                },
            }
        )

    def test_callback_exports_deterministic_bundle_and_enqueues_local_artifacts(self) -> None:
        video_id = self.add_video()
        private_media = self.root / "private-media" / "source-video.mp4"
        private_media.parent.mkdir()
        private_media.write_bytes(b"beijing collector fixture" * 32)
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "video",
                "original_name": "source-video.mp4",
                "local_path": str(private_media),
                "mime_type": "video/mp4",
                "size_bytes": private_media.stat().st_size,
                "sha256": "0" * 64,
                "source": "test",
            }
        )
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "cover",
                "original_name": "missing.jpg",
                "local_path": str(self.root / "private-media" / "missing.jpg"),
                "mime_type": "image/jpeg",
                "size_bytes": 200,
                "sha256": "1" * 64,
                "source": "test",
            }
        )
        self.add_comment(
            video_id,
            "comment-20",
            author="粉丝乙",
            text="这是根评论",
            display_order=20,
            captured_at="2026-08-30T09:22:00+08:00",
            reply_count=1,
        )
        self.add_comment(
            video_id,
            "comment-10",
            author="粉丝甲",
            text="排在最前面的评论",
            display_order=10,
            captured_at="2026-08-30T09:21:00+08:00",
        )
        self.add_comment(
            video_id,
            "comment-30",
            author="贵族之路",
            text="作者回复",
            display_order=30,
            captured_at="2026-08-30T09:23:00+08:00",
            parent_source_comment_id="comment-20",
            root_source_comment_id="comment-20",
            kind="author_reply",
            section="author_interaction",
        )

        adapter = self.adapter()
        first_export = adapter.export(video_id, "primary")
        second_export = adapter.export(video_id, "primary")
        self.assertEqual(first_export.comment_bundle_bytes, second_export.comment_bundle_bytes)
        self.assertEqual(
            first_export.comment_snapshot["bundle"],
            second_export.comment_snapshot["bundle"],
        )

        queued = adapter(video_id, "primary")

        self.assertEqual(queued["action"], "inserted")
        self.assertEqual(queued["comment_count"], 3)
        self.assertEqual(queued["media_count"], 1)
        self.assertEqual(queued["missing_media_count"], 1)
        manifest = queued["manifest"]
        self.assertEqual(
            manifest["creator"]["creator_id"],
            self.registry.get("primary")["creator_uuid"],
        )
        self.assertEqual(manifest["creator"]["platform_user_id"], "creator-sec-001")
        self.assertEqual(manifest["work"]["source_work_id"], "7000000000000000001")
        self.assertTrue(manifest["comment_snapshot"]["complete"])
        self.assertNotIn(str(self.root), json.dumps(manifest, ensure_ascii=False))
        self.assertNotIn("local_path", json.dumps(manifest, ensure_ascii=False))

        artifacts = self.outbox.artifacts_for(manifest["transfer_id"])
        self.assertEqual(len(artifacts), 2)
        media_artifact = next(item for item in artifacts if item["artifact_kind"] == "media")
        comment_artifact = next(
            item for item in artifacts if item["artifact_kind"] == "comment_bundle"
        )
        self.assertEqual(
            comment_artifact["artifact_id"],
            manifest["comment_snapshot"]["bundle"]["bundle_id"],
        )
        self.assertEqual(media_artifact["mime_type"], "video/mp4")
        self.assertEqual(Path(media_artifact["local_path"]), private_media.resolve())
        comment_path = Path(comment_artifact["local_path"])
        self.assertTrue(comment_path.is_file())
        rows = [
            json.loads(line)
            for line in gzip.decompress(comment_path.read_bytes()).decode("utf-8").splitlines()
        ]
        self.assertEqual(
            [row["source_comment_id"] for row in rows],
            ["comment-10", "comment-20", "comment-30"],
        )
        self.assertEqual(rows[-1]["kind"], "author_reply")
        encoded_comments = gzip.decompress(comment_path.read_bytes()).decode("utf-8")
        for forbidden in (
            "author_uid",
            "sec_uid",
            "user_id",
            "local_path",
            "forbidden-comment-cookie",
            "private-comment.json",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, encoded_comments)

    def test_comment_limit_and_missing_media_produce_incomplete_but_valid_manifest(self) -> None:
        video_id = self.add_video("7000000000000000002")
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "video",
                "original_name": "not-downloaded.mp4",
                "local_path": str(self.root / "not-downloaded.mp4"),
                "mime_type": "video/mp4",
                "size_bytes": 100,
                "sha256": "2" * 64,
                "source": "test",
            }
        )
        for index in range(3):
            self.add_comment(
                video_id,
                f"limited-{index}",
                author="粉丝",
                text=f"评论 {index}",
                display_order=index,
                captured_at=f"2026-08-30T09:2{index}:00+08:00",
            )

        queued = self.adapter(max_comments=2)(video_id, "primary")

        snapshot = queued["manifest"]["comment_snapshot"]
        self.assertEqual(snapshot["captured_count"], 2)
        self.assertEqual(snapshot["expected_total"], 3)
        self.assertFalse(snapshot["complete"])
        self.assertEqual(queued["manifest"]["media"], [])
        self.assertEqual(queued["missing_media_count"], 1)
        artifacts = self.outbox.artifacts_for(queued["transfer_id"])
        self.assertEqual([item["artifact_kind"] for item in artifacts], ["comment_bundle"])

    def test_comment_control_characters_are_normalized_only_in_wire_bundle(self) -> None:
        video_id = self.add_video("7000000000000000099")
        original_text = "第一行\r\n第二行\x00\t结束\x7f"
        self.add_comment(
            video_id,
            "comment-control-1",
            author="粉丝\n甲",
            text=original_text,
            display_order=1,
            captured_at="2026-08-30T09:21:00+08:00",
        )

        exported = self.adapter().export(video_id, "primary")
        rows = [
            json.loads(line)
            for line in gzip.decompress(exported.comment_bundle_bytes)
            .decode("utf-8")
            .splitlines()
        ]

        self.assertEqual(rows[0]["author"], "粉丝 甲")
        self.assertEqual(rows[0]["text"], "第一行 第二行 结束")
        stored = self.storage.list_comments(video_id, limit=10)
        self.assertEqual(stored[0]["text"], original_text)

    def test_invalid_export_is_diagnostic_and_does_not_reserve_sequence(self) -> None:
        video_id = self.add_video("7000000000000000003")
        with self.storage.connect() as connection:
            connection.execute(
                "UPDATE videos SET author = ? WHERE id = ?",
                ("另一个博主", video_id),
            )

        with self.assertRaisesRegex(CollectorExportError, "跨博主"):
            self.adapter()(video_id, "primary")

        creator = self.registry.get("primary")
        reservation = self.outbox.reserve(
            node_id="beijing-collector-1",
            creator_id=creator["creator_uuid"],
            platform="douyin",
            source_work_id="7000000000000000003",
        )
        self.assertEqual(reservation.source_sequence, 1)
        self.assertEqual(reservation.work_revision, 1)

    def test_unsupported_audio_and_octet_stream_are_diagnosed_and_omitted(self) -> None:
        video_id = self.add_video("7000000000000000004")
        audio = self.root / "private-media" / "sound.mp3"
        audio_mp4 = self.root / "private-media" / "sound.m4a"
        generic_video = self.root / "private-media" / "unknown.bin"
        audio.parent.mkdir()
        audio.write_bytes(b"not-a-real-mp3-test-fixture")
        audio_mp4.write_bytes(b"not-a-supported-audio-mp4-test-fixture")
        generic_video.write_bytes(b"not-a-supported-video-test-fixture")
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "audio",
                "original_name": "sound.m4a",
                "local_path": str(audio_mp4),
                "mime_type": "audio/mp4",
                "size_bytes": audio_mp4.stat().st_size,
                "sha256": "2" * 64,
                "source": "test",
            }
        )
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "audio",
                "original_name": "sound.mp3",
                "local_path": str(audio),
                "mime_type": "audio/mpeg",
                "size_bytes": audio.stat().st_size,
                "sha256": "0" * 64,
                "source": "test",
            }
        )
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "video",
                "original_name": "unknown.bin",
                "local_path": str(generic_video),
                "mime_type": "application/octet-stream",
                "size_bytes": generic_video.stat().st_size,
                "sha256": "1" * 64,
                "source": "test",
            }
        )

        exported = self.adapter().export(video_id, "primary")
        self.assertEqual(exported.media, ())
        self.assertEqual(exported.unsupported_media_count, 3)
        self.assertEqual(exported.omitted_media_count, 3)

        queued = self.adapter()(video_id, "primary")
        self.assertEqual(queued["manifest"]["media"], [])
        self.assertEqual(queued["unsupported_media_count"], 3)
        self.assertEqual(queued["omitted_media_count"], 3)
        self.assertEqual(
            [
                item["artifact_kind"]
                for item in self.outbox.artifacts_for(queued["transfer_id"])
            ],
            ["comment_bundle"],
        )

    def test_comment_limit_never_accepts_more_than_protocol_maximum(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter(max_comments=MAX_COMMENT_ITEMS + 1)


if __name__ == "__main__":
    unittest.main()
