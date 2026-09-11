from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch


COMPONENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPONENT))

from model_downloader_daemon import (  # noqa: E402
    CloudMonitor,
    INTERVAL_MINUTES,
    VIDEO_CHECK_INTERVAL_MINUTES,
    next_scheduled_check,
)


CHINA = timezone(timedelta(hours=8))


class VideoScheduleTests(unittest.TestCase):
    def assert_next_check_is_three_minutes_later(self, moment: datetime) -> None:
        self.assertEqual(
            moment + timedelta(minutes=3),
            next_scheduled_check(moment),
        )

    def test_video_interval_is_fixed_at_three_minutes(self) -> None:
        self.assertEqual(3, VIDEO_CHECK_INTERVAL_MINUTES)
        self.assertEqual(3, INTERVAL_MINUTES)

    def test_weekday_midnight_has_no_monitoring_gap(self) -> None:
        self.assert_next_check_is_three_minutes_later(
            datetime(2026, 9, 10, 0, 1, tzinfo=CHINA)
        )

    def test_weekend_early_morning_has_no_monitoring_gap(self) -> None:
        self.assert_next_check_is_three_minutes_later(
            datetime(2026, 9, 12, 3, 27, tzinfo=CHINA)
        )

    def test_slow_video_work_does_not_add_another_interval(self) -> None:
        started = datetime(2026, 9, 11, 7, 0, tzinfo=CHINA)
        completed = started + timedelta(minutes=3, seconds=20)
        self.assertEqual(
            completed,
            next_scheduled_check(started, completed),
        )

    def test_fast_video_work_keeps_start_anchored_deadline(self) -> None:
        started = datetime(2026, 9, 11, 7, 0, tzinfo=CHINA)
        completed = started + timedelta(seconds=12)
        self.assertEqual(
            started + timedelta(minutes=3),
            next_scheduled_check(started, completed),
        )

    @staticmethod
    def bare_monitor(library) -> CloudMonitor:
        monitor = CloudMonitor.__new__(CloudMonitor)
        monitor.logger = Mock()
        monitor.stop_event = threading.Event()
        monitor.cancel_event = threading.Event()
        monitor.maintenance_cancel_event = threading.Event()
        monitor.downloaded_ids = set()
        monitor.library = library
        monitor.consecutive_scan_failures = 0
        monitor.today_destination = Mock(return_value=Path("/tmp"))
        return monitor

    def test_video_check_never_enters_comment_or_repair_work(self) -> None:
        class VideoOnlyLibrary:
            def __init__(self):
                self.finished = None

            @staticmethod
            def start_scan(_now):
                return 7

            def finish_scan(self, run_id, **values):
                self.finished = (run_id, values)

            @staticmethod
            def comment_refresh_candidates(**_values):
                raise AssertionError("video check entered comment work")

            @staticmethod
            def repair_requests():
                raise AssertionError("video check entered repair work")

        class Scanner:
            def __init__(self, *_args, **_kwargs):
                pass

            @staticmethod
            def scan(_url, timeout):
                if timeout != 45:
                    raise AssertionError("unexpected profile timeout")
                return "模型先生", []

            @staticmethod
            def close():
                pass

        library = VideoOnlyLibrary()
        monitor = self.bare_monitor(library)
        with patch("model_downloader_daemon.ProfileScanner", Scanner), patch(
            "model_downloader_daemon.save_state"
        ):
            monitor.check_once()
        self.assertEqual(7, library.finished[0])
        self.assertTrue(library.finished[1]["success"])

    def test_idle_gap_runs_at_most_one_maintenance_item(self) -> None:
        rows = [
            {
                "video_id": "2",
                "published_at": "2026-09-11T07:00:00+08:00",
                "comment_refresh_requested": 0,
            },
            {
                "video_id": "1",
                "published_at": "2026-09-11T06:00:00+08:00",
                "comment_refresh_requested": 0,
            },
        ]

        class MaintenanceLibrary:
            @staticmethod
            def repair_requests():
                return []

            @staticmethod
            def comment_refresh_candidates(**_values):
                return rows

        monitor = self.bare_monitor(MaintenanceLibrary())
        monitor._refresh_one_comment = Mock()
        deadline = datetime.now(CHINA) + timedelta(seconds=30)
        self.assertTrue(monitor.run_one_maintenance_task(deadline))
        monitor._refresh_one_comment.assert_called_once_with(rows[0], deadline)

    def test_comment_slice_is_bounded_and_uses_maintenance_cancel(self) -> None:
        class EmptyLibrary:
            pass

        monitor = self.bare_monitor(EmptyLibrary())
        monitor.collect_comments = Mock()
        row = {
            "video_id": "2",
            "title": "test",
            "source_url": "https://www.douyin.com/video/2",
            "published_at": "2026-09-11T07:00:00+08:00",
        }
        deadline = datetime.now(CHINA) + timedelta(seconds=45)
        monitor._refresh_one_comment(row, deadline)
        call = monitor.collect_comments.call_args
        self.assertLessEqual(call.kwargs["timeout"], 35)
        self.assertIs(
            monitor.maintenance_cancel_event,
            call.kwargs["cancel_event"],
        )

    def test_manual_comment_request_precedes_routine_refresh(self) -> None:
        routine = {
            "video_id": "2",
            "published_at": "2026-09-11T07:00:00+08:00",
            "comment_refresh_requested": 0,
        }
        manual = {
            "video_id": "1",
            "published_at": "2026-09-11T06:00:00+08:00",
            "comment_refresh_requested": 1,
        }

        class CandidateLibrary:
            @staticmethod
            def comment_refresh_candidates(**_values):
                return [routine, manual]

        monitor = self.bare_monitor(CandidateLibrary())
        self.assertEqual([manual, routine], monitor._comment_candidates())

    def test_deadline_too_close_skips_all_maintenance(self) -> None:
        class NoMaintenanceLibrary:
            @staticmethod
            def repair_requests():
                raise AssertionError("deadline should be checked first")

        monitor = self.bare_monitor(NoMaintenanceLibrary())
        deadline = datetime.now(CHINA) + timedelta(seconds=1)
        self.assertFalse(monitor.run_one_maintenance_task(deadline))


if __name__ == "__main__":
    unittest.main()
