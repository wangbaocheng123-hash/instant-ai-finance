from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from mx_agent.comment_sync import (
    DouyinCommentSyncService,
    classify_comment_sections,
    comment_reply_completeness,
    extract_aweme_id,
    extract_comments_from_payload,
    is_low_value_comment,
    parse_comments_csv,
)
from mx_agent.storage import Storage


class CommentSyncTests(unittest.TestCase):
    @staticmethod
    def _cloud_csv_bytes() -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "评论ID",
                "父评论ID",
                "回复目标评论ID",
                "接口返回的回复对象",
                "实际被回复用户",
                "被回复的原评论",
                "用户昵称",
                "用户标识",
                "是否模型先生本人",
                "是否博主点赞",
                "公开标签",
                "评论内容",
                "发布时间",
                "点赞数",
                "回复数",
                "IP属地",
                "首次发现时间",
                "最后发现时间",
                "采集时间",
            ]
        )
        writer.writerow(
            [
                "fan-1", "", "0", "", "", "", "粉丝甲", "fan-uid", "否", "否", "",
                "B浪是不是没有了", "2026-08-01T15:40:00", "9", "1", "上海",
                "2026-08-01T07:40:00.000Z", "2026-08-01T08:40:00.000Z",
                "2026-08-01T08:40:00.000Z",
            ]
        )
        writer.writerow(
            [
                "reply-1", "fan-1", "0", "", "粉丝甲", "B浪是不是没有了", "模型先生",
                "model-uid", "是", "否", "作者", "震荡反弹就是B浪", "2026-08-01T15:45:00",
                "135", "0", "安徽", "2026-08-01T08:40:00.000Z",
                "2026-08-01T08:40:00.000Z", "2026-08-01T08:40:00.000Z",
            ]
        )
        writer.writerow(
            [
                "author-root", "", "0", "", "", "", "模型先生", "model-uid", "是", "否",
                "作者", "周末精选个股", "2026-08-01T16:07:00", "193", "0", "安徽",
                "2026-08-01T08:40:00.000Z", "2026-08-01T08:40:00.000Z",
                "2026-08-01T08:40:00.000Z",
            ]
        )
        writer.writerow(
            [
                "fan-liked", "", "0", "", "", "", "粉丝乙", "fan-uid-2", "否", "是", "",
                "商业航天怎么看", "2026-08-01T16:10:00", "18", "0", "广东",
                "2026-08-01T08:40:00.000Z", "2026-08-01T08:40:00.000Z",
                "2026-08-01T08:40:00.000Z",
            ]
        )
        writer.writerow(
            [
                "blank-comment", "", "0", "", "", "", "空评论", "blank", "否", "否", "", "",
                "2026-08-01T16:11:00", "0", "0", "", "", "", "",
            ]
        )
        return ("\ufeff" + output.getvalue()).encode("utf-8")

    def test_extract_aweme_id_from_download_filename(self) -> None:
        self.assertEqual(
            extract_aweme_id("202607261027_7666656007661215217.mp4"),
            "7666656007661215217",
        )

    def test_low_value_filter_is_conservative(self) -> None:
        self.assertTrue(is_low_value_comment("[赞][赞]"))
        self.assertTrue(is_low_value_comment("哈哈哈哈"))
        self.assertTrue(is_low_value_comment("666"))
        self.assertFalse(is_low_value_comment("理论上有反弹"))
        self.assertFalse(is_low_value_comment("兆易创新还能买吗？"))

        classified = classify_comment_sections(
            [
                {
                    "source_comment_id": "noise-1",
                    "author": "粉丝",
                    "text": "哈哈哈",
                    "parent_source_comment_id": "",
                    "kind": "user_comment",
                }
            ],
            "模型先生",
        )
        self.assertTrue(classified[0]["low_value"])
        self.assertFalse(classified[0]["include"])

    def test_extract_and_classify_author_interaction(self) -> None:
        payload = {
            "comments": [
                {
                    "cid": "fan-1",
                    "text": "卫星ETF套住了，可以等B浪吗",
                    "digg_count": 5,
                    "reply_comment_total": 1,
                    "item_comment_total": 1099,
                    "is_author_digged": True,
                    "ip_label": "安徽",
                    "create_time": 1_785_000_000,
                    "user": {"nickname": "粉丝甲", "sec_uid": "fan"},
                    "reply_comment": [
                        {
                            "cid": "reply-1",
                            "reply_id": "fan-1",
                            "text": "ETF问题不大",
                            "digg_count": 38,
                            "create_time": 1_785_000_100,
                            "user": {"nickname": "模型先生", "sec_uid": "model"},
                        }
                    ],
                },
                {
                    "cid": "fan-2",
                    "text": "这次调整之后我会控制仓位",
                    "digg_count": 18,
                    "user": {"nickname": "粉丝乙"},
                },
            ]
        }
        comments = extract_comments_from_payload(
            payload,
            response_url="https://www.douyin.com/aweme/v1/web/comment/list/",
            account_author="模型先生",
        )
        classified = classify_comment_sections(comments, "模型先生")
        by_id = {item["source_comment_id"]: item for item in classified}

        self.assertEqual(by_id["reply-1"]["kind"], "author_reply")
        self.assertEqual(
            by_id["fan-1"]["section"],
            "author_interaction",
        )
        self.assertEqual(
            by_id["reply-1"]["section"],
            "author_interaction",
        )
        self.assertEqual(by_id["fan-2"]["section"], "fan_comment")
        self.assertTrue(by_id["fan-1"]["author_liked"])
        self.assertEqual(by_id["fan-1"]["remote_total"], 1099)
        self.assertEqual(by_id["fan-1"]["ip_label"], "安徽")
        self.assertNotIn("raw_comment", by_id["fan-1"])

    def test_top_level_author_comment_is_author_interaction(self) -> None:
        comments = classify_comment_sections(
            [
                {
                    "source_comment_id": "author-root-1",
                    "author": "模型先生",
                    "text": "需要注意，这个观点不全面。",
                    "parent_source_comment_id": "",
                    "kind": "user_comment",
                }
            ],
            "模型先生",
        )

        self.assertEqual(comments[0]["section"], "author_interaction")
        self.assertTrue(comments[0]["include"])

    def test_auto_match_uses_embedded_aweme_id_without_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "agent.sqlite3")
            video_id, _ = storage.upsert_video(
                {
                    "source": "test",
                    "source_video_id": "local-sha",
                    "author": "模型先生",
                    "title": "202607261027_7666656007661215217",
                    "description": "",
                    "url": "",
                    "cover_url": "",
                    "published_at": "2026-07-26T02:27:00+00:00",
                    "raw_json": {},
                }
            )
            service = DouyinCommentSyncService(storage)
            result = service.auto_match(video_id)

            self.assertTrue(result["matched"])
            self.assertEqual(result["aweme_id"], "7666656007661215217")
            self.assertEqual(
                storage.get_video(video_id)["url"],
                "https://www.douyin.com/video/7666656007661215217",
            )

    def test_closed_chrome_context_is_recreated_automatically(self) -> None:
        class ClosedContext:
            pages: list[object] = []

            @staticmethod
            def new_page() -> object:
                raise RuntimeError(
                    "BrowserContext.new_page: Target page, context or browser has been closed"
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            storage = Storage(root / "agent.sqlite3")
            service = DouyinCommentSyncService(storage)
            fresh_page = object()
            fresh_context = SimpleNamespace(pages=[fresh_page])
            launches: list[dict[str, object]] = []

            def launch_persistent_context(**kwargs: object) -> object:
                launches.append(kwargs)
                return fresh_context

            service._context = ClosedContext()
            service._page = SimpleNamespace(is_closed=lambda: True)
            service._playwright = SimpleNamespace(
                chromium=SimpleNamespace(
                    launch_persistent_context=launch_persistent_context
                )
            )
            with (
                patch(
                    "mx_agent.comment_sync.COMMENT_BROWSER_PROFILE",
                    root / "browser-profile",
                ),
                patch(
                    "mx_agent.comment_sync.chrome_executable_path",
                    return_value=Path(
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe"
                    ),
                ),
            ):
                page = service._ensure_page()

            self.assertIs(page, fresh_page)
            self.assertEqual(len(launches), 1)
            self.assertEqual(
                launches[0]["executable_path"],
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            )

    def test_signed_pagination_continues_until_has_more_is_false(self) -> None:
        class FakePage:
            @staticmethod
            def evaluate(script: str, url: str) -> dict[str, object]:
                cursor = int(parse_qs(urlparse(url).query)["cursor"][0])
                if cursor == 0:
                    comments = [
                        {
                            "cid": "root-1",
                            "text": "第一条",
                            "user": {"nickname": "粉丝一"},
                        },
                        {
                            "cid": "root-2",
                            "text": "第二条",
                            "user": {"nickname": "粉丝二"},
                        },
                    ]
                    payload = {
                        "comments": comments,
                        "cursor": 2,
                        "has_more": 1,
                    }
                else:
                    payload = {
                        "comments": [
                            {
                                "cid": "root-3",
                                "text": "第三条",
                                "user": {"nickname": "粉丝三"},
                            }
                        ],
                        "cursor": 3,
                        "has_more": 0,
                    }
                return {"ok": True, "status": 200, "payload": payload}

        with tempfile.TemporaryDirectory() as temp_dir:
            service = DouyinCommentSyncService(
                Storage(Path(temp_dir) / "agent.sqlite3")
            )
            captured: dict[str, dict[str, object]] = {}
            completed = service._collect_signed_pages(
                FakePage(),
                "https://www.douyin.com/aweme/v1/web/comment/list/"
                "?aweme_id=123&cursor=0&count=10&a_bogus=old",
                captured,
                account_author="模型先生",
                limit=20,
            )

            self.assertTrue(completed)
            self.assertEqual(set(captured), {"root-1", "root-2", "root-3"})

    def test_reply_completeness_follows_nested_and_declared_roots(self) -> None:
        diagnostics = comment_reply_completeness(
            [
                {
                    "source_comment_id": "root-1",
                    "author": "粉丝甲",
                    "text": "主评论",
                    "reply_count": 3,
                    "parent_source_comment_id": "",
                },
                {
                    "source_comment_id": "reply-1",
                    "parent_source_comment_id": "root-1",
                },
                {
                    "source_comment_id": "reply-2",
                    "parent_source_comment_id": "reply-1",
                    "thread_root_source_comment_id": "root-1",
                },
            ]
        )

        self.assertEqual(diagnostics["reply_groups"], 1)
        self.assertEqual(diagnostics["reply_groups_incomplete"], 1)
        self.assertEqual(diagnostics["captured_replies"], 2)
        self.assertEqual(diagnostics["missing_replies"], 1)

    def test_expand_reply_buttons_includes_plain_expand_more_and_cooldown(self) -> None:
        class FakePage:
            patterns: list[str] = []
            script = ""

            def evaluate(self, script: str, patterns: list[str]) -> int:
                self.script = script
                self.patterns = patterns
                return 2

        page = FakePage()
        expanded = DouyinCommentSyncService._expand_reply_buttons(page)

        self.assertEqual(expanded, 2)
        self.assertTrue(any(re.fullmatch(pattern, "展开更多") for pattern in page.patterns))
        self.assertIn("mxReplyLastClicked", page.script)

    def test_confirm_preview_writes_sections_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            storage = Storage(root / "agent.sqlite3")
            video_id, _ = storage.upsert_video(
                {
                    "source": "test",
                    "source_video_id": "video-1",
                    "author": "模型先生",
                    "title": "测试视频",
                    "description": "",
                    "url": "",
                    "cover_url": "",
                    "raw_json": {},
                }
            )
            service = DouyinCommentSyncService(storage)
            preview_id = "a" * 32
            preview_dir = root / "previews"
            preview_dir.mkdir()
            items = classify_comment_sections(
                [
                    {
                        "source_comment_id": "fan-1",
                        "author": "粉丝",
                        "text": "这是粉丝问题",
                        "like_count": 3,
                        "reply_count": 1,
                        "published_at": "",
                        "parent_source_comment_id": "",
                        "reply_depth": 0,
                        "kind": "user_comment",
                    },
                    {
                        "source_comment_id": "reply-1",
                        "author": "模型先生",
                        "text": "这是作者回复",
                        "like_count": 5,
                        "reply_count": 0,
                        "published_at": "",
                        "parent_source_comment_id": "fan-1",
                        "reply_depth": 1,
                        "kind": "author_reply",
                    },
                ],
                "模型先生",
            )
            (preview_dir / f"{preview_id}.json").write_text(
                json.dumps(
                    {
                        "preview_id": preview_id,
                        "video_id": video_id,
                        "aweme_id": "7666656007661215217",
                        "url": "https://www.douyin.com/video/7666656007661215217",
                        "items": items,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("mx_agent.comment_sync.SYNC_PREVIEW_DIR", preview_dir):
                first = service.confirm_preview(video_id, preview_id)

            self.assertEqual(first["created"], 2)
            comments = storage.list_comments(video_id, limit=10)
            sections = {
                item["source_comment_id"]: item["raw_json"]["section"]
                for item in comments
            }
            self.assertEqual(sections["fan-1"], "author_interaction")
            self.assertEqual(sections["reply-1"], "author_interaction")

    def test_cloud_csv_preview_and_confirm_reuse_thread_and_dedup_logic(self) -> None:
        content = self._cloud_csv_bytes()
        parsed = parse_comments_csv(
            content,
            account_author="模型先生",
            source_filename="7668963922510721226-评论.csv",
        )
        self.assertEqual(parsed["aweme_id"], "7668963922510721226")
        self.assertEqual(parsed["summary"]["csv_rows"], 4)
        self.assertEqual(parsed["summary"]["skipped_rows"], 1)
        self.assertEqual(parsed["summary"]["model_comments"], 2)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            storage = Storage(root / "agent.sqlite3")
            video_id, _ = storage.upsert_video(
                {
                    "source": "test",
                    "source_video_id": "latest-video",
                    "author": "模型先生",
                    "title": "最新视频",
                    "description": "",
                    "url": "",
                    "cover_url": "",
                    "raw_json": {},
                }
            )
            service = DouyinCommentSyncService(storage)
            preview_dir = root / "previews"
            with patch("mx_agent.comment_sync.SYNC_PREVIEW_DIR", preview_dir):
                preview = service.preview_csv(
                    video_id,
                    content=content,
                    filename="7668963922510721226-评论.csv",
                )
                self.assertEqual(preview["summary"]["new_comments"], 4)
                selected = [int(item["preview_index"]) for item in preview["items"]]
                first = service.confirm_preview(
                    video_id,
                    str(preview["preview_id"]),
                    selected,
                )
                second_preview = service.preview_csv(
                    video_id,
                    content=content,
                    filename="7668963922510721226-评论.csv",
                )
                self.assertEqual(second_preview["summary"]["already_in_database"], 4)
                second_selected = [
                    int(item["preview_index"]) for item in second_preview["items"]
                ]
                second = service.confirm_preview(
                    video_id,
                    str(second_preview["preview_id"]),
                    second_selected,
                )

            self.assertEqual(first["created"], 4)
            self.assertEqual(second["created"], 0)
            self.assertEqual(second["updated"], 4)
            self.assertEqual(
                storage.get_video(video_id)["url"],
                "https://www.douyin.com/video/7668963922510721226",
            )
            comments = {
                item["source_comment_id"]: item
                for item in storage.list_comments(video_id, limit=20)
            }
            reply_raw = comments["reply-1"]["raw_json"]
            self.assertEqual(reply_raw["kind"], "author_reply")
            self.assertEqual(reply_raw["section"], "author_interaction")
            self.assertEqual(reply_raw["parent_source_comment_id"], "fan-1")
            self.assertEqual(reply_raw["actual_reply_user"], "粉丝甲")
            self.assertEqual(reply_raw["display_order"], 20)
            self.assertEqual(
                reply_raw["source_filename"],
                "7668963922510721226-评论.csv",
            )
            self.assertTrue(comments["fan-liked"]["raw_json"]["author_liked"])


if __name__ == "__main__":
    unittest.main()
