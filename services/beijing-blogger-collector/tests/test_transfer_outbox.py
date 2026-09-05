from __future__ import annotations

import os
import stat
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from mx_agent.transfer_contract import (
    TransferContractError,
    build_comment_bundle,
    new_manifest,
    sha256_file,
)
from mx_agent.transfer_outbox import (
    TransferOutbox,
    TransferOutboxError,
    retry_delay_seconds,
)


CREATOR_UUID = "7de6f4d1-3bf6-4c8b-b2e4-3d8b285dcb5e"
CAPTURED_AT = "2026-08-30T10:05:00+08:00"


class TransferOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="blogger-outbox-")
        self.root = Path(self.temp_dir.name)
        self.outbox = TransferOutbox(self.root / "outbox.sqlite3")
        self.media = self.root / "sample.mp4"
        self.media.write_bytes(b"video-data")
        self.comment_bundle, self.comment_descriptor = build_comment_bundle(
            [
                {
                    "source_comment_id": "comment-1",
                    "author_uid": "fan-sec-uid",
                    "author": "公开用户",
                    "is_creator": False,
                    "text": "评论正文",
                    "like_count": 5,
                    "reply_count": 0,
                    "published_at": "2026-08-30T10:01:00+08:00",
                    "captured_at": CAPTURED_AT,
                    "kind": "user_comment",
                    "section": "fan_comment",
                    "author_liked": None,
                    "low_value": False,
                    "display_order": 10,
                }
            ]
        )
        self.comment_path = self.root / "comments.ndjson.gz"
        self.comment_path.write_bytes(self.comment_bundle)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def manifest(
        self,
        *,
        source_sequence: int = 1,
        work_revision: int = 1,
        title: str = "示例作品",
        source_work_id: str = "7654321098765432100",
    ):
        return new_manifest(
            collector_node_id="beijing-collector-1",
            collector_key_id="hmac-2026-01",
            collector_version="0.1.0",
            source_sequence=source_sequence,
            creator={
                "creator_id": CREATOR_UUID,
                "display_name": "贵族之路",
                "platform": "douyin",
                "platform_user_id": "MS4wLjABAAAA-example",
            },
            work={
                "platform": "douyin",
                "source_work_id": source_work_id,
                "work_type": "video",
                "title": title,
                "description": "说明",
                "source_url": (
                    "https://www.douyin.com/video/" + source_work_id
                ),
                "published_at": "2026-08-30T10:00:00+08:00",
            },
            work_revision=work_revision,
            media=[
                {
                    "media_id": "media-" + source_work_id,
                    "role": "video",
                    "filename": "sample.mp4",
                    "mime_type": "video/mp4",
                    "size_bytes": self.media.stat().st_size,
                    "sha256": sha256_file(self.media),
                    "ordinal": 0,
                }
            ],
            comment_snapshot={
                "snapshot_id": "snapshot-" + source_work_id,
                "captured_at": CAPTURED_AT,
                "complete": True,
                "expected_total": 1,
                "captured_count": 1,
                "top_level_count": 1,
                "reply_groups": 0,
                "reply_groups_incomplete": 0,
                "missing_replies": 0,
                "orphan_replies": 0,
                "rules_version": "comment-rules/v1",
                "bundle": self.comment_descriptor,
            },
            captured_at=CAPTURED_AT,
        )

    def artifacts(self, manifest=None):
        manifest = manifest or self.manifest()
        media = manifest["media"][0]
        bundle = manifest["comment_snapshot"]["bundle"]
        return [
            {
                "artifact_id": media["media_id"],
                "artifact_kind": "media",
                "local_path": str(self.media),
                "size_bytes": media["size_bytes"],
                "sha256": media["sha256"],
            },
            {
                "artifact_id": bundle["bundle_id"],
                "artifact_kind": "comment_bundle",
                "local_path": str(self.comment_path),
                "size_bytes": bundle["size_bytes"],
                "sha256": bundle["sha256"],
            },
        ]

    def test_reservations_are_global_per_node_and_monotonic_per_work(self) -> None:
        first = self.outbox.reserve(
            node_id="beijing-collector-1",
            creator_id=CREATOR_UUID,
            platform="douyin",
            source_work_id="work-1",
        )
        second = self.outbox.reserve(
            node_id="beijing-collector-1",
            creator_id=CREATOR_UUID,
            platform="douyin",
            source_work_id="work-1",
        )
        other_work = self.outbox.reserve(
            node_id="beijing-collector-1",
            creator_id=CREATOR_UUID,
            platform="douyin",
            source_work_id="work-2",
        )
        self.assertEqual((first.source_sequence, first.work_revision), (1, 1))
        self.assertEqual((second.source_sequence, second.work_revision), (2, 2))
        self.assertEqual((other_work.source_sequence, other_work.work_revision), (3, 1))

    def test_enqueue_is_idempotent_and_keeps_local_paths_out_of_manifest(self) -> None:
        manifest = self.manifest()
        first = self.outbox.enqueue(manifest, artifacts=self.artifacts())
        second = self.outbox.enqueue(manifest, artifacts=self.artifacts())
        self.assertEqual(first["action"], "inserted")
        self.assertEqual(second["action"], "duplicate")
        self.assertNotIn(str(self.root), str(first["manifest"]))
        artifacts = self.outbox.artifacts_for(manifest["transfer_id"])
        self.assertEqual(len(artifacts), 2)
        self.assertTrue(all(str(self.root) in item["local_path"] for item in artifacts))

    def test_out_of_order_revision_is_stale_and_same_revision_conflict_fails(self) -> None:
        newest = self.manifest(source_sequence=3, work_revision=3, title="新标题")
        self.assertEqual(
            self.outbox.enqueue(newest, artifacts=self.artifacts(newest))["action"],
            "inserted",
        )
        stale = self.manifest(source_sequence=2, work_revision=2, title="旧标题")
        self.assertEqual(
            self.outbox.enqueue(stale, artifacts=self.artifacts(stale))["action"],
            "stale",
        )
        conflicting = self.manifest(
            source_sequence=3,
            work_revision=3,
            title="冲突标题",
        )
        with self.assertRaises(TransferContractError):
            self.outbox.enqueue(conflicting, artifacts=self.artifacts(conflicting))

    def test_source_sequence_cannot_be_reused_for_another_work(self) -> None:
        first = self.manifest(source_sequence=1, work_revision=1)
        self.outbox.enqueue(first, artifacts=self.artifacts(first))
        other = self.manifest(
            source_sequence=1,
            work_revision=1,
            source_work_id="7654321098765432101",
        )
        with self.assertRaises(TransferOutboxError):
            self.outbox.enqueue(other, artifacts=self.artifacts(other))

    def test_retry_resumes_previous_state_then_delivers(self) -> None:
        manifest = self.manifest()
        self.outbox.enqueue(manifest, artifacts=self.artifacts(manifest))
        transfer_id = manifest["transfer_id"]
        self.outbox.transition(
            transfer_id,
            "manifest_accepted",
            receipt_id="receipt-1",
            http_status=202,
        )
        now = datetime(2026, 8, 30, 3, 0, tzinfo=UTC)
        retry = self.outbox.mark_retry(
            transfer_id,
            error_code="NETWORK_TIMEOUT",
            error_message="连接超时",
            now=now,
            delay_seconds=30,
        )
        self.assertEqual(retry["status"], "retry_wait")
        self.assertEqual(retry["resume_status"], "manifest_accepted")
        self.assertEqual(
            self.outbox.list_ready(now=now + timedelta(seconds=29)),
            [],
        )
        ready = self.outbox.list_ready(now=now + timedelta(seconds=30))
        self.assertEqual([item["transfer_id"] for item in ready], [transfer_id])
        resumed = self.outbox.resume_due(
            transfer_id,
            now=now + timedelta(seconds=30),
        )
        self.assertEqual(resumed["status"], "manifest_accepted")
        self.outbox.transition(transfer_id, "media_uploading")
        for artifact in self.outbox.artifacts_for(transfer_id):
            self.outbox.update_artifact_progress(
                transfer_id,
                artifact["artifact_id"],
                status="verified",
                uploaded_bytes=artifact["size_bytes"],
            )
        self.outbox.transition(transfer_id, "finalizing")
        delivered = self.outbox.transition(transfer_id, "delivered", http_status=200)
        self.assertEqual(delivered["status"], "delivered")
        self.assertIsNotNone(delivered["delivered_at"])
        with self.assertRaises(TransferOutboxError):
            self.outbox.mark_retry(
                transfer_id,
                error_code="SHOULD_NOT_RETRY",
            )

    def test_artifact_progress_never_exceeds_expected_size(self) -> None:
        manifest = self.manifest()
        self.outbox.enqueue(manifest, artifacts=self.artifacts())
        artifact = self.artifacts()[0]
        updated = self.outbox.update_artifact_progress(
            manifest["transfer_id"],
            artifact["artifact_id"],
            status="uploaded",
            uploaded_bytes=artifact["size_bytes"],
        )
        self.assertEqual(updated["status"], "uploaded")
        with self.assertRaises(TransferOutboxError):
            self.outbox.update_artifact_progress(
                manifest["transfer_id"],
                artifact["artifact_id"],
                status="uploaded",
                uploaded_bytes=artifact["size_bytes"] + 1,
            )

    def test_enqueue_requires_exact_manifest_artifact_bijection(self) -> None:
        manifest = self.manifest()
        valid = self.artifacts(manifest)
        cases = {}
        cases["missing"] = valid[:-1]
        cases["extra"] = [*valid, {**valid[0], "artifact_id": "extra-artifact"}]
        cases["duplicate"] = [*valid, dict(valid[0])]
        cases["kind"] = [{**valid[0], "artifact_kind": "comment_bundle"}, valid[1]]
        cases["size"] = [{**valid[0], "size_bytes": valid[0]["size_bytes"] + 1}, valid[1]]
        cases["hash"] = [{**valid[0], "sha256": "f" * 64}, valid[1]]
        for label, artifacts in cases.items():
            with self.subTest(label=label), self.assertRaises(TransferOutboxError):
                self.outbox.enqueue(manifest, artifacts=artifacts)

    def test_allowed_roots_and_regular_file_boundary(self) -> None:
        allowed = self.root / "allowed"
        allowed.mkdir()
        restricted = TransferOutbox(
            self.root / "private" / "outbox.sqlite3",
            allowed_artifact_roots=[allowed],
        )
        manifest = self.manifest()
        with self.assertRaisesRegex(TransferOutboxError, "允许根"):
            restricted.enqueue(manifest, artifacts=self.artifacts(manifest))

        directory_artifacts = self.artifacts(manifest)
        directory_artifacts[0] = {**directory_artifacts[0], "local_path": str(allowed)}
        with self.assertRaisesRegex(TransferOutboxError, "普通文件"):
            restricted.enqueue(manifest, artifacts=directory_artifacts)

        link = allowed / "sample-link.mp4"
        try:
            link.symlink_to(self.media)
        except OSError:
            return
        linked = self.artifacts(manifest)
        linked[0] = {**linked[0], "local_path": str(link)}
        with self.assertRaisesRegex(TransferOutboxError, "符号链接"):
            restricted.enqueue(manifest, artifacts=linked)

    def test_atomic_lease_expiry_and_state_cas(self) -> None:
        manifest = self.manifest()
        self.outbox.enqueue(manifest, artifacts=self.artifacts(manifest))
        now = datetime(2026, 8, 30, 3, 0, tzinfo=UTC)
        first = self.outbox.claim_ready(
            "worker-a", now=now, lease_seconds=30
        )
        self.assertEqual(len(first), 1)
        peer = TransferOutbox(self.outbox.path, allowed_artifact_roots=[self.root])
        self.assertEqual(
            peer.claim_ready("worker-b", now=now + timedelta(seconds=29)), []
        )
        with self.assertRaises(TransferOutboxError):
            peer.transition(
                manifest["transfer_id"],
                "manifest_accepted",
                lease_owner="worker-b",
            )
        recovered = peer.claim_ready(
            "worker-b", now=now + timedelta(seconds=30)
        )
        self.assertEqual([item["transfer_id"] for item in recovered], [manifest["transfer_id"]])
        with self.assertRaisesRegex(TransferOutboxError, "迁移不合法"):
            peer.transition(
                manifest["transfer_id"],
                "finalizing",
                lease_owner="worker-b",
            )

    def test_artifact_state_and_bytes_are_monotonic(self) -> None:
        manifest = self.manifest()
        self.outbox.enqueue(manifest, artifacts=self.artifacts(manifest))
        artifact = self.outbox.artifacts_for(manifest["transfer_id"])[0]
        self.outbox.update_artifact_progress(
            manifest["transfer_id"],
            artifact["artifact_id"],
            status="uploaded",
            uploaded_bytes=artifact["size_bytes"],
        )
        with self.assertRaisesRegex(TransferOutboxError, "不能回退"):
            self.outbox.update_artifact_progress(
                manifest["transfer_id"],
                artifact["artifact_id"],
                status="uploading",
                uploaded_bytes=0,
            )

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_database_permissions_are_private_on_posix(self) -> None:
        self.assertEqual(stat.S_IMODE(self.outbox.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.outbox.path.stat().st_mode), 0o600)

    def test_production_retry_uses_random_jitter(self) -> None:
        with mock.patch("mx_agent.transfer_outbox.random.SystemRandom") as factory:
            factory.return_value.random.return_value = 0.25
            self.assertEqual(retry_delay_seconds(2), 15)
            factory.return_value.random.assert_called_once_with()

    def test_retry_delay_is_exponential_and_capped(self) -> None:
        self.assertEqual(retry_delay_seconds(1, jitter_unit=1), 30)
        self.assertEqual(retry_delay_seconds(2, jitter_unit=1), 60)
        self.assertEqual(
            retry_delay_seconds(100, cap_seconds=600, jitter_unit=1),
            600,
        )


if __name__ == "__main__":
    unittest.main()
