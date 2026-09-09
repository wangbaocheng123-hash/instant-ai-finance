from __future__ import annotations

import unittest

from mx_agent.downloader_engine.comment_collector import CommentCollector


class DownloaderCommentCollectorTests(unittest.TestCase):
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
