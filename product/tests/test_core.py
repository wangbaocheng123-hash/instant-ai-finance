from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from instant_ai import thumbnails
from instant_ai.collectors import parse_feed
from instant_ai.database import DEFAULT_SOURCES, connect, initialize, seed_sources, transaction, utc_now
from instant_ai.launch import client_window_bounds, mobile_preview_window_bounds
from instant_ai.paths import STATIC_ROOT
from instant_ai.reader_translation import translate_reader_item
from instant_ai.rules import analyze, canonical_key, normalized_url
from instant_ai.retention import published_within_hard_limit, retention_preview, run_retention_cleanup
from instant_ai.thumbnails import (
    DownloadedImage,
    DownloadedHtml,
    _article_image_candidates,
    _extract_google_news_images,
    _google_news_image_index,
    invalidate_google_news_image_index,
    get_thumbnail,
    register_thumbnail_candidate,
)
from instant_ai.translation import (
    TranslationProvider,
    needs_translation,
    split_utf8_chunks,
    translate_items,
    utf8_prefix,
)


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

    def test_mobile_preview_window_is_phone_sized(self) -> None:
        width, height, left, top = mobile_preview_window_bounds()
        self.assertGreaterEqual(width, 400)
        self.assertLessEqual(width, 460)
        self.assertGreaterEqual(height, 620)
        self.assertLessEqual(height, 900)
        self.assertGreaterEqual(left, 0)
        self.assertGreaterEqual(top, 0)
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

    def test_missing_feed_date_is_inferred_only_from_a_trailing_date(self) -> None:
        body = b"""<?xml version='1.0' encoding='UTF-8'?>
        <rss version='2.0'><channel><item>
          <title>Archived official release 2017/12/25</title>
          <link>https://example.com/archive</link><guid>archive-1</guid>
          <description>Historical entry 2017/12/25</description>
        </item></channel></rss>"""
        entry = parse_feed(body)[0]
        self.assertEqual(entry.published_at, "2017-12-25T00:00:00+00:00")


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
            self.assertEqual(version, "9")
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
            self.assertIn("reader_translations", tables)
            self.assertIn("translation_usage", tables)
            self.assertIn("item_thumbnails", tables)
            self.assertIn("watch_events", tables)
            self.assertIn("watch_event_matches", tables)
            self.assertIn("watch_sync_state", tables)


class RetentionTests(unittest.TestCase):
    def test_old_items_and_orphan_files_are_removed_without_a_permanent_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "retention.db"
            raw_root = root / "raw"
            cache_root = root / "cache"
            evidence_root = root / "evidence"
            backups_root = root / "backups"
            for folder in (raw_root, cache_root / "thumbnails", evidence_root / "runs", backups_root):
                folder.mkdir(parents=True, exist_ok=True)
            initialize(path)
            seed_sources(path)
            current = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
            recent = (current - timedelta(hours=2)).isoformat()
            ordinary_old = (current - timedelta(days=4)).isoformat()
            critical_old = (current - timedelta(days=8)).isoformat()
            raw_old = raw_root / "old.xml"
            raw_recent = raw_root / "recent.xml"
            raw_old.write_text("old", encoding="utf-8")
            raw_recent.write_text("recent", encoding="utf-8")

            with transaction(path) as connection:
                source_id = connection.execute("SELECT id FROM sources ORDER BY id LIMIT 1").fetchone()[0]
                for item_id, stamp, score in ((1, ordinary_old, 50), (2, critical_old, 95), (3, recent, 50)):
                    connection.execute(
                        """
                        INSERT INTO items(
                            id, canonical_key, title, url, first_seen_at, last_seen_at,
                            published_at, importance_score
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (item_id, f"item-{item_id}", f"Item {item_id}", f"https://example.com/{item_id}", stamp, stamp, stamp, score),
                    )
                    raw_path = raw_recent if item_id == 3 else raw_old
                    evidence_id = f"evidence-{item_id}"
                    connection.execute(
                        """
                        INSERT INTO evidence(
                            id, source_id, url, title, fetched_at, content_hash,
                            raw_path, mime_type, http_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'application/xml', 200)
                        """,
                        (evidence_id, source_id, f"https://example.com/{item_id}", f"Item {item_id}", stamp, evidence_id, str(raw_path)),
                    )
                    connection.execute(
                        "INSERT INTO item_evidence(item_id, evidence_id) VALUES (?, ?)",
                        (item_id, evidence_id),
                    )
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, first_seen_at, last_seen_at,
                        published_at, importance_score
                    ) VALUES (4, 'embedded-old', 'Archived release 2017/12/25',
                              'https://example.com/4', ?, ?, NULL, 95)
                    """,
                    (recent, recent),
                )

            preview = retention_preview(path=path, now=current)
            self.assertEqual(preview["would_remove"]["items"], 2)
            result = run_retention_cleanup(
                path=path,
                raw_root=raw_root,
                cache_root=cache_root,
                evidence_root=evidence_root,
                backups_root=backups_root,
                now=current,
            )
            with connect(path) as connection:
                remaining = [row[0] for row in connection.execute("SELECT id FROM items ORDER BY id")]
                evidence_count = connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            self.assertEqual(remaining, [3])
            self.assertEqual(evidence_count, 1)
            self.assertEqual(result["removed"]["items"], 3)
            self.assertEqual(result["removed"]["corrected_embedded_dates"], 1)
            self.assertFalse(raw_old.exists())
            self.assertTrue(raw_recent.exists())

    def test_ingestion_rejects_items_beyond_the_absolute_window(self) -> None:
        current = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
        self.assertTrue(published_within_hard_limit((current - timedelta(days=6)).isoformat(), current))
        self.assertFalse(published_within_hard_limit((current - timedelta(days=8)).isoformat(), current))


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

    def test_google_preview_cache_is_refreshed_for_new_collection_items(self) -> None:
        feed_url = (
            "https://news.google.com/rss/search?q=markets"
            "&hl=en-US&gl=US&ceid=US:en"
        )
        first_html = """
        <c-wiz jsdata="oM6qxc;first-story;1"><img
          src="/api/attachments/first-w400-h224-p-df"></c-wiz>
        """
        second_html = """
        <c-wiz jsdata="oM6qxc;second-story;1"><img
          src="/api/attachments/second-w400-h224-p-df"></c-wiz>
        """
        thumbnails.GOOGLE_INDEX_CACHE.clear()
        with patch.object(
            thumbnails,
            "_download_html",
            side_effect=[
                DownloadedHtml(first_html, "https://news.google.com/search?q=markets"),
                DownloadedHtml(second_html, "https://news.google.com/search?q=markets"),
            ],
        ) as downloader:
            first = _google_news_image_index(feed_url)
            cached = _google_news_image_index(feed_url)
            invalidate_google_news_image_index(feed_url)
            refreshed = _google_news_image_index(feed_url)

        self.assertIn("first-story", first)
        self.assertEqual(first, cached)
        self.assertIn("second-story", refreshed)
        self.assertEqual(downloader.call_count, 2)
        self.assertEqual(thumbnails.GOOGLE_INDEX_TTL, timedelta(minutes=5))
        self.assertEqual(thumbnails.FAILED_RETRY_AFTER, timedelta(minutes=5))
        thumbnails.GOOGLE_INDEX_CACHE.clear()


class TranslationTests(unittest.TestCase):
    def test_translation_detection_and_utf8_limit(self) -> None:
        self.assertTrue(needs_translation("Gold prices rise before the Fed decision"))
        self.assertFalse(needs_translation("黄金价格上涨，市场等待美联储决定"))
        self.assertLessEqual(len(utf8_prefix("黄金" * 400).encode("utf-8")), 480)
        chunks = split_utf8_chunks("Markets moved after the Fed decision. " * 80)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 480 for chunk in chunks))

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


class ReaderTranslationTests(unittest.TestCase):
    def test_feed_summary_is_translated_and_cached_without_fetching_article(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reader.db"
            initialize(path)
            now = utc_now()
            with transaction(path) as connection:
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, first_seen_at, last_seen_at
                    ) VALUES (1, 'reader-item', 'Global markets advance',
                              'https://publisher.example.com/story',
                              'Markets advanced after the policy signal.', ?, ?)
                    """,
                    (now, now),
                )

            provider = TranslationProvider(
                name="unit-test-reader",
                external=False,
                daily_limit=None,
                translate=lambda text: f"中文译文：{text}",
            )
            with patch("urllib.request.urlopen", side_effect=AssertionError("article fetch is forbidden")) as urlopen:
                first = translate_reader_item(1, path=path, provider=provider)
                second = translate_reader_item(1, path=path, provider=provider)

            self.assertTrue(first["ok"])
            self.assertEqual(first["source_kind"], "summary")
            self.assertEqual(first["original_excerpt"], "Markets advanced after the policy signal.")
            self.assertIn("中文译文", first["translated_text"])
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            urlopen.assert_not_called()

            with transaction(path) as connection:
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, first_seen_at, last_seen_at
                    ) VALUES (2, 'reader-summary-item', 'Central bank policy outlook',
                              'https://publisher.example.com/restricted',
                              'Central bank officials discussed inflation, rates, and the global economic outlook for investors.', ?, ?)
                    """,
                    (now, now),
                )

            fallback = translate_reader_item(2, path=path, provider=provider)
            self.assertTrue(fallback["ok"])
            self.assertEqual(fallback["source_kind"], "summary")
            self.assertIn("中文译文", fallback["translated_text"])

            with transaction(path) as connection:
                connection.execute("DELETE FROM items WHERE id=1")
            with connect(path) as connection:
                cached_count = connection.execute(
                    "SELECT COUNT(*) FROM reader_translations WHERE item_id=1"
                ).fetchone()[0]
            self.assertEqual(cached_count, 0)


class MobileShellTests(unittest.TestCase):
    def test_mobile_shell_is_installable_and_keeps_api_online_only(self) -> None:
        index = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        app = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
        worker = (STATIC_ROOT / "sw.js").read_text(encoding="utf-8")
        manifest = json.loads((STATIC_ROOT / "manifest.webmanifest").read_text(encoding="utf-8"))

        self.assertIn("manifest.webmanifest", index)
        self.assertIn("mobile-dock", styles)
        self.assertIn("header-tools", styles)
        self.assertIn(".finance-panel[hidden]", styles)
        self.assertIn("当前频道内容", app)
        self.assertIn("aria-current", app)
        self.assertIn('behavior:"auto"', app)
        self.assertIn("即时热点", app)
        self.assertIn("临时置顶", app)
        self.assertIn("浏览器翻译原文", app)
        self.assertIn("googlechromes://", app)
        self.assertIn("googlechrome://", app)
        self.assertIn("普通浏览器备用打开", app)
        self.assertIn("中文摘要（备用）", app)
        self.assertIn("重点事件关注", app)
        self.assertIn("watch-events", app)
        self.assertIn("模型先生", app)
        self.assertIn("model-mr", app)
        self.assertIn("主人账户登录", app)
        self.assertIn("30 天", app)
        self.assertIn("显示密码", app)
        self.assertIn('autocapitalize="none"', app)
        self.assertIn("watch-pipeline", app)
        self.assertIn("送达罗盘", app)
        self.assertIn("已发现", app)
        self.assertIn("repeat(7,1fr)", styles.replace(" ", ""))
        self.assertNotIn("搜索公司、人物、商品或事件", app)
        self.assertNotIn("searchInput", app)
        self.assertNotIn("pulse-board", app)
        self.assertNotIn("全球市场中心", app)
        self.assertNotIn("data-query", app)
        self.assertNotIn("terminal-search", styles)
        self.assertNotIn("search-results", styles)
        self.assertNotIn("正在读取公开正文", app)
        self.assertIn('cache:"no-store"', app)
        self.assertIn("visibilitychange", app)
        self.assertIn("pageshow", app)
        self.assertIn("online", app)
        self.assertNotIn("全球热点", app)
        self.assertNotIn("hotspotTrack", app)
        self.assertEqual(manifest["display"], "browser")
        self.assertEqual(manifest["orientation"], "portrait-primary")
        self.assertIn("url.pathname.startsWith('/api/')", worker)
        self.assertIn("fetch(request)", worker)
        self.assertIn("instant-ai-shell-v0.13.1", worker)


if __name__ == "__main__":
    unittest.main()
