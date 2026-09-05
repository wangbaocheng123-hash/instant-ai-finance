from __future__ import annotations

import unittest
from datetime import datetime

from mx_agent.downloader_engine.douyin_core import chromium_runtime_flags
from mx_agent.downloader_engine.profile_monitor import (
    refine_created_at_from_title,
)


class ProfileMonitorDateTests(unittest.TestCase):
    def test_linux_chromium_disables_broken_crashpad_for_automation(self):
        flags = chromium_runtime_flags("posix")
        self.assertIn("--disable-crashpad-for-testing", flags)
        self.assertIn("--no-sandbox", flags)
        self.assertEqual(chromium_runtime_flags("nt"), [])

    def test_explicit_title_date_replaces_aweme_draft_day(self):
        draft_time = datetime(2026, 8, 16, 11, 46, 46)
        corrected = refine_created_at_from_title(
            draft_time,
            "BK(老号)在抖音记录美好生活20260817",
        )
        self.assertEqual(corrected, datetime(2026, 8, 17, 11, 46, 46))

    def test_unrelated_or_missing_date_does_not_change_timestamp(self):
        draft_time = datetime(2026, 8, 16, 11, 46, 46)
        self.assertEqual(
            refine_created_at_from_title(draft_time, "普通作品标题"),
            draft_time,
        )
        self.assertEqual(
            refine_created_at_from_title(
                draft_time,
                "历史回顾 20200101",
            ),
            draft_time,
        )


if __name__ == "__main__":
    unittest.main()
