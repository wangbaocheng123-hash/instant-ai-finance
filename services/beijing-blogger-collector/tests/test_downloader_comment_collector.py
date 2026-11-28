from __future__ import annotations

import unittest
import json
import queue
from pathlib import Path
from unittest.mock import patch

from mx_agent.downloader_engine import comment_collector
from mx_agent.downloader_engine.comment_collector import CommentCollector


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeCDP:
    def __init__(self, clock, *, has_more=True, stalled_body=False):
        self.clock = clock
        self.calls = []
        self.has_more = has_more
        self.stalled_body = stalled_body
        self.events = self
        self.messages = [
            {"method": "Network.responseReceived", "params": {"requestId": "first", "response": {"url": "https://www.douyin.com/aweme/v1/web/comment/list/?cursor=0"}}},
            {"method": "Network.loadingFinished", "params": {"requestId": "first"}},
        ]
        if stalled_body:
            self.messages += [
                {"method": "Network.responseReceived", "params": {"requestId": "stalled", "response": {"url": "https://www.douyin.com/aweme/v1/web/comment/list/?cursor=1"}}},
                {"method": "Network.loadingFinished", "params": {"requestId": "stalled"}},
            ]

    def get(self, *, timeout):
        if self.messages:
            self.clock.advance(min(0.05, timeout))
            return self.messages.pop(0)
        self.clock.advance(timeout)
        raise queue.Empty()

    def call(self, method, params=None, *, timeout=8):
        self.calls.append((method, self.clock.now, timeout))
        if method == "Runtime.evaluate":
            return {"result": {"value": {"clicked": 0, "candidates": 0, "scrollingMore": self.has_more}}}
        if method == "Network.getResponseBody":
            if self.stalled_body and params["requestId"] == "stalled":
                self.clock.advance(timeout)
                raise queue.Empty()
            return {"body": json.dumps({
                "has_more": self.has_more, "total": 10 if self.has_more else 1,
                "comments": [{"cid": "saved-comment", "text": "已经采到的评论", "user": {"nickname": "用户"}}],
            })}
        return {}


class DownloaderCommentCollectorTests(unittest.TestCase):
    def run_timed_collector(self, budget, *, has_more=True, stalled_body=False, limit=100):
        clock = FakeClock()
        cdp = FakeCDP(clock, has_more=has_more, stalled_body=stalled_body)
        collector = CommentCollector(Path("/unused-test-profile"))

        def start():
            clock.advance(min(0.5, budget))
            collector.cdp = cdp

        with patch.object(comment_collector.time, "monotonic", side_effect=clock.monotonic), \
             patch.object(collector, "_start", side_effect=start) as startup:
            rows = collector.collect("https://www.douyin.com/video/123", timeout=budget, limit=limit)
        return collector, rows, cdp, clock, startup

    def test_five_minute_deadline_keeps_partial_comments_and_bounds_every_call(self):
        collector, rows, cdp, clock, _ = self.run_timed_collector(300)
        self.assertEqual([row["comment_id"] for row in rows], ["saved-comment"])
        self.assertAlmostEqual(clock.now, 1300)
        self.assertEqual(collector.last_summary["stop_reason"], "time_limit")
        self.assertEqual(collector.last_summary["time_budget_seconds"], 300)
        self.assertFalse(collector.last_summary["complete"])
        for _, started, timeout in cdp.calls:
            self.assertLess(started, 1300)
            self.assertLessEqual(timeout, 1300 - started)

    def test_stalled_response_is_cut_to_remaining_budget_and_keeps_prior_rows(self):
        collector, rows, cdp, clock, _ = self.run_timed_collector(5, stalled_body=True)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(clock.now, 1005)
        self.assertEqual(collector.last_summary["stop_reason"], "time_limit")
        method, started, timeout = cdp.calls[-1]
        self.assertEqual(method, "Network.getResponseBody")
        self.assertAlmostEqual(timeout, 1005 - started)

    def test_zero_seconds_never_opens_a_comment_browser(self):
        collector, rows, _, _, startup = self.run_timed_collector(0)
        startup.assert_not_called()
        self.assertEqual(rows, [])
        self.assertEqual(collector.last_summary["elapsed_seconds"], 0)

    def test_completed_public_pages_and_count_cap_can_finish_before_deadline(self):
        for options, reason, complete in (
            ({"has_more": False}, "source_exhausted", True),
            ({"limit": 1}, "comment_limit", False),
        ):
            with self.subTest(reason=reason):
                collector, rows, _, clock, _ = self.run_timed_collector(300, **options)
                self.assertEqual(len(rows), 1)
                self.assertLess(clock.now, 1300)
                self.assertEqual(collector.last_summary["stop_reason"], reason)
                self.assertEqual(collector.last_summary["complete"], complete)

    def test_author_like_is_kept_as_a_three_state_field(self) -> None:
        confirmed = CommentCollector._flatten_comment(
            {
                "cid": "fan-liked",
                "text": "请问后续怎么看？",
                "is_author_digged": True,
                "user": {"nickname": "粉丝甲", "sec_uid": "fan-1"},
            }
        )[0]
        confirmed_not_liked = CommentCollector._flatten_comment(
            {
                "cid": "fan-not-liked",
                "text": "这条没有作者点赞",
                "is_author_digged": False,
                "user": {"nickname": "粉丝乙", "sec_uid": "fan-2"},
            }
        )[0]
        unsupported = CommentCollector._flatten_comment(
            {
                "cid": "fan-unknown",
                "text": "来源没有返回点赞字段",
                "user": {"nickname": "粉丝丙", "sec_uid": "fan-3"},
            }
        )[0]

        self.assertIs(confirmed["is_author_digged"], True)
        self.assertIs(confirmed_not_liked["is_author_digged"], False)
        self.assertIsNone(unsupported["is_author_digged"])

    def test_public_author_like_label_is_a_positive_fallback_only(self) -> None:
        labeled = CommentCollector._flatten_comment(
            {
                "cid": "fan-label-liked",
                "text": "公开标签说明作者赞过",
                "label_text": "作者赞过",
                "user": {"nickname": "粉丝甲"},
            }
        )[0]
        unrelated = CommentCollector._flatten_comment(
            {
                "cid": "fan-label-other",
                "text": "普通公开标签",
                "label_text": "热评",
                "user": {"nickname": "粉丝乙"},
            }
        )[0]

        self.assertIs(labeled["is_author_digged"], True)
        self.assertIsNone(unrelated["is_author_digged"])


if __name__ == "__main__":
    unittest.main()
