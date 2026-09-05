from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mx_agent.auto_transcription import AutoDoubaoTranscriptionService
from mx_agent.storage import Storage


class _Doubao:
    def __init__(self, *, text: str = "自动识别的正式原文", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[int] = []

    def speech_enabled(self) -> bool:
        return True

    def transcribe_video_text(self, video_id: int) -> dict:
        self.calls.append(video_id)
        if self.error:
            raise self.error
        return {"text": self.text, "task_id": f"task-{video_id}"}


class AutoDoubaoTranscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="mx-auto-asr-")
        self.root = Path(self.temp_dir.name)
        self.storage = Storage(self.root / "data" / "mx_agent.sqlite3")
        self.settings = SimpleNamespace(auto_doubao_transcribe_enabled=True)
        self.state_path = self.root / "data" / "auto-state.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_video(
        self,
        source_video_id: str,
        *,
        filename: str = "20260821_1200_1234567890.mp4",
        published_at: str = "2026-08-21T04:00:00+00:00",
        asset_type: str = "video",
        mime_type: str = "video/mp4",
    ) -> int:
        video_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": source_video_id,
                "author": "测试博主",
                "title": Path(filename).stem,
                "description": "",
                "published_at": published_at,
            }
        )
        path = self.root / filename
        path.write_bytes(b"fake-media")
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": asset_type,
                "storage_mode": "source_file",
                "original_name": filename,
                "local_path": str(path),
                "mime_type": mime_type,
                "size_bytes": path.stat().st_size,
                "sha256": f"sha-{source_video_id}",
                "source": "test",
                "status": "stored",
                "raw_json": {},
            }
        )
        return video_id

    def test_first_activation_excludes_existing_backlog(self) -> None:
        old_video_id = self._create_video("old")
        doubao = _Doubao()
        service = AutoDoubaoTranscriptionService(
            self.settings,
            self.storage,
            doubao,
            state_path=self.state_path,
            interval_seconds=60,
        )

        result = service.run_once()

        self.assertEqual(result["processed"], 0)
        self.assertEqual(doubao.calls, [])
        self.assertEqual(service.status()["baseline_video_id"], old_video_id)

    def test_new_video_is_transcribed_saved_and_verified(self) -> None:
        doubao = _Doubao(text="第一行。\n第二行。")
        service = AutoDoubaoTranscriptionService(
            self.settings,
            self.storage,
            doubao,
            state_path=self.state_path,
            interval_seconds=60,
        )
        video_id = self._create_video("new")
        service.enqueue(video_id)

        result = service.run_once()

        self.assertEqual(result["saved"], 1)
        self.assertEqual(doubao.calls, [video_id])
        note = self.storage.get_official_original_note(video_id)
        self.assertEqual(note["text"], "第一行。\n第二行。")
        detail = self.storage.get_video_detail(video_id)
        self.assertEqual(detail["notes"]["video_text"]["text"], "第一行。\n第二行。")
        txt_path = (
            self.root
            / "博主数据"
            / "测试博主"
            / "视频原文"
            / "2026"
            / "08"
            / "20260821_1200_1234567890.txt"
        )
        self.assertEqual(txt_path.read_text(encoding="utf-8"), "第一行。\n第二行。")

    def test_image_work_is_never_sent_to_paid_asr(self) -> None:
        doubao = _Doubao()
        service = AutoDoubaoTranscriptionService(
            self.settings,
            self.storage,
            doubao,
            state_path=self.state_path,
            interval_seconds=60,
        )
        image_id = self._create_video(
            "image",
            filename="20260821_1200_1234567890.png",
            asset_type="screenshot",
            mime_type="image/png",
        )
        service.enqueue(image_id)

        result = service.run_once()

        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(doubao.calls, [])

    def test_failed_paid_job_is_not_automatically_retried(self) -> None:
        doubao = _Doubao(error=RuntimeError("paid call failed"))
        service = AutoDoubaoTranscriptionService(
            self.settings,
            self.storage,
            doubao,
            state_path=self.state_path,
            interval_seconds=60,
        )
        video_id = self._create_video("failure")
        service.enqueue(video_id)

        first = service.run_once()
        second = service.run_once()

        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(doubao.calls, [video_id])
        self.assertEqual(service.status()["failed_count"], 1)

    def test_persisted_baseline_recovers_new_video_after_reopen(self) -> None:
        first = AutoDoubaoTranscriptionService(
            self.settings,
            self.storage,
            _Doubao(),
            state_path=self.state_path,
            interval_seconds=60,
        )
        self.assertEqual(first.status()["baseline_video_id"], 0)
        video_id = self._create_video("while-closed")

        reopened_doubao = _Doubao()
        reopened = AutoDoubaoTranscriptionService(
            self.settings,
            self.storage,
            reopened_doubao,
            state_path=self.state_path,
            interval_seconds=60,
        )
        result = reopened.run_once()

        self.assertEqual(result["saved"], 1)
        self.assertEqual(reopened_doubao.calls, [video_id])


if __name__ == "__main__":
    unittest.main()
