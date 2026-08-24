from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .collectors import Entry, Source, collect_source
from .database import connect, transaction, utc_now
from .paths import BACKUPS_ROOT, DATABASE_PATH, EVIDENCE_ROOT, EXPORTS_ROOT, LIBRARY_ROOT, RAW_ROOT
from .rules import analyze, canonical_key


def _source_from_row(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        key=row["key"],
        name=row["name"],
        kind=row["kind"],
        url=row["url"],
        trust_level=row["trust_level"],
        topic_hints=json.loads(row["topic_hints_json"]),
        config=json.loads(row["config_json"]),
        etag=row["etag"],
        last_modified=row["last_modified"],
    )


def list_sources(enabled_only: bool = False) -> list[dict[str, Any]]:
    with connect() as connection:
        sql = "SELECT * FROM sources"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY trust_level DESC, name"
        rows = connection.execute(sql).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["topic_hints"] = json.loads(item.pop("topic_hints_json"))
        item["config"] = json.loads(item.pop("config_json"))
        item["enabled"] = bool(item["enabled"])
        result.append(item)
    return result


def _upsert_entry(
    connection: sqlite3.Connection,
    source: Source,
    entry: Entry,
    content_hash: str,
    raw_path: str,
    mime_type: str,
    http_status: int,
) -> tuple[bool, bool]:
    now = utc_now()
    analysis = analyze(entry.title, entry.summary, source.trust_level, source.topic_hints)
    key = canonical_key(entry.url, entry.title)
    existing = connection.execute("SELECT id, summary FROM items WHERE canonical_key = ?", (key,)).fetchone()
    is_new = existing is None
    is_updated = False

    if is_new:
        cursor = connection.execute(
            """
            INSERT INTO items(
                canonical_key, title, url, summary, published_at, first_seen_at,
                last_seen_at, importance_score, trust_level, topics_json,
                entities_json, event_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                entry.title,
                entry.url,
                entry.summary,
                entry.published_at,
                now,
                now,
                analysis.importance_score,
                source.trust_level,
                json.dumps(analysis.topics, ensure_ascii=False),
                json.dumps(analysis.entities, ensure_ascii=False),
                analysis.event_type,
            ),
        )
        item_id = int(cursor.lastrowid)
        if analysis.importance_score >= 85 and source.trust_level >= 4:
            connection.execute(
                """
                INSERT OR IGNORE INTO notification_outbox(
                    item_id, channel, status, reason_json, created_at
                ) VALUES (?, 'in_app', 'pending', ?, ?)
                """,
                (
                    item_id,
                    json.dumps(
                        {
                            "importance_score": analysis.importance_score,
                            "trust_level": source.trust_level,
                            "event_type": analysis.event_type,
                            "topics": analysis.topics,
                            "reason": "高可信官方来源与高重要度规则同时命中",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
    else:
        item_id = int(existing["id"])
        replacement_summary = entry.summary if len(entry.summary) > len(existing["summary"] or "") else existing["summary"]
        connection.execute(
            """
            UPDATE items SET
                title=?, url=?, summary=?, published_at=COALESCE(?, published_at),
                last_seen_at=?, importance_score=MAX(importance_score, ?),
                trust_level=MAX(trust_level, ?), topics_json=?, entities_json=?, event_type=?
            WHERE id=?
            """,
            (
                entry.title,
                entry.url,
                replacement_summary,
                entry.published_at,
                now,
                analysis.importance_score,
                source.trust_level,
                json.dumps(analysis.topics, ensure_ascii=False),
                json.dumps(analysis.entities, ensure_ascii=False),
                analysis.event_type,
                item_id,
            ),
        )
        is_updated = True

    evidence_basis = f"{source.key}\n{entry.source_item_id}\n{content_hash}\n{entry.url}"
    evidence_id = hashlib.sha256(evidence_basis.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT OR IGNORE INTO evidence(
            id, source_id, source_item_id, url, title, fetched_at,
            published_at, content_hash, raw_path, mime_type, http_status, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            source.id,
            entry.source_item_id,
            entry.url,
            entry.title,
            now,
            entry.published_at,
            content_hash,
            raw_path,
            mime_type,
            http_status,
            json.dumps({"source_key": source.key}, ensure_ascii=False),
        ),
    )
    connection.execute(
        "INSERT OR IGNORE INTO item_evidence(item_id, evidence_id) VALUES (?, ?)",
        (item_id, evidence_id),
    )
    source_count = connection.execute(
        "SELECT COUNT(DISTINCT e.source_id) FROM item_evidence ie JOIN evidence e ON e.id=ie.evidence_id WHERE ie.item_id=?",
        (item_id,),
    ).fetchone()[0]
    connection.execute("UPDATE items SET source_count=? WHERE id=?", (source_count, item_id))
    return is_new, is_updated


def run_collection() -> dict[str, Any]:
    started = utc_now()
    with transaction() as connection:
        cursor = connection.execute(
            "INSERT INTO collection_runs(started_at, status) VALUES (?, 'running')",
            (started,),
        )
        run_id = int(cursor.lastrowid)

    with connect() as connection:
        rows = connection.execute("SELECT * FROM sources WHERE enabled=1 ORDER BY trust_level DESC, id").fetchall()
    sources = [_source_from_row(row) for row in rows]
    totals = {"source_count": len(sources), "fetched_count": 0, "new_count": 0, "updated_count": 0, "error_count": 0}
    details: list[dict[str, Any]] = []

    for source in sources:
        source_detail: dict[str, Any] = {"source": source.name, "key": source.key}
        try:
            result, entries, content_hash, raw_path = collect_source(source)
            if result.status == 304:
                with transaction() as connection:
                    connection.execute(
                        """
                        UPDATE sources
                        SET last_success_at=?, last_error=NULL, updated_at=?
                        WHERE id=?
                        """,
                        (utc_now(), utc_now(), source.id),
                    )
                source_detail.update(status="not_modified", items=0)
                details.append(source_detail)
                continue
            new_count = 0
            updated_count = 0
            with transaction() as connection:
                for entry in entries:
                    is_new, is_updated = _upsert_entry(
                        connection,
                        source,
                        entry,
                        content_hash,
                        raw_path,
                        result.content_type,
                        result.status,
                    )
                    new_count += int(is_new)
                    updated_count += int(is_updated)
                connection.execute(
                    """
                    UPDATE sources SET etag=?, last_modified=?, last_success_at=?,
                        last_error=NULL, last_item_count=?, updated_at=? WHERE id=?
                    """,
                    (result.etag, result.last_modified, utc_now(), len(entries), utc_now(), source.id),
                )
            totals["fetched_count"] += len(entries)
            totals["new_count"] += new_count
            totals["updated_count"] += updated_count
            source_detail.update(status="ok", items=len(entries), new=new_count, updated=updated_count)
        except Exception as error:  # source isolation is intentional
            totals["error_count"] += 1
            message = f"{type(error).__name__}: {error}"[:1000]
            with transaction() as connection:
                connection.execute(
                    "UPDATE sources SET last_error=?, updated_at=? WHERE id=?",
                    (message, utc_now(), source.id),
                )
            source_detail.update(status="error", error=message)
        details.append(source_detail)

    status = "success" if totals["error_count"] == 0 else ("partial" if totals["fetched_count"] else "failed")
    finished = utc_now()
    with transaction() as connection:
        connection.execute(
            """
            UPDATE collection_runs SET finished_at=?, status=?, source_count=?,
                fetched_count=?, new_count=?, updated_count=?, error_count=?, details_json=?
            WHERE id=?
            """,
            (
                finished, status, totals["source_count"], totals["fetched_count"],
                totals["new_count"], totals["updated_count"], totals["error_count"],
                json.dumps(details, ensure_ascii=False), run_id,
            ),
        )
    manifest = {
        "run_id": run_id,
        "started_at": started,
        "finished_at": finished,
        "status": status,
        **totals,
        "details": details,
    }
    manifest_root = EVIDENCE_ROOT / "runs"
    manifest_root.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_root / f"run-{run_id:06d}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _decode_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    source_names = item.pop("source_names", "") or ""
    item["sources"] = [name for name in source_names.split(",") if name]
    item["topics"] = json.loads(item.pop("topics_json"))
    item["entities"] = json.loads(item.pop("entities_json"))
    item["is_saved"] = bool(item["is_saved"])
    item["is_read"] = bool(item["is_read"])
    return item


def query_items(
    *, topic: str = "", query: str = "", saved: bool = False, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    conditions = ["1=1"]
    values: list[Any] = []
    if topic:
        conditions.append("i.topics_json LIKE ?")
        values.append(f'%"{topic}"%')
    if query:
        conditions.append("(i.title LIKE ? OR i.summary LIKE ? OR i.entities_json LIKE ?)")
        token = f"%{query}%"
        values.extend([token, token, token])
    if saved:
        conditions.append("i.is_saved=1")
    values.extend([min(max(limit, 1), 250), max(offset, 0)])
    sql = f"""
        SELECT i.*, GROUP_CONCAT(DISTINCT s.name) AS source_names
        FROM items i
        LEFT JOIN item_evidence ie ON ie.item_id=i.id
        LEFT JOIN evidence e ON e.id=ie.evidence_id
        LEFT JOIN sources s ON s.id=e.source_id
        WHERE {' AND '.join(conditions)}
        GROUP BY i.id
        ORDER BY COALESCE(i.published_at, i.first_seen_at) DESC, i.importance_score DESC
        LIMIT ? OFFSET ?
    """
    with connect() as connection:
        rows = connection.execute(sql, values).fetchall()
    return [_decode_item(row) for row in rows]


def reclassify_items() -> int:
    """Re-run deterministic topic/entity rules after the monitored universe changes."""

    with transaction() as connection:
        rows = connection.execute(
            "SELECT id, title, summary, trust_level FROM items ORDER BY id"
        ).fetchall()
        for row in rows:
            source_rows = connection.execute(
                """
                SELECT DISTINCT s.topic_hints_json
                FROM item_evidence ie
                JOIN evidence e ON e.id=ie.evidence_id
                JOIN sources s ON s.id=e.source_id
                WHERE ie.item_id=?
                """,
                (row["id"],),
            ).fetchall()
            hints: list[str] = []
            for source_row in source_rows:
                for hint in json.loads(source_row["topic_hints_json"]):
                    if hint not in hints:
                        hints.append(hint)
            result = analyze(row["title"], row["summary"], row["trust_level"], hints)
            connection.execute(
                """
                UPDATE items
                SET importance_score=?, topics_json=?, entities_json=?, event_type=?
                WHERE id=?
                """,
                (
                    result.importance_score,
                    json.dumps(result.topics, ensure_ascii=False),
                    json.dumps(result.entities, ensure_ascii=False),
                    result.event_type,
                    row["id"],
                ),
            )
    return len(rows)


def get_item(item_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        evidence_rows = connection.execute(
            """
            SELECT e.*, s.name AS source_name, s.trust_level
            FROM item_evidence ie
            JOIN evidence e ON e.id=ie.evidence_id
            JOIN sources s ON s.id=e.source_id
            WHERE ie.item_id=? ORDER BY e.fetched_at DESC
            """,
            (item_id,),
        ).fetchall()
        ai_row = connection.execute(
            "SELECT id, status, provider, model, prompt_version, result_json, error, created_at, updated_at "
            "FROM ai_jobs WHERE item_id=? ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
    item = _decode_item(row)
    item["evidence"] = [dict(evidence) for evidence in evidence_rows]
    item["sources"] = list(
        dict.fromkeys(evidence["source_name"] for evidence in evidence_rows)
    )
    item["ai_job"] = dict(ai_row) if ai_row else None
    if item["ai_job"] and item["ai_job"]["result_json"]:
        item["ai_job"]["result"] = json.loads(item["ai_job"].pop("result_json"))
    return item


def backfill_notifications(limit: int = 5) -> int:
    """Create a small first inbox for existing high-confidence items."""

    now = utc_now()
    with transaction() as connection:
        rows = connection.execute(
            """
            SELECT id, importance_score, trust_level, event_type, topics_json
            FROM items
            WHERE importance_score >= 85 AND trust_level >= 4
            ORDER BY importance_score DESC, COALESCE(published_at, first_seen_at) DESC
            LIMIT ?
            """,
            (max(0, min(limit, 20)),),
        ).fetchall()
        inserted = 0
        for row in rows:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO notification_outbox(
                    item_id, channel, status, reason_json, created_at
                ) VALUES (?, 'in_app', 'pending', ?, ?)
                """,
                (
                    row["id"],
                    json.dumps(
                        {
                            "importance_score": row["importance_score"],
                            "trust_level": row["trust_level"],
                            "event_type": row["event_type"],
                            "topics": json.loads(row["topics_json"]),
                            "reason": "高可信官方来源与高重要度规则同时命中",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            inserted += max(cursor.rowcount, 0)
    return inserted


def list_notifications(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT n.*, i.title, i.summary, i.url, i.importance_score,
                   i.topics_json, i.event_type, i.published_at, i.first_seen_at
            FROM notification_outbox n
            JOIN items i ON i.id=n.item_id
            WHERE n.status='pending'
            ORDER BY i.importance_score DESC, n.created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    notifications = []
    for row in rows:
        notification = dict(row)
        notification["reason"] = json.loads(notification.pop("reason_json"))
        notification["topics"] = json.loads(notification.pop("topics_json"))
        notifications.append(notification)
    return notifications


def dismiss_notification(notification_id: int) -> bool:
    with transaction() as connection:
        cursor = connection.execute(
            "UPDATE notification_outbox SET status='dismissed', dismissed_at=? WHERE id=?",
            (utc_now(), notification_id),
        )
    return cursor.rowcount > 0


def set_item_flag(item_id: int, field: str, value: bool) -> bool:
    if field not in {"is_saved", "is_read"}:
        raise ValueError("Unsupported item flag")
    with transaction() as connection:
        cursor = connection.execute(f"UPDATE items SET {field}=? WHERE id=?", (int(value), item_id))
    return cursor.rowcount > 0


def toggle_source(source_id: int, enabled: bool) -> bool:
    with transaction() as connection:
        cursor = connection.execute(
            "UPDATE sources SET enabled=?, updated_at=? WHERE id=?",
            (int(enabled), utc_now(), source_id),
        )
    return cursor.rowcount > 0


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM collection_runs ORDER BY id DESC LIMIT ?", (min(limit, 100),)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json"))
        result.append(item)
    return result


def stats() -> dict[str, Any]:
    with connect() as connection:
        counts = connection.execute(
            """
            SELECT COUNT(*) total,
                   SUM(CASE WHEN is_read=0 THEN 1 ELSE 0 END) unread,
                   SUM(CASE WHEN is_saved=1 THEN 1 ELSE 0 END) saved,
                   MAX(last_seen_at) last_seen
            FROM items
            """
        ).fetchone()
        source_counts = connection.execute(
            "SELECT COUNT(*) total, SUM(enabled) enabled, SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) errors FROM sources"
        ).fetchone()
        last_run = connection.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1").fetchone()
        pending_notifications = connection.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE status='pending'"
        ).fetchone()[0]
        ai_jobs = connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0]
    backups = sorted(BACKUPS_ROOT.glob("instant_ai-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    return {
        "items": dict(counts),
        "sources": dict(source_counts),
        "last_run": dict(last_run) if last_run else None,
        "database_path": str(DATABASE_PATH),
        "library_path": str(LIBRARY_ROOT),
        "latest_backup": str(backups[0]) if backups else None,
        "notifications": {"pending": pending_notifications},
        "ai_jobs": ai_jobs,
    }


def raw_evidence(evidence_id: str) -> tuple[Path, str] | None:
    with connect() as connection:
        row = connection.execute("SELECT raw_path, mime_type FROM evidence WHERE id=?", (evidence_id,)).fetchone()
    if row is None:
        return None
    path = Path(row["raw_path"]).resolve()
    raw_root = RAW_ROOT.resolve()
    if raw_root != path and raw_root not in path.parents:
        raise ValueError("Evidence path is outside the raw library")
    return path, row["mime_type"]


def export_csv() -> Path:
    EXPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    target = EXPORTS_ROOT / f"即时AI情报-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    rows = query_items(limit=250)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["标题", "主题", "事件类型", "重要度", "发布时间", "来源链接", "摘要"])
        for item in rows:
            writer.writerow([
                item["title"], "、".join(item["topics"]), item["event_type"],
                item["importance_score"], item["published_at"] or item["first_seen_at"],
                item["url"], item["summary"],
            ])
    return target
