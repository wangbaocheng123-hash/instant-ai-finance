from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


COMPONENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPONENT))

from douyin_core import (  # noqa: E402
    VIDEO_ID_RE,
    _find_aweme_with_images,
    _image_description_from_aweme,
    _image_title,
    _image_title_from_aweme,
    _image_urls_from_aweme,
    extract_douyin_url,
    image_mime_type,
)
from comment_collector import CommentCollector  # noqa: E402
from library_store import LibraryStore, SCHEMA_VERSION  # noqa: E402
from profile_monitor import (  # noqa: E402
    IMAGE_WORK_PATH_TYPES,
    PROFILE_WORK_RE,
    video_created_at,
)


CHINA = timezone(timedelta(hours=8))


class ImagePostTests(unittest.TestCase):
    def test_aweme_timestamp_is_explicit_beijing_time(self) -> None:
        timestamp = int(datetime(2026, 9, 23, 9, 59, 19, tzinfo=timezone.utc).timestamp())
        video_id = str(timestamp << 32)

        created_at = video_created_at(video_id)

        self.assertEqual(
            created_at.isoformat(timespec="seconds"),
            "2026-09-23T17:59:19+08:00",
        )

    def test_comment_timestamp_is_explicit_beijing_time(self) -> None:
        timestamp = int(datetime(2026, 9, 23, 9, 59, 19, tzinfo=timezone.utc).timestamp())

        rows = CommentCollector._flatten_comment(
            {
                "cid": "comment-1",
                "create_time": timestamp,
                "user": {"nickname": "读者"},
                "text": "测试评论",
            }
        )

        self.assertEqual(rows[0]["created_at"], "2026-09-23T17:59:19+08:00")

    def test_version_four_library_migrates_without_rewriting_video_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "library.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE schema_info(version INTEGER NOT NULL);
                    INSERT INTO schema_info VALUES(4);
                    CREATE TABLE videos(
                        video_id TEXT PRIMARY KEY,
                        creator TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        source_url TEXT NOT NULL,
                        published_at TEXT NOT NULL,
                        discovered_at TEXT NOT NULL,
                        downloaded_at TEXT,
                        file_path TEXT,
                        file_size INTEGER,
                        duration_seconds REAL,
                        download_status TEXT NOT NULL DEFAULT 'discovered',
                        comments_collected_at TEXT,
                        comment_refresh_requested INTEGER NOT NULL DEFAULT 0,
                        comment_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO videos(
                        video_id, title, source_url, published_at,
                        discovered_at, updated_at
                    ) VALUES(
                        'legacy-video', '旧视频',
                        'https://www.douyin.com/video/7000000000000000000',
                        '2026-09-15T09:00:00+08:00',
                        '2026-09-15T09:01:00+08:00',
                        '2026-09-15T09:01:00+08:00'
                    );
                    """
                )

            LibraryStore(database, root / "comments")

            with sqlite3.connect(database) as connection:
                connection.row_factory = sqlite3.Row
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(videos)")
                }
                legacy = connection.execute(
                    "SELECT title, work_type, description FROM videos"
                ).fetchone()
                media_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='work_media'"
                ).fetchone()
                version = connection.execute("SELECT version FROM schema_info").fetchone()[0]
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertTrue({"work_type", "description"}.issubset(columns))
            self.assertEqual(dict(legacy), {"title": "旧视频", "work_type": "video", "description": ""})
            self.assertIsNotNone(media_table)

    def test_profile_pattern_accepts_video_and_note(self) -> None:
        video = PROFILE_WORK_RE.search("https://www.douyin.com/video/7000000000000000001")
        note = PROFILE_WORK_RE.search("https://www.douyin.com/note/7000000000000000002?from=profile")
        self.assertEqual(video.groups(), ("video", "7000000000000000001"))
        self.assertEqual(note.groups(), ("note", "7000000000000000002"))

    def test_article_route_is_an_image_work_and_keeps_the_work_id(self) -> None:
        url = "https://www.douyin.com/article/7000000000000000003?from=profile"
        profile_match = PROFILE_WORK_RE.search(url)
        resolver_match = VIDEO_ID_RE.search(url)
        self.assertEqual(
            profile_match.groups(),
            ("article", "7000000000000000003"),
        )
        self.assertIn(profile_match.group(1), IMAGE_WORK_PATH_TYPES)
        self.assertEqual(resolver_match.group(1), "7000000000000000003")
        self.assertEqual(extract_douyin_url(f"新图文 {url}"), url)

    def test_nested_note_keeps_caption_and_original_image_order(self) -> None:
        work_id = "7000000000000000003"
        payload = {
            "data": {
                "aweme_detail": {
                    "aweme_id": work_id,
                    "desc": "第一行正文\n第二行仍需保留",
                    "images": [
                        {
                            "download_url_list": [
                                "https://p3.douyinpic.com/original-1.jpg",
                                "https://p11.douyinpic.com/mirror-1.jpg",
                            ]
                        },
                        {"url_list": ["https://p3.douyinpic.com/original-2.png"]},
                    ],
                }
            }
        }
        aweme = _find_aweme_with_images(payload, work_id)
        self.assertIsNotNone(aweme)
        self.assertEqual(
            _image_urls_from_aweme(aweme or {}),
            [
                "https://p3.douyinpic.com/original-1.jpg",
                "https://p3.douyinpic.com/original-2.png",
            ],
        )
        self.assertEqual(_image_title(aweme["desc"], "fallback"), "第一行正文")

    def test_text_article_keeps_markdown_and_public_origin_cover(self) -> None:
        work_id = "7000000000000000004"
        payload = {
            "aweme_detail": {
                "aweme_id": work_id,
                "desc": "",
                "article_info": {
                    "article_title": "长图文标题",
                    "article_content": json.dumps(
                        {
                            "long_article_abstract": "",
                            "markdown": "第一段正文\n\n第二段正文",
                        },
                        ensure_ascii=False,
                    ),
                    "fe_data": json.dumps(
                        {
                            "image_length": 0,
                            "image_list": [],
                            "is_rich_media": False,
                        }
                    ),
                },
                "video": {
                    "origin_cover": {
                        "url_list": [
                            "https://p3.douyinpic.com/article-cover.jpg",
                            "https://p9.douyinpic.com/article-cover.jpg",
                        ]
                    },
                    "cover": {
                        "url_list": [
                            "https://p3.douyinpic.com/lower-priority-cover.jpg"
                        ]
                    },
                },
            }
        }
        aweme = _find_aweme_with_images(payload, work_id)
        self.assertIsNotNone(aweme)
        self.assertEqual(
            _image_description_from_aweme(aweme or {}),
            "第一段正文\n\n第二段正文",
        )
        self.assertEqual(_image_title_from_aweme(aweme or {}), "长图文标题")
        self.assertEqual(
            _image_urls_from_aweme(aweme or {}),
            ["https://p3.douyinpic.com/article-cover.jpg"],
        )

    def test_article_embedded_images_take_priority_over_cover(self) -> None:
        work_id = "7000000000000000005"
        aweme = {
            "aweme_id": work_id,
            "article_info": {
                "article_title": "带图长文",
                "article_content": json.dumps({"markdown": "正文"}),
                "fe_data": json.dumps(
                    {
                        "image_list": [
                            {"url": "https://p3.douyinpic.com/article-1.webp"},
                            {"url_list": ["https://p3.douyinpic.com/article-2.jpg"]},
                        ]
                    }
                ),
            },
            "video": {
                "origin_cover": {
                    "url_list": ["https://p3.douyinpic.com/cover.jpg"]
                }
            },
        }
        self.assertIs(_find_aweme_with_images(aweme, work_id), aweme)
        self.assertEqual(
            _image_urls_from_aweme(aweme),
            [
                "https://p3.douyinpic.com/article-1.webp",
                "https://p3.douyinpic.com/article-2.jpg",
            ],
        )

    def test_schema_migrates_and_persists_caption_and_multiple_originals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "library.sqlite3"
            store = LibraryStore(database, root / "comments")
            now = datetime(2026, 9, 16, 9, 0, tzinfo=CHINA)
            store.upsert_video(
                video_id="7000000000000000004",
                creator="模型先生",
                title="抖音图文_7000000000000000004",
                description="",
                work_type="image",
                source_url="https://www.douyin.com/note/7000000000000000004",
                published_at=now,
                now=now,
            )
            first = root / "first.jpg"
            first.write_bytes(b"\xff\xd8\xff" + b"a" * 2048)
            second = root / "second.png"
            second.write_bytes(b"\x89PNG\r\n\x1a\n" + b"b" * 2048)
            self.assertEqual(image_mime_type(first), "image/jpeg")
            self.assertEqual(image_mime_type(second), "image/png")

            store.mark_images_downloaded(
                video_id="7000000000000000004",
                title="图文标题",
                description="完整正文\n第二行",
                media=[(first, "image/jpeg"), (second, "image/png")],
                downloaded_at=now,
            )

            media = store.media_files("7000000000000000004")
            with sqlite3.connect(database) as connection:
                connection.row_factory = sqlite3.Row
                work = connection.execute(
                    "SELECT work_type, title, description, file_size FROM videos"
                ).fetchone()
                version = connection.execute(
                    "SELECT version FROM schema_info"
                ).fetchone()[0]
            self.assertEqual(version, SCHEMA_VERSION)
            self.assertEqual(work["work_type"], "image")
            self.assertEqual(work["title"], "图文标题")
            self.assertEqual(work["description"], "完整正文\n第二行")
            self.assertEqual(work["file_size"], first.stat().st_size + second.stat().st_size)
            self.assertEqual([row["ordinal"] for row in media], [0, 1])
            self.assertEqual([row["mime_type"] for row in media], ["image/jpeg", "image/png"])
            self.assertTrue(all(len(row["sha256"]) == 64 for row in media))
            self.assertTrue(store.image_media_complete("7000000000000000004"))
            first.write_bytes(b"\xff\xd8\xff" + b"z" * 2048)
            self.assertFalse(store.image_media_complete("7000000000000000004"))


if __name__ == "__main__":
    unittest.main()
