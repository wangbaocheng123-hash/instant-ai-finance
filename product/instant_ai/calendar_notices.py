"""Local official agenda notices reuse the news UI and its existing push outbox.

The small receipt ledger outlives short-lived news, preventing an old agenda
from being re-announced after news retention. It contains public event metadata
only; it is neither an external memory service nor an investment record.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .collectors import Entry
from .database import connect, transaction
from .rules import Analysis
from .service import _source_from_row, _upsert_entry


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def queue_calendar_notices(*, path: object = None, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    at = _iso(now)
    counts = {"announced": 0, "changed": 0, "reminded": 0, "date_pending": 0}
    with transaction(path) as db:
        source_row = db.execute("SELECT * FROM sources WHERE key='federal-reserve-calendar'").fetchone()
        if source_row is None or not source_row['enabled']:
            if source_row is not None:
                db.execute("""UPDATE notification_outbox SET status='dismissed',dismissed_at=?
                    WHERE status='pending' AND item_id IN (SELECT item_id FROM future_calendar_notices)""", (at,))
            return counts
        source = _source_from_row(source_row)
        blocked = set()
        for state in db.execute("SELECT last_error FROM future_calendar_sources WHERE last_error<>''"):
            blocked.update(re.findall(r"conflict\[(fed:[a-z0-9:-]+)\]", state[0]))
        # Both receiving scopes may contain the same public event. Its local
        # announcement has one identity independent of receiver availability.
        rows = db.execute("""SELECT event_key,payload_json,MAX(checked_at) AS checked_at
            FROM future_calendar_events WHERE event_key LIKE 'fed:%' GROUP BY event_key""").fetchall()
        for row in rows:
            if row["event_key"] in blocked:
                continue
            event = json.loads(row["payload_json"])
            scheduled = datetime.fromisoformat(event["originalScheduledAt"]) if event["originalScheduledAt"] else None
            official_day = datetime.fromisoformat(event["date"]).date()
            zone = ZoneInfo(event["originalTimeZone"])
            local_today = now.astimezone(zone).date()
            in_window = now < scheduled <= now + timedelta(days=14) if scheduled else local_today <= official_day <= local_today + timedelta(days=14)
            if not in_window or event.get("importance", 0) < 4:
                continue
            # Never issue a new reminder from an old cached agenda during an outage.
            if now - datetime.fromisoformat(row["checked_at"]) > timedelta(minutes=30):
                continue
            event_key = row["event_key"]
            scheduled_at = _iso(scheduled) if scheduled else ""
            # Expiry for a date-only notice is the end of the official local
            # day, not an invented event start. It is never shown as a time.
            expires_at = scheduled_at if scheduled else _iso(datetime.combine(official_day + timedelta(days=1), datetime.min.time(), zone))
            schedule_hash = hashlib.sha256(scheduled_at.encode()).hexdigest() if scheduled else f"date:{official_day}"
            previous = db.execute("""SELECT * FROM future_calendar_notices
                WHERE event_key=? AND notice_kind IN ('announced','changed','date_pending')
                ORDER BY created_at DESC,rowid DESC LIMIT 1""", (event_key,)).fetchone()
            changed = previous is not None and previous["schedule_hash"] != schedule_hash
            kinds = []
            if previous is None:
                kinds.append("announced" if scheduled else "date_pending")
            elif changed:
                kinds.append("changed")
            # A newly found event inside one hour receives one useful notice,
            # rather than discovery and imminent reminders in the same tick.
            elif scheduled and timedelta(0) < scheduled - now <= timedelta(hours=1):
                kinds.append("reminded")
            title_base = (f"{event['date'][5:].replace('-', '月')}日 {event['time']}（北京时间） · {event['title']}" if scheduled
                          else f"官方当地日期 {event['date']}，具体时间待确认 · {event['title']}")
            # Refresh the wording/evidence on a retained notice without sending a
            # new alert solely for a heading change or an ET/PT representation.
            if previous and not changed and previous["item_id"] is not None:
                old_prefix = {"announced": "会议预告", "changed": "日程更新", "date_pending": "日期预告"}[previous["notice_kind"]]
                db.execute("UPDATE items SET title=? WHERE id=?", (f"【{old_prefix}】{title_base}", previous["item_id"]))
            for kind in kinds:
                transition = previous["notice_key"] if previous is not None else ""
                notice_key = hashlib.sha256(f"{event_key}|{schedule_hash}|{kind}|{transition}".encode()).hexdigest()
                if db.execute("SELECT 1 FROM future_calendar_notices WHERE notice_key=?", (notice_key,)).fetchone():
                    continue
                if kind == "reminded" and previous and scheduled - datetime.fromisoformat(previous["created_at"]) <= timedelta(hours=1):
                    continue
                if changed or kind == "reminded":
                    # The old pending time must not be delivered after a change.
                    db.execute("""UPDATE notification_outbox SET status='dismissed',dismissed_at=?
                        WHERE status='pending' AND item_id IN (
                            SELECT item_id FROM future_calendar_notices WHERE event_key=?
                        )""", (at, event_key))
                if changed:
                    db.execute("""UPDATE items SET title='【旧日程已更新】'||title
                        WHERE id IN (SELECT item_id FROM future_calendar_notices WHERE event_key=?)
                        AND title NOT LIKE '【旧日程已更新】%'""", (event_key,))
                prefix = {"announced": "会议预告", "changed": "日程更新", "reminded": "一小时内开始", "date_pending": "日期预告"}[kind]
                title = f"【{prefix}】{title_base}"
                timing = f"会议时间：{event['date']} {event['time']}（北京时间）。" if scheduled else f"官方当地举办日期：{event['date']}；具体时点和北京时间尚待官方确认。"
                summary = (f"官方日程核验：{event['title']}。{timing}"
                           f"官方原时区：{event['originalTimeZone']}。这是日程预告，尚不代表讲话已经发生。"
                           "此条时间为即时AI发现/核验时间，官方预告首次发布时间未提供。")
                if changed:
                    if previous["scheduled_at"]:
                        old_time = datetime.fromisoformat(previous["scheduled_at"]).astimezone(zone)
                        summary += f" 官方安排已变化，原时间（{event['originalTimeZone']}）：{old_time:%Y-%m-%d %H:%M}；请以本条和官方议程为准。"
                    else:
                        summary += " 此前仅公布日期，本次补充或更新官方时间；请以本条和官方议程为准。"
                entry = Entry(notice_key, title, event["source"]["url"], summary, None,
                              publisher=event["source"]["name"], publisher_url=event["source"]["url"])
                topics = ["全球财经", "华尔街", "宏观政策"]
                if any(word in (event["title"] + event["source"].get("evidenceExcerpt", "")).lower()
                       for word in ["人工智能", "生产率", "artificial intelligence", "productivity", "super intelligence"]):
                    topics.append("AI产业链")
                analysis = Analysis(topics, ["美联储"], f"官方日程{prefix}", 95 if event["importance"] >= 5 else 86)
                content_hash = hashlib.sha256(json.dumps(event, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                _upsert_entry(db, source, entry, content_hash, "", "application/json", 200,
                              canonical_identity=f"official-calendar-notice:{notice_key}", observed_at=at, analysis_override=analysis)
                canonical = hashlib.sha256(f"official-calendar-notice:{notice_key}".encode()).hexdigest()
                item_id = db.execute("SELECT id FROM items WHERE canonical_key=?", (canonical,)).fetchone()[0]
                for notification in db.execute("SELECT id,reason_json FROM notification_outbox WHERE item_id=?", (item_id,)).fetchall():
                    reason = json.loads(notification["reason_json"])
                    reason["calendar_event_key"] = event_key
                    reason["scheduled_at"] = scheduled_at
                    db.execute("UPDATE notification_outbox SET reason_json=? WHERE id=?", (json.dumps(reason, ensure_ascii=False), notification["id"]))
                db.execute("INSERT INTO future_calendar_notices VALUES(?,?,?,?,?,?,?,?)",
                           (notice_key, event_key, schedule_hash, kind, item_id, at, scheduled_at, expires_at))
                counts[kind] += 1
        db.execute("DELETE FROM future_calendar_notices WHERE expires_at<?", (_iso(now - timedelta(days=7)),))
    return counts


def calendar_snapshot(*, path: object = None, now: datetime | None = None) -> dict[str, Any]:
    """Owner-facing, credential-free discovery state and upcoming local agendas."""
    from .future_events import _sources

    now = now or datetime.now(UTC)
    with connect(path) as db:
        states = {row["source_key"]: dict(row) for row in db.execute("SELECT * FROM future_calendar_sources")}
        source = db.execute("SELECT enabled FROM sources WHERE key='federal-reserve-calendar'").fetchone()
        enabled = source is None or bool(source[0])
        rows = db.execute("""SELECT event_key,payload_json,MAX(checked_at) AS checked_at
            FROM future_calendar_events WHERE event_key LIKE 'fed:%' GROUP BY event_key""").fetchall()
        pending = db.execute("SELECT COUNT(*) FROM future_calendar_events WHERE version>delivered_version AND json_extract(payload_json,'$.originalScheduledAt')<>''").fetchone()[0]
        failed = db.execute("SELECT COUNT(*) FROM future_calendar_events WHERE version>delivered_version AND json_extract(payload_json,'$.originalScheduledAt')<>'' AND last_error<>''").fetchone()[0]
    sources = []
    for config in _sources(now):
        if config["parser"].startswith("fed") or config["parser"].startswith("sf"):
            if not enabled:
                state = "disabled"
                row = states.get(config["key"], {})
            else:
                row = states.get(config["key"], {})
                success = row.get("last_success_at")
                state = "not_started" if not row else "error" if row.get("last_error") else "ok"
                if state == "ok" and (not success or now - datetime.fromisoformat(success) > timedelta(seconds=config.get("poll_seconds", 900) * 2 + 300)):
                    state = "stale"
            sources.append({"key": config["key"], "name": config.get("name", config["key"]), "url": config["url"], "state": state,
                            "lastSuccessAt": row.get("last_success_at", ""), "lastError": row.get("last_error", "")})
    events = []
    for row in rows:
        event = json.loads(row["payload_json"])
        scheduled = datetime.fromisoformat(event["originalScheduledAt"]) if event["originalScheduledAt"] else None
        in_window = now - timedelta(hours=3) <= scheduled <= now + timedelta(days=90) if scheduled else now.date() <= datetime.fromisoformat(event["date"]).date() <= now.date() + timedelta(days=90)
        if in_window:
            events.append({"key": event["key"], "title": event["title"], "date": event["date"], "time": event["time"],
                           "importance": event["importance"], "source": event["source"], "checkedAt": row["checked_at"]})
    events.sort(key=lambda event: (event["date"], event["time"], -event["importance"], event["key"]))
    return {"enabled": enabled, "sources": sources, "events": events[:60], "pendingDelivery": pending, "failedDelivery": failed,
            "coverage": "美联储本月/次月官方月历、旧金山联储近期会议；讲话与证词另循官方Feed更新，未覆盖全部央行/机构。"}
