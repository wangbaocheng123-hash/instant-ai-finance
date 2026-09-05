from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mx_agent.stock_mentions import StockMentionService
from mx_agent.storage import Storage


class StockMentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp_dir.name) / "stock-mentions.sqlite3")
        self.video_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "video-stock-comments",
                "author": "模型先生",
                "title": "评论区评股测试",
                "description": "",
            }
        )
        self.service = StockMentionService(
            self.storage,
            master_items=[
                {"code": "000938", "name": "紫光股份"},
                {"code": "000063", "name": "中兴通讯"},
                {"code": "688256", "name": "寒武纪"},
                {"code": "300024", "name": "机器人"},
            ],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_comment(
        self,
        source_id: str,
        author: str,
        text: str,
        kind: str = "user_comment",
    ) -> None:
        self.storage.upsert_comment(
            {
                "video_id": self.video_id,
                "source": "test",
                "source_comment_id": source_id,
                "author": author,
                "text": text,
                "raw_json": {"kind": kind},
            }
        )

    def test_ranks_by_distinct_comment_count_and_separates_author(self) -> None:
        self.add_comment("1", "甲", "紫光、紫光股份和中兴怎么看？")
        self.add_comment("2", "乙", "紫光还能买吗，000063也套住了。")
        self.add_comment("3", "模型先生", "紫光股份需要看估值。", "author_reply")
        self.add_comment("4", "丙", "寒武纪和寒武纪都很强。")

        result = self.service.analyze(self.video_id)

        self.assertFalse(result["api_used"])
        self.assertEqual(
            [item["name"] for item in result["items"]],
            ["紫光股份", "中兴通讯", "寒武纪"],
        )
        self.assertEqual(result["items"][0]["comment_count"], 3)
        self.assertEqual(result["items"][0]["fan_comment_count"], 2)
        self.assertEqual(result["items"][0]["author_comment_count"], 1)
        self.assertEqual(len(result["items"][0]["comment_ids"]), 3)
        self.assertEqual(len(set(result["items"][0]["comment_ids"])), 3)
        self.assertEqual(result["items"][2]["comment_count"], 1)
        self.assertEqual(result["items"][2]["mention_count"], 2)

    def test_ambiguous_common_word_is_not_forced_into_ranking(self) -> None:
        self.add_comment("1", "甲", "机器人以后会替代很多岗位。")

        result = self.service.analyze(self.video_id)

        self.assertEqual(result["items"], [])
        self.assertEqual(result["uncertain"][0]["text"], "机器人")
        self.assertEqual(result["uncertain"][0]["candidates"], ["机器人"])

    def test_limit_never_exceeds_twenty(self) -> None:
        master = [
            {"code": f"{index:06d}", "name": f"测试股份{index}"}
            for index in range(1, 31)
        ]
        service = StockMentionService(self.storage, master_items=master)
        for index in range(1, 31):
            self.add_comment(str(index), "甲", f"测试股份{index}")

        result = service.analyze(self.video_id, limit=100)

        self.assertLessEqual(len(result["items"]), 20)


if __name__ == "__main__":
    unittest.main()
