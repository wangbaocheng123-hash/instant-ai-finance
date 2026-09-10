from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 4


class LibraryStore:
    """Persistent metadata store kept outside the replaceable application code."""

    def __init__(
        self,
        database_path: Path,
        comments_root: Path,
    ) -> None:
        self.database_path = Path(database_path)
        self.comments_root = Path(comments_root)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.comments_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS videos (
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

                CREATE INDEX IF NOT EXISTS idx_videos_published_at
                ON videos(published_at DESC);

                CREATE TABLE IF NOT EXISTS comments (
                    comment_id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    parent_comment_id TEXT,
                    author_name TEXT NOT NULL DEFAULT '',
                    author_uid TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    created_at TEXT,
                    digg_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    ip_label TEXT NOT NULL DEFAULT '',
                    is_creator INTEGER NOT NULL DEFAULT 0,
                    is_author_digged INTEGER,
                    reply_to_comment_id TEXT NOT NULL DEFAULT '',
                    reply_to_user_name TEXT NOT NULL DEFAULT '',
                    label_text TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    collected_at TEXT NOT NULL,
                    FOREIGN KEY(video_id) REFERENCES videos(video_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_comments_video_created
                ON comments(video_id, created_at);

                CREATE TABLE IF NOT EXISTS comment_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    digg_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    is_author_digged INTEGER,
                    UNIQUE(comment_id, captured_at),
                    FOREIGN KEY(video_id) REFERENCES videos(video_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_comment_snapshots_video_time
                ON comment_snapshots(video_id, captured_at);

                CREATE TABLE IF NOT EXISTS comment_collection_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    rows_seen INTEGER NOT NULL DEFAULT 0,
                    top_level_seen INTEGER NOT NULL DEFAULT 0,
                    expected_total INTEGER NOT NULL DEFAULT 0,
                    main_pages INTEGER NOT NULL DEFAULT 0,
                    reply_pages INTEGER NOT NULL DEFAULT 0,
                    reply_groups_seen INTEGER NOT NULL DEFAULT 0,
                    incomplete_replies INTEGER NOT NULL DEFAULT 0,
                    expand_clicks INTEGER NOT NULL DEFAULT 0,
                    complete INTEGER NOT NULL DEFAULT 0,
                    author_like_supported INTEGER NOT NULL DEFAULT 0,
                    creator_rows INTEGER NOT NULL DEFAULT 0,
                    creator_reply_rows INTEGER NOT NULL DEFAULT 0,
                    author_liked_rows INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(video_id) REFERENCES videos(video_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_comment_runs_video_time
                ON comment_collection_runs(video_id, captured_at DESC);

                CREATE TABLE IF NOT EXISTS video_metric_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    play_count INTEGER,
                    digg_count INTEGER,
                    comment_count INTEGER,
                    collect_count INTEGER,
                    share_count INTEGER,
                    UNIQUE(video_id, captured_at),
                    FOREIGN KEY(video_id) REFERENCES videos(video_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    success INTEGER NOT NULL DEFAULT 0,
                    visible_today INTEGER NOT NULL DEFAULT 0,
                    downloaded INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS deleted_videos (
                    video_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    published_at TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT NOT NULL
                );
                """
            )
            existing_columns = {
                str(column["name"])
                for column in connection.execute(
                    "PRAGMA table_info(comments)"
                ).fetchall()
            }
            migrations = {
                "is_creator": "is_creator INTEGER NOT NULL DEFAULT 0",
                "is_author_digged": "is_author_digged INTEGER",
                "reply_to_comment_id": (
                    "reply_to_comment_id TEXT NOT NULL DEFAULT ''"
                ),
                "reply_to_user_name": (
                    "reply_to_user_name TEXT NOT NULL DEFAULT ''"
                ),
                "label_text": "label_text TEXT NOT NULL DEFAULT ''",
                "first_seen_at": "first_seen_at TEXT",
                "last_seen_at": "last_seen_at TEXT",
            }
            for name, definition in migrations.items():
                if name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE comments ADD COLUMN {definition}"
                    )
            video_columns = {
                str(column["name"])
                for column in connection.execute(
                    "PRAGMA table_info(videos)"
                ).fetchall()
            }
            if "comment_refresh_requested" not in video_columns:
                connection.execute(
                    """
                    ALTER TABLE videos ADD COLUMN
                    comment_refresh_requested INTEGER NOT NULL DEFAULT 0
                    """
                )
            run_columns = {
                str(column["name"])
                for column in connection.execute(
                    "PRAGMA table_info(comment_collection_runs)"
                ).fetchall()
            }
            run_migrations = {
                "reply_groups_seen": (
                    "reply_groups_seen INTEGER NOT NULL DEFAULT 0"
                ),
                "expand_clicks": "expand_clicks INTEGER NOT NULL DEFAULT 0",
                "creator_reply_rows": (
                    "creator_reply_rows INTEGER NOT NULL DEFAULT 0"
                ),
            }
            for name, definition in run_migrations.items():
                if name not in run_columns:
                    connection.execute(
                        "ALTER TABLE comment_collection_runs "
                        f"ADD COLUMN {definition}"
                    )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_comments_creator
                ON comments(video_id, is_creator, is_author_digged)
                """
            )
            row = connection.execute(
                "SELECT version FROM schema_info LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_info(version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif int(row["version"]) < SCHEMA_VERSION:
                connection.execute(
                    "UPDATE schema_info SET version = ?",
                    (SCHEMA_VERSION,),
                )

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat(timespec="seconds")

    def upsert_video(
        self,
        *,
        video_id: str,
        creator: str,
        title: str,
        source_url: str,
        published_at: datetime,
        now: datetime,
    ) -> bool:
        timestamp = self._iso(now)
        with self._connect() as connection:
            deleted = connection.execute(
                "SELECT 1 FROM deleted_videos WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            if deleted is not None:
                return False
            connection.execute(
                """
                INSERT INTO videos(
                    video_id, creator, title, source_url, published_at,
                    discovered_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    creator = excluded.creator,
                    title = CASE
                        WHEN excluded.title LIKE '抖音作品_%'
                        THEN videos.title
                        ELSE excluded.title
                    END,
                    source_url = excluded.source_url,
                    published_at = excluded.published_at,
                    updated_at = excluded.updated_at
                """,
                (
                    video_id,
                    creator,
                    title,
                    source_url,
                    self._iso(published_at),
                    timestamp,
                    timestamp,
                ),
            )
        return True

    def comment_refresh_candidates(
        self,
        *,
        now: datetime,
        young_window_hours: int,
        young_refresh_minutes: int,
        mature_refresh_minutes: int,
    ) -> list[sqlite3.Row]:
        """Return due videos; manual requests always take priority."""
        candidates: list[sqlite3.Row] = []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT video_id, title, source_url, published_at,
                       comments_collected_at, download_status,
                       comment_refresh_requested
                FROM videos
                ORDER BY published_at DESC
                """
            ).fetchall()
        for row in rows:
            try:
                published = datetime.fromisoformat(row["published_at"])
            except (TypeError, ValueError):
                continue
            comparison_now = now
            if published.tzinfo is None and comparison_now.tzinfo is not None:
                published = published.replace(tzinfo=comparison_now.tzinfo)
            elif published.tzinfo is not None and comparison_now.tzinfo is None:
                comparison_now = comparison_now.replace(tzinfo=published.tzinfo)
            if bool(row["comment_refresh_requested"]):
                candidates.append(row)
                continue
            if str(row["download_status"] or "") != "downloaded":
                continue
            age = comparison_now - published
            refresh_minutes = (
                young_refresh_minutes
                if age < timedelta(hours=young_window_hours)
                else mature_refresh_minutes
            )
            last_success = row["comments_collected_at"]
            if not last_success:
                candidates.append(row)
                continue
            try:
                previous = datetime.fromisoformat(last_success)
            except (TypeError, ValueError):
                candidates.append(row)
                continue
            current = comparison_now
            if previous.tzinfo is None and current.tzinfo is not None:
                previous = previous.replace(tzinfo=current.tzinfo)
            elif previous.tzinfo is not None and current.tzinfo is None:
                current = current.replace(tzinfo=previous.tzinfo)
            if current - previous >= timedelta(minutes=refresh_minutes):
                candidates.append(row)
        return candidates

    def mark_comment_refresh_requested(
        self,
        video_id: str,
        now: datetime,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE videos
                SET comment_refresh_requested = 1,
                    comments_collected_at = NULL,
                    updated_at = ?
                WHERE video_id = ?
                """,
                (self._iso(now), video_id),
            )
        return cursor.rowcount > 0

    def repair_requests(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT video_id, title, source_url, published_at, file_path
                FROM videos
                WHERE download_status = 'repair_requested'
                ORDER BY published_at
                """
            ).fetchall()

    def mark_repair_requested(self, video_id: str, now: datetime) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE videos
                SET download_status = 'repair_requested', updated_at = ?
                WHERE video_id = ?
                """,
                (self._iso(now), video_id),
            )
        return cursor.rowcount > 0

    def mark_repair_failed(self, video_id: str, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE videos
                SET download_status = 'repair_failed', updated_at = ?
                WHERE video_id = ?
                """,
                (self._iso(now), video_id),
            )

    def mark_downloaded(
        self,
        *,
        video_id: str,
        title: str,
        file_path: Path,
        file_size: int,
        duration_seconds: float | None,
        downloaded_at: datetime,
    ) -> None:
        timestamp = self._iso(downloaded_at)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE videos SET
                    title = ?,
                    downloaded_at = ?,
                    file_path = ?,
                    file_size = ?,
                    duration_seconds = ?,
                    download_status = 'downloaded',
                    updated_at = ?
                WHERE video_id = ?
                """,
                (
                    title,
                    timestamp,
                    str(file_path),
                    int(file_size),
                    duration_seconds,
                    timestamp,
                    video_id,
                ),
            )

    def mark_download_failed(
        self,
        video_id: str,
        now: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE videos SET
                    download_status = 'failed',
                    updated_at = ?
                WHERE video_id = ?
                """,
                (self._iso(now), video_id),
            )

    def should_refresh_comments(
        self,
        video_id: str,
        now: datetime,
        refresh_minutes: int,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT comments_collected_at
                FROM videos
                WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()
        if row is None or not row["comments_collected_at"]:
            return True
        try:
            previous = datetime.fromisoformat(row["comments_collected_at"])
        except (TypeError, ValueError):
            return True
        comparison_now = now
        if previous.tzinfo is None and comparison_now.tzinfo is not None:
            previous = previous.replace(tzinfo=comparison_now.tzinfo)
        elif previous.tzinfo is not None and comparison_now.tzinfo is None:
            comparison_now = comparison_now.replace(tzinfo=previous.tzinfo)
        return comparison_now - previous >= timedelta(minutes=refresh_minutes)

    @staticmethod
    def _comment_id(comment: dict) -> str:
        return str(
            comment.get("comment_id")
            or comment.get("cid")
            or ""
        )

    def save_comments(
        self,
        *,
        video_id: str,
        published_date: str,
        comments: Iterable[dict],
        collected_at: datetime,
        summary: dict[str, object] | None = None,
    ) -> int:
        rows = [
            row
            for row in comments
            if self._comment_id(row)
        ]
        timestamp = self._iso(collected_at)
        summary = dict(summary or {})
        snapshot_dir = self.comments_root / published_date
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / f"{video_id}.json"
        temporary = snapshot_path.with_suffix(".json.tmp")

        with self._connect() as connection:
            for row in rows:
                comment_id = self._comment_id(row)
                connection.execute(
                    """
                    INSERT INTO comments(
                        comment_id, video_id, parent_comment_id,
                        author_name, author_uid, text, created_at,
                        digg_count, reply_count, ip_label, is_creator,
                        is_author_digged, reply_to_comment_id,
                        reply_to_user_name, label_text, first_seen_at,
                        last_seen_at, raw_json, collected_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(comment_id) DO UPDATE SET
                        parent_comment_id = excluded.parent_comment_id,
                        author_name = excluded.author_name,
                        author_uid = excluded.author_uid,
                        text = excluded.text,
                        created_at = excluded.created_at,
                        digg_count = excluded.digg_count,
                        reply_count = excluded.reply_count,
                        ip_label = excluded.ip_label,
                        is_creator = excluded.is_creator,
                        is_author_digged = CASE
                            WHEN excluded.is_author_digged IS NULL
                            THEN comments.is_author_digged
                            ELSE excluded.is_author_digged
                        END,
                        reply_to_comment_id = excluded.reply_to_comment_id,
                        reply_to_user_name = excluded.reply_to_user_name,
                        label_text = excluded.label_text,
                        last_seen_at = excluded.last_seen_at,
                        raw_json = excluded.raw_json,
                        collected_at = excluded.collected_at
                    """,
                    (
                        comment_id,
                        video_id,
                        str(row.get("parent_comment_id") or ""),
                        str(row.get("author_name") or ""),
                        str(row.get("author_uid") or ""),
                        str(row.get("text") or ""),
                        str(row.get("created_at") or ""),
                        int(row.get("digg_count") or 0),
                        int(row.get("reply_count") or 0),
                        str(row.get("ip_label") or ""),
                        int(bool(row.get("is_creator"))),
                        (
                            None
                            if row.get("is_author_digged") is None
                            else int(bool(row.get("is_author_digged")))
                        ),
                        str(row.get("reply_to_comment_id") or ""),
                        str(row.get("reply_to_user_name") or ""),
                        str(row.get("label_text") or ""),
                        timestamp,
                        timestamp,
                        json.dumps(
                            row.get("raw") or row,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO comment_snapshots(
                        comment_id, video_id, captured_at,
                        digg_count, reply_count, is_author_digged
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        video_id,
                        timestamp,
                        int(row.get("digg_count") or 0),
                        int(row.get("reply_count") or 0),
                        (
                            None
                            if row.get("is_author_digged") is None
                            else int(bool(row.get("is_author_digged")))
                        ),
                    ),
                )
            total = connection.execute(
                "SELECT COUNT(*) FROM comments WHERE video_id = ?",
                (video_id,),
            ).fetchone()[0]
            snapshot_rows = []
            for stored in connection.execute(
                """
                SELECT comment_id, parent_comment_id, author_name,
                       author_uid, text, created_at, digg_count,
                       reply_count, ip_label, is_creator,
                       is_author_digged, reply_to_comment_id,
                       reply_to_user_name, label_text, first_seen_at,
                       last_seen_at, collected_at, raw_json
                FROM comments
                WHERE video_id = ?
                ORDER BY created_at, comment_id
                """,
                (video_id,),
            ).fetchall():
                try:
                    raw = json.loads(stored["raw_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw = {}
                snapshot_rows.append(
                    {
                        "comment_id": stored["comment_id"],
                        "parent_comment_id": stored["parent_comment_id"],
                        "reply_to_comment_id": stored[
                            "reply_to_comment_id"
                        ],
                        "reply_to_user_name": stored[
                            "reply_to_user_name"
                        ],
                        "author_name": stored["author_name"],
                        "author_uid": stored["author_uid"],
                        "is_creator": bool(stored["is_creator"]),
                        "is_author_digged": (
                            None
                            if stored["is_author_digged"] is None
                            else bool(stored["is_author_digged"])
                        ),
                        "label_text": stored["label_text"],
                        "text": stored["text"],
                        "created_at": stored["created_at"],
                        "digg_count": stored["digg_count"],
                        "reply_count": stored["reply_count"],
                        "ip_label": stored["ip_label"],
                        "first_seen_at": stored["first_seen_at"],
                        "last_seen_at": stored["last_seen_at"],
                        "collected_at": stored["collected_at"],
                        "raw": raw,
                    }
                )
            connection.execute(
                """
                UPDATE videos SET
                    comments_collected_at = ?,
                    comment_refresh_requested = 0,
                    comment_count = ?,
                    updated_at = ?
                WHERE video_id = ?
                """,
                (timestamp, int(total), timestamp, video_id),
            )
            connection.execute(
                """
                INSERT INTO comment_collection_runs(
                    video_id, captured_at, rows_seen, top_level_seen,
                    expected_total, main_pages, reply_pages,
                    reply_groups_seen, incomplete_replies, expand_clicks,
                    complete, author_like_supported, creator_rows,
                    creator_reply_rows, author_liked_rows
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    timestamp,
                    int(summary.get("rows_seen") or len(rows)),
                    int(summary.get("top_level_seen") or 0),
                    int(summary.get("expected_total") or 0),
                    int(summary.get("main_pages") or 0),
                    int(summary.get("reply_pages") or 0),
                    int(summary.get("reply_groups_seen") or 0),
                    int(summary.get("incomplete_replies") or 0),
                    int(summary.get("expand_clicks") or 0),
                    int(bool(summary.get("complete"))),
                    int(bool(summary.get("author_like_supported"))),
                    int(summary.get("creator_rows") or 0),
                    int(summary.get("creator_reply_rows") or 0),
                    int(summary.get("author_liked_rows") or 0),
                ),
            )
            metrics = summary.get("video_metrics") or {}
            if isinstance(metrics, dict) and metrics:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO video_metric_snapshots(
                        video_id, captured_at, play_count, digg_count,
                        comment_count, collect_count, share_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        timestamp,
                        metrics.get("play_count"),
                        metrics.get("digg_count"),
                        metrics.get("comment_count"),
                        metrics.get("collect_count"),
                        metrics.get("share_count"),
                    ),
                )
        temporary.write_text(
            json.dumps(
                {
                    "video_id": video_id,
                    "collected_at": timestamp,
                    "count": int(total),
                    "collection_summary": summary,
                    "comments": snapshot_rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(snapshot_path)
        return int(total)

    def start_scan(self, started_at: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO scan_runs(started_at) VALUES (?)",
                (self._iso(started_at),),
            )
            return int(cursor.lastrowid)

    def finish_scan(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        success: bool,
        visible_today: int,
        downloaded: int,
        message: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scan_runs SET
                    finished_at = ?,
                    success = ?,
                    visible_today = ?,
                    downloaded = ?,
                    message = ?
                WHERE id = ?
                """,
                (
                    self._iso(finished_at),
                    1 if success else 0,
                    int(visible_today),
                    int(downloaded),
                    message[:1000],
                    run_id,
                ),
            )
