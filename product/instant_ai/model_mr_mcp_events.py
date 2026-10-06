"""Durable MCP Events handoff for newly ready Model Mr originals.

Only subscription metadata, minimal event indexes and delivery state are kept.
Interpretations, summaries and point tracking remain exclusively in the
subscribed ChatGPT conversation and are never accepted by this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import SplitResult, urlsplit

from .model_mr_mcp import MODEL_MR_MCP, ModelMrMcpLibrary


PROTOCOL_VERSION = "2026-07-28"
EVENT_NAME = "model_mr.original_ready"
DEFAULT_TTL_MS = 30 * 24 * 60 * 60 * 1000
MAX_TTL_MS = DEFAULT_TTL_MS
SECRET_ROTATION_SECONDS = 10 * 60
VERIFICATION_CACHE_SECONDS = 10 * 60
MAX_DELIVERY_ATTEMPTS = 8
MAX_RESPONSE_BYTES = 64 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_DIAGNOSTIC_EVENTS = 64
_DIAGNOSTIC_LABEL = re.compile(r"[a-z0-9_-]{1,64}")


class CallbackEndpointError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def event_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": EVENT_NAME,
            "description": (
                "模型先生一条新视频的保存原文已就绪。事件只提供作品索引；"
                "请用只读工具读取本次原文及按主题选取的必要历史原文。"
            ),
            "delivery": ["webhook"],
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "payloadSchema": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "pattern": "^model-mr-work:[1-9][0-9]{0,11}$",
                    },
                    "title": {"type": "string"},
                    "published_at": {"type": "string"},
                    "original_status": {"type": "string"},
                    "original_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                },
                "required": [
                    "record_id",
                    "title",
                    "published_at",
                    "original_status",
                    "original_sha256",
                ],
                "additionalProperties": False,
            },
        }
    ]


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _subscription_id(
    principal: str, url: str, name: str, arguments: Mapping[str, Any]
) -> str:
    identity = _canonical(
        {
            "principal": principal,
            "url": url,
            "name": name,
            "arguments": dict(arguments),
        }
    ).encode("utf-8")
    return "sub_" + hashlib.sha256(identity).hexdigest()[:40]


def _decode_secret(value: str) -> bytes:
    if not value.startswith("whsec_"):
        raise ValueError("signing_secret_invalid")
    encoded = value[6:]
    try:
        decoded = base64.b64decode(encoded + ("=" * (-len(encoded) % 4)), validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("signing_secret_invalid") from error
    if not 24 <= len(decoded) <= 64:
        raise ValueError("signing_secret_invalid")
    return decoded


def _signature(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    key = _decode_secret(secret)
    signed = f"{message_id}.{timestamp}.".encode("utf-8") + body
    digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
    return f"v1,{digest}"


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to an already validated IP while verifying the URL hostname."""

    def __init__(self, host: str, address: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._validated_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


Resolver = Callable[[str, int], list[str]]
Sender = Callable[[str, bytes, Mapping[str, str]], tuple[int, bytes]]


class ModelMrMcpEvents:
    def __init__(
        self,
        path: Path | None = None,
        library: ModelMrMcpLibrary = MODEL_MR_MCP,
        *,
        resolver: Resolver | None = None,
        sender: Sender | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path or library.snapshot_path.parent / "mcp-events.sqlite3"
        self.library = library
        self._resolver = resolver or self._resolve
        self._sender = sender
        self._clock = clock
        self._worker_active = False
        self._diagnostic_events: deque[dict[str, Any]] = deque(
            maxlen=MAX_DIAGNOSTIC_EVENTS
        )
        self._diagnostic_lock = threading.Lock()

    @staticmethod
    def _diagnostic_label(value: str, fallback: str) -> str:
        candidate = str(value or "").strip().casefold()
        return candidate if _DIAGNOSTIC_LABEL.fullmatch(candidate) else fallback

    def record_diagnostic(self, stage: str, outcome: str) -> None:
        """Keep a bounded, credential-free marker for live subscription checks."""

        event = {
            "at": int(self._clock()),
            "stage": self._diagnostic_label(stage, "unknown"),
            "outcome": self._diagnostic_label(outcome, "unknown"),
        }
        with self._diagnostic_lock:
            self._diagnostic_events.append(event)

    def diagnostic_snapshot(self) -> dict[str, Any]:
        """Return loopback-safe stages and aggregate store counts only.

        Callback URLs, signing secrets, principals, event payloads and record IDs
        are deliberately excluded. The database is opened read-only and is not
        created by a diagnostic request.
        """

        with self._diagnostic_lock:
            events = [dict(event) for event in self._diagnostic_events]
        store: dict[str, Any] = {
            "status": "missing",
            "total_subscriptions": 0,
            "active_subscriptions": 0,
            "verified_callbacks": 0,
            "pending_deliveries": 0,
            "total_events": 0,
        }
        if self.path.exists():
            try:
                database_uri = self.path.resolve().as_uri() + "?mode=ro"
                with sqlite3.connect(database_uri, uri=True, timeout=2) as connection:
                    now = self._clock()
                    store = {
                        "status": "ready",
                        "total_subscriptions": int(
                            connection.execute(
                                "SELECT COUNT(*) FROM subscriptions"
                            ).fetchone()[0]
                        ),
                        "active_subscriptions": int(
                            connection.execute(
                                "SELECT COUNT(*) FROM subscriptions "
                                "WHERE active=1 AND expires_at>?",
                                (now,),
                            ).fetchone()[0]
                        ),
                        "verified_callbacks": int(
                            connection.execute(
                                "SELECT COUNT(*) FROM callback_verifications "
                                "WHERE verified_until>?",
                                (now,),
                            ).fetchone()[0]
                        ),
                        "pending_deliveries": int(
                            connection.execute(
                                "SELECT COUNT(*) FROM deliveries "
                                "WHERE state IN ('pending','retry','sending')"
                            ).fetchone()[0]
                        ),
                        "total_events": int(
                            connection.execute(
                                "SELECT COUNT(*) FROM events"
                            ).fetchone()[0]
                        ),
                    }
            except (OSError, sqlite3.Error):
                store["status"] = "unavailable"
        return {
            "schema": "instant-ai-mcp-event-diagnostics/v1",
            "retention": "memory_only",
            "events": events,
            "store": store,
            "worker_active": self._worker_active,
        }

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    principal TEXT NOT NULL,
                    name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    url TEXT NOT NULL,
                    secret TEXT NOT NULL,
                    old_secret TEXT NOT NULL DEFAULT '',
                    old_secret_until REAL NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS callback_verifications (
                    principal TEXT NOT NULL,
                    url TEXT NOT NULL,
                    verified_until REAL NOT NULL,
                    PRIMARY KEY(principal,url)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    dedupe TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    subscription_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    last_status INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(subscription_id,event_id),
                    FOREIGN KEY(subscription_id) REFERENCES subscriptions(id),
                    FOREIGN KEY(event_id) REFERENCES events(id)
                );
                CREATE INDEX IF NOT EXISTS deliveries_due
                    ON deliveries(state,next_attempt);
                """
            )
            with connection:
                yield connection
        finally:
            connection.close()

    def list_events(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self.record_diagnostic("events_list", "received")
        cursor = params.get("cursor")
        if cursor not in (None, ""):
            raise ValueError("cursor_invalid")
        self.record_diagnostic("events_list", "success")
        return {"events": event_definitions(), "nextCursor": None}

    def subscribe(self, principal: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.record_diagnostic("events_subscribe", "received")
        owner = str(principal or "").strip()
        if not owner:
            raise ValueError("principal_required")
        name, arguments = self._event_identity(params)
        delivery = params.get("delivery")
        if not isinstance(delivery, Mapping) or set(delivery) - {"mode", "url", "secret"}:
            raise ValueError("delivery_invalid")
        if delivery.get("mode") != "webhook":
            raise ValueError("delivery_mode_invalid")
        url = str(delivery.get("url") or "").strip()
        secret = str(delivery.get("secret") or "")
        _decode_secret(secret)
        self._validated_url(url)
        if params.get("cursor") not in (None, ""):
            raise ValueError("cursor_invalid")

        requested_ttl = params.get("ttlMs", DEFAULT_TTL_MS)
        if requested_ttl is None:
            ttl_ms = MAX_TTL_MS
        elif isinstance(requested_ttl, bool):
            raise ValueError("ttl_invalid")
        else:
            try:
                ttl_ms = int(requested_ttl)
            except (TypeError, ValueError) as error:
                raise ValueError("ttl_invalid") from error
            if ttl_ms <= 0:
                raise ValueError("ttl_invalid")
            ttl_ms = min(ttl_ms, MAX_TTL_MS)

        canonical_arguments = _canonical(arguments)
        subscription_id = _subscription_id(owner, url, name, arguments)
        now = self._clock()
        if not self._verification_is_fresh(owner, url, now):
            self.record_diagnostic("callback_verification", "started")
            try:
                self._verify_callback(subscription_id, url, secret)
            except CallbackEndpointError as error:
                self.record_diagnostic(
                    "callback_verification", f"error_{error.reason}"
                )
                raise
            self.record_diagnostic("callback_verification", "success")
            with self.db() as connection:
                connection.execute(
                    "INSERT INTO callback_verifications(principal,url,verified_until) VALUES(?,?,?) "
                    "ON CONFLICT(principal,url) DO UPDATE SET verified_until=excluded.verified_until",
                    (owner, url, now + VERIFICATION_CACHE_SECONDS),
                )
        else:
            self.record_diagnostic("callback_verification", "cached")

        expires_at = now + (ttl_ms / 1000)
        with self.db() as connection:
            existing = connection.execute(
                "SELECT secret FROM subscriptions WHERE id=?", (subscription_id,)
            ).fetchone()
            old_secret = ""
            old_secret_until = 0.0
            if existing is not None and str(existing["secret"]) != secret:
                old_secret = str(existing["secret"])
                old_secret_until = now + SECRET_ROTATION_SECONDS
            connection.execute(
                """
                INSERT INTO subscriptions(
                    id,principal,name,arguments,url,secret,old_secret,old_secret_until,
                    expires_at,active,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    secret=excluded.secret,
                    old_secret=excluded.old_secret,
                    old_secret_until=excluded.old_secret_until,
                    expires_at=excluded.expires_at,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                (
                    subscription_id,
                    owner,
                    name,
                    canonical_arguments,
                    url,
                    secret,
                    old_secret,
                    old_secret_until,
                    expires_at,
                    now,
                    now,
                ),
            )
        self.record_diagnostic("subscription_store", "success")
        return {
            "id": subscription_id,
            "refreshBefore": _iso_timestamp(expires_at),
            "cursor": None,
            "truncated": False,
        }

    def unsubscribe(self, principal: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.record_diagnostic("events_unsubscribe", "received")
        owner = str(principal or "").strip()
        if not owner:
            raise ValueError("principal_required")
        name, arguments = self._event_identity(params)
        delivery = params.get("delivery")
        if not isinstance(delivery, Mapping) or set(delivery) - {"mode", "url"}:
            raise ValueError("delivery_invalid")
        if delivery.get("mode") != "webhook":
            raise ValueError("delivery_mode_invalid")
        url = str(delivery.get("url") or "").strip()
        self._parse_url(url)
        subscription_id = _subscription_id(owner, url, name, arguments)
        if self.path.exists():
            with self.db() as connection:
                connection.execute(
                    "UPDATE subscriptions SET active=0,updated_at=? WHERE id=? AND principal=?",
                    (self._clock(), subscription_id, owner),
                )
                connection.execute(
                    "UPDATE deliveries SET state='cancelled',updated_at=? "
                    "WHERE subscription_id=? AND state IN ('pending','retry','sending')",
                    (self._clock(), subscription_id),
                )
        self.record_diagnostic("events_unsubscribe", "success")
        return {}

    def record_original_ready(self, work_id: int, text: str) -> dict[str, Any]:
        identifier = int(work_id)
        original = str(text or "").strip()
        if identifier <= 0 or not original:
            raise ValueError("original_required")
        record_id = f"model-mr-work:{identifier}"
        detail = self.library.get_work_for_mcp(record_id)
        if not detail.get("found"):
            raise ValueError("work_not_found")
        work = detail.get("work") if isinstance(detail.get("work"), Mapping) else {}
        video_original = (
            detail.get("video_original")
            if isinstance(detail.get("video_original"), Mapping)
            else {}
        )
        content_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
        data = {
            "record_id": record_id,
            "title": str(work.get("title") or "")[:500],
            "published_at": str(work.get("published_at") or "")[:80],
            "original_status": str(video_original.get("status") or "unconfirmed")[:80],
            "original_sha256": content_hash,
        }
        dedupe = f"{EVENT_NAME}:{identifier}:{content_hash}"
        event_id = "evt_" + hashlib.sha256(dedupe.encode("utf-8")).hexdigest()[:40]
        now = self._clock()
        serialized = _canonical(data)
        with self.db() as connection:
            connection.execute(
                "UPDATE subscriptions SET active=0,updated_at=? "
                "WHERE active=1 AND expires_at<=?",
                (now, now),
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO events(id,dedupe,name,occurred_at,data) VALUES(?,?,?,?,?)",
                (event_id, dedupe, EVENT_NAME, now, serialized),
            )
            created = cursor.rowcount > 0
            if created:
                subscriptions = connection.execute(
                    "SELECT id FROM subscriptions WHERE active=1 AND name=? AND expires_at>?",
                    (EVENT_NAME, now),
                ).fetchall()
                connection.executemany(
                    "INSERT OR IGNORE INTO deliveries(subscription_id,event_id,next_attempt,updated_at) "
                    "VALUES(?,?,?,?)",
                    [(str(row["id"]), event_id, now, now) for row in subscriptions],
                )
        return {"event_id": event_id, "created": created}

    def dispatch_one(self) -> bool:
        if not self.path.exists():
            return False
        now = self._clock()
        with self.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE deliveries SET state='retry',updated_at=? WHERE state='sending'",
                (now,),
            )
            row = connection.execute(
                """
                SELECT d.subscription_id,d.event_id,d.attempts,s.url,s.secret,
                       s.old_secret,s.old_secret_until,s.expires_at,e.name,e.occurred_at,e.data
                FROM deliveries d
                JOIN subscriptions s ON s.id=d.subscription_id
                JOIN events e ON e.id=d.event_id
                WHERE d.state IN ('pending','retry') AND d.next_attempt<=?
                  AND s.active=1 AND s.expires_at>?
                ORDER BY d.next_attempt,e.occurred_at LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["attempts"]) + 1
            connection.execute(
                "UPDATE deliveries SET state='sending',attempts=?,updated_at=? "
                "WHERE subscription_id=? AND event_id=?",
                (attempts, now, row["subscription_id"], row["event_id"]),
            )

        event = {
            "eventId": str(row["event_id"]),
            "name": str(row["name"]),
            "timestamp": _iso_timestamp(float(row["occurred_at"])),
            "data": json.loads(str(row["data"])),
            "cursor": None,
        }
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_EVENT_BYTES:
            self._finish_delivery(row, "terminal", 413, now)
            return True
        signed_at = int(now)
        signatures = [_signature(str(row["secret"]), str(row["event_id"]), signed_at, body)]
        if str(row["old_secret"]) and float(row["old_secret_until"]) > now:
            signatures.append(
                _signature(str(row["old_secret"]), str(row["event_id"]), signed_at, body)
            )
        headers = {
            "Content-Type": "application/json",
            "webhook-id": str(row["event_id"]),
            "webhook-timestamp": str(signed_at),
            "webhook-signature": " ".join(signatures),
            "X-MCP-Subscription-Id": str(row["subscription_id"]),
        }
        status = 0
        try:
            status, _response = self._request(str(row["url"]), body, headers)
        except (
            CallbackEndpointError,
            OSError,
            TimeoutError,
            ssl.SSLError,
            http.client.HTTPException,
        ):
            status = 0

        if 200 <= status < 300:
            state, next_attempt = "delivered", 0.0
        elif (
            300 <= status < 400
            or status in {410, 413}
            or (400 <= status < 500 and status not in {408, 425, 429})
        ):
            state, next_attempt = "terminal", 0.0
        elif attempts >= MAX_DELIVERY_ATTEMPTS:
            state, next_attempt = "terminal", 0.0
        else:
            state = "retry"
            next_attempt = now + min(15 * (2 ** (attempts - 1)), 60 * 60)
        self._finish_delivery(row, state, status, now, next_attempt)
        return True

    def run(self, stop: threading.Event | None = None) -> None:
        import fcntl

        stop = stop or threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self._worker_active = True
            try:
                while not stop.is_set():
                    try:
                        worked = self.dispatch_one()
                    except Exception:
                        worked = False
                    stop.wait(0.2 if worked else 3)
            finally:
                self._worker_active = False

    def _finish_delivery(
        self,
        row: sqlite3.Row,
        state: str,
        status: int,
        now: float,
        next_attempt: float = 0,
    ) -> None:
        with self.db() as connection:
            connection.execute(
                "UPDATE deliveries SET state=?,next_attempt=?,last_status=?,updated_at=? "
                "WHERE subscription_id=? AND event_id=?",
                (
                    state,
                    next_attempt,
                    status,
                    now,
                    row["subscription_id"],
                    row["event_id"],
                ),
            )

    def _event_identity(self, params: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
        allowed = {"name", "arguments", "delivery", "cursor", "ttlMs"}
        if set(params) - allowed:
            raise ValueError("params_invalid")
        name = str(params.get("name") or "")
        if name != EVENT_NAME:
            raise ValueError("event_not_found")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping) or arguments:
            raise ValueError("arguments_invalid")
        return name, arguments

    def _verification_is_fresh(self, principal: str, url: str, now: float) -> bool:
        if not self.path.exists():
            return False
        with self.db() as connection:
            row = connection.execute(
                "SELECT verified_until FROM callback_verifications WHERE principal=? AND url=?",
                (principal, url),
            ).fetchone()
        return row is not None and float(row["verified_until"]) > now

    def _verify_callback(self, subscription_id: str, url: str, secret: str) -> None:
        challenge = secrets.token_urlsafe(32)
        message_id = "msg_verification_" + secrets.token_hex(16)
        body = json.dumps(
            {"type": "verification", "challenge": challenge}, separators=(",", ":")
        ).encode("utf-8")
        signed_at = int(self._clock())
        headers = {
            "Content-Type": "application/json",
            "webhook-id": message_id,
            "webhook-timestamp": str(signed_at),
            "webhook-signature": _signature(secret, message_id, signed_at, body),
            "X-MCP-Subscription-Id": subscription_id,
        }
        try:
            status, response = self._request(url, body, headers)
        except (
            CallbackEndpointError,
            OSError,
            TimeoutError,
            ssl.SSLError,
            http.client.HTTPException,
        ) as error:
            reason = error.reason if isinstance(error, CallbackEndpointError) else "timeout"
            raise CallbackEndpointError(reason) from error
        if not 200 <= status < 300:
            raise CallbackEndpointError("http_error")
        try:
            payload = json.loads(response)
            echoed = str(payload.get("challenge") or "") if isinstance(payload, dict) else ""
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CallbackEndpointError("challenge_failed") from error
        if not hmac.compare_digest(echoed, challenge):
            raise CallbackEndpointError("challenge_failed")

    def _request(
        self, url: str, body: bytes, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        parsed, _addresses = self._validated_url(url)
        if self._sender is not None:
            return self._sender(url, body, headers)
        host = str(parsed.hostname)
        port = parsed.port or 443
        # Resolve again at connection time to prevent DNS rebinding between
        # subscription verification and delivery.
        _parsed, addresses = self._validated_url(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        request_headers = {**dict(headers), "Host": parsed.netloc, "Connection": "close"}
        last_error: OSError | ssl.SSLError | http.client.HTTPException | None = None
        for address in addresses:
            connection = _PinnedHTTPSConnection(host, address, port, timeout=10)
            try:
                connection.request("POST", target, body=body, headers=request_headers)
                response = connection.getresponse()
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(response_body) > MAX_RESPONSE_BYTES:
                    raise CallbackEndpointError("response_too_large")
                return int(response.status), response_body
            except (OSError, ssl.SSLError, http.client.HTTPException) as error:
                last_error = error
            finally:
                connection.close()
        if last_error is not None:
            raise last_error
        raise CallbackEndpointError("dns_failed")

    def _validated_url(self, url: str) -> tuple[SplitResult, list[str]]:
        parsed = self._parse_url(url)
        host = str(parsed.hostname)
        port = parsed.port or 443
        try:
            addresses = self._resolver(host, port)
        except (OSError, socket.gaierror) as error:
            raise CallbackEndpointError("dns_failed") from error
        if not addresses:
            raise CallbackEndpointError("dns_failed")
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as error:
                raise CallbackEndpointError("address_invalid") from error
            if not address.is_global or address.is_multicast:
                raise CallbackEndpointError("address_not_public")
        return parsed, addresses

    @staticmethod
    def _parse_url(url: str) -> SplitResult:
        value = str(url or "")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise ValueError("callback_url_invalid")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("callback_url_invalid") from error
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("callback_url_invalid")
        return parsed

    @staticmethod
    def _resolve(host: str, port: int) -> list[str]:
        addresses = {
            str(item[4][0])
            for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
        return sorted(addresses)


MODEL_MR_MCP_EVENTS = ModelMrMcpEvents()


__all__ = [
    "CallbackEndpointError",
    "EVENT_NAME",
    "MAX_DIAGNOSTIC_EVENTS",
    "MODEL_MR_MCP_EVENTS",
    "ModelMrMcpEvents",
    "PROTOCOL_VERSION",
    "event_definitions",
]
