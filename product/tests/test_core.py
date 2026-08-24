from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from instant_ai.collectors import parse_feed
from instant_ai.database import DEFAULT_SOURCES, connect, initialize, seed_sources, transaction, utc_now
from instant_ai.launch import client_window_bounds
from instant_ai.rules import analyze, canonical_key, normalized_url
from instant_ai.thumbnails import (
    DownloadedImage,
    _article_image_candidates,
    _extract_google_news_images,
    get_thumbnail,
    register_thumbnail_candidate,
)
from instant_ai.translation import TranslationProvider, needs_translation, translate_items, utf8_prefix


RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Sample</title>
<item><title>Zijin copper production guidance</title><link>https://example.com/a?utm_source=test</link>
<guid>item-1</guid><description>Copper and gold production increased.</description>
<media:content medium="image" type="image/jpeg" url="https://images.example.com/copper.jpg" />
<pubDate>Sun, 23 Aug 2026 08:00:00 GMT</pubDate></item></channel></rss>"""


class RuleTests(unittest.TestCase):
    def test_tracking_parameters_are_removed(self) -> None:
        self.assertEqual(normalized_url("HTTPS://Example.com/a?utm_source=x&id=1#top"), "https://example.com/a?id=1")

    def test_topic_and_event_analysis(self) -> None:
        result = analyze("紫金矿业发布铜金产量业绩预告", "黄金和铜产量上升", 5, [])
        self.assertIn("紫金矿业", result.topics)
        self.assertIn("黄金", result.topics)
        self.assertIn("铜/有色", result.topics)
        self.assertEqual(result.event_type, "业绩/财报")
        self.assertGreaterEqual(result.importance_score, 80)

    def test_canonical_key_ignores_tracking(self) -> None:
        first = canonical_key("https://example.com/a?utm_source=x", "A")
        second = canonical_key("https://example.com/a", "B")
        self.assertEqual(first, second)

    def test_global_finance_topics_and_ascii_boundaries(self) -> None:
        result = analyze(
            "NVIDIA and TSMC lift Nasdaq as Wall Street watches AI chips",
            "Goldman Sachs published a global markets outlook.",
            4,
            ["全球财经"],
        )
        self.assertIn("全球财经", result.topics)
        self.assertIn("华尔街", result.topics)
        self.assertIn("AI产业链", result.topics)
        self.assertIn("英伟达", result.entities)
        self.assertNotIn("AI产业链", analyze("Daily oil market update", "", 3, []).topics)

    def test_desktop_client_window_has_a_bounded_size(self) -> None:
        width, height, left, top = client_window_bounds()
        self.assertGreaterEqual(width, 760)
        self.assertGreaterEqual(height, 560)
        self.assertLessEqual(width, 1240)
        self.assertLessEqual(height, 820)
        self.assertGreaterEqual(left, 0)
        self.assertGreaterEqual(top, 0)


class FeedTests(unittest.TestCase):
    def test_rss_parsing(self) -> None:
        entries = parse_feed(RSS_SAMPLE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].source_item_id, "item-1")
        self.assertEqual(entries[0].url, "https://example.com/a")
        self.assertTrue(entries[0].published_at.startswith("2026-08-23"))
        self.assertEqual(entries[0].image_url, "https://images.example.com/copper.jpg")


class DatabaseTests(unittest.TestCase):
    def test_schema_and_source_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            initialize(path)
            seed_sources(path)
            with connect(path) as connection:
                source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
                version = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
            self.assertEqual(source_count, len(DEFAULT_SOURCES))
            self.assertEqual(version, "4")
            with connect(path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            self.assertIn("ai_jobs", tables)
            self.assertIn("notification_outbox", tables)
            self.assertIn("item_translations", tables)
            self.assertIn("translation_usage", tables)
            self.assertIn("item_thumbnails", tables)


class ThumbnailTests(unittest.TestCase):
    def test_article_thumbnail_is_discovered_cached_and_missing_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "thumbnail.db"
            cache_root = root / "cache"
            initialize(path)
            now = utc_now()
            with transaction(path) as connection:
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, first_seen_at, last_seen_at,
                        topics_json, event_type
                    ) VALUES (1, 'with-image', 'NVIDIA chip news', 'https://example.com/a',
                              '', ?, ?, '[\"AI产业链\"]', '一般动态')
                    """,
                    (now, now),
                )
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, first_seen_at, last_seen_at,
                        topics_json, event_type
                    ) VALUES (2, 'without-image', 'Gold market news', 'https://example.com/b',
                              '', ?, ?, '[\"黄金\"]', '价格/市场')
                    """,
                    (now, now),
                )
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, first_seen_at, last_seen_at,
                        topics_json, event_type
                    ) VALUES (3, 'no-original', 'News with no publisher image', 'https://example.com/c',
                              '', ?, ?, '[]', '一般动态')
                    """,
                    (now, now),
                )
                register_thumbnail_candidate(connection, 1, "https://images.example.com/chip.png")

            calls: list[str] = []

            def fake_fetcher(url: str) -> DownloadedImage:
                calls.append(url)
                return DownloadedImage(b"\x89PNG\r\n\x1a\nthumbnail", "image/png")

            def fake_discoverer(url: str, feed_urls: list[str]) -> str | None:
                self.assertEqual(feed_urls, [])
                return "https://images.example.com/gold.png" if url.endswith("/b") else None

            first = get_thumbnail(1, path=path, cache_root=cache_root, fetcher=fake_fetcher)
            second = get_thumbnail(1, path=path, cache_root=cache_root, fetcher=fake_fetcher)
            discovered = get_thumbnail(
                2, path=path, cache_root=cache_root, fetcher=fake_fetcher, discoverer=fake_discoverer
            )
            missing = get_thumbnail(
                3, path=path, cache_root=cache_root, fetcher=fake_fetcher, discoverer=fake_discoverer
            )

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNotNone(discovered)
            self.assertIsNotNone(missing)
            self.assertEqual(first.kind, "article")
            self.assertEqual(second.kind, "article")
            self.assertEqual(discovered.kind, "article")
            self.assertEqual(missing.kind, "no-original")
            self.assertEqual(missing.mime_type, "image/svg+xml")
            self.assertIn("暂无新闻原图", missing.content.decode("utf-8"))
            self.assertEqual(
                calls,
                ["https://images.example.com/chip.png", "https://images.example.com/gold.png"],
            )

    def test_publisher_metadata_and_google_news_preview_are_extracted(self) -> None:
        html = """
        <html><head>
        <meta property="og:image" content="/images/article-cover.webp">
        <script type="application/ld+json">
        {"@type":"NewsArticle","image":{"url":"https://cdn.example.com/cover.jpg"}}
        </script></head></html>
        """
        candidates = _article_image_candidates(html, "https://publisher.example.com/news/story")
        self.assertEqual(candidates[0], "https://publisher.example.com/images/article-cover.webp")
        self.assertIn("https://cdn.example.com/cover.jpg", candidates)

        article_id = "CBMi-test-story"
        google_html = f"""
        <c-wiz jsdata="oM6qxc;{article_id};1"><figure><img
          srcset="/api/attachments/preview-w200-h112-p-df 1x,
                  /api/attachments/preview-w400-h224-p-df 2x"></figure></c-wiz>
        <c-wiz jsdata="oM6qxc;CBMi-next-story;2"></c-wiz>
        """
        previews = _extract_google_news_images(google_html)
        self.assertEqual(
            previews[article_id],
            "https://news.google.com/api/attachments/preview-w400-h224-p-df",
        )


class TranslationTests(unittest.TestCase):
    def test_translation_detection_and_utf8_limit(self) -> None:
        self.assertTrue(needs_translation("Gold prices rise before the Fed decision"))
        self.assertFalse(needs_translation("黄金价格上涨，市场等待美联储决定"))
        self.assertLessEqual(len(utf8_prefix("黄金" * 400).encode("utf-8")), 480)

    def test_translation_is_cached_in_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "translation.db"
            initialize(path)
            now = utc_now()
            with transaction(path) as connection:
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, first_seen_at, last_seen_at
                    ) VALUES (1, 'english-item', 'Gold prices rise before the Fed decision',
                              'https://example.com/gold', '', ?, ?)
                    """,
                    (now, now),
                )
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, first_seen_at, last_seen_at
                    ) VALUES (2, 'chinese-item', '黄金价格上涨',
                              'https://example.com/china', '', ?, ?)
                    """,
                    (now, now),
                )

            provider = TranslationProvider(
                name="unit-test-translator",
                external=False,
                daily_limit=None,
                translate=lambda text: f"测试译文：{text}",
            )
            first = translate_items([1, 2], path=path, provider=provider)
            second = translate_items([1], path=path, provider=provider)

            self.assertEqual(first["translated_count"], 1)
            self.assertEqual(first["skipped_count"], 1)
            self.assertEqual(second["translated_count"], 0)
            self.assertEqual(second["cached_count"], 1)
            self.assertEqual(second["translations"]["1"], "测试译文：Gold prices rise before the Fed decision")


if __name__ == "__main__":
    unittest.main()
