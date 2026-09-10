from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest


COMPONENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPONENT))

from model_downloader_daemon import (  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
