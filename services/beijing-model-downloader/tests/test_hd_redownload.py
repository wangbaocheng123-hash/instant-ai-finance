from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


COMPONENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPONENT))

from douyin_core import (  # noqa: E402
    ParseError,
    inspect_mp4,
    probe_media_streams,
    source_quality_improves,
    source_quality_video_command,
)
from library_store import LibraryStore  # noqa: E402
from model_downloader_daemon import CloudMonitor  # noqa: E402


VIDEO_ID = "7420000000000000001"
OTHER_VIDEO_ID = "7420000000000000002"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


class SourceQualityCommandTests(unittest.TestCase):
    def test_source_quality_command_has_no_video_or_audio_reencoding(self) -> None:
        command = source_quality_video_command(
            "/usr/bin/ffmpeg",
            Path("source.mp4"),
            Path("hd.mp4"),
            audio_input=Path("audio.m4a"),
        )

        self.assertEqual(command.count("-i"), 2)
        self.assertIn("1:a:0", command)
        self.assertEqual("copy", command[command.index("-c:v") + 1])
        self.assertEqual("copy", command[command.index("-c:a") + 1])
        self.assertIn("+faststart", command)
        for forbidden in ("-vf", "libx264", "-crf", "-pix_fmt", "-b:a"):
            self.assertNotIn(forbidden, command)

    def test_replacement_must_be_strictly_higher_resolution(self) -> None:
        self.assertTrue(source_quality_improves((360, 640), (720, 1280)))
        self.assertFalse(source_quality_improves((360, 640), (360, 640)))
        self.assertFalse(source_quality_improves((720, 1280), (360, 1280)))
        self.assertFalse(source_quality_improves((0, 640), (720, 1280)))

    def test_real_ffmpeg_stream_copy_preserves_source_streams(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("ffmpeg/ffprobe is not installed")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.mp4"
            output = root / "hd.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=720x1280:r=10:d=0.3",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=stereo:d=0.3",
                    "-shortest",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "5",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                source_quality_video_command(ffmpeg, source, output),
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            source_streams = probe_media_streams(source)
            output_streams = probe_media_streams(output)
            output_inspection = inspect_mp4(output)

        for field in ("codec_name", "width", "height", "pix_fmt"):
            self.assertEqual(
                source_streams["video"][field],
                output_streams["video"][field],
            )
        self.assertEqual(
            source_streams["audio"]["codec_name"],
            output_streams["audio"]["codec_name"],
        )
        self.assertEqual(
            (output_streams["video"]["width"], output_streams["video"]["height"]),
            (720, 1280),
        )
        self.assertTrue(output_inspection["fast_start"])


class HdRedownloadAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = LibraryStore(root / "library.sqlite3", root / "comments")
        for video_id in (VIDEO_ID, OTHER_VIDEO_ID):
            self.store.upsert_video(
                video_id=video_id,
                creator="模型先生",
                title=f"作品 {video_id}",
                source_url=f"https://www.douyin.com/video/{video_id}",
                published_at=NOW,
                now=NOW,
            )
            self.store.mark_downloaded(
                video_id=video_id,
                title=f"作品 {video_id}",
                file_path=root / f"{video_id}.mp4",
                file_size=2048,
                duration_seconds=10.0,
                downloaded_at=NOW,
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_audit_is_exact_and_profile_mark_downloaded_cannot_erase_it(self) -> None:
        self.store.start_hd_redownload(VIDEO_ID, NOW, (360, 640))

        # This is the same write the frequent profile scan performs for an
        # already-present file; the independent HD run must survive it.
        self.store.mark_downloaded(
            video_id=VIDEO_ID,
            title="作品",
            file_path=Path(self.temporary.name) / f"{VIDEO_ID}.mp4",
            file_size=2048,
            duration_seconds=10.0,
            downloaded_at=NOW,
        )

        rows = self.store.hd_redownload_status()
        self.assertEqual([VIDEO_ID], [row["video_id"] for row in rows])
        self.assertEqual("running", rows[0]["status"])
        self.assertEqual([], self.store.hd_redownload_status(OTHER_VIDEO_ID))

    def test_complete_records_before_and_after_dimensions(self) -> None:
        self.store.start_hd_redownload(VIDEO_ID, NOW, (360, 640))
        self.store.complete_hd_redownload(
            video_id=VIDEO_ID,
            title="高清作品",
            file_path=Path(self.temporary.name) / f"{VIDEO_ID}.mp4",
            file_size=8192,
            duration_seconds=10.0,
            completed_at=NOW,
            previous_dimensions=(360, 640),
            result_dimensions=(720, 1280),
        )

        row = self.store.hd_redownload_status(VIDEO_ID)[0]
        self.assertEqual("completed", row["status"])
        self.assertEqual((360, 640), (row["previous_width"], row["previous_height"]))
        self.assertEqual((720, 1280), (row["result_width"], row["result_height"]))
        self.assertEqual(8192, row["result_file_size"])


class AtomicHdReplacementTests(unittest.TestCase):
    @staticmethod
    def monitor(library) -> CloudMonitor:
        monitor = CloudMonitor.__new__(CloudMonitor)
        monitor.logger = Mock(spec=logging.Logger)
        monitor.stop_event = threading.Event()
        monitor.cancel_event = threading.Event()
        monitor.maintenance_cancel_event = threading.Event()
        monitor.downloaded_ids = set()
        monitor.library = library
        monitor.consecutive_scan_failures = 0
        return monitor

    def assert_guard_failure_keeps_old_file(
        self,
        *,
        resolved_video_id: str = VIDEO_ID,
        old_duration: float = 10.0,
        new_duration: float = 10.0,
        download_error: str | None = None,
        expected_message: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            day = root / "2026-10-12"
            day.mkdir()
            existing = day / f"20261012_1200_{VIDEO_ID}.mp4"
            old_bytes = b"old-compact" * 300
            existing.write_bytes(old_bytes)

            class Library:
                failed = None

                @staticmethod
                def video_for_hd_redownload(_video_id):
                    return {
                        "video_id": VIDEO_ID,
                        "title": "测试作品",
                        "description": "",
                        "source_url": f"https://www.douyin.com/video/{VIDEO_ID}",
                        "published_at": NOW.isoformat(),
                        "file_path": str(existing),
                        "file_size": len(old_bytes),
                        "duration_seconds": old_duration,
                        "work_type": "video",
                        "download_status": "downloaded",
                    }

                @staticmethod
                def start_hd_redownload(*_args):
                    return None

                @staticmethod
                def complete_hd_redownload(**_kwargs):
                    raise AssertionError("guard failure must happen before commit")

                @classmethod
                def fail_hd_redownload(cls, _video_id, _now, message):
                    cls.failed = message

            class Resolver:
                def __init__(self, *_args, **_kwargs):
                    pass

                @staticmethod
                def resolve(_url, timeout=None):
                    self.assertEqual(50, timeout)
                    return SimpleNamespace(
                        title="测试作品",
                        video_id=resolved_video_id,
                    )

                @staticmethod
                def close():
                    return None

            download_called = Mock()

            def fake_download(_result, destination, **_kwargs):
                download_called()
                if download_error:
                    raise ParseError(download_error)
                output = Path(destination) / existing.name
                output.write_bytes(b"candidate" * 600)
                return output

            monitor = self.monitor(Library())
            inspections = [
                {
                    "size": len(old_bytes),
                    "duration_seconds": old_duration,
                    "has_mdat": True,
                    "has_video": True,
                    "has_audio": True,
                },
                {
                    "size": 5400,
                    "duration_seconds": new_duration,
                    "has_mdat": True,
                    "has_video": True,
                    "has_audio": True,
                },
            ]
            with patch(
                "model_downloader_daemon.DOWNLOAD_ROOT", root
            ), patch(
                "model_downloader_daemon.DouyinResolver", Resolver
            ), patch(
                "model_downloader_daemon.download_video", fake_download
            ), patch(
                "model_downloader_daemon.inspect_mp4", side_effect=inspections
            ), patch(
                "model_downloader_daemon.video_dimensions",
                side_effect=[(360, 640), (720, 1280)],
            ):
                with self.assertRaisesRegex(ParseError, expected_message):
                    monitor.redownload_hd_now(VIDEO_ID)

            self.assertEqual(old_bytes, existing.read_bytes())
            self.assertRegex(Library.failed, expected_message)
            self.assertEqual([], list(day.glob("*.hd-backup-*")))
            if resolved_video_id != VIDEO_ID:
                download_called.assert_not_called()
            else:
                download_called.assert_called_once()

    def test_resolved_work_id_mismatch_never_downloads_or_replaces(self) -> None:
        self.assert_guard_failure_keeps_old_file(
            resolved_video_id=OTHER_VIDEO_ID,
            expected_message="作品 ID 与精确目标不一致",
        )

    def test_material_duration_mismatch_never_replaces_old_file(self) -> None:
        self.assert_guard_failure_keeps_old_file(
            old_duration=100.0,
            new_duration=92.0,
            expected_message="时长不一致",
        )

    def test_non_improving_resolution_never_replaces_old_file(self) -> None:
        self.assert_guard_failure_keeps_old_file(
            download_error="最高画质候选没有高于现有压缩版",
            expected_message="没有高于现有压缩版",
        )

    def test_database_failure_restores_old_file_after_atomic_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            day = root / "2026-10-12"
            day.mkdir()
            existing = day / f"20261012_1200_{VIDEO_ID}.mp4"
            old_bytes = b"old-compact" * 300
            new_bytes = b"new-source-quality" * 300
            existing.write_bytes(old_bytes)

            class Library:
                failed = None

                @staticmethod
                def video_for_hd_redownload(_video_id):
                    return {
                        "video_id": VIDEO_ID,
                        "title": "测试作品",
                        "description": "",
                        "source_url": f"https://www.douyin.com/video/{VIDEO_ID}",
                        "published_at": NOW.isoformat(),
                        "file_path": str(existing),
                        "file_size": len(old_bytes),
                        "duration_seconds": 10.0,
                        "work_type": "video",
                        "download_status": "downloaded",
                    }

                @staticmethod
                def start_hd_redownload(*_args):
                    return None

                @staticmethod
                def complete_hd_redownload(**_kwargs):
                    raise sqlite3.OperationalError("synthetic DB failure")

                @classmethod
                def fail_hd_redownload(cls, _video_id, _now, message):
                    cls.failed = message

            class Resolver:
                def __init__(self, *_args, **_kwargs):
                    pass

                @staticmethod
                def resolve(_url, timeout):
                    self.assertEqual(50, timeout)
                    return SimpleNamespace(title="测试作品", video_id=VIDEO_ID)

                @staticmethod
                def close():
                    return None

            def fake_download(_result, destination, **kwargs):
                self.assertTrue(kwargs["source_quality"])
                self.assertEqual((360, 640), kwargs["minimum_video_dimensions"])
                output = Path(destination) / existing.name
                output.write_bytes(new_bytes)
                return output

            monitor = self.monitor(Library())
            with patch(
                "model_downloader_daemon.DOWNLOAD_ROOT", root
            ), patch(
                "model_downloader_daemon.DouyinResolver", Resolver
            ), patch(
                "model_downloader_daemon.download_video", fake_download
            ), patch(
                "model_downloader_daemon.inspect_mp4",
                return_value={
                    "size": len(new_bytes),
                    "duration_seconds": 10.0,
                    "has_mdat": True,
                    "has_video": True,
                    "has_audio": True,
                },
            ), patch(
                "model_downloader_daemon.video_dimensions",
                side_effect=[(360, 640), (720, 1280)],
            ):
                with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic"):
                    monitor.redownload_hd_now(VIDEO_ID)

            self.assertEqual(old_bytes, existing.read_bytes())
            self.assertIn("synthetic DB failure", Library.failed)
            self.assertEqual([], list(day.glob(f".{VIDEO_ID}.hd-redownload-*")))
            self.assertEqual([], list(day.glob("*.hd-backup-*")))

    def test_concurrent_run_fails_before_audit_or_media_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            day = root / "2026-10-12"
            day.mkdir()
            existing = day / f"20261012_1200_{VIDEO_ID}.mp4"
            old_bytes = b"old-compact" * 300
            existing.write_bytes(old_bytes)

            class Library:
                start_calls = 0

                @staticmethod
                def video_for_hd_redownload(_video_id):
                    return {
                        "video_id": VIDEO_ID,
                        "title": "测试作品",
                        "description": "",
                        "source_url": f"https://www.douyin.com/video/{VIDEO_ID}",
                        "published_at": NOW.isoformat(),
                        "file_path": str(existing),
                        "file_size": len(old_bytes),
                        "duration_seconds": 10.0,
                        "work_type": "video",
                        "download_status": "downloaded",
                    }

                @classmethod
                def start_hd_redownload(cls, *_args):
                    cls.start_calls += 1

            monitor = self.monitor(Library())
            with patch("model_downloader_daemon.DOWNLOAD_ROOT", root):
                with monitor.hd_redownload_lock(existing, VIDEO_ID):
                    with self.assertRaisesRegex(RuntimeError, "正在执行另一次高清重下"):
                        monitor.redownload_hd_now(VIDEO_ID)

            self.assertEqual(0, Library.start_calls)
            self.assertEqual(old_bytes, existing.read_bytes())


if __name__ == "__main__":
    unittest.main()
