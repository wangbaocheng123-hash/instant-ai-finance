from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .database import connect, transaction


MAX_OFFICIAL_RESPONSE_BYTES = 1024 * 1024
MAX_URLS_PER_RUN = 12
OFFICIAL_HOST_SUFFIXES = frozenset({
    "stats.gov.cn",
    "bls.gov",
    "bea.gov",
    "customs.gov.cn",
    "federalreserve.gov",
    "ustr.gov",
    "semi.org",
    "semicontaiwan.org",
    "broadcom.com",
    "tsmc.com",
    "huawei.com",
    "meta.com",
    "atmeta.com",
    "openai.com",
    "micron.com",
    "opencompute.org",
    "semiconwest.org",
    "asml.com",
    "nvidia.com",
    "oracle.com",
    "microsoft.com",
    "abc.xyz",
    "aboutamazon.com",
    "ivanhoemines.com",
    "lbma.org.uk",
    "lme.com",
    "zijinmining.com",
    "alcoa.com",
    "nasa.gov",
})


def _host_allowed(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in OFFICIAL_HOST_SUFFIXES)


def validate_official_url(value: object, *, resolve_dns: bool = False) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("官方监测渠道必须是无凭据的 HTTPS 地址")
    if parsed.port not in {None, 443} or not _host_allowed(parsed.hostname):
        raise ValueError("官方监测渠道域名不在核验白名单")
    if resolve_dns:
        addresses = {
            record[4][0]
            for record in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        }
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError("官方监测渠道未解析到公网地址")
    return candidate


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        target = urljoin(request.full_url, newurl)
        validate_official_url(target, resolve_dns=True)
        return super().redirect_request(request, fp, code, msg, headers, target)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript", "template"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript", "template"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _visible_text(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        decoded = body.decode(charset, errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    parser = _VisibleTextParser()
    try:
        parser.feed(decoded)
        text = "\n".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", decoded)
    text = unicodedata.normalize("NFKC", html.unescape(text)).lower()
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if len(line) >= 2)


def signal_evidence(body: bytes, content_type: str, expected_terms: list[str]) -> dict[str, Any]:
    text = _visible_text(body, content_type)
    terms = [unicodedata.normalize("NFKC", str(term)).strip().lower() for term in expected_terms if str(term).strip()]
    matched_lines = [line for line in text.splitlines() if any(term in line for term in terms)] if terms else []
    signal_found = bool(matched_lines)
    material = "\n".join(matched_lines[:300]) if terms else text[:500_000]
    if terms and not material:
        material = "official-signal-absent"
    matched_terms = [term for term in terms if any(term in line for line in matched_lines)]
    return {
        "fingerprint": hashlib.sha256(material.encode("utf-8")).hexdigest(),
        "signal_found": signal_found,
        "matched_terms": matched_terms[:24],
        "evidence_excerpt": "\n".join(matched_lines[:12])[:2000],
    }


def signal_fingerprint(body: bytes, content_type: str, expected_terms: list[str]) -> tuple[str, bool]:
    evidence = signal_evidence(body, content_type, expected_terms)
    return str(evidence["fingerprint"]), bool(evidence["signal_found"])


def _fetch_official_channel(url: str, *, etag: str = "", last_modified: str = "") -> dict[str, Any]:
    validate_official_url(url, resolve_dns=True)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        "User-Agent": "Instant-AI-Official-Monitor/1.0 (+operator-owned event monitoring)",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = Request(url, headers=headers)
    opener = build_opener(_SafeRedirectHandler())
    try:
        response = opener.open(request, timeout=10)  # noqa: S310 - URL is curated and DNS-validated
    except HTTPError as error:
        if error.code == 304:
            return {"status": 304, "not_modified": True, "body": b"", "content_type": "", "headers": dict(error.headers)}
        raise
    with response:
        validate_official_url(response.geturl(), resolve_dns=True)
        content_length = int(response.headers.get("Content-Length", "0") or "0")
        if content_length > MAX_OFFICIAL_RESPONSE_BYTES:
            raise ValueError("官方监测页面超过大小限制")
        body = response.read(MAX_OFFICIAL_RESPONSE_BYTES + 1)
        if len(body) > MAX_OFFICIAL_RESPONSE_BYTES:
            raise ValueError("官方监测页面超过大小限制")
        return {
            "status": int(response.status),
            "not_modified": False,
            "body": body,
            "content_type": response.headers.get("Content-Type", ""),
            "headers": dict(response.headers),
        }


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _next_check(now: datetime, window_start: str, window_end: str, *, error: bool = False) -> str:
    start = _parse_time(window_start) or now
    end = _parse_time(window_end) or start + timedelta(days=1)
    if error:
        interval = timedelta(minutes=30) if start - timedelta(hours=6) <= now <= end + timedelta(days=2) else timedelta(hours=6)
    elif now < start:
        distance = start - now
        if distance > timedelta(days=7):
            interval = timedelta(hours=24)
        elif distance > timedelta(days=2):
            interval = timedelta(hours=6)
        elif distance > timedelta(hours=6):
            interval = timedelta(hours=1)
        else:
            interval = timedelta(minutes=15)
    elif now <= end:
        interval = timedelta(minutes=5)
    elif now <= end + timedelta(days=2):
        interval = timedelta(hours=1)
    else:
        interval = timedelta(hours=24)
    return (now + interval).replace(microsecond=0).isoformat()


def monitor_official_channels(
    *, path: object = None,
    fetcher: Callable[..., dict[str, Any]] = _fetch_official_channel,
    now: datetime | None = None,
    limit: int = MAX_URLS_PER_RUN,
) -> dict[str, Any]:
    checked_at = (now or datetime.now(UTC)).replace(microsecond=0)
    checked_at_text = checked_at.isoformat()
    with connect(path) as connection:
        rows = connection.execute(
            """
            SELECT c.*, e.scope AS event_scope, e.source_kind, e.title AS event_title,
                   e.event_date, e.event_time, e.category AS event_category,
                   e.importance AS event_importance
            FROM watch_event_channels c
            JOIN watch_events e ON e.event_key=c.event_key
            WHERE c.is_active=1 AND e.is_active=1
              AND (c.next_check_at IS NULL OR c.next_check_at<=?)
            ORDER BY COALESCE(c.next_check_at, ''), COALESCE(c.last_checked_at, ''),
                     e.event_date, e.event_time, c.url
            LIMIT 400
            """,
            (checked_at_text,),
        ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["url"]].append(dict(row))
    selected_urls = list(grouped)[:max(1, min(50, int(limit)))]
    changes = 0
    errors = 0
    checked_channels = 0
    signals_queued = 0

    for url in selected_urls:
        channels = grouped[url]
        representative = channels[0]
        needs_baseline = any(not str(channel.get("content_hash") or "") for channel in channels)
        try:
            response = fetcher(
                url,
                etag="" if needs_baseline else str(representative.get("etag") or ""),
                last_modified="" if needs_baseline else str(representative.get("last_modified") or ""),
            )
            status = int(response.get("status") or 0)
            if status not in {200, 304}:
                raise ValueError(f"官方监测页面返回 HTTP {status}")
            with transaction(path) as connection:
                for channel in channels:
                    expected_terms = json.loads(channel.get("expected_terms_json") or "[]")
                    previous_hash = str(channel.get("content_hash") or "")
                    if response.get("not_modified"):
                        fingerprint = previous_hash
                        signal_found = bool(channel.get("signal_found"))
                        matched_terms: list[str] = []
                        evidence_excerpt = ""
                    else:
                        evidence = signal_evidence(
                            response.get("body") or b"",
                            str(response.get("content_type") or ""),
                            expected_terms,
                        )
                        fingerprint = str(evidence["fingerprint"])
                        signal_found = bool(evidence["signal_found"])
                        matched_terms = list(evidence["matched_terms"])
                        evidence_excerpt = str(evidence["evidence_excerpt"])
                    changed = bool(previous_hash and fingerprint and previous_hash != fingerprint)
                    changes += int(changed)
                    checked_channels += 1
                    headers = response.get("headers") or {}
                    if changed and signal_found:
                        signal_id = "instant-ai:" + hashlib.sha256(
                            f"{channel['event_key']}|{channel['channel_key']}|{fingerprint}".encode("utf-8")
                        ).hexdigest()[:48]
                        inserted = connection.execute(
                            """
                            INSERT OR IGNORE INTO watch_event_signals(
                                signal_id, event_key, channel_key, previous_hash, evidence_hash,
                                matched_terms_json, evidence_excerpt, detected_at, status,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                            """,
                            (
                                signal_id, channel["event_key"], channel["channel_key"], previous_hash,
                                fingerprint, json.dumps(matched_terms, ensure_ascii=False),
                                evidence_excerpt, checked_at_text, checked_at_text, checked_at_text,
                            ),
                        )
                        signals_queued += int(inserted.rowcount or 0)
                    connection.execute(
                        """
                        UPDATE watch_event_channels SET
                            last_checked_at=?, last_success_at=?,
                            last_changed_at=CASE WHEN ? THEN ? ELSE last_changed_at END,
                            next_check_at=?, http_status=?, etag=?, last_modified=?,
                            content_hash=CASE WHEN ?<>'' THEN ? ELSE content_hash END,
                            signal_found=?, last_error=NULL
                        WHERE event_key=? AND channel_key=?
                        """,
                        (
                            checked_at_text, checked_at_text, changed, checked_at_text,
                            _next_check(checked_at, channel["window_start"], channel["window_end"]),
                            status, str(headers.get("ETag") or channel.get("etag") or "")[:500],
                            str(headers.get("Last-Modified") or channel.get("last_modified") or "")[:500],
                            fingerprint, fingerprint, int(signal_found), channel["event_key"], channel["channel_key"],
                        ),
                    )
        except Exception as error:
            errors += len(channels)
            checked_channels += len(channels)
            message = f"{type(error).__name__}: {error}"[:1000]
            with transaction(path) as connection:
                for channel in channels:
                    connection.execute(
                        """
                        UPDATE watch_event_channels SET last_checked_at=?, next_check_at=?, last_error=?
                        WHERE event_key=? AND channel_key=?
                        """,
                        (
                            checked_at_text,
                            _next_check(checked_at, channel["window_start"], channel["window_end"], error=True),
                            message,
                            channel["event_key"], channel["channel_key"],
                        ),
                    )

    return {
        "ok": errors == 0,
        "urls_checked": len(selected_urls),
        "channels_checked": checked_channels,
        "changes": changes,
        "signals_queued": signals_queued,
        "errors": errors,
        "checked_at": checked_at_text,
    }
