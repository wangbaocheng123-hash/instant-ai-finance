from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .database import connect, transaction, utc_now


DEFAULT_COMPASS_EVENTS_URL = "http://127.0.0.1:32180/api/v1/watch-events"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

ALIAS_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("semicon",), ("semicon",)),
    (("broadcom", "博通"), ("broadcom", "博通")),
    (("台积电", "tsmc"), ("台积电", "tsmc")),
    (("华为", "huawei"), ("华为", "huawei")),
    (("meta connect",), ("meta connect",)),
    (("openai", "devday"), ("openai", "devday")),
    (("micron", "美光"), ("micron", "美光")),
    (("ocp", "open compute"), ("ocp", "open compute")),
    (("asml", "阿斯麦"), ("asml", "阿斯麦")),
    (("nvidia", "英伟达", "gtc"), ("nvidia", "英伟达", "gtc")),
    (("oracle", "甲骨文"), ("oracle", "甲骨文")),
    (("microsoft", "微软"), ("microsoft", "微软")),
    (("alphabet", "google", "谷歌"), ("alphabet", "google", "谷歌")),
    (("amazon", "亚马逊"), ("amazon", "亚马逊")),
    (("meta",), ("meta",)),
    (("紫金矿业", "zijin"), ("紫金矿业", "zijin")),
    (("卡莫阿", "kamoa", "ivanhoe"), ("卡莫阿", "kamoa", "ivanhoe")),
    (("lbma",), ("lbma",)),
    (("lme",), ("lme",)),
    (("alcoa", "美国铝业"), ("alcoa", "美国铝业")),
    (("杰克逊霍尔", "jackson hole"), ("杰克逊霍尔", "jackson hole")),
    (("jolts",), ("jolts",)),
    (("非农", "nonfarm", "payrolls"), ("非农", "nonfarm", "payrolls")),
    (("fomc",), ("fomc",)),
    (("pmi", "采购经理指数"), ("pmi", "采购经理指数")),
    (("cpi", "消费者价格指数"), ("cpi", "消费者价格指数")),
    (("ppi", "生产者价格指数"), ("ppi", "生产者价格指数")),
    (("pce", "个人消费支出"), ("pce", "个人消费支出")),
    (("gdp", "国内生产总值"), ("gdp", "国内生产总值")),
    (("section 301", "301关税"), ("section 301", "301关税")),
)

IGNORED_LATIN_TERMS = {
    "event", "future", "report", "results", "conference", "summit", "global",
    "window", "fiscal", "quarter", "quarterly", "annual", "world",
}
IGNORED_CHINESE_TERMS = {
    "日期待确认", "暂定", "观察窗口", "披露窗口", "同行对照窗口", "北京时间",
    "行业大会", "重点事件", "已确认", "会议区间", "公布", "发布", "更新",
}


def _normalize(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().lower()


def _valid_https_url(value: object) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme == "https" and bool(parsed.netloc) else ""


def _derive_monitor_terms(event: dict[str, Any]) -> list[str]:
    corpus = _normalize(" ".join((event.get("title", ""), event.get("note", ""), event.get("category", ""))))
    terms: set[str] = set()
    for triggers, aliases in ALIAS_GROUPS:
        if any(trigger in corpus for trigger in triggers):
            terms.update(aliases)
    if terms:
        return sorted(terms, key=lambda term: (-len(term), term))[:24]

    title = _normalize(event.get("title", ""))
    title = re.sub(r"\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?", " ", title)
    title = re.sub(r"\d{1,2}月(?:\d{1,2}(?:—|-|至)\d{1,2}日?|\d{1,2}日)?", " ", title)
    for token in re.findall(r"[a-z][a-z0-9.+-]{2,}", title):
        if token not in IGNORED_LATIN_TERMS and not token.isdigit():
            terms.add(token)
    for token in re.findall(r"[\u3400-\u9fff]{3,}", title):
        if token not in IGNORED_CHINESE_TERMS and len(token) <= 16:
            terms.add(token)

    return sorted(terms, key=lambda term: (-len(term), term))[:24]


def _term_in_text(term: str, content: str) -> bool:
    if re.fullmatch(r"[a-z0-9.+ -]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", content) is not None
    return term in content


def _match_event(terms: list[str], item: dict[str, Any]) -> tuple[int, list[str]] | None:
    content = _normalize(" ".join((
        item.get("title", ""), item.get("summary", ""), item.get("topics_json", ""), item.get("entities_json", "")
    )))
    matched = [term for term in terms if _term_in_text(term, content)]
    if not matched:
        return None
    score = min(100, 55 + (len(matched) - 1) * 12)
    return score, matched[:12]


def _fetch_payload(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("罗盘重点事件地址无效")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Instant-AI-Watch/1.0"})
    with urlopen(request, timeout=8) as response:  # noqa: S310 - operator-configured URL
        content_length = int(response.headers.get("Content-Length", "0") or "0")
        if content_length > MAX_RESPONSE_BYTES:
            raise ValueError("罗盘重点事件响应超过限制")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("罗盘重点事件响应超过限制")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("events"), list):
        raise ValueError("罗盘重点事件响应格式无效")
    return payload


def _normalized_event(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    scope = str(raw.get("scope") or "").strip()
    event_key = str(raw.get("eventKey") or "").strip()[:300]
    title = str(raw.get("title") or "").strip()[:500]
    event_date = str(raw.get("date") or "").strip()
    if scope not in {"home", "zijin"} or not event_key or not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
        return None
    event_time = str(raw.get("time") or "").strip()
    if event_time and not re.fullmatch(r"\d{2}:\d{2}", event_time):
        event_time = ""
    sources = []
    for source in raw.get("sources", []) if isinstance(raw.get("sources"), list) else []:
        if not isinstance(source, dict):
            continue
        url = _valid_https_url(source.get("url"))
        if url:
            sources.append({
                "name": str(source.get("name") or "事件资料来源").strip()[:200],
                "url": url,
                "verifiedAt": str(source.get("verifiedAt") or "").strip()[:20],
            })
    try:
        importance = max(1, min(5, int(raw.get("importance") or 3)))
    except (TypeError, ValueError):
        importance = 3
    normalized = {
        "event_key": event_key,
        "scope": scope,
        "source_kind": str(raw.get("sourceKind") or "timeline").strip()[:50],
        "source_event_id": str(raw.get("sourceEventId") or event_key).strip()[:300],
        "title": title,
        "event_date": event_date,
        "event_time": event_time,
        "category": str(raw.get("category") or "event").strip()[:100],
        "importance": importance,
        "event_status": str(raw.get("status") or "planned").strip()[:50],
        "note": str(raw.get("note") or "").strip()[:4000],
        "sources": sources,
        "source_updated_at": str(raw.get("updatedAt") or "").strip()[:100],
    }
    normalized["monitor_terms"] = _derive_monitor_terms(normalized)
    return normalized


def sync_watch_events(
    *, path: object = None, source_url: str | None = None,
    fetcher: Callable[[str], dict[str, Any]] = _fetch_payload,
) -> dict[str, Any]:
    url = source_url or os.environ.get("INSTANT_AI_COMPASS_EVENTS_URL", DEFAULT_COMPASS_EVENTS_URL)
    attempted_at = utc_now()
    try:
        payload = fetcher(url)
        events = [_normalized_event(raw) for raw in payload.get("events", [])[:1000]]
        events = [event for event in events if event is not None]
        with transaction(path) as connection:
            connection.execute("UPDATE watch_events SET is_active=0")
            for event in events:
                connection.execute(
                    """
                    INSERT INTO watch_events(
                        event_key, scope, source_kind, source_event_id, title, event_date,
                        event_time, category, importance, event_status, note, sources_json,
                        monitor_terms_json, source_updated_at, is_active, last_synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(event_key) DO UPDATE SET
                        scope=excluded.scope, source_kind=excluded.source_kind,
                        source_event_id=excluded.source_event_id, title=excluded.title,
                        event_date=excluded.event_date, event_time=excluded.event_time,
                        category=excluded.category, importance=excluded.importance,
                        event_status=excluded.event_status, note=excluded.note,
                        sources_json=excluded.sources_json,
                        monitor_terms_json=excluded.monitor_terms_json,
                        source_updated_at=excluded.source_updated_at,
                        is_active=1, last_synced_at=excluded.last_synced_at
                    """,
                    (
                        event["event_key"], event["scope"], event["source_kind"], event["source_event_id"],
                        event["title"], event["event_date"], event["event_time"], event["category"],
                        event["importance"], event["event_status"], event["note"],
                        json.dumps(event["sources"], ensure_ascii=False),
                        json.dumps(event["monitor_terms"], ensure_ascii=False), event["source_updated_at"], attempted_at,
                    ),
                )
            connection.execute(
                """
                INSERT INTO watch_sync_state(id, source_url, last_attempt_at, last_success_at, last_error, source_revision, event_count)
                VALUES (1, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,
                    last_attempt_at=excluded.last_attempt_at, last_success_at=excluded.last_success_at,
                    last_error=NULL, source_revision=excluded.source_revision, event_count=excluded.event_count
                """,
                (url, attempted_at, attempted_at, payload.get("revision"), len(events)),
            )
        return {"ok": True, "synced": len(events), "revision": payload.get("revision"), "warnings": payload.get("warnings", [])}
    except Exception as error:
        message = f"{type(error).__name__}: {error}"[:1000]
        with transaction(path) as connection:
            connection.execute(
                """
                INSERT INTO watch_sync_state(id, source_url, last_attempt_at, last_error)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET source_url=excluded.source_url,
                    last_attempt_at=excluded.last_attempt_at, last_error=excluded.last_error
                """,
                (url, attempted_at, message),
            )
        return {"ok": False, "error": message}


def scan_watch_events(*, path: object = None) -> dict[str, Any]:
    checked_at = utc_now()
    with transaction(path) as connection:
        events = connection.execute(
            "SELECT event_key, monitor_terms_json FROM watch_events WHERE is_active=1"
        ).fetchall()
        items = connection.execute(
            "SELECT id, title, summary, topics_json, entities_json FROM items"
        ).fetchall()
        matched_count = 0
        for event in events:
            connection.execute("DELETE FROM watch_event_matches WHERE event_key=?", (event["event_key"],))
            terms = json.loads(event["monitor_terms_json"] or "[]")
            for item_row in items:
                item = dict(item_row)
                match = _match_event(terms, item)
                if match is None:
                    continue
                score, matched_terms = match
                connection.execute(
                    """
                    INSERT INTO watch_event_matches(event_key, item_id, match_score, matched_terms_json, matched_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(event_key, item_id) DO UPDATE SET
                        match_score=excluded.match_score,
                        matched_terms_json=excluded.matched_terms_json,
                        matched_at=excluded.matched_at
                    """,
                    (event["event_key"], item["id"], score, json.dumps(matched_terms, ensure_ascii=False), checked_at),
                )
                matched_count += 1
            connection.execute(
                "UPDATE watch_events SET last_checked_at=? WHERE event_key=?",
                (checked_at, event["event_key"]),
            )
    return {"ok": True, "checked": len(events), "matches": matched_count, "checked_at": checked_at}


def refresh_watch_events(*, path: object = None) -> dict[str, Any]:
    sync = sync_watch_events(path=path)
    scan = scan_watch_events(path=path)
    return {"sync": sync, "scan": scan}


def list_watch_events(*, path: object = None) -> dict[str, Any]:
    with connect(path) as connection:
        rows = connection.execute(
            """
            SELECT w.*,
                   COUNT(m.item_id) AS match_count,
                   MAX(m.matched_at) AS latest_match_at
            FROM watch_events w
            LEFT JOIN watch_event_matches m ON m.event_key=w.event_key
            WHERE w.is_active=1
            GROUP BY w.event_key
            ORDER BY w.event_date, w.event_time, w.importance DESC, w.title
            """
        ).fetchall()
        sync_row = connection.execute("SELECT * FROM watch_sync_state WHERE id=1").fetchone()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["sources"] = json.loads(event.pop("sources_json") or "[]")
            event.pop("monitor_terms_json", None)
            event["is_active"] = bool(event["is_active"])
            match_rows = connection.execute(
                """
                SELECT m.item_id, m.match_score, m.matched_terms_json, m.matched_at,
                       i.title, t.translated_title, i.url, i.published_at, i.first_seen_at,
                       i.importance_score
                FROM watch_event_matches m
                JOIN items i ON i.id=m.item_id
                LEFT JOIN item_translations t
                  ON t.item_id=i.id AND t.target_language='zh-CN' AND t.original_title=i.title
                WHERE m.event_key=?
                ORDER BY COALESCE(i.published_at, i.first_seen_at) DESC, m.match_score DESC
                LIMIT 3
                """,
                (event["event_key"],),
            ).fetchall()
            event["latest_matches"] = []
            for match_row in match_rows:
                match = dict(match_row)
                match["matched_terms"] = json.loads(match.pop("matched_terms_json") or "[]")
                event["latest_matches"].append(match)
            events.append(event)

    today = datetime.now(SHANGHAI).date().isoformat()
    for event in events:
        if event["match_count"]:
            event["monitor_status"] = "有相关消息"
        elif event["event_date"] == today:
            event["monitor_status"] = "今日重点"
        elif event["event_status"] == "tentative":
            event["monitor_status"] = "持续监测（日期待确认）"
        else:
            event["monitor_status"] = "监测中"
    return {
        "events": events,
        "counts": {
            "total": len(events),
            "home": sum(event["scope"] == "home" for event in events),
            "zijin": sum(event["scope"] == "zijin" for event in events),
            "matched": sum(bool(event["match_count"]) for event in events),
        },
        "sync": dict(sync_row) if sync_row else None,
        "time_zone": "Asia/Shanghai",
    }
