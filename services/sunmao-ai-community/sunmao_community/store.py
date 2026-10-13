from __future__ import annotations

import math
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections import deque
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MAX_NICKNAME_LENGTH = 20
MAX_MESSAGE_LENGTH = 500
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_MESSAGES = 5000


def _clean_text(value: Any) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value or ""))
        if unicodedata.category(character) not in {"Cc", "Cf"}
        or character in {"\n", "\t"}
    ).strip()


def validate_community_message(nickname: Any, body: Any) -> tuple[str, str]:
    if not isinstance(nickname, str) or not isinstance(body, str):
        raise ValueError("invalid_message")

    clean_nickname = re.sub(r"\s+", " ", _clean_text(nickname)).strip()
    clean_body = _clean_text(body)
    clean_body = re.sub(r"[ \t]+", " ", clean_body)
    clean_body = re.sub(r"\n{3,}", "\n\n", clean_body)

    if not clean_nickname or len(clean_nickname) > MAX_NICKNAME_LENGTH:
        raise ValueError("invalid_nickname")
    if not clean_body or len(clean_body) > MAX_MESSAGE_LENGTH:
        raise ValueError("invalid_message")
    return clean_nickname, clean_body


class CommunityMessageStore:
    """Small, isolated SQLite store for the public text-only discussion area."""

    def __init__(
        self,
        database_path: Path,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_messages: int = DEFAULT_MAX_MESSAGES,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.retention_days = max(1, int(retention_days))
        self.max_messages = max(100, int(max_messages))
        self._write_lock = threading.Lock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS community_messages (
                    id TEXT PRIMARY KEY,
                    nickname TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_at_epoch INTEGER NOT NULL,
                    is_visible INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS community_messages_visible_created
                ON community_messages (is_visible, created_at_epoch DESC)
                """
            )

    def _purge_expired(self, connection: sqlite3.Connection, now_epoch: int) -> None:
        cutoff = now_epoch - (self.retention_days * 24 * 60 * 60)
        connection.execute(
            "DELETE FROM community_messages WHERE created_at_epoch < ?",
            (cutoff,),
        )
        connection.execute(
            """
            DELETE FROM community_messages
            WHERE id NOT IN (
                SELECT id FROM community_messages
                ORDER BY created_at_epoch DESC, rowid DESC
                LIMIT ?
            )
            """,
            (self.max_messages,),
        )

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, str]:
        return {
            "id": str(row["id"]),
            "nickname": str(row["nickname"]),
            "body": str(row["body"]),
            "created_at": str(row["created_at"]),
        }

    def list_messages(self, *, limit: int = 80) -> list[dict[str, str]]:
        safe_limit = max(1, min(100, int(limit)))
        now_epoch = int(time.time())
        cutoff = now_epoch - (self.retention_days * 24 * 60 * 60)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, nickname, body, created_at
                FROM community_messages
                WHERE is_visible = 1 AND created_at_epoch >= ?
                ORDER BY created_at_epoch DESC, rowid DESC
                LIMIT ?
                """,
                (cutoff, safe_limit),
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def create_message(self, nickname: Any, body: Any) -> dict[str, str]:
        clean_nickname, clean_body = validate_community_message(nickname, body)
        created = datetime.now(UTC)
        message_id = secrets.token_urlsafe(12)
        with self._write_lock:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO community_messages (
                        id, nickname, body, created_at, created_at_epoch, is_visible
                    ) VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (
                        message_id,
                        clean_nickname,
                        clean_body,
                        created.isoformat(),
                        int(created.timestamp()),
                    ),
                )
                self._purge_expired(connection, int(created.timestamp()))
        return {
            "id": message_id,
            "nickname": clean_nickname,
            "body": clean_body,
            "created_at": created.isoformat(),
        }


class CommunityRateLimiter:
    """Ephemeral per-client limiter; client addresses are never persisted."""

    def __init__(self, *, cooldown_seconds: int = 15, hourly_limit: int = 10) -> None:
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.hourly_limit = max(1, int(hourly_limit))
        self._lock = threading.Lock()
        self._attempts: dict[str, deque[float]] = {}

    def allow(self, client_key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts.setdefault(client_key, deque())
            while attempts and now - attempts[0] >= 3600:
                attempts.popleft()

            if attempts and now - attempts[-1] < self.cooldown_seconds:
                retry_after = math.ceil(self.cooldown_seconds - (now - attempts[-1]))
                return False, max(1, retry_after)
            if len(attempts) >= self.hourly_limit:
                retry_after = math.ceil(3600 - (now - attempts[0]))
                return False, max(1, retry_after)

            attempts.append(now)
            if len(self._attempts) > 4096:
                for key, values in list(self._attempts.items()):
                    while values and now - values[0] >= 3600:
                        values.popleft()
                    if not values:
                        self._attempts.pop(key, None)
            return True, 0
