from __future__ import annotations

import gc
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mx_agent.comment_ocr import parse_ocr_comments
from mx_agent.cover_title import select_cover_title
from mx_agent.knowledge import KnowledgeReader
from mx_agent.keyword_taxonomy import KEYWORD_CATEGORIES, KEYWORD_SCHEMA_VERSION
from mx_agent.storage import Storage
from mx_agent.transcriber import normalize_transcript
from mx_agent import transcriber


class KnowledgeRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mx-agent-test-"))
        self.database_path = self.temp_dir / "knowledge.sqlite3"
        self.storage = Storage(self.database_path)
        self.video_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "video-1",
                "author": "模型先生",
                "title": "202607171200",
                "description": "",
                "published_at": "2026-07-17T12:00:00+08:00",
            }
        )
        self.storage.save_note(
            self.video_id,
            "video_text",
            "紫金矿业的长期逻辑来自资源价格和盈利能力。短期波动不改变长期判断。",
        )
        self.storage.upsert_comment(
            {
                "video_id": self.video_id,
                "source": "test",
                "source_comment_id": "question-1",
                "author": "普通用户",
                "text": "现在应该卖出紫金矿业吗？",
                "raw_json": {"display_order": 1, "kind": "user_comment"},
            }
        )
        self.storage.upsert_comment(
            {
                "video_id": self.video_id,
                "source": "test",
                "source_comment_id": "reply-1",
                "author": "模型先生",
                "text": "长线暂时想不到卖出的理由。",
                "raw_json": {"display_order": 2, "kind": "author_reply"},
            }
        )
        self.storage.save_note(
            self.video_id,
            "interpretation",
            "我的感悟是需要区分长期逻辑和短期交易。",
        )
        self.reader = KnowledgeReader(self.database_path)

    def tearDown(self) -> None:
        self.reader = None
        self.storage = None
        gc.collect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_three_sections_are_classified_and_user_comments_are_context_only(self) -> None:
        with Storage(self.database_path).connect() as conn:
            chunks = conn.execute(
                "SELECT source_type, content, context FROM knowledge_chunks ORDER BY id"
            ).fetchall()
        self.assertEqual(
            {row["source_type"] for row in chunks},
            {"video_title", "video_original", "model_comment_reply", "user_interpretation"},
        )
        reply = next(row for row in chunks if row["source_type"] == "model_comment_reply")
        self.assertIn("卖出紫金矿业", reply["context"])
        self.assertNotIn("普通用户", {row["source_type"] for row in chunks})

    def test_search_returns_evidence_type_and_question_context(self) -> None:
        result = self.reader.search("模型先生怎么看紫金矿业？")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["record_id"], f"video:{self.video_id}")
        self.assertIn("video_original", result["items"][0]["matched_in"])

        reply_result = self.reader.search("现在应该卖出")
        matches = reply_result["items"][0]["matches"]
        reply = next(match for match in matches if match["content_type"] == "model_comment_reply")
        self.assertIn("紫金矿业", reply["context"])

    def test_get_separates_original_reply_and_interpretation(self) -> None:
        result = self.reader.get(f"video:{self.video_id}")
        sections = result["content_sections"]
        self.assertTrue(sections["video_original"]["verified"])
        self.assertEqual(len(sections["model_comment_threads"]), 1)
        self.assertIn("不是博主原话", sections["user_interpretation"]["attribution_warning"])

    def test_future_video_is_incrementally_indexed_without_full_rebuild(self) -> None:
        second_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "video-2",
                "author": "模型先生",
                "title": "202607171300",
                "published_at": "2026-07-17T13:00:00+08:00",
            }
        )
        self.storage.save_note(second_id, "video_text", "算力产业链仍然处于早期发展阶段。")
        result = self.reader.search("算力产业链")
        self.assertEqual(result["items"][0]["record_id"], f"video:{second_id}")
        with self.storage.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM knowledge_dirty").fetchone()[0], 0)

    def test_categorized_keywords_locate_then_read_official_original(self) -> None:
        text = "国产算力和半导体产业链正在经历震荡调整，仓位控制很重要。"
        original = self.storage.save_content_original(self.video_id, text)
        categories = {category: [] for category in KEYWORD_CATEGORIES}
        categories["行业与板块"] = ["国产算力", "半导体"]
        categories["时间周期与走势状态"] = ["震荡调整"]
        categories["交易管理与风险控制"] = ["仓位控制"]
        self.storage.save_content_keywords(
            self.video_id,
            categories=categories,
            source_hash=original["text_sha256"],
            schema_version=KEYWORD_SCHEMA_VERSION,
            model="doubao:test",
        )

        found = self.reader.search_video_originals_by_keywords(
            keywords=["芯片", "仓位控制"],
            match_all=True,
        )

        self.assertEqual(found["count"], 1)
        self.assertEqual(found["items"][0]["record_id"], f"video:{self.video_id}")
        self.assertIn("半导体", found["items"][0]["matched_keywords"]["行业与板块"])
        self.assertTrue(found["items"][0]["keyword_index_current"])
        self.assertNotIn("完整正式原文", found["items"][0]["original_excerpt"])

        complete = self.reader.get_video_original(f"video:{self.video_id}")
        self.assertTrue(complete["found"])
        self.assertEqual(complete["video_original"]["text"], text)
        self.assertTrue(complete["video_original"]["verified"])
        self.assertEqual(
            complete["keyword_index"]["categories"]["交易管理与风险控制"],
            ["仓位控制"],
        )
        self.assertNotIn("summary", complete)

    def test_latest_question_returns_latest_record_without_literal_keyword_match(self) -> None:
        second_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "video-latest",
                "author": "模型先生",
                "title": "202607181300",
                "published_at": "2026-07-18T13:00:00+08:00",
            }
        )
        self.storage.save_note(second_id, "video_text", "存储板块今天出现超跌反弹。")

        result = self.reader.search("模型先生最新观点")

        self.assertEqual(result["query_mode"], "latest")
        self.assertEqual(result["items"][0]["record_id"], f"video:{second_id}")
        self.assertEqual(result["latest_available_at"], "2026-07-18T13:00:00+08:00")

    def test_explicit_date_question_returns_that_days_records(self) -> None:
        second_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "video-requested-date",
                "author": "模型先生",
                "title": "202607181500",
                "published_at": "2026-07-18T15:00:00+08:00",
            }
        )
        self.storage.save_note(second_id, "video_text", "国产算力需要观察补跌后的修复。")

        result = self.reader.search("模型先生2026年7月18日有什么观点")

        self.assertEqual(result["query_mode"], "latest")
        self.assertEqual(result["requested_date"], "2026-07-18")
        self.assertEqual({item["record_id"] for item in result["items"]}, {f"video:{second_id}"})

        topic_result = self.reader.search("模型先生2026年7月18日对国产算力怎么看")
        self.assertEqual(topic_result["search_terms"][0], "国产算力")
        self.assertEqual(
            {item["record_id"] for item in topic_result["items"]},
            {f"video:{second_id}"},
        )

        verbose_result = self.reader.search(
            "请重新查询模型先生2026年7月18日最新的视频和观点，"
            "只列出记录编号和北京时间，不要沿用上次结果。"
        )
        self.assertEqual(verbose_result["search_terms"], [])
        self.assertEqual(
            {item["record_id"] for item in verbose_result["items"]},
            {f"video:{second_id}"},
        )

    def test_video_list_and_author_replies_are_isolated_by_account(self) -> None:
        world_video_id, _ = self.storage.upsert_video(
            {
                "source": "model-world-video-drop",
                "source_video_id": "world-video-1",
                "author": "模型哥看世界",
                "title": "世界格局观察",
                "published_at": "2026-07-22T08:00:00+08:00",
            }
        )
        self.storage.upsert_comment(
            {
                "video_id": world_video_id,
                "source": "test",
                "source_comment_id": "world-question-1",
                "author": "普通用户",
                "text": "这件事应该怎么看？",
                "raw_json": {"display_order": 1, "kind": "user_comment"},
            }
        )
        self.storage.upsert_comment(
            {
                "video_id": world_video_id,
                "source": "test",
                "source_comment_id": "world-reply-1",
                "author": "模型哥看世界",
                "text": "先看事实，再判断长期影响。",
                "raw_json": {"display_order": 2, "kind": "author_reply"},
            }
        )

        model_items = self.storage.list_videos(account="模型先生")
        world_items = self.storage.list_videos(account="模型哥看世界")
        self.assertEqual({item["id"] for item in model_items}, {self.video_id})
        self.assertEqual({item["id"] for item in world_items}, {world_video_id})

        detail = self.reader.get(f"video:{world_video_id}")
        threads = detail["content_sections"]["model_comment_threads"]
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["reply_author"], "模型哥看世界")

    def test_cover_title_is_high_priority_and_manual_title_is_never_overwritten(self) -> None:
        ocr = self.storage.save_ocr_title(
            self.video_id,
            "紫金矿业长期估值",
            confidence=0.91,
            frame_timestamp=0.0,
        )
        self.assertEqual(ocr["active_title"], "紫金矿业长期估值")
        self.assertEqual(ocr["title_source"], "cover_ocr")
        self.assertEqual(self.reader.search("紫金矿业长期估值")["items"][0]["title_kind"], "cover_ocr")

        manual = self.storage.save_manual_title(self.video_id, "紫金矿业的长期投资与卖出逻辑")
        self.assertTrue(manual["verified"])
        self.storage.save_ocr_title(self.video_id, "错误的再次识别标题", confidence=0.99)
        current = self.storage.get_video_title(self.video_id)
        self.assertEqual(current["active_title"], "紫金矿业的长期投资与卖出逻辑")
        self.assertEqual(current["title_source"], "manual")
        self.assertEqual(self.reader.search("长期投资与卖出逻辑")["items"][0]["record_id"], f"video:{self.video_id}")

    def test_manual_publish_time_is_never_overwritten_by_filename_scan(self) -> None:
        manual_time = "2026-07-04T09:32:00+00:00"
        self.storage.update_video_published_at_manual(self.video_id, manual_time)
        changed = self.storage.update_video_publish_time_from_filename(
            self.video_id,
            "2026-07-19T00:42:00+00:00",
            title="202607190842",
            source="model-video-drop",
            force=True,
        )
        self.assertFalse(changed)
        current = self.storage.get_video(self.video_id)
        self.assertEqual(manual_time, current["published_at"])
        self.assertEqual("manual_verified", json.loads(current["raw_json"])["published_at_source"])

    def test_title_only_record_is_searchable_without_being_model_evidence(self) -> None:
        title_only_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "title-only",
                "author": "模型先生",
                "title": "202607171400",
                "published_at": "2026-07-17T14:00:00+08:00",
            }
        )
        self.storage.save_manual_title(title_only_id, "科技股还要调整多久？")
        result = self.reader.search("科技股还要调整多久")
        self.assertEqual(result["items"][0]["record_id"], f"video:{title_only_id}")
        self.assertEqual(result["items"][0]["matches"][0]["content_type"], "video_title")
        self.assertEqual(result["items"][0]["matches"][0]["content_label"], "视频标题（检索元数据）")

    def test_largest_cover_text_is_selected_instead_of_interface_text(self) -> None:
        payload = {
            "lines": [
                {"text": "关注 推荐", "words": [{"x": 50, "y": 90, "width": 80, "height": 16}]},
                {"text": "国 产 算 力", "words": [{"x": 80, "y": 500, "width": 420, "height": 92}]},
                {"text": "@模型先生", "words": [{"x": 20, "y": 900, "width": 120, "height": 20}]},
            ]
        }
        result = select_cover_title(payload, (720, 1280))
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "国产算力")

    def test_comment_ocr_keeps_parent_question_and_model_reply(self) -> None:
        comments = parse_ocr_comments(
            [
                "俯 怖 的 妈 咪",
                "最 后 悔 的 就 是 没 听 您 的 6 月 30 号 清 仓 0 0 0",
                "23 分 前 · 安 徽 回 复",
                "模型先生作者",
                "模型先生",
                "0 63 0",
                "你 是 现 在 说，10 号 左 右 科 技 股 反 弹 的 时 候 骂 的 我 都",
                "不 敢 看 评 论",
                "11 分 钟 前 · 安 徽 回 复",
                "0 231 0",
            ]
        )
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]["author"], "布布的妈咪")
        self.assertEqual(comments[0]["kind"], "user_comment")
        self.assertIn("6月30号清仓", comments[0]["text"])
        self.assertEqual(comments[1]["author"], "模型先生")
        self.assertEqual(comments[1]["kind"], "author_reply")
        self.assertIn("10号左右科技股反弹", comments[1]["text"])

    def test_comment_ocr_supports_model_world_account_author(self) -> None:
        comments = parse_ocr_comments(
            [
                "模型哥看世界作者",
                "模型哥看世界",
                "先看事实，再判断长期影响。",
                "11分钟前 · 安徽 回复",
                "回复 分享 18",
            ],
            author_name="模型哥看世界",
        )
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author"], "模型哥看世界")
        self.assertEqual(comments[0]["kind"], "author_reply")

    def test_positioned_comment_parser_marks_nested_user_replies(self) -> None:
        from mx_agent.comment_ocr import positioned_content_items

        items = [
            {"text": "哥哥", "bounds": (106, 165, 140, 181)},
            {"text": "同样斜率V回来是不可能的。来个反弹", "bounds": (107, 195, 420, 215)},
            {"text": "然后阴跌，重新建仓再拉", "bounds": (107, 225, 360, 245)},
        ]
        content = positioned_content_items(items)
        self.assertEqual(len(content), 2)
        self.assertTrue(content[0]["text"].startswith("同样斜率"))

    def test_positioned_comment_parser_separates_wide_follow_badge_from_nickname(self) -> None:
        from mx_agent.comment_ocr import positioned_content_items, positioned_line_text

        nickname = positioned_line_text(
            {
                "text": "全 能 的 野 人 一",
                "words": [
                    {"text": "全", "width": 16},
                    {"text": "能", "width": 15},
                    {"text": "的", "width": 15},
                    {"text": "野", "width": 15},
                    {"text": "人", "width": 16},
                    {"text": "一", "width": 73},
                ],
            }
        )
        self.assertEqual("全能的野人", nickname)
        items = [
            {"text": "佾", "bounds": (4, 27, 44, 64)},
            {"text": nickname, "bounds": (53, 28, 214, 48)},
            {"text": "这就是人性的博弈的意思！！当所有人都等反弹", "bounds": (53, 60, 520, 78)},
        ]
        content = positioned_content_items(items)
        self.assertEqual(1, len(content))
        self.assertTrue(content[0]["text"].startswith("这就是人性"))

    def test_video_transcript_is_simplified_and_has_basic_punctuation(self) -> None:
        text = normalize_transcript(
            "科技股中有一部分個股是回不到高點的就是這波航情中它一定炒上去的但是現在屬於通煞\n"
            "因為從具體策略上比如說對普通投資者來說理解起來太困難"
        )
        self.assertNotIn("個股", text)
        self.assertNotIn("高點", text)
        self.assertIn("个股", text)
        self.assertIn("高点", text)
        self.assertIn("行情", text)
        self.assertIn("通杀", text)
        self.assertIn("，", text)
        self.assertTrue(all(line.endswith("。") for line in text.splitlines()))

    def test_portable_simplified_conversion_covers_server_release_contract(self) -> None:
        with mock.patch.object(transcriber.os, "name", "posix"):
            text = transcriber.to_simplified_chinese("個股高點屬於現在，因為從具體策略來說很難")
        self.assertEqual(text, "个股高点属于现在，因为从具体策略来说很难")


if __name__ == "__main__":
    unittest.main()
