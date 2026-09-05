from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .settings import DATA_DIR


LIST_FIELDS = {
    "core_conclusions",
    "related_record_ids",
    "securities",
    "industries",
    "keywords",
    "unresolved_questions",
    "verification_items",
}
REFINED_LIST_FIELDS = LIST_FIELDS
TEXT_FIELDS = {
    "title",
    "discussion_topic",
    "model_mr_view",
    "user_view",
    "gpt_analysis",
    "source_chat_reference",
}
ALLOWED_STATUSES = {"active", "archived", "superseded"}
DEFAULT_CHAT_TIMEZONE = "Asia/Shanghai"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = re.split(r"[,，;；\n]+", value)
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _clip_text(value: Any, limit: int = 600) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _normalize_chat_interval(
    chat_started_at: str,
    chat_ended_at: str,
    chat_timezone: str,
) -> tuple[str, str, str]:
    timezone_name = _clean_text(chat_timezone) or DEFAULT_CHAT_TIMEZONE
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"无法识别聊天时区：{timezone_name}") from exc

    def parse(value: str, label: str) -> datetime:
        text = _clean_text(value)
        if not text:
            raise ValueError(f"必须填写聊天{label}时间。")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"聊天{label}时间格式不正确，请使用 ISO 8601，例如 2026-07-19T09:00:00+08:00。"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        return parsed.astimezone(UTC)

    started = parse(chat_started_at, "开始")
    ended = parse(chat_ended_at, "结束")
    if ended <= started:
        raise ValueError("聊天结束时间必须晚于开始时间。")
    return started.isoformat(), ended.isoformat(), timezone_name


def _interval_hash(
    source_chat_reference: str,
    chat_session_id: str,
    chat_started_at: str,
    chat_ended_at: str,
) -> str:
    identity = "\n".join(
        (
            _clean_text(source_chat_reference).casefold(),
            _clean_text(chat_session_id).casefold(),
            chat_started_at,
            chat_ended_at,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _search_terms(query: str) -> list[str]:
    normalized = _clean_text(query).casefold()
    if not normalized:
        return []

    terms: list[str] = []
    for part in re.split(r"[\s,，。；;：:、！？!?/\\|()（）\[\]{}]+", normalized):
        if not part:
            continue
        terms.append(part)
        for latin in re.findall(r"[a-z0-9][a-z0-9._%-]*", part):
            terms.append(latin)
        for chinese in re.findall(r"[\u4e00-\u9fff]{2,}", part):
            if len(chinese) <= 4:
                terms.append(chinese)
                continue
            for size in (2, 3, 4):
                terms.extend(chinese[index : index + size] for index in range(len(chinese) - size + 1))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))


def _fts_query(terms: list[str]) -> str:
    searchable = [term for term in terms if len(term) >= 3][:64]
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in searchable)


class ConversationMemoryStore:
    """Structured long-term memories written through MCP, isolated from source knowledge."""

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "mx_agent.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with closing(sqlite3.connect(self.path, timeout=5.0)) as conn, conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS conversation_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    discussion_topic TEXT NOT NULL DEFAULT '',
                    core_conclusions_json TEXT NOT NULL DEFAULT '[]',
                    related_record_ids_json TEXT NOT NULL DEFAULT '[]',
                    securities_json TEXT NOT NULL DEFAULT '[]',
                    industries_json TEXT NOT NULL DEFAULT '[]',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    model_mr_view TEXT NOT NULL DEFAULT '',
                    user_view TEXT NOT NULL DEFAULT '',
                    gpt_analysis TEXT NOT NULL DEFAULT '',
                    unresolved_questions_json TEXT NOT NULL DEFAULT '[]',
                    verification_items_json TEXT NOT NULL DEFAULT '[]',
                    source_chat_reference TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'mcp_chat',
                    status TEXT NOT NULL DEFAULT 'active',
                    version INTEGER NOT NULL DEFAULT 1,
                    content_hash TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_memory_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL REFERENCES conversation_memories(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    change_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(memory_id, version)
                );

                DROP INDEX IF EXISTS idx_conversation_memories_hash;
                CREATE INDEX IF NOT EXISTS idx_conversation_memories_hash_lookup
                    ON conversation_memories(content_hash);
                CREATE INDEX IF NOT EXISTS idx_conversation_memories_updated
                    ON conversation_memories(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversation_memory_versions
                    ON conversation_memory_versions(memory_id, version DESC);

                CREATE TABLE IF NOT EXISTS conversation_memory_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_key TEXT NOT NULL UNIQUE,
                    memory_id INTEGER NOT NULL UNIQUE
                        REFERENCES conversation_memories(id) ON DELETE CASCADE,
                    source_chat_reference TEXT NOT NULL,
                    chat_session_id TEXT NOT NULL DEFAULT '',
                    chat_started_at TEXT NOT NULL,
                    chat_ended_at TEXT NOT NULL,
                    chat_timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    interval_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_conversation_memory_batches_scope
                    ON conversation_memory_batches (
                        source_chat_reference, chat_session_id, chat_started_at, chat_ended_at
                    );
                CREATE INDEX IF NOT EXISTS idx_conversation_memory_batches_latest
                    ON conversation_memory_batches (chat_ended_at DESC);

                CREATE TABLE IF NOT EXISTS refined_conversation_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL UNIQUE
                        REFERENCES conversation_memories(id) ON DELETE CASCADE,
                    batch_id INTEGER
                        REFERENCES conversation_memory_batches(id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    discussion_topic TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL,
                    core_conclusions_json TEXT NOT NULL DEFAULT '[]',
                    related_record_ids_json TEXT NOT NULL DEFAULT '[]',
                    securities_json TEXT NOT NULL DEFAULT '[]',
                    industries_json TEXT NOT NULL DEFAULT '[]',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    model_mr_view TEXT NOT NULL DEFAULT '',
                    user_view TEXT NOT NULL DEFAULT '',
                    gpt_analysis TEXT NOT NULL DEFAULT '',
                    unresolved_questions_json TEXT NOT NULL DEFAULT '[]',
                    verification_items_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    memory_version INTEGER NOT NULL DEFAULT 1,
                    search_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_refined_conversation_memories_search
                    ON refined_conversation_memories(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_refined_conversation_memories_batch
                    ON refined_conversation_memories(batch_id);

                CREATE VIRTUAL TABLE IF NOT EXISTS refined_conversation_memories_fts
                USING fts5(
                    title,
                    discussion_topic,
                    summary,
                    entities,
                    search_text,
                    tokenize='trigram'
                );

                CREATE TRIGGER IF NOT EXISTS refined_conversation_memories_ai
                AFTER INSERT ON refined_conversation_memories
                BEGIN
                    INSERT INTO refined_conversation_memories_fts (
                        rowid, title, discussion_topic, summary, entities, search_text
                    ) VALUES (
                        new.id,
                        new.title,
                        new.discussion_topic,
                        new.summary,
                        new.securities_json || ' ' || new.industries_json || ' ' || new.keywords_json,
                        new.search_text
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS refined_conversation_memories_au
                AFTER UPDATE ON refined_conversation_memories
                BEGIN
                    DELETE FROM refined_conversation_memories_fts WHERE rowid = old.id;
                    INSERT INTO refined_conversation_memories_fts (
                        rowid, title, discussion_topic, summary, entities, search_text
                    ) VALUES (
                        new.id,
                        new.title,
                        new.discussion_topic,
                        new.summary,
                        new.securities_json || ' ' || new.industries_json || ' ' || new.keywords_json,
                        new.search_text
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS refined_conversation_memories_ad
                AFTER DELETE ON refined_conversation_memories
                BEGIN
                    DELETE FROM refined_conversation_memories_fts WHERE rowid = old.id;
                END;
                """
            )
            conn.row_factory = sqlite3.Row
            self._backfill_refined_memories(conn)
            refined_count = conn.execute(
                "SELECT COUNT(*) FROM refined_conversation_memories"
            ).fetchone()[0]
            fts_count = conn.execute(
                "SELECT COUNT(*) FROM refined_conversation_memories_fts"
            ).fetchone()[0]
            if refined_count != fts_count:
                conn.execute("DELETE FROM refined_conversation_memories_fts")
                conn.execute(
                    """
                    INSERT INTO refined_conversation_memories_fts (
                        rowid, title, discussion_topic, summary, entities, search_text
                    )
                    SELECT
                        id,
                        title,
                        discussion_topic,
                        summary,
                        securities_json || ' ' || industries_json || ' ' || keywords_json,
                        search_text
                    FROM refined_conversation_memories
                    """
                )

    @staticmethod
    def _payload(
        *,
        title: str,
        discussion_topic: str = "",
        core_conclusions: list[str] | None = None,
        related_record_ids: list[str] | None = None,
        securities: list[str] | None = None,
        industries: list[str] | None = None,
        keywords: list[str] | None = None,
        model_mr_view: str = "",
        user_view: str = "",
        gpt_analysis: str = "",
        unresolved_questions: list[str] | None = None,
        verification_items: list[str] | None = None,
        source_chat_reference: str = "",
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": _clean_text(title),
            "discussion_topic": _clean_text(discussion_topic),
            "core_conclusions": _clean_list(core_conclusions),
            "related_record_ids": _clean_list(related_record_ids),
            "securities": _clean_list(securities),
            "industries": _clean_list(industries),
            "keywords": _clean_list(keywords),
            "model_mr_view": _clean_text(model_mr_view),
            "user_view": _clean_text(user_view),
            "gpt_analysis": _clean_text(gpt_analysis),
            "unresolved_questions": _clean_list(unresolved_questions),
            "verification_items": _clean_list(verification_items),
            "source_chat_reference": _clean_text(source_chat_reference),
            "status": _clean_text(status) or "active",
            "metadata": metadata or {},
        }
        if not payload["title"]:
            raise ValueError("长期记忆必须填写标题。")
        if payload["status"] not in ALLOWED_STATUSES:
            raise ValueError("记忆状态只能是 active、archived 或 superseded。")
        content_values = (
            payload["core_conclusions"],
            payload["model_mr_view"],
            payload["user_view"],
            payload["gpt_analysis"],
            payload["unresolved_questions"],
            payload["verification_items"],
        )
        if not any(content_values):
            raise ValueError("长期记忆至少需要一项结论、观点、分析或待验证事项。")
        return payload

    @staticmethod
    def _content_hash(payload: dict[str, Any]) -> str:
        substantive = {key: payload[key] for key in sorted(TEXT_FIELDS | LIST_FIELDS | {"status"})}
        return hashlib.sha256(_json_dump(substantive).encode("utf-8")).hexdigest()

    @staticmethod
    def _search_text(payload: dict[str, Any]) -> str:
        parts: list[str] = []
        for field in ("title", "discussion_topic", "model_mr_view", "user_view", "gpt_analysis"):
            parts.append(payload[field])
        for field in LIST_FIELDS:
            parts.extend(payload[field])
        return "\n".join(part for part in parts if part).casefold()

    @staticmethod
    def _refined_payload(payload: dict[str, Any]) -> dict[str, Any]:
        conclusions = _clean_list(payload.get("core_conclusions"))[:12]
        summary_parts: list[str] = []
        if payload.get("discussion_topic"):
            summary_parts.append(f"主题：{_clip_text(payload['discussion_topic'], 240)}")
        if conclusions:
            summary_parts.append(f"核心结论：{'；'.join(conclusions)}")
        if payload.get("model_mr_view"):
            summary_parts.append(f"博主原始观点：{_clip_text(payload['model_mr_view'], 360)}")
        if payload.get("user_view"):
            summary_parts.append(f"用户判断：{_clip_text(payload['user_view'], 360)}")
        if payload.get("gpt_analysis"):
            summary_parts.append(f"GPT分析：{_clip_text(payload['gpt_analysis'], 360)}")
        unresolved = _clean_list(payload.get("unresolved_questions"))[:8]
        if unresolved:
            summary_parts.append(f"尚未解决：{'；'.join(unresolved)}")
        verification = _clean_list(payload.get("verification_items"))[:8]
        if verification:
            summary_parts.append(f"后续验证：{'；'.join(verification)}")

        refined: dict[str, Any] = {
            "title": _clip_text(payload.get("title"), 240),
            "discussion_topic": _clip_text(payload.get("discussion_topic"), 360),
            "summary": "\n".join(summary_parts),
            "model_mr_view": _clip_text(payload.get("model_mr_view"), 600),
            "user_view": _clip_text(payload.get("user_view"), 600),
            "gpt_analysis": _clip_text(payload.get("gpt_analysis"), 600),
            "status": _clean_text(payload.get("status")) or "active",
        }
        for field in REFINED_LIST_FIELDS:
            maximum = 12 if field == "core_conclusions" else 20
            refined[field] = _clean_list(payload.get(field))[:maximum]
        refined["search_text"] = "\n".join(
            part
            for part in (
                refined["title"],
                refined["discussion_topic"],
                refined["summary"],
                *(item for field in REFINED_LIST_FIELDS for item in refined[field]),
            )
            if part
        ).casefold()
        return refined

    @staticmethod
    def _row_to_refined_item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for field in REFINED_LIST_FIELDS:
            item[field] = _json_load(item.pop(f"{field}_json"), [])
        item.pop("search_text", None)
        return item

    @staticmethod
    def _row_to_batch_item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        timezone_name = item["chat_timezone"]
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            timezone = UTC
        item["chat_started_at_local"] = (
            datetime.fromisoformat(item["chat_started_at"]).astimezone(timezone).isoformat()
        )
        item["chat_ended_at_local"] = (
            datetime.fromisoformat(item["chat_ended_at"]).astimezone(timezone).isoformat()
        )
        item["interval_semantics"] = "[chat_started_at, chat_ended_at)"
        return item

    def _upsert_refined_memory(
        self,
        conn: sqlite3.Connection,
        *,
        memory_id: int,
        payload: dict[str, Any],
        memory_version: int,
        batch_id: int | None,
        created_at: str,
        updated_at: str,
    ) -> None:
        refined = self._refined_payload(payload)
        columns: dict[str, Any] = {
            "memory_id": memory_id,
            "batch_id": batch_id,
            "title": refined["title"],
            "discussion_topic": refined["discussion_topic"],
            "summary": refined["summary"],
            "model_mr_view": refined["model_mr_view"],
            "user_view": refined["user_view"],
            "gpt_analysis": refined["gpt_analysis"],
            "status": refined["status"],
            "memory_version": memory_version,
            "search_text": refined["search_text"],
            "created_at": created_at,
            "updated_at": updated_at,
        }
        for field in REFINED_LIST_FIELDS:
            columns[f"{field}_json"] = _json_dump(refined[field])

        names = list(columns)
        placeholders = ", ".join("?" for _ in names)
        updates = ", ".join(
            f"{name} = excluded.{name}" for name in names if name not in {"memory_id", "created_at"}
        )
        conn.execute(
            f"""
            INSERT INTO refined_conversation_memories ({', '.join(names)})
            VALUES ({placeholders})
            ON CONFLICT(memory_id) DO UPDATE SET {updates}
            """,
            tuple(columns[name] for name in names),
        )

    def _backfill_refined_memories(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT memories.*
            FROM conversation_memories AS memories
            LEFT JOIN refined_conversation_memories AS refined
                ON refined.memory_id = memories.id
            WHERE refined.id IS NULL
            """
        ).fetchall()
        for row in rows:
            item = self._row_to_item(row)
            payload = {
                field: item[field]
                for field in TEXT_FIELDS | LIST_FIELDS | {"status"}
            }
            batch = conn.execute(
                "SELECT id FROM conversation_memory_batches WHERE memory_id = ?",
                (item["id"],),
            ).fetchone()
            self._upsert_refined_memory(
                conn,
                memory_id=item["id"],
                payload=payload,
                memory_version=item["version"],
                batch_id=int(batch["id"]) if batch else None,
                created_at=item["created_at"],
                updated_at=item["updated_at"],
            )

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for field in LIST_FIELDS:
            item[field] = _json_load(item.pop(f"{field}_json"), [])
        item["metadata"] = _json_load(item.pop("metadata_json"), {})
        item.pop("search_text", None)
        return item

    @staticmethod
    def _find_row(conn: sqlite3.Connection, reference: str | int) -> sqlite3.Row | None:
        value = str(reference).strip()
        if value.startswith("memory:"):
            value = value.split(":", 1)[1]
        if value.isdigit():
            return conn.execute(
                "SELECT * FROM conversation_memories WHERE id = ?",
                (int(value),),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM conversation_memories WHERE memory_key = ?",
            (value,),
        ).fetchone()

    @staticmethod
    def _save_version(
        conn: sqlite3.Connection,
        item: dict[str, Any],
        change_note: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO conversation_memory_versions (
                memory_id, version, snapshot_json, change_note, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["version"],
                _json_dump(item),
                _clean_text(change_note),
                now_iso(),
            ),
        )

    def save(
        self,
        *,
        title: str,
        chat_started_at: str,
        chat_ended_at: str,
        source_chat_reference: str,
        chat_timezone: str = DEFAULT_CHAT_TIMEZONE,
        chat_session_id: str = "",
        discussion_topic: str = "",
        core_conclusions: list[str] | None = None,
        related_record_ids: list[str] | None = None,
        securities: list[str] | None = None,
        industries: list[str] | None = None,
        keywords: list[str] | None = None,
        model_mr_view: str = "",
        user_view: str = "",
        gpt_analysis: str = "",
        unresolved_questions: list[str] | None = None,
        verification_items: list[str] | None = None,
        memory_key: str = "",
        batch_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_reference = _clean_text(source_chat_reference)
        if not source_reference:
            raise ValueError("必须填写来源聊天标识，才能按聊天时间段防止重复保存。")
        started_at, ended_at, timezone_name = _normalize_chat_interval(
            chat_started_at,
            chat_ended_at,
            chat_timezone,
        )
        session_id = _clean_text(chat_session_id)
        interval_digest = _interval_hash(
            source_reference,
            session_id,
            started_at,
            ended_at,
        )
        payload = self._payload(
            title=title,
            discussion_topic=discussion_topic,
            core_conclusions=core_conclusions,
            related_record_ids=related_record_ids,
            securities=securities,
            industries=industries,
            keywords=keywords,
            model_mr_view=model_mr_view,
            user_view=user_view,
            gpt_analysis=gpt_analysis,
            unresolved_questions=unresolved_questions,
            verification_items=verification_items,
            source_chat_reference=source_reference,
            metadata=metadata,
        )
        content_hash = self._content_hash(payload)
        timestamp = now_iso()
        key = _clean_text(memory_key) or f"memory-{uuid.uuid4().hex[:16]}"
        resolved_batch_key = _clean_text(batch_key) or f"chat-batch-{interval_digest[:20]}"

        with self.connect() as conn:
            exact_batch = conn.execute(
                "SELECT * FROM conversation_memory_batches WHERE interval_hash = ?",
                (interval_digest,),
            ).fetchone()
            if exact_batch:
                existing_row = conn.execute(
                    "SELECT * FROM conversation_memories WHERE id = ?",
                    (exact_batch["memory_id"],),
                ).fetchone()
                item = self._row_to_item(existing_row)
                if item["content_hash"] != content_hash:
                    raise ValueError(
                        "该聊天起止时间已经保存过，但本次内容不同。请更新原记忆，不要重复保存同一时间段。"
                    )
                refined_row = conn.execute(
                    "SELECT * FROM refined_conversation_memories WHERE memory_id = ?",
                    (item["id"],),
                ).fetchone()
                return {
                    "saved": True,
                    "duplicate": True,
                    "duplicate_reason": "same_chat_interval",
                    "memory": item,
                    "chat_batch": self._row_to_batch_item(exact_batch),
                    "refined_memory": (
                        self._row_to_refined_item(refined_row) if refined_row else None
                    ),
                }

            overlap = conn.execute(
                """
                SELECT *
                FROM conversation_memory_batches
                WHERE source_chat_reference = ?
                  AND chat_session_id = ?
                  AND chat_started_at < ?
                  AND chat_ended_at > ?
                ORDER BY chat_started_at
                LIMIT 1
                """,
                (source_reference, session_id, ended_at, started_at),
            ).fetchone()
            if overlap:
                existing = self._row_to_batch_item(overlap)
                raise ValueError(
                    "本次聊天时间段与已保存批次重叠："
                    f"{existing['chat_started_at_local']} 至 {existing['chat_ended_at_local']}。"
                    "请把新批次开始时间调整为上次结束时间或更晚。"
                )

            key_row = conn.execute(
                "SELECT id FROM conversation_memories WHERE memory_key = ?",
                (key,),
            ).fetchone()
            if key_row:
                raise ValueError("该 memory_key 已存在，请使用 update_conversation_memory 更新。")

            columns = {
                "memory_key": key,
                "title": payload["title"],
                "discussion_topic": payload["discussion_topic"],
                "model_mr_view": payload["model_mr_view"],
                "user_view": payload["user_view"],
                "gpt_analysis": payload["gpt_analysis"],
                "source_chat_reference": payload["source_chat_reference"],
                "source": "mcp_chat",
                "status": payload["status"],
                "version": 1,
                "content_hash": content_hash,
                "search_text": self._search_text(payload),
                "metadata_json": _json_dump(payload["metadata"]),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            for field in LIST_FIELDS:
                columns[f"{field}_json"] = _json_dump(payload[field])

            names = list(columns)
            placeholders = ", ".join("?" for _ in names)
            cur = conn.execute(
                f"INSERT INTO conversation_memories ({', '.join(names)}) VALUES ({placeholders})",
                tuple(columns[name] for name in names),
            )
            row = conn.execute(
                "SELECT * FROM conversation_memories WHERE id = ?",
                (int(cur.lastrowid),),
            ).fetchone()
            item = self._row_to_item(row)
            batch_cursor = conn.execute(
                """
                INSERT INTO conversation_memory_batches (
                    batch_key, memory_id, source_chat_reference, chat_session_id,
                    chat_started_at, chat_ended_at, chat_timezone, interval_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_batch_key,
                    item["id"],
                    source_reference,
                    session_id,
                    started_at,
                    ended_at,
                    timezone_name,
                    interval_digest,
                    timestamp,
                ),
            )
            batch_row = conn.execute(
                "SELECT * FROM conversation_memory_batches WHERE id = ?",
                (int(batch_cursor.lastrowid),),
            ).fetchone()
            self._upsert_refined_memory(
                conn,
                memory_id=item["id"],
                payload=payload,
                memory_version=item["version"],
                batch_id=int(batch_cursor.lastrowid),
                created_at=timestamp,
                updated_at=timestamp,
            )
            refined_row = conn.execute(
                "SELECT * FROM refined_conversation_memories WHERE memory_id = ?",
                (item["id"],),
            ).fetchone()
            self._save_version(conn, item, "首次保存")
            return {
                "saved": True,
                "duplicate": False,
                "memory": item,
                "chat_batch": self._row_to_batch_item(batch_row),
                "refined_memory": self._row_to_refined_item(refined_row),
            }

    def get(self, reference: str | int) -> dict[str, Any]:
        with self.connect() as conn:
            row = self._find_row(conn, reference)
            if row is None:
                return {"found": False, "reference": str(reference), "message": "未找到该长期记忆。"}
            item = self._row_to_item(row)
            batch_row = conn.execute(
                "SELECT * FROM conversation_memory_batches WHERE memory_id = ?",
                (item["id"],),
            ).fetchone()
            refined_row = conn.execute(
                "SELECT * FROM refined_conversation_memories WHERE memory_id = ?",
                (item["id"],),
            ).fetchone()
            versions = conn.execute(
                """
                SELECT version, change_note, created_at
                FROM conversation_memory_versions
                WHERE memory_id = ?
                ORDER BY version DESC
                """,
                (item["id"],),
            ).fetchall()
        item["reference"] = f"memory:{item['id']}"
        item["version_history"] = [dict(version) for version in versions]
        return {
            "found": True,
            "memory": item,
            "chat_batch": self._row_to_batch_item(batch_row) if batch_row else None,
            "refined_memory": (
                self._row_to_refined_item(refined_row) if refined_row else None
            ),
        }

    def search(
        self,
        query: str = "",
        limit: int = 10,
        source_chat_reference: str = "",
        chat_session_id: str = "",
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 50))
        terms = _search_terms(query)
        fts_expression = _fts_query(terms)
        source_reference = _clean_text(source_chat_reference)
        session_id = _clean_text(chat_session_id)
        with self.connect() as conn:
            select_sql = """
                SELECT
                    refined.*,
                    batches.batch_key AS chat_batch_key,
                    batches.source_chat_reference AS batch_source_chat_reference,
                    batches.chat_session_id AS batch_chat_session_id,
                    batches.chat_started_at AS batch_chat_started_at,
                    batches.chat_ended_at AS batch_chat_ended_at,
                    batches.chat_timezone AS batch_chat_timezone
            """
            scope_sql = """
                  AND (? = '' OR batches.source_chat_reference = ?)
                  AND (? = '' OR batches.chat_session_id = ?)
            """
            scope_params = (source_reference, source_reference, session_id, session_id)
            if fts_expression:
                rows = conn.execute(
                    select_sql
                    + """
                    FROM refined_conversation_memories_fts
                    JOIN refined_conversation_memories AS refined
                        ON refined.id = refined_conversation_memories_fts.rowid
                    LEFT JOIN conversation_memory_batches AS batches
                        ON batches.id = refined.batch_id
                    WHERE refined_conversation_memories_fts MATCH ?
                      AND refined.status = 'active'
                    """
                    + scope_sql
                    + """
                    ORDER BY bm25(refined_conversation_memories_fts), refined.updated_at DESC
                    LIMIT 500
                    """,
                    (fts_expression, *scope_params),
                ).fetchall()
            elif terms:
                short_terms = terms[:16]
                like_sql = " OR ".join("refined.search_text LIKE ?" for _ in short_terms)
                rows = conn.execute(
                    select_sql
                    + """
                    FROM refined_conversation_memories AS refined
                    LEFT JOIN conversation_memory_batches AS batches
                        ON batches.id = refined.batch_id
                    WHERE refined.status = 'active'
                    """
                    + scope_sql
                    + f" AND ({like_sql}) ORDER BY refined.updated_at DESC LIMIT 2000",
                    (*scope_params, *(f"%{term}%" for term in short_terms)),
                ).fetchall()
            else:
                rows = conn.execute(
                    select_sql
                    + """
                    FROM refined_conversation_memories AS refined
                    LEFT JOIN conversation_memory_batches AS batches
                        ON batches.id = refined.batch_id
                    WHERE refined.status = 'active'
                    """
                    + scope_sql
                    + " ORDER BY refined.updated_at DESC LIMIT ?",
                    (*scope_params, safe_limit),
                ).fetchall()
            checkpoint = conn.execute(
                """
                SELECT *
                FROM conversation_memory_batches
                WHERE (? = '' OR source_chat_reference = ?)
                  AND (? = '' OR chat_session_id = ?)
                ORDER BY chat_ended_at DESC
                LIMIT 1
                """,
                (source_reference, source_reference, session_id, session_id),
            ).fetchone()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            item = self._row_to_refined_item(row)
            if not terms:
                score = 1.0
            else:
                fields = {
                    "title": item["title"].casefold(),
                    "topic": item["discussion_topic"].casefold(),
                    "summary": item["summary"].casefold(),
                    "securities": " ".join(item["securities"]).casefold(),
                    "industries": " ".join(item["industries"]).casefold(),
                    "keywords": " ".join(item["keywords"]).casefold(),
                    "conclusions": " ".join(item["core_conclusions"]).casefold(),
                    "views": " ".join(
                        (item["model_mr_view"], item["user_view"], item["gpt_analysis"])
                    ).casefold(),
                    "other": " ".join(
                        item["unresolved_questions"] + item["verification_items"]
                    ).casefold(),
                }
                score = 0.0
                for term in terms:
                    score += 8.0 if term in fields["title"] else 0.0
                    score += 6.0 if term in fields["securities"] else 0.0
                    score += 5.0 if term in fields["keywords"] else 0.0
                    score += 4.0 if term in fields["topic"] else 0.0
                    score += 3.0 if term in fields["industries"] else 0.0
                    score += 3.0 if term in fields["summary"] else 0.0
                    score += 2.0 if term in fields["conclusions"] else 0.0
                    score += 1.0 if term in fields["views"] else 0.0
                    score += 0.5 if term in fields["other"] else 0.0
                if score <= 0:
                    continue

            scored.append(
                (
                    score,
                    {
                        "reference": f"memory:{item['memory_id']}",
                        "refined_reference": f"refined-memory:{item['id']}",
                        "title": item["title"],
                        "discussion_topic": item["discussion_topic"],
                        "summary": item["summary"],
                        "core_conclusions": item["core_conclusions"],
                        "related_record_ids": item["related_record_ids"],
                        "securities": item["securities"],
                        "industries": item["industries"],
                        "keywords": item["keywords"],
                        "memory_version": item["memory_version"],
                        "chat_batch_key": item.get("chat_batch_key"),
                        "chat_started_at": item.get("batch_chat_started_at"),
                        "chat_ended_at": item.get("batch_chat_ended_at"),
                        "chat_timezone": item.get("batch_chat_timezone"),
                        "updated_at": item["updated_at"],
                        "score": round(score, 2),
                    },
                )
            )

        scored.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
        items = [item for _, item in scored[:safe_limit]]
        return {
            "query": query,
            "retrieval_layer": "refined_conversation_memories",
            "count": len(items),
            "items": items,
            "latest_saved_batch": (
                self._row_to_batch_item(checkpoint) if checkpoint else None
            ),
        }

    def update(
        self,
        reference: str | int,
        *,
        title: str | None = None,
        discussion_topic: str | None = None,
        core_conclusions: list[str] | None = None,
        related_record_ids: list[str] | None = None,
        securities: list[str] | None = None,
        industries: list[str] | None = None,
        keywords: list[str] | None = None,
        model_mr_view: str | None = None,
        user_view: str | None = None,
        gpt_analysis: str | None = None,
        unresolved_questions: list[str] | None = None,
        verification_items: list[str] | None = None,
        source_chat_reference: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        change_note: str = "",
    ) -> dict[str, Any]:
        provided: dict[str, Any] = {
            "title": title,
            "discussion_topic": discussion_topic,
            "core_conclusions": core_conclusions,
            "related_record_ids": related_record_ids,
            "securities": securities,
            "industries": industries,
            "keywords": keywords,
            "model_mr_view": model_mr_view,
            "user_view": user_view,
            "gpt_analysis": gpt_analysis,
            "unresolved_questions": unresolved_questions,
            "verification_items": verification_items,
            "source_chat_reference": source_chat_reference,
            "status": status,
            "metadata": metadata,
        }
        if not any(value is not None for value in provided.values()):
            raise ValueError("没有提供需要更新的字段。")

        with self.connect() as conn:
            row = self._find_row(conn, reference)
            if row is None:
                return {"updated": False, "found": False, "reference": str(reference)}
            current = self._row_to_item(row)
            if (
                source_chat_reference is not None
                and _clean_text(source_chat_reference) != current["source_chat_reference"]
            ):
                raise ValueError("来源聊天标识属于保存批次，不能通过内容更新工具修改。")
            merged = {
                field: provided[field] if provided[field] is not None else current[field]
                for field in TEXT_FIELDS | LIST_FIELDS | {"status", "metadata"}
            }
            payload = self._payload(**merged)
            content_hash = self._content_hash(payload)
            if content_hash == current["content_hash"]:
                batch_row = conn.execute(
                    "SELECT * FROM conversation_memory_batches WHERE memory_id = ?",
                    (current["id"],),
                ).fetchone()
                refined_row = conn.execute(
                    "SELECT * FROM refined_conversation_memories WHERE memory_id = ?",
                    (current["id"],),
                ).fetchone()
                return {
                    "updated": False,
                    "found": True,
                    "unchanged": True,
                    "memory": current,
                    "chat_batch": (
                        self._row_to_batch_item(batch_row) if batch_row else None
                    ),
                    "refined_memory": (
                        self._row_to_refined_item(refined_row) if refined_row else None
                    ),
                }

            next_version = int(current["version"]) + 1
            columns: dict[str, Any] = {
                "title": payload["title"],
                "discussion_topic": payload["discussion_topic"],
                "model_mr_view": payload["model_mr_view"],
                "user_view": payload["user_view"],
                "gpt_analysis": payload["gpt_analysis"],
                "source_chat_reference": payload["source_chat_reference"],
                "status": payload["status"],
                "version": next_version,
                "content_hash": content_hash,
                "search_text": self._search_text(payload),
                "metadata_json": _json_dump(payload["metadata"]),
                "updated_at": now_iso(),
            }
            for field in LIST_FIELDS:
                columns[f"{field}_json"] = _json_dump(payload[field])
            assignments = ", ".join(f"{name} = ?" for name in columns)
            conn.execute(
                f"UPDATE conversation_memories SET {assignments} WHERE id = ?",
                (*columns.values(), current["id"]),
            )
            updated_row = conn.execute(
                "SELECT * FROM conversation_memories WHERE id = ?",
                (current["id"],),
            ).fetchone()
            item = self._row_to_item(updated_row)
            batch_row = conn.execute(
                "SELECT * FROM conversation_memory_batches WHERE memory_id = ?",
                (current["id"],),
            ).fetchone()
            self._upsert_refined_memory(
                conn,
                memory_id=current["id"],
                payload=payload,
                memory_version=next_version,
                batch_id=int(batch_row["id"]) if batch_row else None,
                created_at=current["created_at"],
                updated_at=item["updated_at"],
            )
            refined_row = conn.execute(
                "SELECT * FROM refined_conversation_memories WHERE memory_id = ?",
                (current["id"],),
            ).fetchone()
            self._save_version(conn, item, change_note or "更新长期记忆")
            return {
                "updated": True,
                "found": True,
                "memory": item,
                "chat_batch": self._row_to_batch_item(batch_row) if batch_row else None,
                "refined_memory": self._row_to_refined_item(refined_row),
            }
