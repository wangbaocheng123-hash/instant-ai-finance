from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import sqlite3
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable

from .collectors import parse_feed
from .database import connect, transaction, utc_now
from .paths import CACHE_ROOT


THUMBNAIL_CACHE_ROOT = CACHE_ROOT / "thumbnails"
MAX_IMAGE_BYTES = 1_250_000
MAX_ARTICLE_HTML_BYTES = 650_000
MAX_GOOGLE_NEWS_HTML_BYTES = 5_000_000
FAILED_RETRY_AFTER = timedelta(minutes=5)
GOOGLE_INDEX_TTL = timedelta(minutes=5)
GOOGLE_INDEX_MISS_REFRESH_AFTER = timedelta(minutes=1)
THUMBNAIL_BROWSER_CACHE_VERSION = "2"
NO_ORIGINAL_CACHE_SECONDS = 60
DOWNLOAD_SEMAPHORE = threading.BoundedSemaphore(3)
DISCOVERY_SEMAPHORE = threading.BoundedSemaphore(2)
LOCK_GUARD = threading.Lock()
ITEM_LOCKS: dict[int, threading.Lock] = {}
GOOGLE_INDEX_LOCKS: dict[str, threading.Lock] = {}
GOOGLE_INDEX_CACHE: dict[str, tuple[datetime, dict[str, str]]] = {}

GOOGLE_ARTICLE_RE = re.compile(r"^/(?:rss/articles|read)/([^/?#]+)")
GOOGLE_MARKER_RE = re.compile(r'jsdata="oM6qxc;([^;"]+);')
GOOGLE_IMAGE_400_RE = re.compile(r"(/api/attachments/[A-Za-z0-9_=%-]+-w400-h224-p-df)")
GOOGLE_IMAGE_200_RE = re.compile(r"(/api/attachments/[A-Za-z0-9_=%-]+-w200-h112-p-df)")


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


def _google_index_lock(source_url: str) -> threading.Lock:
    with LOCK_GUARD:
        return GOOGLE_INDEX_LOCKS.setdefault(source_url, threading.Lock())


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


@dataclass(frozen=True)
class DownloadedHtml:
    text: str
    final_url: str


class _ArticleMetadataParser(HTMLParser):
    """Collect only publisher-declared lead images, not arbitrary page decoration."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[str] = []
        self._json_depth = 0
        self._json_chunks: list[str] = []
        self.json_documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "meta":
            marker = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            ).lower()
            if marker in {
                "og:image",
                "og:image:url",
                "og:image:secure_url",
                "twitter:image",
                "twitter:image:src",
                "image",
            }:
                value = attributes.get("content", "").strip()
                if value:
                    self.images.append(value)
        elif tag.lower() == "link" and "image_src" in attributes.get("rel", "").lower().split():
            value = attributes.get("href", "").strip()
            if value:
                self.images.append(value)
        elif tag.lower() == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self._json_depth += 1
            self._json_chunks = []

    def handle_data(self, data: str) -> None:
        if self._json_depth:
            self._json_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._json_depth:
            self._json_depth -= 1
            document = "".join(self._json_chunks).strip()
            if document:
                self.json_documents.append(document)
            self._json_chunks = []


def _structured_image_values(value: object) -> Iterable[str]:
    if isinstance(value, list):
        for entry in value:
            yield from _structured_image_values(entry)
        return
    if not isinstance(value, dict):
        return
    for key, entry in value.items():
        if key.lower() == "image":
            if isinstance(entry, str):
                yield entry
            elif isinstance(entry, dict):
                for field in ("url", "contentUrl"):
                    candidate = entry.get(field)
                    if isinstance(candidate, str):
                        yield candidate
            elif isinstance(entry, list):
                for candidate in entry:
                    if isinstance(candidate, str):
                        yield candidate
                    elif isinstance(candidate, dict):
                        for field in ("url", "contentUrl"):
                            nested = candidate.get(field)
                            if isinstance(nested, str):
                                yield nested
        yield from _structured_image_values(entry)


def _article_image_candidates(html_text: str, base_url: str) -> list[str]:
    parser = _ArticleMetadataParser()
    parser.feed(html_text)
    candidates = list(parser.images)
    for document in parser.json_documents:
        try:
            candidates.extend(_structured_image_values(json.loads(document)))
        except (json.JSONDecodeError, RecursionError):
            continue

    resolved: list[str] = []
    for candidate in candidates:
        absolute = urllib.parse.urljoin(base_url, unescape(candidate.strip()))
        parsed = urllib.parse.urlparse(absolute)
        try:
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or port not in {None, 80, 443}
        ):
            continue
        if absolute not in resolved:
            resolved.append(absolute)
    return resolved


def _download_html(source_url: str, max_bytes: int = MAX_ARTICLE_HTML_BYTES) -> DownloadedHtml:
    _ensure_public_url(source_url)
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": "Instant-AI/0.6 local-news-preview",
            "Accept": "text/html,application/xhtml+xml;q=0.9",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )
    opener = urllib.request.build_opener(_PublicRedirectHandler())
    with DISCOVERY_SEMAPHORE, opener.open(request, timeout=18) as response:
        final_url = _ensure_public_url(response.geturl())
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("news page is not HTML")
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            content = content[:max_bytes]
        charset = response.headers.get_content_charset() or "utf-8"
    return DownloadedHtml(content.decode(charset, errors="replace"), final_url)


def download_public_html(source_url: str, max_bytes: int = MAX_ARTICLE_HTML_BYTES) -> DownloadedHtml:
    """Fetch bounded public HTML with the same SSRF and redirect checks as thumbnails."""

    return _download_html(source_url, max_bytes)


def _discover_article_image(article_url: str) -> str | None:
    document = _download_html(article_url)
    candidates = _article_image_candidates(document.text, document.final_url)
    return candidates[0] if candidates else None


def _google_article_id(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.hostname not in {"news.google.com", "www.news.google.com"}:
        return ""
    match = GOOGLE_ARTICLE_RE.match(parsed.path)
    return match.group(1) if match else ""


def _google_search_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.hostname not in {"news.google.com", "www.news.google.com"} or parsed.path != "/rss/search":
        return ""
    return urllib.parse.urlunparse(parsed._replace(path="/search"))


def invalidate_google_news_image_index(feed_url: str) -> None:
    """Drop a stale preview index after its RSS source yields new stories."""

    search_url = _google_search_url(feed_url)
    if not search_url:
        return
    with _google_index_lock(search_url):
        GOOGLE_INDEX_CACHE.pop(search_url, None)


def _extract_google_news_images(html_text: str) -> dict[str, str]:
    """Map Google News article IDs to its publisher-derived lead-image previews."""

    decoded = unescape(html_text)
    markers = list(GOOGLE_MARKER_RE.finditer(decoded))
    images: dict[str, str] = {}
    for index, marker in enumerate(markers):
        article_id = marker.group(1)
        if article_id in images:
            continue
        end = len(decoded)
        for following in markers[index + 1 :]:
            if following.group(1) != article_id:
                end = following.start()
                break
        chunk = decoded[marker.start() : min(end, marker.start() + 80_000)]
        image_match = GOOGLE_IMAGE_400_RE.search(chunk) or GOOGLE_IMAGE_200_RE.search(chunk)
        if image_match:
            images[article_id] = urllib.parse.urljoin("https://news.google.com/", image_match.group(1))
    return images


def _google_news_image_index(feed_url: str, expected_article_id: str = "") -> dict[str, str]:
    search_url = _google_search_url(feed_url)
    if not search_url:
        return {}
    now = datetime.now(UTC)
    cached = GOOGLE_INDEX_CACHE.get(search_url)
    if cached and now - cached[0] < GOOGLE_INDEX_TTL and (
        not expected_article_id
        or expected_article_id in cached[1]
        or now - cached[0] < GOOGLE_INDEX_MISS_REFRESH_AFTER
    ):
        return cached[1]

    with _google_index_lock(search_url):
        cached = GOOGLE_INDEX_CACHE.get(search_url)
        now = datetime.now(UTC)
        if cached and now - cached[0] < GOOGLE_INDEX_TTL and (
            not expected_article_id
            or expected_article_id in cached[1]
            or now - cached[0] < GOOGLE_INDEX_MISS_REFRESH_AFTER
        ):
            return cached[1]
        document = _download_html(search_url, MAX_GOOGLE_NEWS_HTML_BYTES)
        images = _extract_google_news_images(document.text)
        GOOGLE_INDEX_CACHE[search_url] = (datetime.now(UTC), images)
        return images


def _discover_image(article_url: str, feed_urls: list[str]) -> str | None:
    article_id = _google_article_id(article_url)
    if article_id:
        for feed_url in feed_urls:
            try:
                candidate = _google_news_image_index(feed_url, article_id).get(article_id)
            except (OSError, ValueError, http.client.HTTPException):
                continue
            if candidate:
                return candidate
        return None
    return _discover_article_image(article_url)


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
            "User-Agent": "Instant-AI/0.6 local-thumbnail-cache",
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


def _no_original_image() -> bytes:
    return """<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100" viewBox="0 0 160 100">
<rect width="160" height="100" rx="8" fill="#f2f4f7"/><rect x="45" y="22" width="70" height="45" rx="5" fill="#fff" stroke="#cbd5e1" stroke-width="2"/><circle cx="65" cy="39" r="7" fill="#dbe4ee"/><path d="M51 61l18-15 13 11 11-9 16 13" fill="none" stroke="#a8b5c4" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/><text x="80" y="87" text-anchor="middle" font-family="Microsoft YaHei UI, sans-serif" font-size="12" fill="#667085">暂无新闻原图</text>
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
    discoverer: Callable[[str, list[str]], str | None] = _discover_image,
) -> ThumbnailResult | None:
    cache_root = cache_root or THUMBNAIL_CACHE_ROOT
    with _item_lock(item_id):
        with connect(path) as connection:
            row = connection.execute(
                """
                SELECT i.id, i.url,
                       t.source_url, t.local_path, t.mime_type, t.status, t.checked_at
                FROM items i
                LEFT JOIN item_thumbnails t ON t.item_id=i.id
                WHERE i.id=?
                """,
                (item_id,),
            ).fetchone()
            feed_urls = [
                str(entry["url"])
                for entry in connection.execute(
                    """
                    SELECT DISTINCT s.url
                    FROM item_evidence ie
                    JOIN evidence e ON e.id=ie.evidence_id
                    JOIN sources s ON s.id=e.source_id
                    WHERE ie.item_id=?
                    """,
                    (item_id,),
                ).fetchall()
            ]
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
                "article",
            )

        source_url = str(row["source_url"] or "")
        discovery_deferred = row["status"] in {"failed", "not_found"} and _failed_recently(row["checked_at"])
        if not source_url and not discovery_deferred:
            try:
                source_url = discoverer(str(row["url"]), feed_urls) or ""
            except (OSError, ValueError, http.client.HTTPException):
                source_url = ""
            with transaction(path) as connection:
                if source_url:
                    register_thumbnail_candidate(connection, item_id, source_url)
                else:
                    connection.execute(
                        """
                        INSERT INTO item_thumbnails(
                            item_id, source_url, local_path, mime_type, byte_size, status, checked_at
                        ) VALUES (?, '', NULL, NULL, NULL, 'not_found', ?)
                        ON CONFLICT(item_id) DO UPDATE SET
                            status='not_found', checked_at=excluded.checked_at
                        """,
                        (item_id, utc_now()),
                    )

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
                return ThumbnailResult(downloaded.content, mime_type, 7 * 24 * 60 * 60, digest, "article")
            except (OSError, ValueError, sqlite3.Error, http.client.HTTPException):
                with transaction(path) as connection:
                    connection.execute(
                        "UPDATE item_thumbnails SET status='failed', checked_at=? WHERE item_id=?",
                        (utc_now(), item_id),
                    )

        content = _no_original_image()
        return ThumbnailResult(
            content,
            "image/svg+xml",
            NO_ORIGINAL_CACHE_SECONDS,
            hashlib.sha256(content).hexdigest(),
            "no-original",
        )
