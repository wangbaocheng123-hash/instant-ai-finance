from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any, Iterable


MODEL_AUTHOR = "模型先生"
INDEX_VERSION = "2026-08-01-v8-categorized-keywords"

CONTENT_LABELS = {
    "video_title": "视频标题（检索元数据）",
    "video_original": "视频原文",
    "transcript_draft": "自动转写（未人工核对）",
    "model_comment_reply": "账号作者评论回复",
    "user_interpretation": "用户解读感悟",
}

EVIDENCE_PRIORITIES = {
    "video_title": 50,
    "video_original": 100,
    "model_comment_reply": 95,
    "transcript_draft": 80,
    "user_interpretation": 60,
}

_GENERIC_TITLE_PATTERNS = (
    re.compile(r"^\d{8,}(?:[_-]\d+)?$"),
    re.compile(r"none[_-]?content", re.IGNORECASE),
    re.compile(r"^模型先生[-_].*\d{8,}$", re.IGNORECASE),
    re.compile(r"^模型哥看世界[-_].*\d{8,}$", re.IGNORECASE),
)

_DOMAIN_TERMS = (
    "人工智能",
    "半导体",
    "科创板",
    "科创芯片",
    "国产替代",
    "算力",
    "芯片",
    "存储",
    "消费电子",
    "机器人",
    "商业航天",
    "创新药",
    "有色资源",
    "紫金矿业",
    "中芯国际",
    "浪潮信息",
    "紫光股份",
    "中科曙光",
    "中兴通讯",
    "估值",
    "市盈率",
    "周期",
    "主升",
    "反弹",
    "双顶",
    "异动",
    "特殊性",
    "质变",
    "确定性",
    "赔率",
    "安全边际",
    "龙头",
    "仓位",
    "交易",
    "分析",
    "风险",
    "长期",
    "短期",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _from_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _source_hash(payload: Any) -> str:
    raw = _to_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_useful_source_title(title: str) -> bool:
    value = _compact(title)
    if len(value) < 4:
        return False
    return not any(pattern.search(value) for pattern in _GENERIC_TITLE_PATTERNS)


def _derive_retrieval_title(
    source_title: str,
    texts: Iterable[str],
    published_at: str | None,
    author_name: str = MODEL_AUTHOR,
) -> tuple[str, str]:
    if _is_useful_source_title(source_title):
        return _compact(source_title)[:80], "source"

    for text in texts:
        compact = _compact(text)
        if not compact:
            continue
        first_clause = re.split(r"[。！？!?；;]", compact, maxsplit=1)[0].strip("，,：:、 ")
        if len(first_clause) < 4:
            first_clause = compact
        if len(first_clause) > 38:
            first_clause = first_clause[:38].rstrip("，,：:、 ") + "…"
        return first_clause, "derived"

    date_text = str(published_at or "")[:10]
    suffix = f" {date_text}" if date_text else ""
    return f"{author_name or MODEL_AUTHOR}资料{suffix}", "fallback"


def _extract_keywords(title: str, source_title: str, texts: Iterable[str], raw_json: str | None) -> list[str]:
    combined = "\n".join([title, source_title, *list(texts)])
    found: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        item = _compact(value).strip("#，,。；;：:、 ")
        key = item.casefold()
        if not item or len(item) > 40 or key in seen:
            return
        seen.add(key)
        found.append(item)

    metadata = _from_json(raw_json, {})
    for key in ("keywords", "tags", "topics"):
        values = metadata.get(key, []) if isinstance(metadata, dict) else []
        if isinstance(values, str):
            values = re.split(r"[,，、;；\s]+", values)
        if isinstance(values, list):
            for value in values:
                add(str(value))

    for term in _DOMAIN_TERMS:
        if term.casefold() in combined.casefold():
            add(term)

    for token in re.findall(r"[A-Za-z][A-Za-z0-9.+_-]{1,24}", combined):
        add(token)

    if _is_useful_source_title(source_title):
        add(source_title)
    add(title)
    return found[:24]


def _chunk_text(text: str, max_chars: int = 420, overlap_chars: int = 60) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    pieces = [piece.strip() for piece in re.split(r"(?<=[。！？!?；;])|\n+", value) if piece.strip()]
    if len(pieces) <= 1:
        step = max_chars - overlap_chars
        return [value[start : start + max_chars].strip() for start in range(0, len(value), step) if value[start : start + max_chars].strip()]

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = current + piece
        if current and len(candidate) > max_chars:
            chunks.append(current.strip())
            overlap = current[-overlap_chars:] if overlap_chars else ""
            current = overlap + piece
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _comment_order(row: sqlite3.Row) -> tuple[str, int, int]:
    raw = _from_json(row["raw_json"], {})
    group = str(raw.get("thread_id") or raw.get("source_image") or row["source"] or "")
    try:
        display_order = int(raw.get("display_order", row["id"]))
    except (TypeError, ValueError):
        display_order = int(row["id"])
    return group, display_order, int(row["id"])


def _is_model_reply(row: sqlite3.Row, model_author: str = MODEL_AUTHOR) -> bool:
    raw = _from_json(row["raw_json"], {})
    author = _compact(row["author"])
    return raw.get("kind") == "author_reply" or author == _compact(model_author)


def build_model_comment_threads(
    rows: Iterable[sqlite3.Row],
    model_author: str = MODEL_AUTHOR,
) -> list[dict[str, Any]]:
    ordered = sorted(list(rows), key=_comment_order)
    by_source_id = {str(row["source_comment_id"]): row for row in ordered}
    threads: list[dict[str, Any]] = []

    for index, reply in enumerate(ordered):
        if not _is_model_reply(reply, model_author=model_author) or not _compact(reply["text"]):
            continue
        raw = _from_json(reply["raw_json"], {})
        question = None
        parent_id = str(raw.get("parent_source_comment_id") or "")
        if parent_id:
            question = by_source_id.get(parent_id)

        if question is None:
            reply_group = _comment_order(reply)[0]
            for candidate in reversed(ordered[:index]):
                if _is_model_reply(candidate, model_author=model_author):
                    continue
                candidate_group = _comment_order(candidate)[0]
                if reply_group and candidate_group and candidate_group != reply_group:
                    continue
                question = candidate
                break

        threads.append(
            {
                "reply_id": int(reply["id"]),
                "reply": _compact(reply["text"]),
                "reply_author": reply["author"],
                "question_id": int(question["id"]) if question is not None else None,
                "question": _compact(question["text"]) if question is not None else "",
                "question_author": question["author"] if question is not None else "",
                "published_at": reply["published_at"] or reply["captured_at"],
                "source": reply["source"],
            }
        )
    return threads


def ensure_knowledge_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_records (
            video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
            retrieval_title TEXT NOT NULL,
            title_kind TEXT NOT NULL DEFAULT 'derived',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            source_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL DEFAULT 0,
            author_role TEXT NOT NULL,
            evidence_priority INTEGER NOT NULL,
            retrieval_title TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            context TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            source_url TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(video_id, source_type, source_table, source_id, ordinal)
        );

        CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_video
            ON knowledge_chunks(video_id, evidence_priority DESC);
        CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_type
            ON knowledge_chunks(source_type, published_at DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
            retrieval_title,
            keywords,
            content,
            context,
            content='knowledge_chunks',
            content_rowid='id',
            tokenize='trigram'
        );

        CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
            INSERT INTO knowledge_chunks_fts(rowid, retrieval_title, keywords, content, context)
            VALUES (new.id, new.retrieval_title, new.keywords, new.content, new.context);
        END;
        CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ad AFTER DELETE ON knowledge_chunks BEGIN
            INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, retrieval_title, keywords, content, context)
            VALUES ('delete', old.id, old.retrieval_title, old.keywords, old.content, old.context);
        END;
        CREATE TRIGGER IF NOT EXISTS knowledge_chunks_au AFTER UPDATE ON knowledge_chunks BEGIN
            INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, retrieval_title, keywords, content, context)
            VALUES ('delete', old.id, old.retrieval_title, old.keywords, old.content, old.context);
            INSERT INTO knowledge_chunks_fts(rowid, retrieval_title, keywords, content, context)
            VALUES (new.id, new.retrieval_title, new.keywords, new.content, new.context);
        END;

        CREATE TABLE IF NOT EXISTS knowledge_dirty (
            video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
            marked_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS knowledge_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS videos_knowledge_ai AFTER INSERT ON videos BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS videos_knowledge_au AFTER UPDATE ON videos BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS transcripts_knowledge_ai AFTER INSERT ON transcripts BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS transcripts_knowledge_au AFTER UPDATE ON transcripts BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS transcripts_knowledge_ad AFTER DELETE ON transcripts BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (old.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS comments_knowledge_ai AFTER INSERT ON comments BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS comments_knowledge_au AFTER UPDATE ON comments BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS comments_knowledge_ad AFTER DELETE ON comments BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (old.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS notes_knowledge_ai AFTER INSERT ON video_notes BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS notes_knowledge_au AFTER UPDATE ON video_notes BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS notes_knowledge_ad AFTER DELETE ON video_notes BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (old.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS titles_knowledge_ai AFTER INSERT ON video_titles BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS titles_knowledge_au AFTER UPDATE ON video_titles BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (new.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        CREATE TRIGGER IF NOT EXISTS titles_knowledge_ad AFTER DELETE ON video_titles BEGIN
            INSERT INTO knowledge_dirty(video_id, marked_at)
            VALUES (old.video_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at;
        END;
        """
    )
    version_row = conn.execute(
        "SELECT value FROM knowledge_meta WHERE key = 'index_version'"
    ).fetchone()
    if version_row is None or str(version_row[0]) != INDEX_VERSION:
        conn.execute(
            """
            INSERT INTO knowledge_dirty(video_id, marked_at)
            SELECT id, ? FROM videos WHERE 1
            ON CONFLICT(video_id) DO UPDATE SET marked_at=excluded.marked_at
            """,
            (_now_iso(),),
        )
        conn.execute(
            """
            INSERT INTO knowledge_meta(key, value) VALUES ('index_version', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (INDEX_VERSION,),
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO knowledge_dirty(video_id, marked_at)
        SELECT v.id, ? FROM videos v
        LEFT JOIN knowledge_records kr ON kr.video_id = v.id
        WHERE kr.video_id IS NULL
        """,
        (_now_iso(),),
    )


def refresh_video_knowledge(conn: sqlite3.Connection, video_id: int, *, force: bool = False) -> bool:
    conn.row_factory = sqlite3.Row
    video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if video is None:
        conn.execute("DELETE FROM knowledge_records WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM knowledge_chunks WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM knowledge_dirty WHERE video_id = ?", (video_id,))
        return False

    notes = {
        row["note_type"]: row
        for row in conn.execute(
            "SELECT * FROM video_notes WHERE video_id = ? ORDER BY updated_at DESC, id DESC",
            (video_id,),
        ).fetchall()
    }
    transcripts = conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ? ORDER BY created_at DESC, id DESC",
        (video_id,),
    ).fetchall()
    comments = conn.execute(
        "SELECT * FROM comments WHERE video_id = ? ORDER BY captured_at ASC, id ASC",
        (video_id,),
    ).fetchall()
    title_row = conn.execute(
        "SELECT * FROM video_titles WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    content_original = conn.execute(
        "SELECT * FROM content_originals WHERE content_id = ?",
        (video_id,),
    ).fetchone()
    keyword_set = conn.execute(
        "SELECT * FROM content_keyword_sets WHERE content_id = ?",
        (video_id,),
    ).fetchone()
    keyword_rows = conn.execute(
        """
        SELECT category, keyword, normalized_keyword, ordinal
        FROM content_keywords
        WHERE content_id = ?
        ORDER BY ordinal ASC, id ASC
        """,
        (video_id,),
    ).fetchall()

    source_payload = {
        "index_version": INDEX_VERSION,
        "video": {key: video[key] for key in video.keys()},
        "notes": [{key: row[key] for key in row.keys()} for row in notes.values()],
        "transcripts": [{key: row[key] for key in row.keys()} for row in transcripts],
        "comments": [{key: row[key] for key in row.keys()} for row in comments],
        "title": {key: title_row[key] for key in title_row.keys()} if title_row else None,
        "content_original": (
            {key: content_original[key] for key in content_original.keys()}
            if content_original
            else None
        ),
        "keyword_set": (
            {key: keyword_set[key] for key in keyword_set.keys()}
            if keyword_set
            else None
        ),
        "keywords": [
            {key: row[key] for key in row.keys()}
            for row in keyword_rows
        ],
    }
    digest = _source_hash(source_payload)
    current = conn.execute(
        "SELECT source_hash FROM knowledge_records WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if not force and current is not None and current["source_hash"] == digest:
        conn.execute("DELETE FROM knowledge_dirty WHERE video_id = ?", (video_id,))
        return False

    video_text_note = notes.get("video_text")
    interpretation_note = notes.get("interpretation")
    ai_keywords_note = notes.get("ai_keywords")
    video_text = str(
        content_original["original_text"]
        if content_original
        else (video_text_note["text"] if video_text_note else "")
    ).strip()
    interpretation = str(interpretation_note["text"] if interpretation_note else "").strip()
    model_author = str(video["author"] or MODEL_AUTHOR)
    comment_threads = build_model_comment_threads(comments, model_author=model_author)

    title_inputs = [video_text]
    title_inputs.extend(
        f"{thread['question']}—{thread['reply']}" if thread["question"] else thread["reply"]
        for thread in comment_threads
    )
    title_inputs.append(interpretation)
    active_title = _compact(title_row["active_title"] if title_row else "")
    if active_title:
        retrieval_title = active_title[:120]
        title_kind = str(title_row["title_source"] or "cover_ocr")
    else:
        retrieval_title, title_kind = _derive_retrieval_title(
            str(video["title"] or ""),
            title_inputs,
            video["published_at"] or video["discovered_at"],
            model_author,
        )
    saved_keyword_data = _from_json(ai_keywords_note["text"], {}) if ai_keywords_note else {}
    saved_keywords = saved_keyword_data.get("keywords", []) if isinstance(saved_keyword_data, dict) else []
    keyword_categories: list[str] = []
    if keyword_set is not None:
        keywords = []
        keyword_keys: set[str] = set()
        category_keys: set[str] = set()
        for row in keyword_rows:
            category = _compact(row["category"])
            if category and category not in category_keys:
                category_keys.add(category)
                keyword_categories.append(category)
            item = _compact(row["keyword"]).strip("#，,。；;：:、 ")
            key = item.casefold()
            if not item or len(item) > 40 or key in keyword_keys:
                continue
            keyword_keys.add(key)
            keywords.append(item)
            if len(keywords) >= 40:
                break
    elif isinstance(saved_keywords, list) and any(_compact(item) for item in saved_keywords):
        keywords = []
        keyword_keys: set[str] = set()
        for value in saved_keywords:
            item = _compact(value).strip("#，,。；;：:、 ")
            key = item.casefold()
            if not item or len(item) > 40 or key in keyword_keys:
                continue
            keyword_keys.add(key)
            keywords.append(item)
            if len(keywords) >= 40:
                break
    else:
        keywords = _extract_keywords(
            retrieval_title,
            str(video["title"] or ""),
            title_inputs,
            video["raw_json"],
        )
    keywords_text = " ".join([*keyword_categories, *keywords])
    now = _now_iso()

    conn.execute("DELETE FROM knowledge_chunks WHERE video_id = ?", (video_id,))
    conn.execute(
        """
        INSERT INTO knowledge_records (
            video_id, retrieval_title, title_kind, keywords_json, source_hash, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            retrieval_title=excluded.retrieval_title,
            title_kind=excluded.title_kind,
            keywords_json=excluded.keywords_json,
            source_hash=excluded.source_hash,
            updated_at=excluded.updated_at
        """,
        (video_id, retrieval_title, title_kind, _to_json(keywords), digest, now),
    )

    def insert_chunks(
        *,
        source_type: str,
        source_table: str,
        source_id: int,
        text: str,
        author_role: str,
        context: str = "",
        published_at: str | None = None,
    ) -> None:
        for ordinal, chunk in enumerate(_chunk_text(text), start=1):
            conn.execute(
                """
                INSERT INTO knowledge_chunks (
                    video_id, source_type, source_table, source_id, ordinal,
                    author_role, evidence_priority, retrieval_title, keywords,
                    content, context, published_at, source_url, content_hash,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    source_type,
                    source_table,
                    source_id,
                    ordinal,
                    author_role,
                    EVIDENCE_PRIORITIES[source_type],
                    retrieval_title,
                    keywords_text,
                    chunk,
                    context,
                    published_at or video["published_at"] or video["discovered_at"],
                    str(video["url"] or ""),
                    _source_hash({"content": chunk, "context": context}),
                    now,
                    now,
                ),
            )

    # Every record needs one searchable metadata row, including videos whose
    # transcript/interpretation has not been entered yet.  This row is only a
    # locator and is deliberately lower-priority than every substantive source.
    if retrieval_title:
        insert_chunks(
            source_type="video_title",
            source_table="video_titles" if title_row else "videos",
            source_id=video_id,
            text=retrieval_title,
            author_role="record_metadata",
        )

    if video_text:
        insert_chunks(
            source_type="video_original",
            source_table="content_originals" if content_original else "video_notes",
            source_id=(
                int(content_original["content_id"])
                if content_original
                else int(video_text_note["id"])
            ),
            text=video_text,
            author_role="model_original",
        )
    for thread in comment_threads:
        context = f"用户问题：{thread['question']}" if thread["question"] else ""
        insert_chunks(
            source_type="model_comment_reply",
            source_table="comments",
            source_id=int(thread["reply_id"]),
            text=thread["reply"],
            author_role="model_reply",
            context=context,
            published_at=thread["published_at"],
        )

    if interpretation:
        insert_chunks(
            source_type="user_interpretation",
            source_table="video_notes",
            source_id=int(interpretation_note["id"]),
            text=interpretation,
            author_role="user_interpretation",
        )

    conn.execute("DELETE FROM knowledge_dirty WHERE video_id = ?", (video_id,))
    return True


def sync_dirty_knowledge(conn: sqlite3.Connection, limit: int | None = None) -> dict[str, int]:
    conn.row_factory = sqlite3.Row
    sql = "SELECT video_id FROM knowledge_dirty ORDER BY marked_at ASC"
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(1, int(limit)),)
    video_ids = [int(row["video_id"]) for row in conn.execute(sql, params).fetchall()]
    refreshed = 0
    for video_id in video_ids:
        if refresh_video_knowledge(conn, video_id):
            refreshed += 1
    return {"checked": len(video_ids), "refreshed": refreshed}
