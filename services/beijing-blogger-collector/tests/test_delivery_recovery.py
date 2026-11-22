from __future__ import annotations

import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from mx_agent import creator_sync
from mx_agent.collector_export import CollectorContentReadyAdapter
from mx_agent.creators import CreatorRegistry, CreatorSyncManager, MISC_CREATOR_ID
from mx_agent.settings import load_settings
from mx_agent.storage import Storage
from mx_agent.transfer_outbox import TransferOutbox


class DeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        runtime = self.root / "runtime"
        for name, value in {"RUNTIME_DIR": runtime, "PROFILE_DIR": runtime / "profile",
                            "STATE_PATH": runtime / "state.json"}.items():
            self.stack.enter_context(patch.object(creator_sync, name, value))
        self.stack.enter_context(patch.object(creator_sync.CreatorSyncService, "start"))
        self.scanner = self.stack.enter_context(patch.object(creator_sync, "ProfileScanner"))
        self.download = self.stack.enter_context(patch.object(
            creator_sync.CreatorSyncService, "_ensure_video", side_effect=AssertionError("must not download")))
        self.comments = self.stack.enter_context(patch.object(
            creator_sync.CreatorSyncService, "_collect_and_merge_comments", side_effect=AssertionError("must not fetch comments")))
        self.storage = Storage(self.root / "library.sqlite3")
        self.uuid = "e20f3957-28ad-4faa-a210-b43493c3f31d"
        self.registry_path = self.root / "creators.json"
        self.registry_path.write_text(json.dumps({"creators": [{
            "id": "creator-old-misc", "creator_uuid": self.uuid, "name": "大杂烩",
            "profile_url": "", "platform_user_id": "", "creator_comments_enabled": False,
        }]}))
        self.registry = CreatorRegistry(load_settings(), path=self.registry_path)
        self.outbox = TransferOutbox(self.root / "outbox.sqlite3", allowed_artifact_roots=[self.root])
        self.work_id = "7000000000000000066"
        self.video_id, _ = self.storage.upsert_video({
            "source": "douyin-auto", "source_video_id": self.work_id, "author": "大杂烩",
            "title": "已下载待交接的作品", "url": f"https://www.douyin.com/video/{self.work_id}",
            "published_at": "2026-11-20T17:00:00+08:00", "discovered_at": "2026-11-22T02:20:00+00:00",
            "raw_json": {"douyin_aweme_id": self.work_id},
        })
        self.media = self.root / "existing.mp4"
        self.media.write_bytes(b"existing video fixture" * 32)
        self.storage.save_asset({"video_id": self.video_id, "asset_type": "video",
            "local_path": str(self.media), "mime_type": "video/mp4", "size_bytes": self.media.stat().st_size})
        self.state_path = runtime / "creators" / "creator-old-misc" / "state.json"
        self.state_path.parent.mkdir(parents=True)
        self.seed_pending()

    def seed_pending(self, **extra):
        entry = {"local_video_id": self.video_id, "content_ready_pending": True,
                 "last_content_ready_error": "博主缺少已持久化的 platform_user_id。", **extra}
        self.state_path.write_text(json.dumps({"videos": {self.work_id: entry}}, ensure_ascii=False))

    def adapter(self):
        return CollectorContentReadyAdapter(self.storage, self.registry, self.outbox,
            artifact_dir=self.root / "artifacts", collector_node_id="test-node",
            collector_key_id="test-key", collector_version="1.3.0-test")

    def manager(self, callback=None, gate=None):
        return CreatorSyncManager(self.registry, self.storage, on_content_ready=callback or self.adapter(),
                                  automatic_rotation=False, execution_lock=gate)

    def test_legacy_pending_is_recovered_with_original_runtime_uuid_and_file(self):
        original = self.media.read_bytes()
        manager = self.manager()
        service = manager.ensure(MISC_CREATOR_ID)
        self.assertEqual(service.state_path, self.state_path)
        self.assertEqual(service.runtime_key, "creator-old-misc")
        self.assertEqual(service.status()["content_ready_pending"], 1)
        self.assertEqual(manager.retry_pending_content_ready(), {"attempted": 1, "enqueued": 1, "failed": 0})
        self.assertEqual(self.outbox.status_counts()["pending"], 1)
        item = self.outbox.list_recent()[0]
        self.assertEqual(item["creator_id"], self.uuid)
        saved = json.loads(self.state_path.read_text())["videos"][self.work_id]
        self.assertFalse(saved["content_ready_pending"])
        self.assertEqual(saved["content_ready_transfer_id"], item["transfer_id"])
        self.assertEqual(self.media.read_bytes(), original)
        self.scanner.assert_not_called()
        self.download.assert_not_called()
        self.comments.assert_not_called()
        self.assertEqual(manager.retry_pending_content_ready()["attempted"], 0)

    def test_restart_after_enqueue_reuses_outbox_instead_of_creating_revision(self):
        self.manager().retry_pending_content_ready()
        transfer_id = self.outbox.list_recent()[0]["transfer_id"]
        self.seed_pending()  # A crash preserved the pre-callback intent.
        self.assertEqual(self.manager().retry_pending_content_ready()["enqueued"], 1)
        self.assertEqual(self.outbox.status_counts()["pending"], 1)
        self.assertEqual(self.outbox.list_recent()[0]["transfer_id"], transfer_id)

    def test_changed_evidence_produces_new_revision_after_retry(self):
        self.manager().retry_pending_content_ready()
        first = self.outbox.list_recent()[0]
        with self.storage.connect() as connection:
            connection.execute("UPDATE videos SET title=? WHERE id=?", ("新增有实质变化的标题", self.video_id))
        self.seed_pending()
        self.manager().retry_pending_content_ready()
        newest = self.outbox.list_recent()[0]
        self.assertNotEqual(newest["transfer_id"], first["transfer_id"])
        self.assertEqual(newest["work_revision"], first["work_revision"] + 1)

    def test_failures_back_off_persist_and_allow_manual_force(self):
        def fail(*_args):
            raise RuntimeError("temporary producer failure")
        manager = self.manager(fail)
        self.assertEqual(manager.retry_pending_content_ready()["failed"], 1)
        self.assertEqual(manager.retry_pending_content_ready()["attempted"], 0)
        self.assertEqual(self.manager(fail).retry_pending_content_ready()["attempted"], 0)
        self.assertEqual(manager.retry_pending_content_ready(force=True)["attempted"], 1)
        self.assertTrue(self.media.is_file())

    def test_recovery_cannot_export_a_different_author_or_work(self):
        with self.storage.connect() as connection:
            connection.execute("UPDATE videos SET author=? WHERE id=?", ("另一博主", self.video_id))
        self.assertEqual(self.manager().retry_pending_content_ready()["failed"], 1)
        self.assertEqual(self.outbox.status_counts()["pending"], 0)

    def test_missing_cached_media_stays_pending_without_creating_empty_delivery(self):
        self.media.unlink()
        self.assertEqual(self.manager().retry_pending_content_ready()["failed"], 1)
        self.assertEqual(self.outbox.status_counts()["pending"], 0)
        self.download.assert_not_called()

    def test_busy_browser_defers_recovery_without_waiting(self):
        gate = threading.Lock()
        gate.acquire()
        try:
            self.assertEqual(self.manager(gate=gate).retry_pending_content_ready()["attempted"], 0)
        finally:
            gate.release()

    def test_old_ordinary_creator_flags_stay_in_review_even_on_force(self):
        manager = self.manager()
        service = manager.ensure("primary")
        service._state["videos"][self.work_id] = {
            "local_video_id": self.video_id, "content_ready_pending": True,
        }
        self.assertEqual(service.status()["content_ready_legacy_review"], 1)
        self.assertEqual(service.retry_pending_content_ready(force=True)["attempted"], 0)
        self.assertEqual(self.outbox.status_counts()["pending"], 0)

    def test_identical_dead_letter_requires_review_without_creating_revision(self):
        self.manager().retry_pending_content_ready()
        transfer_id = self.outbox.list_recent()[0]["transfer_id"]
        self.outbox.mark_dead_letter(transfer_id, error_code="rejected")
        self.seed_pending()
        self.assertEqual(self.manager().retry_pending_content_ready()["failed"], 1)
        self.assertEqual(self.outbox.status_counts()["dead_letter"], 1)
        self.assertEqual(self.outbox.status_counts()["pending"], 0)

    def test_intent_is_durable_before_callback_and_retried_after_save_failure(self):
        adapter = self.adapter()
        def callback(video_id, key):
            saved = json.loads(self.state_path.read_text())["videos"][self.work_id]
            self.assertTrue(saved["content_ready_pending"])
            return adapter(video_id, key)
        manager = self.manager(callback)
        service = manager.ensure(MISC_CREATOR_ID)
        save = service._save_state
        calls = 0
        def fail_once():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated state write failure after enqueue")
            return save()
        with patch.object(service, "_save_state", side_effect=fail_once):
            self.assertEqual(manager.retry_pending_content_ready()["failed"], 1)
        self.assertEqual(manager.retry_pending_content_ready()["enqueued"], 1)
        self.assertEqual(self.outbox.status_counts()["pending"], 1)


if __name__ == "__main__":
    unittest.main()
