from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import deque
from typing import Any


MAX_OAUTH_DIAGNOSTIC_EVENTS = 64
_SAFE_LABEL = re.compile(r"[a-z0-9_-]{1,48}")
_EVENTS: deque[dict[str, Any]] = deque(maxlen=MAX_OAUTH_DIAGNOSTIC_EVENTS)
_LOCK = threading.Lock()


def _safe_label(value: str, fallback: str) -> str:
    candidate = str(value or "").strip().casefold()
    return candidate if _SAFE_LABEL.fullmatch(candidate) else fallback


def _client_reference(client_id: str) -> str:
    value = str(client_id or "")
    if not value:
        return "none"
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def record_oauth_event(stage: str, outcome: str, *, client_id: str = "") -> None:
    """Record only a bounded, credential-free OAuth stage marker.

    The event deliberately excludes request URLs, IP addresses, usernames,
    passwords, authorization codes, state, PKCE material, cookies and tokens.
    """

    event = {
        "at": int(time.time()),
        "stage": _safe_label(stage, "unknown"),
        "outcome": _safe_label(outcome, "unknown"),
        "client_ref": _client_reference(client_id),
    }
    with _LOCK:
        _EVENTS.append(event)


def oauth_diagnostic_snapshot() -> dict[str, Any]:
    with _LOCK:
        events = [dict(event) for event in _EVENTS]
    return {
        "schema": "instant-ai-oauth-diagnostics/v1",
        "retention": "memory_only",
        "events": events,
    }


def clear_oauth_diagnostics_for_tests() -> None:
    with _LOCK:
        _EVENTS.clear()


__all__ = [
    "MAX_OAUTH_DIAGNOSTIC_EVENTS",
    "clear_oauth_diagnostics_for_tests",
    "oauth_diagnostic_snapshot",
    "record_oauth_event",
]
