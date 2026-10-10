from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .collectors import Entry, Source, collect_source
from .database import connect, transaction, utc_now
from .paths import BACKUPS_ROOT, DATABASE_PATH, EVIDENCE_ROOT, EXPORTS_ROOT, LIBRARY_ROOT, RAW_ROOT
from .publishers import UNKNOWN_PUBLISHER
from .rules import analyze, canonical_key
from .retention import published_within_hard_limit, run_retention_cleanup
from .thumbnails import (
    THUMBNAIL_BROWSER_CACHE_VERSION,
    invalidate_google_news_image_index,
    register_thumbnail_candidate,
)
from .web_push import WebPushSender, validate_endpoint, validate_subscription


WEB_PUSH_SENDER = WebPushSender(LIBRARY_ROOT / "notifications" / "vapid-private.pem")


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


def _notification_reason(source: Source, analysis: Any) -> dict[str, Any] | None:
    threshold = int(source.config.get("notification_min_score", 85))
    if source.trust_level < 4 or analysis.importance_score < threshold:
        return None
    allowed_events = {
        str(value) for value in source.config.get("notification_event_types", []) if str(value)
    }
    if allowed_events and analysis.event_type not in allowed_events:
        return None
    if source.config.get("notification_require_entity") and not analysis.entities:
        return None
    return {
        "importance_score": analysis.importance_score,
        "trust_level": source.trust_level,
        "event_type": analysis.event_type,
        "topics": analysis.topics,
        "entities": analysis.entities,
        "source_key": source.key,
        "reason": str(
            source.config.get("notification_reason")
            or "高可信来源与高重要度规则同时命中"
        ),
    }


def _queue_notifications(
    connection: sqlite3.Connection,
    *,
    item_id: int,
    source: Source,
    analysis: Any,
    created_at: str,
) -> None:
    reason = _notification_reason(source, analysis)
    if reason is None:
        return
    reason_json = json.dumps(reason, ensure_ascii=False)
    connection.execute(
        """
        INSERT OR IGNORE INTO notification_outbox(
            item_id, channel, status, reason_json, created_at
        ) VALUES (?, 'in_app', 'pending', ?, ?)
        """,
        (item_id, reason_json, created_at),
    )
    active_push = connection.execute(
        "SELECT COUNT(*) FROM web_push_subscriptions WHERE enabled=1"
    ).fetchone()[0]
    if active_push:
        connection.execute(
            """
            INSERT OR IGNORE INTO notification_outbox(
                item_id, channel, status, reason_json, created_at
            ) VALUES (?, 'web_push', 'pending', ?, ?)
            """,
            (item_id, reason_json, created_at),
        )


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

    _queue_notifications(
        connection,
        item_id=item_id,
        source=source,
        analysis=analysis,
        created_at=now,
    )

    if entry.image_url:
        register_thumbnail_candidate(connection, item_id, entry.image_url)

    entry_basis = "\n".join(
        (entry.source_item_id, entry.url, entry.title, entry.summary, entry.published_at or "", entry.image_url)
    )
    entry_content_hash = hashlib.sha256(entry_basis.encode("utf-8")).hexdigest()
    evidence_basis = f"{source.key}\n{entry.source_item_id}\n{entry_content_hash}\n{entry.url}"
    evidence_id = hashlib.sha256(evidence_basis.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO evidence(
            id, source_id, source_item_id, url, title, fetched_at,
            published_at, content_hash, raw_path, mime_type, http_status,
            publisher_name, publisher_url, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            publisher_name=CASE
                WHEN excluded.publisher_name<>'' THEN excluded.publisher_name
                ELSE evidence.publisher_name
            END,
            publisher_url=CASE
                WHEN excluded.publisher_url<>'' THEN excluded.publisher_url
                ELSE evidence.publisher_url
            END
        """,
        (
            evidence_id,
            source.id,
            entry.source_item_id,
            entry.url,
            entry.title,
            now,
            entry.published_at,
            entry_content_hash,
            raw_path,
            mime_type,
            http_status,
            entry.publisher,
            entry.publisher_url,
            json.dumps(
                {
                    "source_key": source.key,
                    "image_url": entry.image_url or None,
                    "publisher": entry.publisher or None,
                    "publisher_url": entry.publisher_url or None,
                    "feed_content_hash": content_hash,
                },
                ensure_ascii=False,
            ),
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
                    if not published_within_hard_limit(entry.published_at):
                        continue
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
            if new_count:
                invalidate_google_news_image_index(source.url)
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
    manifest["retention"] = run_retention_cleanup()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _decode_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    publisher_names_json = item.pop("publisher_names_json", None)
    if publisher_names_json is None:
        source_names = item.pop("source_names", "") or ""
        publisher_names = [name for name in source_names.split(",") if name]
    else:
        try:
            decoded_names = json.loads(publisher_names_json)
        except (TypeError, json.JSONDecodeError):
            decoded_names = []
        publisher_names = [name for name in decoded_names if isinstance(name, str) and name]
    item["sources"] = _preferred_publisher_names(publisher_names)
    item["topics"] = json.loads(item.pop("topics_json"))
    item["entities"] = json.loads(item.pop("entities_json"))
    item["is_saved"] = bool(item["is_saved"])
    item["is_read"] = bool(item["is_read"])
    item["thumbnail_url"] = (
        f"/api/items/{item['id']}/thumbnail?v={THUMBNAIL_BROWSER_CACHE_VERSION}"
    )
    return item


def _preferred_publisher_names(names: list[str]) -> list[str]:
    unique = list(dict.fromkeys(name for name in names if name))
    identified = [name for name in unique if name != UNKNOWN_PUBLISHER]
    return identified or unique


def query_items(
    *, topic: str = "", query: str = "", saved: bool = False, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    conditions = ["1=1"]
    values: list[Any] = []
    if topic:
        conditions.append("i.topics_json LIKE ?")
        values.append(f'%"{topic}"%')
    if query:
        conditions.append("(i.title LIKE ? OR i.summary LIKE ? OR i.entities_json LIKE ? OR t.translated_title LIKE ?)")
        token = f"%{query}%"
        values.extend([token, token, token, token])
    if saved:
        conditions.append("i.is_saved=1")
    values.extend([min(max(limit, 1), 250), max(offset, 0)])
    sql = f"""
        WITH selected AS (
            SELECT i.*, t.translated_title, t.provider AS translation_provider
            FROM items i
            LEFT JOIN item_translations t
              ON t.item_id=i.id AND t.target_language='zh-CN' AND t.original_title=i.title
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(i.published_at, i.first_seen_at) DESC, i.importance_score DESC
            LIMIT ? OFFSET ?
        )
        SELECT selected.*,
               json_group_array(DISTINCT NULLIF(e.publisher_name, '')) AS publisher_names_json
        FROM selected
        LEFT JOIN item_evidence ie ON ie.item_id=selected.id
        LEFT JOIN evidence e ON e.id=ie.evidence_id
        LEFT JOIN sources s ON s.id=e.source_id
        GROUP BY selected.id
        ORDER BY COALESCE(selected.published_at, selected.first_seen_at) DESC,
                 selected.importance_score DESC
    """
    with connect() as connection:
        rows = connection.execute(sql, values).fetchall()
    return [_decode_item(row) for row in rows]


def query_hot_items(limit: int = 40) -> list[dict[str, Any]]:
    cutoff = (datetime.now(UTC) - timedelta(hours=72)).replace(microsecond=0).isoformat()
    sql = """
        WITH selected AS (
            SELECT i.*, t.translated_title, t.provider AS translation_provider,
                   (i.importance_score + MIN(i.source_count, 5) * 24) AS hot_score
            FROM items i
            LEFT JOIN item_translations t
              ON t.item_id=i.id AND t.target_language='zh-CN' AND t.original_title=i.title
            WHERE COALESCE(i.published_at, i.first_seen_at) >= ?
            ORDER BY hot_score DESC, COALESCE(i.published_at, i.first_seen_at) DESC
            LIMIT ?
        )
        SELECT selected.*,
               json_group_array(DISTINCT NULLIF(e.publisher_name, '')) AS publisher_names_json
        FROM selected
        LEFT JOIN item_evidence ie ON ie.item_id=selected.id
        LEFT JOIN evidence e ON e.id=ie.evidence_id
        LEFT JOIN sources s ON s.id=e.source_id
        GROUP BY selected.id
        ORDER BY selected.hot_score DESC,
                 COALESCE(selected.published_at, selected.first_seen_at) DESC
    """
    with connect() as connection:
        rows = connection.execute(sql, (cutoff, min(max(limit, 1), 100))).fetchall()
    result = []
    for row in rows:
        item = _decode_item(row)
        item.pop("hot_score", None)
        result.append(item)
    return result


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
        row = connection.execute(
            """
            SELECT i.*, t.translated_title, t.provider AS translation_provider
            FROM items i
            LEFT JOIN item_translations t
              ON t.item_id=i.id AND t.target_language='zh-CN' AND t.original_title=i.title
            WHERE i.id=?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        evidence_rows = connection.execute(
            """
            SELECT e.*,
                   COALESCE(NULLIF(e.publisher_name, ''), '原站待识别') AS source_name,
                   s.name AS collection_source_name, s.trust_level
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
    item["sources"] = _preferred_publisher_names(
        [evidence["source_name"] for evidence in evidence_rows]
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
            WHERE n.status='pending' AND n.channel='in_app'
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


def web_push_status(
    path: Path | str | None = None,
    *,
    sender: WebPushSender = WEB_PUSH_SENDER,
) -> dict[str, Any]:
    with connect(path) as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM web_push_subscriptions WHERE enabled=1"
        ).fetchone()[0]
    if not sender.available:
        return {
            "available": False,
            "active_subscriptions": active,
            "public_key": "",
            "message": "服务器暂不具备手机推送加密组件。",
        }
    try:
        public_key = sender.public_key()
    except (OSError, RuntimeError, ValueError) as error:
        return {
            "available": False,
            "active_subscriptions": active,
            "public_key": "",
            "message": f"手机推送密钥暂不可用：{type(error).__name__}",
        }
    return {
        "available": True,
        "active_subscriptions": active,
        "public_key": public_key,
        "message": "只推送规则确认的高相关重要新消息。",
    }


def save_web_push_subscription(
    payload: object,
    path: Path | str | None = None,
    *,
    sender: WebPushSender = WEB_PUSH_SENDER,
) -> dict[str, Any]:
    if not sender.available:
        raise ValueError("服务器暂不支持手机推送。")
    checked = validate_subscription(payload)
    subscription_id = hashlib.sha256(checked["endpoint"].encode("utf-8")).hexdigest()
    now = utc_now()
    with transaction(path) as connection:
        connection.execute(
            """
            INSERT INTO web_push_subscriptions(
                id, endpoint, p256dh, auth_secret, enabled, created_at, updated_at,
                last_success_at, last_error, failure_count
            ) VALUES (?, ?, ?, ?, 1, ?, ?, NULL, NULL, 0)
            ON CONFLICT(id) DO UPDATE SET
                endpoint=excluded.endpoint,
                p256dh=excluded.p256dh,
                auth_secret=excluded.auth_secret,
                enabled=1,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                last_error=NULL,
                failure_count=0
            """,
            (
                subscription_id,
                checked["endpoint"],
                checked["p256dh"],
                checked["auth"],
                now,
                now,
            ),
        )
    result = sender.send(
        checked,
        {
            "title": "即时 AI 手机通知已开启",
            "body": "后续只提醒高相关、重要的新消息。",
            "url": "/",
            "tag": "instant-ai-push-ready",
        },
    )
    with transaction(path) as connection:
        if result.ok:
            connection.execute(
                """
                UPDATE web_push_subscriptions
                SET last_success_at=?, last_error=NULL, failure_count=0, updated_at=?
                WHERE id=?
                """,
                (utc_now(), utc_now(), subscription_id),
            )
        else:
            connection.execute(
                """
                UPDATE web_push_subscriptions
                SET enabled=?, last_error=?, failure_count=1, updated_at=?
                WHERE id=?
                """,
                (0 if result.expired else 1, result.error, utc_now(), subscription_id),
            )
    return {
        "ok": True,
        "test_sent": result.ok,
        "message": (
            "手机通知已开启，测试通知已经发出。"
            if result.ok
            else "手机通知已保存；测试通知暂未送达，系统会在后续重要消息时重试。"
        ),
    }


def remove_web_push_subscription(
    endpoint: str,
    path: Path | str | None = None,
) -> bool:
    checked_endpoint = validate_endpoint(endpoint)
    subscription_id = hashlib.sha256(checked_endpoint.encode("utf-8")).hexdigest()
    with transaction(path) as connection:
        cursor = connection.execute(
            """
            UPDATE web_push_subscriptions
            SET enabled=0, updated_at=?, last_error=NULL
            WHERE id=? AND endpoint=?
            """,
            (utc_now(), subscription_id, checked_endpoint),
        )
    return cursor.rowcount > 0


def dispatch_web_push_notifications(
    path: Path | str | None = None,
    *,
    sender: WebPushSender = WEB_PUSH_SENDER,
    limit: int = 20,
) -> dict[str, int]:
    summary = {"attempted": 0, "delivered": 0, "failed": 0, "expired": 0}
    if not sender.available:
        return summary
    now = utc_now()
    with transaction(path) as connection:
        notifications = connection.execute(
            """
            SELECT n.id
            FROM notification_outbox n
            WHERE n.channel='web_push' AND n.status='pending'
            ORDER BY n.created_at ASC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
        for notification in notifications:
            connection.execute(
                """
                INSERT OR IGNORE INTO web_push_deliveries(
                    notification_id, subscription_id, state, attempts, updated_at
                )
                SELECT ?, id, 'pending', 0, ?
                FROM web_push_subscriptions
                WHERE enabled=1 AND created_at <= (
                    SELECT created_at FROM notification_outbox WHERE id=?
                )
                """,
                (notification["id"], now, notification["id"]),
            )
        tasks = connection.execute(
            """
            SELECT d.notification_id, d.subscription_id, d.attempts,
                   s.endpoint, s.p256dh, s.auth_secret,
                   i.id AS item_id, i.title, i.event_type,
                   n.reason_json
            FROM web_push_deliveries d
            JOIN web_push_subscriptions s ON s.id=d.subscription_id
            JOIN notification_outbox n ON n.id=d.notification_id
            JOIN items i ON i.id=n.item_id
            WHERE n.channel='web_push' AND n.status='pending'
              AND s.enabled=1
              AND d.state IN ('pending', 'retry')
              AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
            ORDER BY n.created_at ASC
            LIMIT ?
            """,
            (now, max(1, min(limit * 4, 200))),
        ).fetchall()

    touched: set[int] = set()
    for row in tasks:
        summary["attempted"] += 1
        touched.add(int(row["notification_id"]))
        reason = json.loads(row["reason_json"])
        entities = [str(value) for value in reason.get("entities", []) if str(value)]
        heading = "、".join(entities[:2]) or row["event_type"] or "重要消息"
        result = sender.send(
            {
                "endpoint": row["endpoint"],
                "p256dh": row["p256dh"],
                "auth": row["auth_secret"],
            },
            {
                "title": f"即时 AI · {heading}",
                "body": row["title"],
                "url": f"/?item={row['item_id']}",
                "tag": f"instant-ai-item-{row['item_id']}",
                "item_id": row["item_id"],
            },
        )
        attempts = int(row["attempts"]) + 1
        with transaction(path) as connection:
            if result.ok:
                summary["delivered"] += 1
                connection.execute(
                    """
                    UPDATE web_push_deliveries
                    SET state='delivered', attempts=?, next_attempt_at=NULL,
                        last_status=?, last_error=NULL, updated_at=?, delivered_at=?
                    WHERE notification_id=? AND subscription_id=?
                    """,
                    (
                        attempts, result.status, utc_now(), utc_now(),
                        row["notification_id"], row["subscription_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE web_push_subscriptions
                    SET last_success_at=?, last_error=NULL, failure_count=0, updated_at=?
                    WHERE id=?
                    """,
                    (utc_now(), utc_now(), row["subscription_id"]),
                )
            else:
                expired = result.expired
                final_failure = expired or attempts >= 5
                summary["expired" if expired else "failed"] += 1
                next_attempt = None if final_failure else (
                    datetime.now(UTC) + timedelta(minutes=min(5 * (2 ** (attempts - 1)), 60))
                ).replace(microsecond=0).isoformat()
                connection.execute(
                    """
                    UPDATE web_push_deliveries
                    SET state=?, attempts=?, next_attempt_at=?, last_status=?,
                        last_error=?, updated_at=?
                    WHERE notification_id=? AND subscription_id=?
                    """,
                    (
                        "failed" if final_failure else "retry",
                        attempts,
                        next_attempt,
                        result.status,
                        result.error,
                        utc_now(),
                        row["notification_id"],
                        row["subscription_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE web_push_subscriptions
                    SET enabled=?, last_error=?, failure_count=failure_count+1, updated_at=?
                    WHERE id=?
                    """,
                    (0 if expired else 1, result.error, utc_now(), row["subscription_id"]),
                )

    with transaction(path) as connection:
        for notification_id in touched:
            states = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN state IN ('pending', 'retry') THEN 1 ELSE 0 END) AS remaining,
                    SUM(CASE WHEN state='delivered' THEN 1 ELSE 0 END) AS delivered
                FROM web_push_deliveries WHERE notification_id=?
                """,
                (notification_id,),
            ).fetchone()
            if int(states["remaining"] or 0) == 0:
                connection.execute(
                    "UPDATE notification_outbox SET status=? WHERE id=?",
                    ("delivered" if int(states["delivered"] or 0) else "failed", notification_id),
                )
    return summary


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
            "SELECT COUNT(*) FROM notification_outbox WHERE status='pending' AND channel='in_app'"
        ).fetchone()[0]
        active_push_subscriptions = connection.execute(
            "SELECT COUNT(*) FROM web_push_subscriptions WHERE enabled=1"
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
        "notifications": {
            "pending": pending_notifications,
            "mobile_subscriptions": active_push_subscriptions,
        },
        "ai_jobs": ai_jobs,
        "retention": {
            "ordinary_hours": 72,
            "important_days": 5,
            "critical_days": 7,
            "archive_enabled": False,
        },
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
        writer.writerow(["中文标题", "英文原题", "主题", "事件类型", "重要度", "发布时间", "来源链接", "摘要"])
        for item in rows:
            writer.writerow([
                item.get("translated_title") or item["title"], item["title"],
                "、".join(item["topics"]), item["event_type"],
                item["importance_score"], item["published_at"] or item["first_seen_at"],
                item["url"], item["summary"],
            ])
    return target
