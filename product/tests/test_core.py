from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from instant_ai import thumbnails
from instant_ai.ai_provider import _evidence_packet
from instant_ai.collectors import (
    Entry,
    FetchResult,
    Source,
    collect_source,
    parse_bing_news_feed,
    parse_feed,
    parse_kb_research_today,
    parse_stockplus_breaking,
    parse_wechat_public_index,
)
from instant_ai.database import DEFAULT_SOURCES, connect, initialize, seed_sources, transaction, utc_now
from instant_ai.publishers import (
    UNKNOWN_PUBLISHER,
    publisher_from_title,
    resolve_publisher_identity,
)
from instant_ai.launch import client_window_bounds, mobile_preview_window_bounds
from instant_ai.paths import STATIC_ROOT
from instant_ai.reader_translation import translate_reader_item
from instant_ai.rules import analyze, canonical_key, normalized_url
from instant_ai.retention import published_within_hard_limit, retention_preview, run_retention_cleanup
from instant_ai.service import _upsert_entry, get_item, query_items
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

GOOGLE_NEWS_RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google News</title><item>
<title>Gold rises as markets assess rate outlook - Reuters</title>
<link>https://news.google.com/rss/articles/example</link><guid>google-item-1</guid>
<pubDate>Tue, 08 Sep 2026 00:00:00 GMT</pubDate>
<source url="https://www.reuters.com">Reuters</source>
</item></channel></rss>"""

WECHAT_PUBLIC_INDEX_SAMPLE = """<!doctype html><html><head><title>财联社 - 微信公众号</title></head><body>
<div class="ae"><a class="ae-container-2" href="/articles/article-one">
  <span>^__^</span><span>•</span><span>8 / 30</span>
  <div><span class="pretty">A股政策出现重要变化</span></div>
</a></div>
<div class="ae"><a href="/articles/ignored">普通链接</a></div>
<div class="ae"><a class="ae-container-2" href="/articles/article-two">
  <span>8 / 29</span><span class="pretty">上市公司发布半年报</span>
</a></div></body></html>""".encode("utf-8")

KB_RESEARCH_TODAY_SAMPLE = """<!doctype html><html lang="ko"><body>
<div class="ytb-tit"><span>2026년 9월 7일</span><span>LIVE</span></div>
<div id="ytb-cont"><p>
  <span><a href="https://www.youtube.com/watch?v=sample&amp;t=13s">00:13</a></span>
  <span>[KB리서치 모닝코멘트 0907]</span>
  <span><a href="https://www.youtube.com/watch?v=sample&amp;t=444s">07:24</a></span>
  <span>[반도체 - 내년 사상 초유의 공급 부족]</span>
</p></div></body></html>""".encode("utf-8")

STOCKPLUS_BREAKING_SAMPLE = json.dumps(
    {
        "data": {
            "cursor": "1788827171048",
            "breakingNews": [
                {
                    "id": 34125,
                    "title": "코스피, 0.72% 상승 출발..7,000선 회복",
                    "publishedAt": 1788827171048,
                    "summaries": ["不应保存的摘要"],
                }
            ],
            "hasNext": True,
        }
    },
    ensure_ascii=False,
).encode("utf-8")

BING_KOREAN_NEWS_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Bing News</title><item>
  <title>삼성·SK하이닉스 반도체 공급 전망</title>
  <link>http://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fwww.hankyung.com%2Farticle%2F202609078162H&amp;c=1</link>
  <guid>bing-result-1</guid><pubDate>Mon, 07 Sep 2026 09:46:00 GMT</pubDate>
  <description>不应保存的索引摘要</description>
</item><item>
  <title>Unrelated result must be dropped</title>
  <link>http://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fexample.com%2Farticle&amp;c=2</link>
  <guid>bing-result-2</guid><pubDate>Mon, 07 Sep 2026 09:45:00 GMT</pubDate>
</item></channel></rss>""".encode("utf-8")


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

    def test_korean_chip_headline_is_classified_without_relabeling_its_source(self) -> None:
        result = analyze(
            "삼성전자·SK하이닉스 메모리 재고 10일 미만 전망",
            "",
            4,
            ["全球财经", "亚洲市场", "投行观点"],
        )
        self.assertIn("AI产业链", result.topics)
        self.assertIn("三星电子", result.entities)
        self.assertIn("SK海力士", result.entities)
        self.assertEqual(result.event_type, "产量/库存")

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

    def test_google_news_feed_keeps_real_publisher_metadata(self) -> None:
        entry = parse_feed(GOOGLE_NEWS_RSS_SAMPLE)[0]
        self.assertEqual(entry.publisher, "Reuters")
        self.assertEqual(entry.publisher_url, "https://www.reuters.com")

    def test_collection_enriches_feed_publisher_before_evidence_storage(self) -> None:
        source = Source(
            1,
            "china-finance-wire",
            "中国财经资讯发现",
            "rss",
            "https://news.google.com/rss/search?q=china",
            3,
            ["中国财经"],
            {"discovery_only": True, "max_entries": 10},
        )
        response = FetchResult(200, "application/rss+xml", GOOGLE_NEWS_RSS_SAMPLE, None, None)
        with patch("instant_ai.collectors.fetch", return_value=response), patch(
            "instant_ai.collectors.store_raw", return_value=("feed-hash", "/tmp/feed.xml")
        ):
            _result, entries, _digest, _raw_path = collect_source(source)
        self.assertEqual(entries[0].publisher, "路透社")
        self.assertEqual(entries[0].publisher_url, "https://www.reuters.com")

    def test_publisher_resolution_never_treats_a_collection_channel_as_media(self) -> None:
        resolved = resolve_publisher_identity(
            article_url="https://news.google.com/rss/articles/example",
            title="Credit outlook is revised - Moody's",
            source_name="中国财经资讯发现",
            source_url="https://news.google.com/rss/search?q=china",
            source_config={"discovery_only": True},
        )
        self.assertEqual(resolved.name, "穆迪")
        self.assertNotEqual(resolved.name, "中国财经资讯发现")
        self.assertEqual(publisher_from_title("Markets rally - Reuters"), "路透社")
        self.assertEqual(publisher_from_title("Investment outlook - Morgan Stanley"), "摩根士丹利")

        unknown = resolve_publisher_identity(
            article_url="https://news.google.com/rss/articles/no-source",
            title="Headline without a publisher suffix",
            source_name="全球财经媒体发现",
            source_url="https://news.google.com/rss/search?q=markets",
            source_config={"discovery_only": True},
        )
        self.assertEqual(unknown.name, UNKNOWN_PUBLISHER)

    def test_unknown_original_site_uses_its_domain_as_the_publisher(self) -> None:
        resolved = resolve_publisher_identity(
            article_url="https://research.example.org/market/outlook",
            source_name="华尔街即时资讯发现",
            source_config={"discovery_only": True},
        )
        self.assertEqual(resolved.name, "research.example.org")

    def test_missing_feed_date_is_inferred_only_from_a_trailing_date(self) -> None:
        body = b"""<?xml version='1.0' encoding='UTF-8'?>
        <rss version='2.0'><channel><item>
          <title>Archived official release 2017/12/25</title>
          <link>https://example.com/archive</link><guid>archive-1</guid>
          <description>Historical entry 2017/12/25</description>
        </item></channel></rss>"""
        entry = parse_feed(body)[0]
        self.assertEqual(entry.published_at, "2017-12-25T00:00:00+00:00")

    def test_wechat_public_index_is_title_only_and_dated(self) -> None:
        source = Source(
            1,
            "cls-wechat-public-index",
            "财联社公众号公开文章发现",
            "wechat_public_index",
            "https://qnmlgb.tech/authors/example",
            2,
            ["中国财经"],
            {"expected_account": "财联社", "max_entries": 30},
        )
        entries = parse_wechat_public_index(
            source,
            WECHAT_PUBLIC_INDEX_SAMPLE,
            now=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        )
        self.assertEqual([entry.title for entry in entries], ["A股政策出现重要变化", "上市公司发布半年报"])
        self.assertEqual(entries[0].source_item_id, "article-one")
        self.assertEqual(entries[0].url, "https://qnmlgb.tech/articles/article-one")
        self.assertEqual(entries[0].summary, "")
        self.assertEqual(entries[0].published_at, "2026-08-29T16:00:00+00:00")

    def test_wechat_public_index_rejects_an_account_mismatch(self) -> None:
        source = Source(
            1,
            "cls-wechat-public-index",
            "财联社公众号公开文章发现",
            "wechat_public_index",
            "https://qnmlgb.tech/authors/example",
            2,
            ["中国财经"],
            {"expected_account": "另一个公众号"},
        )
        with self.assertRaisesRegex(ValueError, "account mismatch"):
            parse_wechat_public_index(source, WECHAT_PUBLIC_INDEX_SAMPLE)

    def test_wechat_public_index_rejects_an_identifier_mismatch(self) -> None:
        source = Source(
            1,
            "cls-wechat-public-index",
            "财联社公众号公开文章发现",
            "wechat_public_index",
            "https://qnmlgb.tech/authors/example",
            2,
            ["中国财经"],
            {"expected_account": "财联社", "wechat_biz": "wrong-biz"},
        )
        with self.assertRaisesRegex(ValueError, "identifier mismatch"):
            parse_wechat_public_index(source, WECHAT_PUBLIC_INDEX_SAMPLE)

    def test_kb_research_today_reads_dated_public_segments(self) -> None:
        source = Source(
            1,
            "kb-securities-research-today",
            "KB证券官方研究晨会（韩国）",
            "kb_research_today",
            "https://rc.kbsec.com/today/index.able",
            4,
            ["全球财经", "亚洲市场", "投行观点"],
            {"max_entries": 30, "title_link_only": True},
        )
        entries = parse_kb_research_today(source, KB_RESEARCH_TODAY_SAMPLE)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].published_at, "2026-09-06T15:00:00+00:00")
        self.assertEqual(entries[1].title, "KB证券晨会：반도체 - 내년 사상 초유의 공급 부족（07:24）")
        self.assertEqual(entries[1].url, "https://www.youtube.com/watch?v=sample&t=444s")
        self.assertEqual(entries[1].summary, "")

    def test_stockplus_breaking_keeps_only_public_title_date_and_link(self) -> None:
        source = Source(
            1,
            "stockplus-korea-newsroom",
            "Stockplus Newsroom 韩国快讯发现",
            "stockplus_breaking_json",
            "https://spn.stockplus.com/news/api/v2/breaking-news?limit=20&includeCrix=false",
            2,
            ["全球财经", "亚洲市场"],
            {"max_entries": 20, "title_link_only": True},
        )
        entries = parse_stockplus_breaking(source, STOCKPLUS_BREAKING_SAMPLE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].source_item_id, "stockplus-breaking-34125")
        self.assertEqual(entries[0].title, "코스피, 0.72% 상승 출발..7,000선 회복")
        self.assertEqual(entries[0].url, "https://newsroom.stockplus.com/breaking-news/34125")
        self.assertEqual(entries[0].published_at, "2026-09-08T00:26:11+00:00")
        self.assertEqual(entries[0].summary, "")

    def test_bing_korean_feed_unwraps_and_enforces_publisher_domain(self) -> None:
        source = Source(
            1,
            "hankyung-korea-finance",
            "韩国经济日报财经新闻发现",
            "bing_news_rss",
            "https://www.bing.com/news/search?q=site%3Ahankyung.com&format=rss&setlang=ko-kr",
            3,
            ["全球财经", "亚洲市场"],
            {
                "max_entries": 70,
                "allowed_domains": ["hankyung.com"],
                "required_title_keywords": ["반도체"],
                "title_link_only": True,
            },
        )
        entries = parse_bing_news_feed(source, BING_KOREAN_NEWS_SAMPLE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].url, "https://www.hankyung.com/article/202609078162H")
        self.assertEqual(entries[0].summary, "")


class DatabaseTests(unittest.TestCase):
    def test_schema_ten_evidence_table_migrates_without_replacing_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schema-ten.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE evidence (
                        id TEXT PRIMARY KEY,
                        source_id INTEGER NOT NULL,
                        source_item_id TEXT,
                        url TEXT NOT NULL,
                        title TEXT NOT NULL,
                        fetched_at TEXT NOT NULL,
                        published_at TEXT,
                        content_hash TEXT NOT NULL,
                        raw_path TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        http_status INTEGER NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO evidence(
                        id, source_id, url, title, fetched_at, content_hash,
                        raw_path, mime_type, http_status
                    ) VALUES ('kept', 1, 'https://example.com/story', 'Kept evidence',
                              '2026-09-08T00:00:00+00:00', 'hash', '/tmp/raw',
                              'application/rss+xml', 200)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            initialize(path)
            with connect(path) as migrated:
                columns = {
                    row[1] for row in migrated.execute("PRAGMA table_info(evidence)")
                }
                kept = migrated.execute(
                    "SELECT publisher_name, publisher_url FROM evidence WHERE id='kept'"
                ).fetchone()
            self.assertIn("publisher_name", columns)
            self.assertIn("publisher_url", columns)
            self.assertEqual(dict(kept), {"publisher_name": "", "publisher_url": ""})

    def test_cls_sources_are_scoped_to_china_and_title_metadata(self) -> None:
        sources = {source["key"]: source for source in DEFAULT_SOURCES}
        website = sources["cls-official-news"]
        wechat = sources["cls-wechat-public-index"]
        self.assertEqual(website["topic_hints"], ["中国财经"])
        self.assertEqual(wechat["topic_hints"], ["中国财经"])
        self.assertTrue(website["config"]["title_link_only"])
        self.assertTrue(wechat["config"]["title_link_only"])
        self.assertEqual(wechat["config"]["wechat_id"], "cailianpress")
        self.assertLess(wechat["trust_level"], website["trust_level"])

    def test_korean_sources_keep_research_and_media_roles_separate(self) -> None:
        sources = {source["key"]: source for source in DEFAULT_SOURCES}
        kb = sources["kb-securities-research-today"]
        hankyung = sources["hankyung-korea-finance"]
        sedaily = sources["seoul-economic-daily-korea"]
        stockplus = sources["stockplus-korea-newsroom"]
        self.assertEqual(kb["kind"], "kb_research_today")
        self.assertEqual(kb["config"]["evidence_role"], "broker_research_primary")
        self.assertTrue(kb["config"]["not_company_disclosure"])
        self.assertEqual(kb["trust_level"], 4)
        self.assertEqual(hankyung["trust_level"], 3)
        self.assertEqual(sedaily["trust_level"], 3)
        self.assertEqual(stockplus["trust_level"], 2)
        self.assertEqual(stockplus["kind"], "stockplus_breaking_json")
        self.assertEqual(stockplus["config"]["evidence_role"], "early_discovery_only")
        for source in (hankyung, sedaily):
            self.assertEqual(source["kind"], "bing_news_rss")
            self.assertIn("format=rss&setlang=ko-kr", source["url"])
            self.assertEqual(source["config"]["index_provider"], "Bing News")
            self.assertTrue(source["config"]["title_link_only"])
            self.assertTrue(source["config"]["discovery_only"])
        self.assertEqual(stockplus["config"]["rights_scope"], "title_date_link_only")
        self.assertTrue(stockplus["config"]["title_link_only"])
        self.assertTrue(stockplus["config"]["discovery_only"])

    def test_schema_and_source_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            initialize(path)
            seed_sources(path)
            with connect(path) as connection:
                source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
                version = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
            self.assertEqual(source_count, len(DEFAULT_SOURCES))
            self.assertEqual(version, "11")
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

    def test_existing_discovery_evidence_backfills_the_real_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publisher-backfill.db"
            initialize(path)
            seed_sources(path)
            now = utc_now()
            with transaction(path) as connection:
                source_id = connection.execute(
                    "SELECT id FROM sources WHERE key='china-finance-wire'"
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, first_seen_at, last_seen_at
                    ) VALUES (1, 'publisher-backfill', ?, ?, ?, ?)
                    """,
                    (
                        "China credit outlook changes - Moody's",
                        "https://news.google.com/rss/articles/legacy",
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO evidence(
                        id, source_id, url, title, fetched_at, content_hash,
                        raw_path, mime_type, http_status
                    ) VALUES ('legacy-evidence', ?, ?, ?, ?, 'legacy-hash',
                              '/tmp/legacy.xml', 'application/rss+xml', 200)
                    """,
                    (
                        source_id,
                        "https://news.google.com/rss/articles/legacy",
                        "China credit outlook changes - Moody's",
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO item_evidence(item_id, evidence_id) VALUES (1, 'legacy-evidence')"
                )

            seed_sources(path)
            with connect(path) as connection:
                evidence = connection.execute(
                    "SELECT publisher_name FROM evidence WHERE id='legacy-evidence'"
                ).fetchone()
            self.assertEqual(evidence["publisher_name"], "穆迪")

    def test_item_list_detail_and_ai_packet_expose_publisher_not_collection_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publisher-api.db"
            initialize(path)
            seed_sources(path)
            with transaction(path) as connection:
                row = connection.execute(
                    "SELECT * FROM sources WHERE key='china-finance-wire'"
                ).fetchone()
                source = Source(
                    id=row["id"],
                    key=row["key"],
                    name=row["name"],
                    kind=row["kind"],
                    url=row["url"],
                    trust_level=row["trust_level"],
                    topic_hints=json.loads(row["topic_hints_json"]),
                    config=json.loads(row["config_json"]),
                )
                _upsert_entry(
                    connection,
                    source,
                    Entry(
                        source_item_id="publisher-item",
                        title="China credit outlook changes - Moody's",
                        url="https://news.google.com/rss/articles/publisher-item",
                        summary="",
                        published_at=utc_now(),
                        publisher="穆迪",
                        publisher_url="https://www.moodys.com/",
                    ),
                    "feed-hash",
                    "/tmp/feed.xml",
                    "application/rss+xml",
                    200,
                )

            with patch("instant_ai.database.DATABASE_PATH", path):
                listing = query_items(limit=10)
                detail = get_item(listing[0]["id"])
                packet = _evidence_packet(listing[0]["id"])

            self.assertEqual(listing[0]["sources"], ["穆迪"])
            self.assertIsNotNone(detail)
            self.assertEqual(detail["sources"], ["穆迪"])
            self.assertEqual(detail["evidence"][0]["source_name"], "穆迪")
            self.assertEqual(
                detail["evidence"][0]["collection_source_name"],
                "中国财经资讯发现",
            )
            self.assertIsNotNone(packet)
            self.assertEqual(packet["evidence"][0]["source_name"], "穆迪")
            self.assertEqual(
                packet["evidence"][0]["collection_source_name"],
                "中国财经资讯发现",
            )


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
        self.assertIn("media-src 'self'", index)
        self.assertNotIn("media-src 'none'", index)
        self.assertIn("mobile-dock", styles)
        self.assertIn("header-tools", styles)
        self.assertIn(".finance-panel[hidden]", styles)
        self.assertIn("当前频道内容", app)
        self.assertIn("aria-current", app)
        self.assertIn('behavior:"auto"', app)
        self.assertIn("即时热点", app)
        self.assertIn("来源：", app)
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
        self.assertIn("播放本地视频", app)
        self.assertIn("视频原文", app)
        self.assertIn("豆包识别文字", app)
        self.assertIn("抖音原链接", app)
        self.assertIn("model-work-detail", styles)
        self.assertIn("继续向下滑自动加载", app)
        self.assertIn("IntersectionObserver", app)
        self.assertIn("model-work-loader", styles)
        self.assertIn("offset=", app)
        self.assertIn("主人账户登录", app)
        self.assertIn("30 天", app)
        self.assertIn("显示密码", app)
        self.assertIn('autocapitalize="none"', app)
        self.assertIn("watch-pipeline", app)
        self.assertIn("送达罗盘", app)
        self.assertIn("已发现", app)
        self.assertIn("repeat(8,minmax(44px,1fr))", styles.replace(" ", ""))
        self.assertIn("博主资料", app)
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
        self.assertIn("instant-ai-shell-v0.21.4", worker)


if __name__ == "__main__":
    unittest.main()
