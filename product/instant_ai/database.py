from __future__ import annotations

import json
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import quote_plus

from .paths import BACKUPS_ROOT, CACHE_ROOT, DATABASE_PATH, EVIDENCE_ROOT, ensure_layout


SCHEMA_VERSION = 10


class ClosingConnection(sqlite3.Connection):
    """A sqlite context manager that also releases the Windows file handle."""

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _google_news(query: str, *, chinese: bool = False) -> str:
    locale = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans" if chinese else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&{locale}"

DEFAULT_SOURCES = (
    {
        "key": "zijin-news",
        "name": "紫金矿业官方新闻",
        "kind": "html_links",
        "url": "https://www.zjky.cn/news/news_list.jsp?esgkey=21194",
        "trust_level": 5,
        "topic_hints": ["紫金矿业", "黄金", "铜", "有色金属"],
        "config": {
            "same_domain": True,
            "url_contains": ["/news/"],
            "exclude_url_contains": ["news_list.jsp"],
            "min_title_length": 8,
            "max_entries": 40,
        },
    },
    {
        "key": "federal-reserve-press",
        "name": "美联储官方新闻",
        "kind": "rss",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "trust_level": 5,
        "topic_hints": ["宏观政策"],
        "config": {"max_entries": 40},
    },
    {
        "key": "eia-today-in-energy",
        "name": "美国能源信息署 Today in Energy",
        "kind": "rss",
        "url": "https://www.eia.gov/rss/todayinenergy.xml",
        "trust_level": 5,
        "topic_hints": ["宏观政策"],
        "config": {"max_entries": 40},
    },
    {
        "key": "eia-press",
        "name": "美国能源信息署新闻稿",
        "kind": "rss",
        "url": "https://www.eia.gov/rss/press_rss.xml",
        "trust_level": 5,
        "topic_hints": ["宏观政策"],
        "config": {"max_entries": 40},
    },
    {
        "key": "openai-news",
        "name": "OpenAI 官方新闻",
        "kind": "rss",
        "url": "https://openai.com/news/rss.xml",
        "trust_level": 5,
        "topic_hints": ["AI产业链"],
        "config": {"max_entries": 40},
    },
    {
        "key": "nvidia-blog",
        "name": "NVIDIA 官方博客",
        "kind": "rss",
        "url": "https://blogs.nvidia.com/feed/",
        "trust_level": 5,
        "topic_hints": ["AI产业链", "华尔街"],
        "config": {"max_entries": 40},
    },
    {
        "key": "google-company-news",
        "name": "Google 官方博客",
        "kind": "rss",
        "url": "https://blog.google/rss/",
        "trust_level": 5,
        "topic_hints": ["AI产业链", "华尔街"],
        "config": {"max_entries": 40},
    },
    {
        "key": "apple-newsroom",
        "name": "Apple 官方新闻室",
        "kind": "rss",
        "url": "https://www.apple.com/newsroom/rss-feed.rss",
        "trust_level": 5,
        "topic_hints": ["AI产业链", "华尔街"],
        "config": {"max_entries": 40},
    },
    {
        "key": "microsoft-company-news",
        "name": "Microsoft 官方博客",
        "kind": "rss",
        "url": "https://blogs.microsoft.com/feed/",
        "trust_level": 5,
        "topic_hints": ["AI产业链", "华尔街"],
        "config": {"max_entries": 40},
    },
    {
        "key": "sec-press-releases",
        "name": "美国证监会 SEC 新闻稿",
        "kind": "rss",
        "url": "https://www.sec.gov/news/pressreleases.rss",
        "trust_level": 5,
        "topic_hints": ["华尔街", "宏观政策"],
        "config": {"max_entries": 50},
    },
    {
        "key": "global-financial-wire",
        "name": "全球财经媒体发现（Reuters/Bloomberg/FT/WSJ）",
        "kind": "rss",
        "url": _google_news('(site:reuters.com OR site:bloomberg.com OR site:ft.com OR site:wsj.com) (markets OR economy OR stocks OR commodities) when:2d'),
        "trust_level": 3,
        "topic_hints": ["全球财经", "华尔街"],
        "config": {"max_entries": 80, "discovery_only": True},
    },
    {
        "key": "wall-street-wire",
        "name": "华尔街即时资讯发现",
        "kind": "rss",
        "url": _google_news('(Wall Street OR Nasdaq OR S&P 500 OR Dow Jones OR US stocks) when:2d'),
        "trust_level": 3,
        "topic_hints": ["全球财经", "华尔街"],
        "config": {"max_entries": 70, "discovery_only": True},
    },
    {
        "key": "global-bank-research",
        "name": "全球投行公开观点发现",
        "kind": "rss",
        "url": _google_news('(site:goldmansachs.com OR site:morganstanley.com OR site:jpmorgan.com OR site:blackrock.com OR site:ubs.com) (insights OR outlook OR research) when:14d'),
        "trust_level": 3,
        "topic_hints": ["华尔街", "投行观点"],
        "config": {"max_entries": 60, "discovery_only": True},
    },
    {
        "key": "china-finance-wire",
        "name": "中国财经资讯发现",
        "kind": "rss",
        "url": _google_news('(A股 OR 港股 OR 人民银行 OR 中国经济 OR 上交所 OR 深交所 OR 港交所 OR 财新 OR 第一财经) when:2d', chinese=True),
        "trust_level": 3,
        "topic_hints": ["全球财经", "中国财经"],
        "config": {"max_entries": 80, "discovery_only": True},
    },
    {
        "key": "cls-official-news",
        "name": "财联社官网公开新闻发现",
        "kind": "rss",
        "url": _google_news("site:cls.cn when:2d", chinese=True),
        "trust_level": 3,
        "topic_hints": ["中国财经"],
        "config": {
            "max_entries": 100,
            "discovery_only": True,
            "title_link_only": True,
            "publisher": "财联社",
            "rights_scope": "title_date_link_only",
        },
    },
    {
        "key": "cls-wechat-public-index",
        "name": "财联社公众号公开文章发现",
        "kind": "wechat_public_index",
        "url": "https://qnmlgb.tech/authors/5cf63404497ff42829443b22",
        "trust_level": 2,
        "topic_hints": ["中国财经"],
        "config": {
            "max_entries": 30,
            "discovery_only": True,
            "title_link_only": True,
            "expected_account": "财联社",
            "wechat_id": "cailianpress",
            "wechat_biz": "Mzg5MzEyNzEwNQ==",
            "index_provider": "瓦斯阅读",
            "rights_scope": "title_date_link_only",
        },
    },
    {
        "key": "asia-markets-wire",
        "name": "亚洲市场资讯发现",
        "kind": "rss",
        "url": _google_news('(Nikkei OR TOPIX OR KOSPI OR SGX OR Asian markets OR Bank of Japan OR India stocks) when:2d'),
        "trust_level": 3,
        "topic_hints": ["全球财经", "亚洲市场"],
        "config": {"max_entries": 70, "discovery_only": True},
    },
    {
        "key": "gold-mining-wire",
        "name": "黄金与全球矿业资讯发现",
        "kind": "rss",
        "url": _google_news('(gold OR bullion OR copper OR mining OR Zijin OR gold mine OR copper mine) (Reuters OR Bloomberg OR FT OR Mining.com) when:3d'),
        "trust_level": 3,
        "topic_hints": ["全球财经", "黄金", "铜/有色"],
        "config": {"max_entries": 80, "discovery_only": True},
    },
    {
        "key": "market-geopolitics-wire",
        "name": "战争、制裁与供应链市场影响",
        "kind": "rss",
        "url": _google_news('(war OR conflict OR sanctions OR Red Sea OR Hormuz OR shipping disruption) (markets OR oil OR gold OR stocks OR supply chain) when:2d'),
        "trust_level": 3,
        "topic_hints": ["全球财经", "战争/地缘", "宏观政策"],
        "config": {"max_entries": 80, "discovery_only": True},
    },
    {
        "key": "ai-big-tech-wire",
        "name": "AI、芯片与大型科技资讯发现",
        "kind": "rss",
        "url": _google_news('(NVIDIA OR Google OR Apple OR Microsoft OR Amazon OR Meta OR TSMC OR ASML OR AMD OR AI chips OR semiconductors) when:2d'),
        "trust_level": 3,
        "topic_hints": ["全球财经", "华尔街", "AI产业链"],
        "config": {"max_entries": 90, "discovery_only": True},
    },
    {
        "key": "ai-venture-wire",
        "name": "AI 创业融资与并购资讯发现",
        "kind": "rss",
        "url": _google_news('(AI startup OR artificial intelligence funding OR semiconductor acquisition OR AI venture capital) when:7d'),
        "trust_level": 3,
        "topic_hints": ["AI产业链", "创业融资"],
        "config": {"max_entries": 60, "discovery_only": True},
    },
    {
        "key": "nasdaq-investor-education",
        "name": "Nasdaq 投资与市场知识发现",
        "kind": "rss",
        "url": _google_news('site:nasdaq.com/articles (investing OR investor education OR market structure OR stocks) when:14d'),
        "trust_level": 3,
        "topic_hints": ["华尔街", "财经知识"],
        "config": {"max_entries": 50, "discovery_only": True},
    },
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    ensure_layout()
    target = str(path or DATABASE_PATH)
    connection = sqlite3.connect(
        target,
        timeout=30,
        check_same_thread=False,
        factory=ClosingConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def transaction(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(path: Path | str | None = None) -> None:
    with transaction(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                trust_level INTEGER NOT NULL DEFAULT 3,
                topic_hints_json TEXT NOT NULL DEFAULT '[]',
                config_json TEXT NOT NULL DEFAULT '{}',
                etag TEXT,
                last_modified TEXT,
                last_success_at TEXT,
                last_error TEXT,
                last_item_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collection_runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                source_count INTEGER NOT NULL DEFAULT 0,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                importance_score INTEGER NOT NULL DEFAULT 0,
                trust_level INTEGER NOT NULL DEFAULT 1,
                topics_json TEXT NOT NULL DEFAULT '[]',
                entities_json TEXT NOT NULL DEFAULT '[]',
                event_type TEXT NOT NULL DEFAULT '一般动态',
                source_count INTEGER NOT NULL DEFAULT 1,
                is_saved INTEGER NOT NULL DEFAULT 0,
                is_read INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id),
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
            );

            CREATE TABLE IF NOT EXISTS item_evidence (
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
                PRIMARY KEY (item_id, evidence_id)
            );

            CREATE TABLE IF NOT EXISTS ai_jobs (
                id INTEGER PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                prompt_version TEXT NOT NULL DEFAULT 'evidence-v1',
                input_json TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notification_outbox (
                id INTEGER PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                channel TEXT NOT NULL DEFAULT 'in_app',
                status TEXT NOT NULL DEFAULT 'pending',
                reason_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                dismissed_at TEXT,
                UNIQUE(item_id, channel)
            );

            CREATE TABLE IF NOT EXISTS item_translations (
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                target_language TEXT NOT NULL DEFAULT 'zh-CN',
                original_title TEXT NOT NULL,
                translated_title TEXT NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (item_id, target_language)
            );

            CREATE TABLE IF NOT EXISTS reader_translations (
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                target_language TEXT NOT NULL DEFAULT 'zh-CN',
                item_fingerprint TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                original_excerpt TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                provider TEXT NOT NULL,
                source_truncated INTEGER NOT NULL DEFAULT 0,
                translation_partial INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (item_id, target_language)
            );

            CREATE TABLE IF NOT EXISTS translation_usage (
                usage_date TEXT NOT NULL,
                provider TEXT NOT NULL,
                character_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (usage_date, provider)
            );

            CREATE TABLE IF NOT EXISTS item_thumbnails (
                item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
                source_url TEXT NOT NULL,
                local_path TEXT,
                mime_type TEXT,
                byte_size INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                checked_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watch_events (
                event_key TEXT PRIMARY KEY,
                scope TEXT NOT NULL CHECK(scope IN ('home', 'zijin')),
                source_kind TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'event',
                importance INTEGER NOT NULL DEFAULT 3,
                event_status TEXT NOT NULL DEFAULT 'planned',
                note TEXT NOT NULL DEFAULT '',
                sources_json TEXT NOT NULL DEFAULT '[]',
                monitoring_json TEXT NOT NULL DEFAULT '{}',
                analysis_feedback_json TEXT NOT NULL DEFAULT '{}',
                monitor_terms_json TEXT NOT NULL DEFAULT '[]',
                source_updated_at TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                last_synced_at TEXT NOT NULL,
                last_checked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS watch_event_matches (
                event_key TEXT NOT NULL REFERENCES watch_events(event_key) ON DELETE CASCADE,
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                match_score INTEGER NOT NULL,
                matched_terms_json TEXT NOT NULL DEFAULT '[]',
                matched_at TEXT NOT NULL,
                PRIMARY KEY (event_key, item_id)
            );

            CREATE TABLE IF NOT EXISTS watch_event_channels (
                event_key TEXT NOT NULL REFERENCES watch_events(event_key) ON DELETE CASCADE,
                channel_key TEXT NOT NULL,
                publisher TEXT NOT NULL,
                name TEXT NOT NULL,
                channel_type TEXT NOT NULL DEFAULT 'html',
                channel_role TEXT NOT NULL DEFAULT 'official-release',
                url TEXT NOT NULL,
                verified_at TEXT NOT NULL DEFAULT '',
                expected_terms_json TEXT NOT NULL DEFAULT '[]',
                time_status TEXT NOT NULL DEFAULT 'unverified',
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_checked_at TEXT,
                last_success_at TEXT,
                last_changed_at TEXT,
                next_check_at TEXT,
                http_status INTEGER,
                etag TEXT,
                last_modified TEXT,
                content_hash TEXT,
                signal_found INTEGER NOT NULL DEFAULT 0,
                delivery_token TEXT NOT NULL DEFAULT '',
                last_error TEXT,
                PRIMARY KEY (event_key, channel_key)
            );

            CREATE TABLE IF NOT EXISTS watch_event_signals (
                signal_id TEXT PRIMARY KEY,
                event_key TEXT NOT NULL REFERENCES watch_events(event_key) ON DELETE CASCADE,
                channel_key TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                matched_terms_json TEXT NOT NULL DEFAULT '[]',
                evidence_excerpt TEXT NOT NULL DEFAULT '',
                detected_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'delivered', 'failed')),
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                delivered_at TEXT,
                compass_signal_id TEXT NOT NULL DEFAULT '',
                compass_signal_status TEXT NOT NULL DEFAULT '',
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(event_key, channel_key, evidence_hash),
                FOREIGN KEY (event_key, channel_key)
                    REFERENCES watch_event_channels(event_key, channel_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS watch_sync_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                source_url TEXT NOT NULL,
                last_attempt_at TEXT,
                last_success_at TEXT,
                last_error TEXT,
                source_revision INTEGER,
                event_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_items_activity ON items(last_seen_at DESC, importance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_items_score ON items(importance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_item_evidence_item ON item_evidence(item_id, evidence_id);
            CREATE INDEX IF NOT EXISTS idx_item_evidence_evidence ON item_evidence(evidence_id, item_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id, fetched_at DESC);
            CREATE INDEX IF NOT EXISTS idx_runs_started ON collection_runs(started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_jobs_item ON ai_jobs(item_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_outbox_status ON notification_outbox(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_item_translations_provider ON item_translations(provider, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_reader_translations_provider ON reader_translations(provider, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_item_thumbnails_status ON item_thumbnails(status, checked_at DESC);
            CREATE INDEX IF NOT EXISTS idx_watch_events_date ON watch_events(is_active, event_date, event_time);
            CREATE INDEX IF NOT EXISTS idx_watch_matches_event ON watch_event_matches(event_key, matched_at DESC);
            CREATE INDEX IF NOT EXISTS idx_watch_matches_item ON watch_event_matches(item_id, matched_at DESC);
            CREATE INDEX IF NOT EXISTS idx_watch_channels_due ON watch_event_channels(is_active, next_check_at, url);
            CREATE INDEX IF NOT EXISTS idx_watch_signals_status ON watch_event_signals(status, updated_at DESC);
            """
        )
        watch_event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(watch_events)").fetchall()
        }
        if "monitoring_json" not in watch_event_columns:
            connection.execute("ALTER TABLE watch_events ADD COLUMN monitoring_json TEXT NOT NULL DEFAULT '{}'")
        if "analysis_feedback_json" not in watch_event_columns:
            connection.execute("ALTER TABLE watch_events ADD COLUMN analysis_feedback_json TEXT NOT NULL DEFAULT '{}'")
        watch_channel_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(watch_event_channels)").fetchall()
        }
        if "delivery_token" not in watch_channel_columns:
            connection.execute("ALTER TABLE watch_event_channels ADD COLUMN delivery_token TEXT NOT NULL DEFAULT ''")
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

        try:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                    title, summary, topics,
                    content='items', content_rowid='id', tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
                    INSERT INTO items_fts(rowid, title, summary, topics)
                    VALUES (new.id, new.title, new.summary, new.topics_json);
                END;
                CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, title, summary, topics)
                    VALUES ('delete', old.id, old.title, old.summary, old.topics_json);
                END;
                CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, title, summary, topics)
                    VALUES ('delete', old.id, old.title, old.summary, old.topics_json);
                    INSERT INTO items_fts(rowid, title, summary, topics)
                    VALUES (new.id, new.title, new.summary, new.topics_json);
                END;
                """
            )
        except sqlite3.OperationalError:
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('fts5', 'unavailable') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )


def seed_sources(path: Path | str | None = None) -> None:
    now = utc_now()
    with transaction(path) as connection:
        for source in DEFAULT_SOURCES:
            connection.execute(
                """
                INSERT INTO sources(
                    key, name, kind, url, enabled, trust_level,
                    topic_hints_json, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    name=excluded.name,
                    kind=excluded.kind,
                    url=excluded.url,
                    trust_level=excluded.trust_level,
                    topic_hints_json=excluded.topic_hints_json,
                    config_json=excluded.config_json,
                    updated_at=excluded.updated_at
                """,
                (
                    source["key"],
                    source["name"],
                    source["kind"],
                    source["url"],
                    source["trust_level"],
                    json.dumps(source["topic_hints"], ensure_ascii=False),
                    json.dumps(source["config"], ensure_ascii=False),
                    now,
                    now,
                ),
            )


def create_backup(force: bool = False) -> Path | None:
    ensure_layout()
    if not DATABASE_PATH.is_file():
        return None
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S") if force else datetime.now().strftime("%Y-%m-%d")
    target = BACKUPS_ROOT / f"instant_ai-{suffix}.db"
    if target.is_file():
        return target
    source = connect()
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".db.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    from .retention import prune_backups

    prune_backups()
    return target


def run_restore_drill(backup_path: Path | None = None) -> dict[str, object]:
    """Restore a backup into an isolated H-drive cache file and verify it."""

    ensure_layout()
    candidates = sorted(BACKUPS_ROOT.glob("instant_ai-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    source_path = backup_path or (candidates[0] if candidates else None)
    if source_path is None or not source_path.is_file():
        raise FileNotFoundError("No Instant AI database backup is available")

    expected_hash_path = source_path.with_suffix(".db.sha256")
    actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    expected_hash = ""
    if expected_hash_path.is_file():
        expected_hash = expected_hash_path.read_text(encoding="ascii").split()[0].lower()
    checksum_ok = not expected_hash or expected_hash == actual_hash.lower()
    if not checksum_ok:
        raise ValueError("Backup SHA-256 verification failed")

    drill_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    restored_path = CACHE_ROOT / f"restore-drill-{drill_id}.db"
    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(restored_path))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()

    verification = sqlite3.connect(str(restored_path))
    try:
        integrity = verification.execute("PRAGMA integrity_check").fetchone()[0]
        item_count = int(verification.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        source_count = int(verification.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
        schema_version = verification.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    finally:
        verification.close()

    report = {
        "drill_id": drill_id,
        "backup_path": str(source_path),
        "backup_sha256": actual_hash,
        "checksum_ok": checksum_ok,
        "integrity_check": integrity,
        "item_count": item_count,
        "source_count": source_count,
        "schema_version": schema_version,
        "verified_at": utc_now(),
    }
    report_root = EVIDENCE_ROOT / "restore-drills"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / f"restore-drill-{drill_id}.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for suffix in ("", "-wal", "-shm"):
        temporary = Path(f"{restored_path}{suffix}")
        if temporary.is_file():
            temporary.unlink()
    return report
