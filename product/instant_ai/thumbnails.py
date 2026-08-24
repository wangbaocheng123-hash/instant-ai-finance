from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import socket
import sqlite3
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from .collectors import parse_feed
from .database import connect, transaction, utc_now
from .paths import CACHE_ROOT


THUMBNAIL_CACHE_ROOT = CACHE_ROOT / "thumbnails"
MAX_IMAGE_BYTES = 1_250_000
FAILED_RETRY_AFTER = timedelta(hours=24)
DOWNLOAD_SEMAPHORE = threading.BoundedSemaphore(3)
LOCK_GUARD = threading.Lock()
ITEM_LOCKS: dict[int, threading.Lock] = {}


@dataclass(frozen=True)
class DownloadedImage:
    content: bytes
    mime_type: str


@dataclass(frozen=True)
class ThumbnailResult:
    content: bytes
    mime_type: str
    cache_seconds: int
    etag: str
    kind: str


def _item_lock(item_id: int) -> threading.Lock:
    with LOCK_GUARD:
        return ITEM_LOCKS.setdefault(item_id, threading.Lock())


def register_thumbnail_candidate(connection: object, item_id: int, source_url: str) -> None:
    source_url = source_url.strip()
    if not source_url:
        return
    now = utc_now()
    connection.execute(
        """
        INSERT INTO item_thumbnails(
            item_id, source_url, local_path, mime_type, byte_size, status, checked_at
        ) VALUES (?, ?, NULL, NULL, NULL, 'pending', ?)
        ON CONFLICT(item_id) DO UPDATE SET
            source_url=excluded.source_url,
            local_path=CASE
                WHEN item_thumbnails.source_url=excluded.source_url THEN item_thumbnails.local_path
                ELSE NULL
            END,
            mime_type=CASE
                WHEN item_thumbnails.source_url=excluded.source_url THEN item_thumbnails.mime_type
                ELSE NULL
            END,
            byte_size=CASE
                WHEN item_thumbnails.source_url=excluded.source_url THEN item_thumbnails.byte_size
                ELSE NULL
            END,
            status=CASE
                WHEN item_thumbnails.source_url=excluded.source_url THEN item_thumbnails.status
                ELSE 'pending'
            END,
            checked_at=CASE
                WHEN item_thumbnails.source_url=excluded.source_url THEN item_thumbnails.checked_at
                ELSE excluded.checked_at
            END
        """,
        (item_id, source_url, now),
    )


def backfill_thumbnail_candidates(path: Path | str | None = None) -> int:
    """Recover image URLs from the newest already-archived XML feed per source."""

    with connect(path) as connection:
        raw_rows = connection.execute(
            """
            WITH latest AS (
                SELECT source_id, MAX(fetched_at) AS fetched_at
                FROM evidence
                WHERE mime_type LIKE '%xml%'
                GROUP BY source_id
            )
            SELECT DISTINCT e.raw_path
            FROM evidence e
            JOIN latest l ON l.source_id=e.source_id AND l.fetched_at=e.fetched_at
            WHERE e.raw_path <> ''
            """
        ).fetchall()

    registered = 0
    for raw_row in raw_rows:
        raw_path = Path(str(raw_row["raw_path"]))
        if not raw_path.is_file() or raw_path.stat().st_size > 8 * 1024 * 1024:
            continue
        try:
            entries = parse_feed(raw_path.read_bytes(), 250)
        except (OSError, ValueError, SyntaxError):
            continue
        image_by_source_id = {
            entry.source_item_id: entry.image_url
            for entry in entries
            if entry.source_item_id and entry.image_url
        }
        if not image_by_source_id:
            continue
        with transaction(path) as connection:
            evidence_rows = connection.execute(
                """
                SELECT ie.item_id, e.source_item_id
                FROM evidence e
                JOIN item_evidence ie ON ie.evidence_id=e.id
                WHERE e.raw_path=?
                """,
                (str(raw_path),),
            ).fetchall()
            for evidence in evidence_rows:
                image_url = image_by_source_id.get(str(evidence["source_item_id"] or ""), "")
                if image_url:
                    register_thumbnail_candidate(connection, int(evidence["item_id"]), image_url)
                    registered += 1
    return registered


def _ensure_public_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("thumbnail URL is not a public HTTP URL")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("thumbnail URL uses an unsupported port")
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        }
    except socket.gaierror as error:
        raise ValueError("thumbnail host cannot be resolved") from error
    if not addresses:
        raise ValueError("thumbnail host has no address")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("thumbnail host resolves to a non-public address")
    return value


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request | None:
        _ensure_public_url(new_url)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def _sniff_image_type(content: bytes, declared: str) -> tuple[str, str]:
    declared = declared.split(";", 1)[0].strip().lower()
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if len(content) >= 12 and content[4:12] in {b"ftypavif", b"ftypavis"}:
        return "image/avif", ".avif"
    raise ValueError(f"unsupported thumbnail content type: {declared or 'unknown'}")


def _download_image(source_url: str) -> DownloadedImage:
    _ensure_public_url(source_url)
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": "Instant-AI/0.5 local-thumbnail-cache",
            "Accept": "image/avif,image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.1",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    )
    opener = urllib.request.build_opener(_PublicRedirectHandler())
    with DOWNLOAD_SEMAPHORE, opener.open(request, timeout=12) as response:
        content = response.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("thumbnail exceeds the local size limit")
        mime_type, _ = _sniff_image_type(content, response.headers.get("Content-Type", ""))
    return DownloadedImage(content, mime_type)


def _placeholder(topics_json: str, event_type: str) -> bytes:
    try:
        topics = json.loads(topics_json or "[]")
    except json.JSONDecodeError:
        topics = []
    joined = " ".join(str(topic) for topic in topics)
    if "AI" in joined:
        label, start, end = "AI", "#e8e3ff", "#c9bfff"
    elif "黄金" in joined:
        label, start, end = "金", "#fff3cb", "#f1d27b"
    elif "紫金" in joined or "铜" in joined or "有色" in joined:
        label, start, end = "矿", "#f4e4d4", "#d7a979"
    elif "战争" in joined:
        label, start, end = "事", "#ffe3e3", "#f3aaaa"
    elif "宏观" in joined:
        label, start, end = "宏", "#dfeeff", "#9fc8f4"
    elif "中国" in joined or "亚洲" in joined:
        label, start, end = "亚", "#ffe6e6", "#f4b2b2"
    elif "创业" in joined:
        label, start, end = "创", "#dcf5e9", "#9bdabf"
    else:
        label, start, end = "财", "#e3edff", "#aec7ee"
    safe_event = "重要" if any(token in event_type for token in ("财报", "并购", "制裁", "事故")) else "快讯"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" viewBox="0 0 160 100">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{start}"/><stop offset="1" stop-color="{end}"/></linearGradient></defs>
<rect width="160" height="100" rx="10" fill="url(#g)"/><path d="M12 76 L42 58 L65 67 L92 35 L118 48 L148 20" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" opacity=".8"/><circle cx="92" cy="35" r="6" fill="#fff" opacity=".9"/><text x="15" y="35" font-family="Microsoft YaHei UI, sans-serif" font-size="26" font-weight="700" fill="#182235">{label}</text><text x="15" y="91" font-family="Microsoft YaHei UI, sans-serif" font-size="11" fill="#26364f" opacity=".75">即时 AI · {safe_event}</text>
</svg>""".encode("utf-8")


def _failed_recently(value: str | None) -> bool:
    if not value:
        return False
    try:
        checked = datetime.fromisoformat(value)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        return datetime.now(UTC) - checked.astimezone(UTC) < FAILED_RETRY_AFTER
    except ValueError:
        return False


def _cached_file(local_path: str | None, cache_root: Path) -> Path | None:
    if not local_path:
        return None
    target = Path(local_path).resolve()
    root = cache_root.resolve()
    if root != target and root not in target.parents:
        return None
    return target if target.is_file() else None


def get_thumbnail(
    item_id: int,
    *,
    path: Path | str | None = None,
    cache_root: Path | None = None,
    fetcher: Callable[[str], DownloadedImage] = _download_image,
) -> ThumbnailResult | None:
    cache_root = cache_root or THUMBNAIL_CACHE_ROOT
    with _item_lock(item_id):
        with connect(path) as connection:
            row = connection.execute(
                """
                SELECT i.id, i.topics_json, i.event_type,
                       t.source_url, t.local_path, t.mime_type, t.status, t.checked_at
                FROM items i
                LEFT JOIN item_thumbnails t ON t.item_id=i.id
                WHERE i.id=?
                """,
                (item_id,),
            ).fetchone()
        if row is None:
            return None

        cached = _cached_file(row["local_path"], cache_root)
        if cached and row["mime_type"]:
            content = cached.read_bytes()
            return ThumbnailResult(
                content,
                str(row["mime_type"]),
                7 * 24 * 60 * 60,
                hashlib.sha256(content).hexdigest(),
                "source",
            )

        source_url = str(row["source_url"] or "")
        should_download = bool(source_url) and not (
            row["status"] == "failed" and _failed_recently(row["checked_at"])
        )
        if should_download:
            try:
                downloaded = fetcher(source_url)
                mime_type, suffix = _sniff_image_type(downloaded.content, downloaded.mime_type)
                digest = hashlib.sha256(downloaded.content).hexdigest()
                cache_root.mkdir(parents=True, exist_ok=True)
                target = cache_root / f"{digest}{suffix}"
                if not target.is_file():
                    target.write_bytes(downloaded.content)
                with transaction(path) as connection:
                    connection.execute(
                        """
                        UPDATE item_thumbnails
                        SET local_path=?, mime_type=?, byte_size=?, status='cached', checked_at=?
                        WHERE item_id=?
                        """,
                        (str(target), mime_type, len(downloaded.content), utc_now(), item_id),
                    )
                return ThumbnailResult(downloaded.content, mime_type, 7 * 24 * 60 * 60, digest, "source")
            except (OSError, ValueError, sqlite3.Error, http.client.HTTPException):
                with transaction(path) as connection:
                    connection.execute(
                        "UPDATE item_thumbnails SET status='failed', checked_at=? WHERE item_id=?",
                        (utc_now(), item_id),
                    )

        content = _placeholder(str(row["topics_json"]), str(row["event_type"]))
        return ThumbnailResult(
            content,
            "image/svg+xml",
            5 * 60,
            hashlib.sha256(content).hexdigest(),
            "placeholder",
        )
