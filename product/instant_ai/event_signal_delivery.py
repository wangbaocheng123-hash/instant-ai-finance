from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .database import connect, transaction


DEFAULT_COMPASS_SIGNAL_URL = "http://127.0.0.1:32180/api/v1/integrations/instant-ai/event-signals"
MAX_RESPONSE_BYTES = 64 * 1024


def validate_compass_signal_url(value: object) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path != "/api/v1/integrations/instant-ai/event-signals"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("罗盘事件信号只能发送到服务器本机固定接口")
    return candidate


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    validate_compass_signal_url(url)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Instant-AI-Event-Signal/1.0",
        },
    )
    with urlopen(request, timeout=8) as response:  # noqa: S310 - fixed loopback-only endpoint
        content_length = int(response.headers.get("Content-Length", "0") or "0")
        if content_length > MAX_RESPONSE_BYTES:
            raise ValueError("罗盘事件信号响应超过限制")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("罗盘事件信号响应超过限制")
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("罗盘未确认接收事件信号")
    return result


def _signal_payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        matched_terms = json.loads(row.get("matched_terms_json") or "[]")
    except json.JSONDecodeError:
        matched_terms = []
    return {
        "contractVersion": 1,
        "sourceSystem": "instant-ai",
        "signalKind": "official-page-change",
        "signalFound": True,
        "signalToken": row.get("delivery_token") or "",
        "eventKey": row["event_key"],
        "scope": row["event_scope"],
        "title": row["event_title"],
        "date": row["event_date"],
        "time": row["event_time"],
        "category": row["event_category"],
        "importance": int(row["event_importance"] or 3),
        "channelKey": row["channel_key"],
        "publisher": row["publisher"],
        "channelName": row["channel_name"],
        "channelUrl": row["channel_url"],
        "previousHash": row["previous_hash"],
        "evidenceHash": row["evidence_hash"],
        "matchedTerms": matched_terms,
        "evidenceExcerpt": row.get("evidence_excerpt") or "",
        "detectedAt": row["detected_at"],
        "drill": row.get("source_kind") == "trial",
    }


def deliver_event_signals(
    *,
    path: object = None,
    target_url: str = DEFAULT_COMPASS_SIGNAL_URL,
    sender: Callable[[str, dict[str, Any]], dict[str, Any]] = _post_json,
    now: datetime | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    url = validate_compass_signal_url(target_url)
    attempted_at = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    with connect(path) as connection:
        rows = connection.execute(
            """
            SELECT s.*, e.scope AS event_scope, e.source_kind, e.title AS event_title,
                   e.event_date, e.event_time, e.category AS event_category,
                   e.importance AS event_importance,
                   c.publisher, c.name AS channel_name, c.url AS channel_url,
                   c.delivery_token
            FROM watch_event_signals s
            JOIN watch_events e ON e.event_key=s.event_key
            JOIN watch_event_channels c
              ON c.event_key=s.event_key AND c.channel_key=s.channel_key
            WHERE s.status IN ('pending', 'failed') AND s.delivery_attempts < 5
            ORDER BY s.detected_at, s.created_at
            LIMIT ?
            """,
            (max(1, min(50, int(limit))),),
        ).fetchall()

    delivered = 0
    failed = 0
    waiting_token = 0
    for row_raw in rows:
        row = dict(row_raw)
        if not str(row.get("delivery_token") or "").strip():
            waiting_token += 1
            continue
        try:
            response = sender(url, _signal_payload(row))
            signal = response.get("signal") if isinstance(response.get("signal"), dict) else {}
            with transaction(path) as connection:
                connection.execute(
                    """
                    UPDATE watch_event_signals SET status='delivered',
                        delivery_attempts=delivery_attempts+1, last_attempt_at=?, delivered_at=?,
                        compass_signal_id=?, compass_signal_status=?, last_error=NULL, updated_at=?
                    WHERE signal_id=?
                    """,
                    (
                        attempted_at, attempted_at, str(signal.get("id") or "")[:120],
                        str(signal.get("status") or "received")[:40], attempted_at, row["signal_id"],
                    ),
                )
            delivered += 1
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:1000]
            with transaction(path) as connection:
                connection.execute(
                    """
                    UPDATE watch_event_signals SET status='failed',
                        delivery_attempts=delivery_attempts+1, last_attempt_at=?,
                        last_error=?, updated_at=? WHERE signal_id=?
                    """,
                    (attempted_at, message, attempted_at, row["signal_id"]),
                )
            failed += 1

    return {
        "ok": failed == 0,
        "attempted": delivered + failed,
        "delivered": delivered,
        "failed": failed,
        "waiting_token": waiting_token,
        "checked_at": attempted_at,
    }
