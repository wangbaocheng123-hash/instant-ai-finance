from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .creator_paths import ensure_creator_directories
from .keyword_taxonomy import (
    KEYWORD_CATEGORIES,
    KEYWORD_SCHEMA_VERSION,
    flatten_keyword_categories,
    normalize_keyword,
    normalize_keyword_categories,
)
from .knowledge_index import (
    ensure_knowledge_schema,
    refresh_video_knowledge,
    sync_dirty_knowledge,
)
from .investment_thoughts import ensure_investment_thought_schema


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def to_json(value: Any) -> str:
    if value is None:
        return "{}"
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return {} if default is None else default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if default is None else default


CHINA_TZ = timezone(timedelta(hours=8))
CANONICAL_MEDIA_STEM = re.compile(r"^\d{8}_\d{4}_[A-Za-z0-9-]+$")


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.project_root = self.path.parent.parent if self.path.parent.name == "data" else self.path.parent
        configured_media_root = str(os.getenv("BLOGGER_AGENT_MEDIA_DIR", "") or "").strip()
        self.creator_data_root = (
            Path(configured_media_root).expanduser().resolve()
            if configured_media_root
            else self.project_root / "博主数据"
        )
        self.legacy_originals_dir = self.project_root / "视频原文"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_video_id TEXT NOT NULL,
                    author TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    cover_url TEXT NOT NULL DEFAULT '',
                    published_at TEXT,
                    discovered_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(source, source_video_id)
                );

                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    language TEXT NOT NULL DEFAULT 'zh',
                    text TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    confidence REAL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS video_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    asset_type TEXT NOT NULL,
                    storage_mode TEXT NOT NULL DEFAULT 'local_file',
                    original_name TEXT NOT NULL DEFAULT '',
                    local_path TEXT NOT NULL DEFAULT '',
                    remote_url TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    sha256 TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL DEFAULT 'stored',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    source TEXT NOT NULL DEFAULT 'manual',
                    source_comment_id TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    like_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    sentiment TEXT NOT NULL DEFAULT 'neutral',
                    risk_level TEXT NOT NULL DEFAULT 'normal',
                    published_at TEXT,
                    captured_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(video_id, source, source_comment_id)
                );

                CREATE TABLE IF NOT EXISTS video_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    note_type TEXT NOT NULL DEFAULT 'interpretation',
                    text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(video_id, note_type)
                );

                CREATE TABLE IF NOT EXISTS content_originals (
                    content_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
                    original_text TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS content_keyword_sets (
                    content_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
                    schema_version TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    categories_json TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS content_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    normalized_keyword TEXT NOT NULL,
                    ordinal INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(content_id, category, normalized_keyword)
                );

                CREATE TABLE IF NOT EXISTS video_titles (
                    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
                    ocr_title TEXT NOT NULL DEFAULT '',
                    manual_title TEXT NOT NULL DEFAULT '',
                    active_title TEXT NOT NULL DEFAULT '',
                    title_source TEXT NOT NULL DEFAULT 'none',
                    confidence REAL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    frame_timestamp REAL,
                    frame_path TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    insights_json TEXT NOT NULL DEFAULT '[]',
                    recommendations_json TEXT NOT NULL DEFAULT '[]',
                    risk_flags_json TEXT NOT NULL DEFAULT '[]',
                    score_json TEXT NOT NULL DEFAULT '{}',
                    raw_output TEXT NOT NULL DEFAULT '{}',
                    trace_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
                    rating INTEGER NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eval_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    video_id INTEGER REFERENCES videos(id) ON DELETE SET NULL,
                    expected_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eval_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    eval_case_id INTEGER REFERENCES eval_cases(id) ON DELETE SET NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_state (
                    source TEXT PRIMARY KEY,
                    cursor TEXT,
                    last_checked_at TEXT,
                    last_item_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS investment_mainlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    line_key TEXT NOT NULL UNIQUE,
                    number TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    nodes_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS investment_mainline_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mainline_id INTEGER NOT NULL REFERENCES investment_mainlines(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    nodes_json TEXT NOT NULL DEFAULT '[]',
                    change_summary TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(mainline_id, version_number)
                );

                CREATE TABLE IF NOT EXISTS investment_mainline_sources (
                    version_id INTEGER NOT NULL REFERENCES investment_mainline_versions(id) ON DELETE CASCADE,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    evidence TEXT NOT NULL DEFAULT '',
                    impact TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(version_id, video_id)
                );

                CREATE TABLE IF NOT EXISTS investment_mainline_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mainline_id INTEGER NOT NULL REFERENCES investment_mainlines(id) ON DELETE CASCADE,
                    source_video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    source_hash TEXT NOT NULL UNIQUE,
                    relevance REAL NOT NULL DEFAULT 0,
                    update_required INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    proposal_json TEXT NOT NULL DEFAULT '{}',
                    model TEXT NOT NULL DEFAULT '',
                    response_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_videos_discovered_at ON videos(discovered_at DESC);
                CREATE INDEX IF NOT EXISTS idx_videos_author_sort
                    ON videos(author, published_at DESC, discovered_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_analyses_video_id ON analyses(video_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_assets_video_id ON video_assets(video_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_comments_video_id ON comments(video_id, captured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_video_notes_video_id ON video_notes(video_id, note_type);
                CREATE INDEX IF NOT EXISTS idx_content_keywords_lookup
                    ON content_keywords(category, normalized_keyword, content_id);
                CREATE INDEX IF NOT EXISTS idx_content_keywords_content
                    ON content_keywords(content_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_video_titles_source ON video_titles(title_source, verified);
                CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_mainline_versions
                    ON investment_mainline_versions(mainline_id, version_number DESC);
                CREATE INDEX IF NOT EXISTS idx_mainline_drafts
                    ON investment_mainline_drafts(mainline_id, status, created_at DESC);
                """
            )
            ensure_investment_thought_schema(conn)
            ensure_knowledge_schema(conn)
            self._migrate_legacy_keyword_notes(conn)
            self._normalize_keyword_notes(conn)
            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS content_originals_knowledge_ai
                AFTER INSERT ON content_originals BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (NEW.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_originals_knowledge_au
                AFTER UPDATE ON content_originals BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (NEW.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_originals_knowledge_ad
                AFTER DELETE ON content_originals BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (OLD.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_keyword_sets_knowledge_ai
                AFTER INSERT ON content_keyword_sets BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (NEW.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_keyword_sets_knowledge_au
                AFTER UPDATE ON content_keyword_sets BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (NEW.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_keyword_sets_knowledge_ad
                AFTER DELETE ON content_keyword_sets BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (OLD.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_keywords_knowledge_ai
                AFTER INSERT ON content_keywords BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (NEW.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_keywords_knowledge_au
                AFTER UPDATE ON content_keywords BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (NEW.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                CREATE TRIGGER IF NOT EXISTS content_keywords_knowledge_ad
                AFTER DELETE ON content_keywords BEGIN
                    INSERT INTO knowledge_dirty(video_id, marked_at)
                    VALUES (OLD.content_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
                END;

                DROP VIEW IF EXISTS content_original_catalog;
                CREATE VIEW content_original_catalog AS
                SELECT
                    co.content_id,
                    v.id AS video_id,
                    CASE
                        WHEN pa.asset_type = 'screenshot' THEN 'image'
                        ELSE pa.asset_type
                    END AS media_type,
                    v.published_at,
                    CASE
                        WHEN instr(pa.original_name, '.') > 0
                            THEN substr(pa.original_name, 1, instr(pa.original_name, '.') - 1)
                        ELSE pa.original_name
                    END AS canonical_name,
                    COALESCE(
                        NULLIF(vt.active_title, ''),
                        NULLIF(kr.retrieval_title, ''),
                        v.title
                    ) AS active_title,
                    co.original_text,
                    co.text_sha256,
                    COALESCE(kr.keywords_json, '[]') AS keywords_json,
                    COALESCE(cks.categories_json, '{}') AS keyword_categories_json,
                    COALESCE(cks.schema_version, '') AS keyword_schema_version,
                    COALESCE(cks.source_hash, '') AS keyword_source_hash
                FROM content_originals co
                JOIN videos v ON v.id = co.content_id
                LEFT JOIN video_assets pa ON pa.id = (
                    SELECT candidate.id
                    FROM video_assets candidate
                    WHERE candidate.video_id = v.id
                    ORDER BY
                        CASE
                            WHEN candidate.asset_type IN ('video', 'screenshot') THEN 0
                            ELSE 1
                        END,
                        candidate.id DESC
                    LIMIT 1
                )
                LEFT JOIN video_titles vt ON vt.video_id = v.id
                LEFT JOIN knowledge_records kr ON kr.video_id = v.id
                LEFT JOIN content_keyword_sets cks ON cks.content_id = v.id;
                """
            )
            sync_dirty_knowledge(conn)
            conn.execute("PRAGMA optimize")

    @staticmethod
    def _migrate_legacy_keyword_notes(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT n.video_id, n.text, n.updated_at
            FROM video_notes n
            LEFT JOIN content_keyword_sets cks ON cks.content_id = n.video_id
            WHERE n.note_type = 'ai_keywords' AND cks.content_id IS NULL
            """
        ).fetchall()
        for row in rows:
            payload = from_json(row["text"], {})
            if not isinstance(payload, dict):
                continue
            categories = normalize_keyword_categories(payload)
            keywords = flatten_keyword_categories(categories)
            if not keywords and not payload.get("confirmed_at"):
                continue
            source_hash = str(payload.get("source_hash") or "")
            if not source_hash:
                original = conn.execute(
                    "SELECT original_text, text_sha256 FROM content_originals WHERE content_id = ?",
                    (row["video_id"],),
                ).fetchone()
                if original:
                    source_hash = str(original["text_sha256"] or "")
                else:
                    note = conn.execute(
                        """
                        SELECT text FROM video_notes
                        WHERE video_id = ? AND note_type = 'video_text'
                        """,
                        (row["video_id"],),
                    ).fetchone()
                    source_hash = hashlib.sha256(
                        str(note["text"] if note else "").encode("utf-8")
                    ).hexdigest()
            schema_version = str(payload.get("schema_version") or "legacy-v2-migrated")
            confirmed_at = str(payload.get("confirmed_at") or row["updated_at"] or now_iso())
            conn.execute(
                """
                INSERT INTO content_keyword_sets (
                    content_id, schema_version, source_hash, model,
                    categories_json, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["video_id"],
                    schema_version,
                    source_hash,
                    str(payload.get("model") or "legacy"),
                    to_json(categories),
                    confirmed_at,
                ),
            )
            ordinal = 0
            for category in KEYWORD_CATEGORIES:
                for keyword in categories[category]:
                    ordinal += 1
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO content_keywords (
                            content_id, category, keyword, normalized_keyword, ordinal
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            row["video_id"],
                            category,
                            keyword,
                            normalize_keyword(keyword),
                            ordinal,
                        ),
                    )
            conn.execute(
                """
                INSERT INTO knowledge_dirty(video_id, marked_at) VALUES (?, ?)
                ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at
                """,
                (row["video_id"], now_iso()),
            )

    @staticmethod
    def _normalize_keyword_notes(conn: sqlite3.Connection) -> None:
        """Keep the compatibility note keyword-only; remove legacy AI summaries."""
        rows = conn.execute(
            """
            SELECT
                n.id AS note_id, n.text,
                cks.schema_version, cks.source_hash, cks.model,
                cks.categories_json, cks.confirmed_at
            FROM content_keyword_sets cks
            JOIN video_notes n
              ON n.video_id = cks.content_id AND n.note_type = 'ai_keywords'
            """
        ).fetchall()
        for row in rows:
            categories = normalize_keyword_categories(
                {"categories": from_json(row["categories_json"], {})}
            )
            payload = {
                "version": 3,
                "schema_version": row["schema_version"],
                "categories": categories,
                "keywords": flatten_keyword_categories(categories),
                "model": row["model"],
                "source_hash": row["source_hash"],
                "confirmed_at": row["confirmed_at"],
            }
            normalized_text = to_json(payload)
            if str(row["text"] or "") == normalized_text:
                continue
            conn.execute(
                "UPDATE video_notes SET text = ? WHERE id = ?",
                (normalized_text, row["note_id"]),
            )

    def start_run(self, run_type: str, input_data: dict[str, Any] | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO runs (run_type, status, started_at, input_json)
                VALUES (?, 'running', ?, ?)
                """,
                (run_type, now_iso(), to_json(input_data)),
            )
            return int(cur.lastrowid)

    def get_source_state(self, source: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_state WHERE source = ?",
                (source,),
            ).fetchone()
            if not row:
                return {
                    "source": source,
                    "cursor": None,
                    "last_checked_at": None,
                    "last_item_at": None,
                    "metadata": {},
                }
            item = dict(row)
            item["metadata"] = from_json(item.pop("metadata_json"), {})
            return item

    def save_source_state(
        self,
        source: str,
        cursor: str | int | None = None,
        last_item_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        current = self.get_source_state(source)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_state (
                    source, cursor, last_checked_at, last_item_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    cursor = excluded.cursor,
                    last_checked_at = excluded.last_checked_at,
                    last_item_at = COALESCE(excluded.last_item_at, source_state.last_item_at),
                    metadata_json = excluded.metadata_json
                """,
                (
                    source,
                    str(cursor if cursor is not None else current.get("cursor") or 0),
                    now_iso(),
                    last_item_at,
                    to_json(metadata or {}),
                ),
            )

    def finish_run(
        self,
        run_id: int,
        status: str,
        output_data: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, finished_at = ?, output_json = ?, error = ?
                WHERE id = ?
                """,
                (status, now_iso(), to_json(output_data), error, run_id),
            )

    def upsert_video(self, video: dict[str, Any]) -> tuple[int, bool]:
        discovered_at = video.get("discovered_at") or now_iso()
        raw_json = to_json(video.get("raw_json", video))
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM videos
                WHERE source = ? AND source_video_id = ?
                """,
                (video["source"], video["source_video_id"]),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE videos
                    SET author = ?, title = ?, description = ?, url = ?, cover_url = ?,
                        published_at = COALESCE(?, published_at), raw_json = ?
                    WHERE id = ?
                    """,
                    (
                        video.get("author", ""),
                        video.get("title", ""),
                        video.get("description", ""),
                        video.get("url", ""),
                        video.get("cover_url", ""),
                        video.get("published_at"),
                        raw_json,
                        int(existing["id"]),
                    ),
                )
                refresh_video_knowledge(conn, int(existing["id"]))
                return int(existing["id"]), False

            cur = conn.execute(
                """
                INSERT INTO videos (
                    source, source_video_id, author, title, description, url, cover_url,
                    published_at, discovered_at, status, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video["source"],
                    video["source_video_id"],
                    video.get("author", ""),
                    video.get("title", ""),
                    video.get("description", ""),
                    video.get("url", ""),
                    video.get("cover_url", ""),
                    video.get("published_at"),
                    discovered_at,
                    video.get("status", "new"),
                    raw_json,
                ),
            )
            video_id = int(cur.lastrowid)
            refresh_video_knowledge(conn, video_id)
            return video_id, True

    def update_video_publish_time_from_filename(
        self,
        video_id: int,
        published_at: str,
        *,
        title: str | None = None,
        source_path: str | None = None,
        source: str = "filename",
        force: bool = False,
    ) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if not row:
                return False

            raw_json = from_json(row["raw_json"], {})
            previous_source = str(raw_json.get("published_at_source") or "")
            # A date explicitly confirmed in the UI is authoritative.  File
            # discovery or a later duplicate scan must never move it again.
            if previous_source == "manual_verified":
                return False
            if previous_source == "model_filename" and not force:
                return False

            previous_published_at = row["published_at"]
            next_title = title if title else row["title"]
            if previous_published_at == published_at and next_title == row["title"]:
                return False

            raw_json.update(
                {
                    "published_at_source": "model_filename" if force else "filename",
                    "published_at_corrected_at": now_iso(),
                    "published_at_corrected_from": source,
                    "published_at_previous": previous_published_at,
                }
            )
            if source_path:
                raw_json["published_at_filename_source_path"] = source_path

            conn.execute(
                """
                UPDATE videos
                SET published_at = ?, title = ?, raw_json = ?
                WHERE id = ?
                """,
                (published_at, next_title, to_json(raw_json), video_id),
            )
            refresh_video_knowledge(conn, video_id)
            return True

    def update_video_published_at_manual(self, video_id: int, published_at: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if not row:
                return None

            previous_published_at = row["published_at"]
            raw_json = from_json(row["raw_json"], {})
            raw_json.update(
                {
                    "published_at_source": "manual_verified",
                    "published_at_manual_updated_at": now_iso(),
                    "published_at_previous": previous_published_at,
                }
            )

            conn.execute(
                """
                UPDATE videos
                SET published_at = ?, raw_json = ?
                WHERE id = ?
                """,
                (published_at, to_json(raw_json), video_id),
            )
            refresh_video_knowledge(conn, video_id)
            updated = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return dict(updated) if updated else None

    def update_video_published_at_detected(
        self,
        video_id: int,
        published_at: str,
        *,
        source: str,
    ) -> bool:
        """Save a stronger automatic publication-time signal."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT published_at, raw_json FROM videos WHERE id = ?",
                (video_id,),
            ).fetchone()
            if not row:
                return False
            raw_json = from_json(row["raw_json"], {})
            if not isinstance(raw_json, dict):
                raw_json = {}
            if str(raw_json.get("published_at_source") or "") == "manual_verified":
                return False
            previous = str(row["published_at"] or "")
            if previous == published_at and str(
                raw_json.get("published_at_source") or ""
            ) == source:
                return False
            raw_json.update(
                {
                    "published_at_source": source,
                    "published_at_corrected_at": now_iso(),
                    "published_at_previous": previous,
                }
            )
            conn.execute(
                "UPDATE videos SET published_at = ?, raw_json = ? WHERE id = ?",
                (published_at, to_json(raw_json), video_id),
            )
            refresh_video_knowledge(conn, video_id)
            return True

    def save_transcript(
        self,
        video_id: int,
        text: str,
        source: str = "manual",
        language: str = "zh",
        confidence: float | None = None,
        raw: dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO transcripts (
                    video_id, language, text, source, confidence, raw_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (video_id, language, text, source, confidence, to_json(raw), now_iso()),
            )
            transcript_id = int(cur.lastrowid)
            refresh_video_knowledge(conn, video_id)
            return transcript_id

    def list_transcripts(self, video_id: int, limit: int = 5) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM transcripts
                WHERE video_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (video_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def save_asset(self, asset: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO video_assets (
                    video_id, asset_type, storage_mode, original_name, local_path,
                    remote_url, mime_type, size_bytes, sha256, source, status,
                    raw_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset["video_id"],
                    asset.get("asset_type", "video"),
                    asset.get("storage_mode", "local_file"),
                    asset.get("original_name", ""),
                    asset.get("local_path", ""),
                    asset.get("remote_url", ""),
                    asset.get("mime_type", ""),
                    int(asset.get("size_bytes", 0)),
                    asset.get("sha256", ""),
                    asset.get("source", "manual"),
                    asset.get("status", "stored"),
                    to_json(asset.get("raw_json", {})),
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def get_asset(self, asset_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM video_assets WHERE id = ?", (asset_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            item["raw_json"] = from_json(item.get("raw_json"), {})
            return item

    def find_asset_by_sha(self, sha256: str, author: str | None = None) -> dict[str, Any] | None:
        if not sha256:
            return None
        with self.connect() as conn:
            author_filter = " AND v.author = ?" if author else ""
            params: tuple[Any, ...] = (sha256, author) if author else (sha256,)
            row = conn.execute(
                f"""
                SELECT va.*, v.title AS video_title
                FROM video_assets va
                JOIN videos v ON v.id = va.video_id
                WHERE va.sha256 = ?
                {author_filter}
                ORDER BY va.created_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["raw_json"] = from_json(item.get("raw_json"), {})
            return item

    def known_asset_source_paths(self) -> set[str]:
        """Return original drop-folder paths already represented in the library."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT raw_json
                FROM video_assets
                WHERE raw_json IS NOT NULL AND raw_json != ''
                """
            ).fetchall()
        paths: set[str] = set()
        for row in rows:
            raw = from_json(row["raw_json"], {})
            source_path = str(raw.get("source_path") or "").strip()
            if source_path:
                try:
                    source_path = str(Path(source_path).expanduser().resolve())
                except OSError:
                    pass
                paths.add(source_path.casefold())
        return paths

    def list_assets(self, video_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM video_assets
                WHERE video_id = ?
                ORDER BY created_at DESC
                """,
                (video_id,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["raw_json"] = from_json(item.get("raw_json"), {})
                items.append(item)
            return items

    def upsert_comment(self, comment: dict[str, Any]) -> tuple[int, bool]:
        captured_at = comment.get("captured_at") or now_iso()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM comments
                WHERE video_id = ? AND source = ? AND source_comment_id = ?
                """,
                (
                    comment["video_id"],
                    comment.get("source", "manual"),
                    comment["source_comment_id"],
                ),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE comments
                    SET author = ?, text = ?, like_count = ?, reply_count = ?,
                        sentiment = ?, risk_level = ?, published_at = ?,
                        captured_at = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (
                        comment.get("author", ""),
                        comment.get("text", ""),
                        int(comment.get("like_count", 0)),
                        int(comment.get("reply_count", 0)),
                        comment.get("sentiment", "neutral"),
                        comment.get("risk_level", "normal"),
                        comment.get("published_at"),
                        captured_at,
                        to_json(comment.get("raw_json", comment)),
                        int(existing["id"]),
                    ),
                )
                refresh_video_knowledge(conn, int(comment["video_id"]))
                return int(existing["id"]), False

            cur = conn.execute(
                """
                INSERT INTO comments (
                    video_id, source, source_comment_id, author, text, like_count,
                    reply_count, sentiment, risk_level, published_at, captured_at,
                    raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comment["video_id"],
                    comment.get("source", "manual"),
                    comment["source_comment_id"],
                    comment.get("author", ""),
                    comment.get("text", ""),
                    int(comment.get("like_count", 0)),
                    int(comment.get("reply_count", 0)),
                    comment.get("sentiment", "neutral"),
                    comment.get("risk_level", "normal"),
                    comment.get("published_at"),
                    captured_at,
                    to_json(comment.get("raw_json", comment)),
                ),
            )
            comment_id = int(cur.lastrowid)
            refresh_video_knowledge(conn, int(comment["video_id"]))
            return comment_id, True

    def list_comments(
        self,
        video_id: int,
        limit: int = 100,
        *,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM comments
                WHERE video_id = ?
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (video_id, limit),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                raw = from_json(item.get("raw_json"), {})
                if compact:
                    raw = {
                        key: raw[key]
                        for key in (
                            "display_order",
                            "kind",
                            "author_liked",
                            "reply_depth",
                            "parent_source_comment_id",
                            "root_source_comment_id",
                            "thread_id",
                        )
                        if key in raw
                    }
                item["raw_json"] = raw
                items.append(item)
            return items

    def count_comments(self, video_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM comments WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            return int(row["total"] or 0)

    def delete_comment(self, comment_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if not row:
                return None
            video_id = int(row["video_id"])
            conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            refresh_video_knowledge(conn, video_id)
            return {"comment_id": comment_id, "video_id": video_id, "deleted": True}

    def get_note(self, video_id: int, note_type: str = "interpretation") -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM video_notes
                WHERE video_id = ? AND note_type = ?
                """,
                (video_id, note_type),
            ).fetchone()
            return dict(row) if row else None

    def get_content_original(self, content_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_originals WHERE content_id = ?",
                (content_id,),
            ).fetchone()
            return dict(row) if row else None

    def save_content_original(self, content_id: int, text: str) -> dict[str, Any]:
        value = str(text or "")
        if not value.strip():
            raise ValueError("正式原文不能为空。")
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO content_originals (content_id, original_text, text_sha256)
                VALUES (?, ?, ?)
                ON CONFLICT(content_id) DO UPDATE SET
                    original_text=excluded.original_text,
                    text_sha256=excluded.text_sha256
                """,
                (content_id, value, digest),
            )
            refresh_video_knowledge(conn, content_id)
            row = conn.execute(
                "SELECT * FROM content_originals WHERE content_id = ?",
                (content_id,),
            ).fetchone()
            return dict(row)

    def get_content_original_catalog(self, content_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_original_catalog WHERE content_id = ?",
                (content_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_content_keyword_set(self, content_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_keyword_sets WHERE content_id = ?",
                (content_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["categories"] = normalize_keyword_categories(
                {"categories": from_json(item.pop("categories_json"), {})}
            )
            item["keywords"] = flatten_keyword_categories(item["categories"])
            return item

    def save_content_keywords(
        self,
        content_id: int,
        *,
        categories: dict[str, Any],
        source_hash: str,
        schema_version: str = KEYWORD_SCHEMA_VERSION,
        model: str = "",
    ) -> dict[str, Any]:
        normalized_categories = normalize_keyword_categories({"categories": categories})
        keywords = flatten_keyword_categories(normalized_categories)
        confirmed_at = now_iso()
        payload = {
            "version": 3,
            "schema_version": str(schema_version or KEYWORD_SCHEMA_VERSION),
            "categories": normalized_categories,
            "keywords": keywords,
            "model": str(model or "")[:120],
            "source_hash": str(source_hash or ""),
            "confirmed_at": confirmed_at,
        }
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM videos WHERE id = ?",
                (content_id,),
            ).fetchone()
            if not exists:
                raise ValueError("作品不存在。")
            conn.execute(
                """
                INSERT INTO content_keyword_sets (
                    content_id, schema_version, source_hash, model,
                    categories_json, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    source_hash=excluded.source_hash,
                    model=excluded.model,
                    categories_json=excluded.categories_json,
                    confirmed_at=excluded.confirmed_at
                """,
                (
                    content_id,
                    payload["schema_version"],
                    payload["source_hash"],
                    payload["model"],
                    to_json(normalized_categories),
                    confirmed_at,
                ),
            )
            conn.execute("DELETE FROM content_keywords WHERE content_id = ?", (content_id,))
            ordinal = 0
            for category in KEYWORD_CATEGORIES:
                for keyword in normalized_categories[category]:
                    ordinal += 1
                    conn.execute(
                        """
                        INSERT INTO content_keywords (
                            content_id, category, keyword, normalized_keyword, ordinal
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            content_id,
                            category,
                            keyword,
                            normalize_keyword(keyword),
                            ordinal,
                        ),
                    )
            conn.execute(
                """
                INSERT INTO video_notes (video_id, note_type, text, created_at, updated_at)
                VALUES (?, 'ai_keywords', ?, ?, ?)
                ON CONFLICT(video_id, note_type) DO UPDATE SET
                    text=excluded.text,
                    updated_at=excluded.updated_at
                """,
                (content_id, to_json(payload), confirmed_at, confirmed_at),
            )
            refresh_video_knowledge(conn, content_id, force=True)
            note = conn.execute(
                """
                SELECT id FROM video_notes
                WHERE video_id = ? AND note_type = 'ai_keywords'
                """,
                (content_id,),
            ).fetchone()
        return {
            **payload,
            "note_id": int(note["id"]) if note else None,
            "stale": False,
            "can_extract": True,
        }

    @staticmethod
    def _official_note_row(row: dict[str, Any] | sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        content_id = int(item["content_id"])
        return {
            "id": content_id,
            "video_id": content_id,
            "note_type": "video_text",
            "text": str(item["original_text"]),
            "text_sha256": str(item["text_sha256"]),
            "official": True,
        }

    def get_official_original_note(self, content_id: int) -> dict[str, Any] | None:
        return self._official_note_row(self.get_content_original(content_id))

    def _official_original_target(
        self,
        conn: sqlite3.Connection,
        content_id: int,
    ) -> tuple[Path, dict[str, Any]]:
        row = conn.execute(
            """
            SELECT
                v.id AS content_id,
                v.author,
                v.published_at,
                a.asset_type,
                a.original_name,
                a.local_path
            FROM videos v
            LEFT JOIN video_assets a ON a.id = (
                SELECT candidate.id
                FROM video_assets candidate
                WHERE candidate.video_id = v.id
                ORDER BY
                    CASE
                        WHEN candidate.asset_type IN ('video', 'screenshot') THEN 0
                        ELSE 1
                    END,
                    candidate.id DESC
                LIMIT 1
            )
            WHERE v.id = ?
            """,
            (content_id,),
        ).fetchone()
        if row is None:
            raise ValueError("作品不存在。")
        published_at = str(row["published_at"] or "").strip()
        if not published_at:
            raise ValueError("作品缺少真实发布时间，不能归档正式原文。")
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        local_time = parsed.astimezone(CHINA_TZ)

        source_name = str(row["original_name"] or row["local_path"] or "").strip()
        canonical_name = Path(source_name).stem
        if not CANONICAL_MEDIA_STEM.fullmatch(canonical_name):
            raise ValueError("媒体文件名称尚未统一，不能归档正式原文。")
        if not canonical_name.startswith(local_time.strftime("%Y%m%d_%H%M_")):
            raise ValueError("媒体文件名称与作品真实发布时间不一致，不能归档正式原文。")

        originals_dir = ensure_creator_directories(
            str(row["author"] or "未命名博主"),
            data_root=self.creator_data_root,
        )["originals"]
        target = (
            originals_dir
            / local_time.strftime("%Y")
            / local_time.strftime("%m")
            / f"{canonical_name}.txt"
        ).resolve()
        if not target.is_relative_to(originals_dir.resolve()):
            raise RuntimeError("正式原文目标路径越界。")
        return target, dict(row)

    @staticmethod
    def _write_atomic_temp(target: Path, payload: bytes) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temp = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if temp.read_bytes() != payload:
                raise RuntimeError("正式原文临时文件校验失败。")
            return temp
        except Exception:
            if temp.exists():
                temp.unlink()
            raise

    def save_official_original(self, content_id: int, text: str) -> dict[str, Any]:
        """Persist one manually confirmed original to DB, TXT and search index."""
        value = str(text or "")
        if not value.strip():
            raise ValueError("正式原文不能为空。")
        payload = value.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        target: Path | None = None
        staged: Path | None = None
        previous_bytes: bytes | None = None
        target_replaced = False
        saved_original: dict[str, Any] | None = None
        saved_catalog: dict[str, Any] | None = None
        try:
            with self.connect() as conn:
                target, _ = self._official_original_target(conn, content_id)
                previous_bytes = target.read_bytes() if target.exists() else None
                staged = self._write_atomic_temp(target, payload)
                now = now_iso()
                conn.execute(
                    """
                    INSERT INTO video_notes (video_id, note_type, text, created_at, updated_at)
                    VALUES (?, 'video_text', ?, ?, ?)
                    ON CONFLICT(video_id, note_type) DO UPDATE SET
                        text=excluded.text,
                        updated_at=excluded.updated_at
                    """,
                    (content_id, value, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO content_originals (content_id, original_text, text_sha256)
                    VALUES (?, ?, ?)
                    ON CONFLICT(content_id) DO UPDATE SET
                        original_text=excluded.original_text,
                        text_sha256=excluded.text_sha256
                    """,
                    (content_id, value, digest),
                )
                staged.replace(target)
                staged = None
                target_replaced = True
                refresh_video_knowledge(conn, content_id)
                original_row = conn.execute(
                    "SELECT * FROM content_originals WHERE content_id = ?",
                    (content_id,),
                ).fetchone()
                catalog_row = conn.execute(
                    "SELECT * FROM content_original_catalog WHERE content_id = ?",
                    (content_id,),
                ).fetchone()
                if original_row is None or original_row["original_text"] != value:
                    raise RuntimeError("正式原文数据库写入校验失败。")
                if target.read_bytes() != payload:
                    raise RuntimeError("正式原文 TXT 写入校验失败。")
                saved_original = dict(original_row)
                saved_catalog = dict(catalog_row) if catalog_row else None

            if saved_original is None:
                raise RuntimeError("正式原文保存结果缺失。")
            return {
                "note": self._official_note_row(saved_original),
                "original": saved_original,
                "catalog": saved_catalog,
                "txt_path": str(target),
                "cleanup": None,
            }
        except Exception:
            if staged is not None and staged.exists():
                staged.unlink()
            if target is not None and target_replaced:
                if previous_bytes is None:
                    if target.exists():
                        target.unlink()
                else:
                    restore = self._write_atomic_temp(target, previous_bytes)
                    restore.replace(target)
            raise

    def save_note(self, video_id: int, note_type: str, text: str) -> dict[str, Any]:
        now = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO video_notes (video_id, note_type, text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(video_id, note_type) DO UPDATE SET
                    text = excluded.text,
                    updated_at = excluded.updated_at
                """,
                (video_id, note_type, text, now, now),
            )
            row = conn.execute(
                """
                SELECT * FROM video_notes
                WHERE video_id = ? AND note_type = ?
                """,
                (video_id, note_type),
            ).fetchone()
            refresh_video_knowledge(conn, video_id)
            return dict(row)

    def get_video_title(self, video_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM video_titles WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["verified"] = bool(item.get("verified"))
            item["raw"] = from_json(item.pop("raw_json"), {})
            return item

    def save_ocr_title(
        self,
        video_id: int,
        title: str,
        *,
        confidence: float | None = None,
        frame_timestamp: float | None = None,
        frame_path: str = "",
        raw: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = str(title or "").strip()
        if not value:
            raise ValueError("recognized title is empty")
        if len(value) > 120:
            raise ValueError("recognized title is too long")
        now = now_iso()
        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM video_titles WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            manual_title = str(current["manual_title"] or "") if current else ""
            verified = bool(current["verified"]) if current else False
            active_title = manual_title if verified and manual_title else value
            title_source = "manual" if verified and manual_title else "cover_ocr"
            created_at = str(current["created_at"] or now) if current else now
            conn.execute(
                """
                INSERT INTO video_titles (
                    video_id, ocr_title, manual_title, active_title, title_source,
                    confidence, verified, frame_timestamp, frame_path, raw_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    ocr_title=excluded.ocr_title,
                    active_title=excluded.active_title,
                    title_source=excluded.title_source,
                    confidence=excluded.confidence,
                    frame_timestamp=excluded.frame_timestamp,
                    frame_path=excluded.frame_path,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    video_id,
                    value,
                    manual_title,
                    active_title,
                    title_source,
                    confidence,
                    1 if verified else 0,
                    frame_timestamp,
                    frame_path,
                    to_json(raw or {}),
                    created_at,
                    now,
                ),
            )
            refresh_video_knowledge(conn, video_id)
            row = conn.execute(
                "SELECT * FROM video_titles WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            item = dict(row)
            item["verified"] = bool(item.get("verified"))
            item["raw"] = from_json(item.pop("raw_json"), {})
            return item

    def save_manual_title(self, video_id: int, title: str) -> dict[str, Any]:
        value = str(title or "").strip()
        if len(value) > 120:
            raise ValueError("title cannot exceed 120 characters")
        now = now_iso()
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone():
                raise ValueError("video not found")
            current = conn.execute(
                "SELECT * FROM video_titles WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            ocr_title = str(current["ocr_title"] or "") if current else ""
            active_title = value or ocr_title
            title_source = "manual" if value else ("cover_ocr" if ocr_title else "none")
            created_at = str(current["created_at"] or now) if current else now
            conn.execute(
                """
                INSERT INTO video_titles (
                    video_id, ocr_title, manual_title, active_title, title_source,
                    confidence, verified, frame_timestamp, frame_path, raw_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    manual_title=excluded.manual_title,
                    active_title=excluded.active_title,
                    title_source=excluded.title_source,
                    verified=excluded.verified,
                    updated_at=excluded.updated_at
                """,
                (
                    video_id,
                    ocr_title,
                    value,
                    active_title,
                    title_source,
                    current["confidence"] if current else None,
                    1 if value else 0,
                    current["frame_timestamp"] if current else None,
                    str(current["frame_path"] or "") if current else "",
                    str(current["raw_json"] or "{}") if current else "{}",
                    created_at,
                    now,
                ),
            )
            refresh_video_knowledge(conn, video_id)
            row = conn.execute(
                "SELECT * FROM video_titles WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            item = dict(row)
            item["verified"] = bool(item.get("verified"))
            item["raw"] = from_json(item.pop("raw_json"), {})
            return item

    def save_analysis(self, analysis: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO analyses (
                    video_id, version, prompt_version, model, summary, insights_json,
                    recommendations_json, risk_flags_json, score_json, raw_output,
                    trace_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis["video_id"],
                    analysis["version"],
                    analysis["prompt_version"],
                    analysis["model"],
                    analysis["summary"],
                    to_json(analysis.get("insights", [])),
                    to_json(analysis.get("recommendations", [])),
                    to_json(analysis.get("risk_flags", [])),
                    to_json(analysis.get("score", {})),
                    to_json(analysis.get("raw_output", {})),
                    analysis.get("trace_id"),
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def latest_transcript(self, video_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM transcripts
                WHERE video_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (video_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_video(self, video_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return dict(row) if row else None

    def bind_video_remote_source(
        self,
        video_id: int,
        *,
        url: str,
        aweme_id: str,
        match_source: str,
        match_confidence: float,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT raw_json FROM videos WHERE id = ?",
                (video_id,),
            ).fetchone()
            if not row:
                return None
            raw = from_json(row["raw_json"], {})
            if not isinstance(raw, dict):
                raw = {}
            raw.update(
                {
                    "douyin_aweme_id": aweme_id,
                    "douyin_url": url,
                    "douyin_match_source": match_source,
                    "douyin_match_confidence": round(float(match_confidence), 4),
                    "douyin_bound_at": now_iso(),
                }
            )
            if metadata:
                raw["douyin_match_metadata"] = metadata
            conn.execute(
                "UPDATE videos SET url = ?, raw_json = ? WHERE id = ?",
                (url, to_json(raw), video_id),
            )
            refresh_video_knowledge(conn, video_id)
            updated = conn.execute(
                "SELECT * FROM videos WHERE id = ?",
                (video_id,),
            ).fetchone()
            return dict(updated) if updated else None

    def delete_video(self, video_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
            return cur.rowcount > 0

    def rename_video_author(self, old_name: str, new_name: str) -> int:
        """Keep a creator's existing library attached when its tab is renamed."""
        old_name = str(old_name or "").strip()
        new_name = str(new_name or "").strip()
        if not old_name or not new_name or old_name == new_name:
            return 0
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM videos WHERE author = ?",
                (old_name,),
            ).fetchall()
            conn.execute(
                "UPDATE videos SET author = ? WHERE author = ?",
                (new_name, old_name),
            )
            for row in rows:
                refresh_video_knowledge(conn, int(row["id"]))
            return len(rows)

    def list_videos(self, limit: int = 50, account: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            account_filter = "WHERE v.author = ?" if account else ""
            params: tuple[Any, ...] = (account, limit) if account else (limit,)
            rows = conn.execute(
                f"""
                SELECT
                    v.*,
                    vt.active_title,
                    vt.title_source,
                    vt.confidence AS title_confidence,
                    vt.verified AS title_verified,
                    pa.id AS primary_asset_id,
                    pa.asset_type AS primary_asset_type,
                    pa.original_name AS primary_asset_name,
                    pa.mime_type AS primary_asset_mime,
                    pa.size_bytes AS primary_asset_size,
                    CASE
                        WHEN co.content_id IS NOT NULL AND TRIM(co.original_text) != '' THEN 1
                        WHEN video_text.id IS NOT NULL AND TRIM(video_text.text) != '' THEN 1
                        ELSE 0
                    END AS has_video_text,
                    COALESCE(co.text_sha256, '') AS content_original_hash,
                    CASE
                        WHEN interpretation.id IS NOT NULL AND TRIM(interpretation.text) != '' THEN 1
                        ELSE 0
                    END AS has_interpretation,
                    ai_keywords.text AS ai_keywords_json,
                    cks.categories_json AS keyword_categories_json,
                    cks.schema_version AS keyword_schema_version,
                    cks.source_hash AS keyword_source_hash,
                    cks.model AS keyword_model,
                    cks.confirmed_at AS keyword_confirmed_at,
                    (SELECT COUNT(*) FROM video_assets va WHERE va.video_id = v.id) AS asset_count,
                    (SELECT COUNT(*) FROM comments c WHERE c.video_id = v.id) AS comment_count
                FROM videos v
                LEFT JOIN video_assets pa ON pa.id = (
                    SELECT va.id
                    FROM video_assets va
                    WHERE va.video_id = v.id
                    ORDER BY
                        CASE
                            WHEN va.mime_type LIKE 'video/%' THEN 0
                            WHEN va.mime_type LIKE 'image/%' THEN 1
                            ELSE 2
                        END,
                        va.created_at DESC,
                        va.id DESC
                    LIMIT 1
                )
                LEFT JOIN video_titles vt ON vt.video_id = v.id
                LEFT JOIN content_originals co ON co.content_id = v.id
                LEFT JOIN video_notes video_text
                    ON video_text.video_id = v.id
                    AND video_text.note_type = 'video_text'
                LEFT JOIN video_notes interpretation
                    ON interpretation.video_id = v.id
                    AND interpretation.note_type = 'interpretation'
                LEFT JOIN video_notes ai_keywords
                    ON ai_keywords.video_id = v.id
                    AND ai_keywords.note_type = 'ai_keywords'
                LEFT JOIN content_keyword_sets cks ON cks.content_id = v.id
                {account_filter}
                ORDER BY COALESCE(v.published_at, v.discovered_at) DESC, v.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def list_missing_video_text_videos(
        self,
        account: str | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        """List works whose saved video-text note is absent or blank."""
        filters = [
            """
            NOT EXISTS (
                SELECT 1
                FROM content_originals co
                WHERE co.content_id = v.id
                  AND TRIM(co.original_text) != ''
            )
            AND NOT EXISTS (
                SELECT 1
                FROM video_notes n
                WHERE n.video_id = v.id
                  AND n.note_type = 'video_text'
                  AND TRIM(n.text) != ''
            )
            """
        ]
        params: list[Any] = []
        if account:
            filters.append("v.author = ?")
            params.append(account)
        limit_clause = ""
        if limit > 0:
            limit_clause = "LIMIT ?"
            params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT v.*
                FROM videos v
                WHERE {" AND ".join(filters)}
                ORDER BY COALESCE(v.published_at, v.discovered_at) DESC, v.id DESC
                {limit_clause}
                """,
                tuple(params),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_missing_video_text_videos(self, account: str | None = None) -> int:
        account_filter = "AND v.author = ?" if account else ""
        params: tuple[Any, ...] = (account,) if account else ()
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM videos v
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM content_originals co
                    WHERE co.content_id = v.id
                      AND TRIM(co.original_text) != ''
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM video_notes n
                    WHERE n.video_id = v.id
                      AND n.note_type = 'video_text'
                      AND TRIM(n.text) != ''
                )
                {account_filter}
                """,
                params,
            ).fetchone()
            return int(row["total"] or 0)

    def max_video_id(self) -> int:
        """Return the current import watermark for background jobs."""
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM videos").fetchone()
            return int(row[0] or 0)

    def get_video_detail(self, video_id: int) -> dict[str, Any] | None:
        video = self.get_video(video_id)
        if not video:
            return None
        return {
            "video": video,
            "assets": self.list_assets(video_id),
            "transcripts": self.list_transcripts(video_id),
            "comments": self.list_comments(video_id, limit=5000, compact=True),
            "comment_total": self.count_comments(video_id),
            "analyses": self.list_analyses(video_id),
            "notes": {
                "interpretation": self.get_note(video_id, "interpretation"),
                "video_text": (
                    self.get_official_original_note(video_id)
                    or self.get_note(video_id, "video_text")
                ),
                "ai_keywords": self.get_note(video_id, "ai_keywords"),
            },
            "content_original": self.get_content_original_catalog(video_id),
            "title_info": self.get_video_title(video_id),
        }

    def list_analyses(self, video_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM analyses
                WHERE video_id = ?
                ORDER BY created_at DESC
                """,
                (video_id,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["insights"] = from_json(item.pop("insights_json"), [])
                item["recommendations"] = from_json(item.pop("recommendations_json"), [])
                item["risk_flags"] = from_json(item.pop("risk_flags_json"), [])
                item["score"] = from_json(item.pop("score_json"), {})
                item["raw_output"] = from_json(item.pop("raw_output"), {})
                items.append(item)
            return items

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "videos": int(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]),
                "analyses": int(conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]),
                "transcripts": int(conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]),
                "assets": int(conn.execute("SELECT COUNT(*) FROM video_assets").fetchone()[0]),
                "comments": int(conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]),
                "feedback": int(conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]),
                "runs": int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
            }

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["input"] = from_json(item.pop("input_json"), {})
                item["output"] = from_json(item.pop("output_json"), {})
                result.append(item)
            return result

    def save_feedback(self, analysis_id: int, rating: int, note: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO feedback (analysis_id, rating, note, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (analysis_id, rating, note, now_iso()),
            )
            return int(cur.lastrowid)

    def latest_feedback_summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count, AVG(rating) AS avg_rating
                FROM feedback
                """
            ).fetchone()
            recent = conn.execute(
                """
                SELECT f.*, a.video_id
                FROM feedback f
                JOIN analyses a ON a.id = f.analysis_id
                ORDER BY f.created_at DESC
                LIMIT 5
                """
            ).fetchall()
            return {
                "count": int(row["count"]),
                "avg_rating": float(row["avg_rating"] or 0),
                "recent": [dict(item) for item in recent],
            }
