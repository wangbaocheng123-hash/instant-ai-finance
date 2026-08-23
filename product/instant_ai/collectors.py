from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .database import utc_now
from .paths import RAW_ROOT
from .rules import clean_text, normalized_url


USER_AGENT = "InstantAI/0.2 (+local personal research client)"


@dataclass(frozen=True)
class Source:
    id: int
    key: str
    name: str
    kind: str
    url: str
    trust_level: int
    topic_hints: list[str]
    config: dict
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class Entry:
    source_item_id: str
    title: str
    url: str
    summary: str
    published_at: str | None


@dataclass(frozen=True)
class FetchResult:
    status: int
    content_type: str
    body: bytes
    etag: str | None
    last_modified: str | None


def fetch(source: Source, timeout: int = 30) -> FetchResult:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified

    request = urllib.request.Request(source.url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return FetchResult(
                status=response.status,
                content_type=response.headers.get_content_type(),
                body=response.read(8 * 1024 * 1024),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return FetchResult(304, "", b"", source.etag, source.last_modified)
        raise


def store_raw(source: Source, result: FetchResult) -> tuple[str, str]:
    digest = hashlib.sha256(result.body).hexdigest()
    date_part = datetime.now(UTC).strftime("%Y-%m-%d")
    suffix = ".xml" if "xml" in result.content_type or source.kind == "rss" else ".html"
    target = RAW_ROOT / source.key / date_part / f"{digest}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(result.body)
    return digest, str(target)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(element: ET.Element, names: set[str]) -> str:
    for child in element.iter():
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _entry_link(element: ET.Element) -> str:
    for child in element.iter():
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def parse_date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def parse_feed(body: bytes, max_entries: int = 50) -> list[Entry]:
    root = ET.fromstring(body)
    candidates = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    entries: list[Entry] = []
    for element in candidates[:max_entries]:
        title = clean_text(_first_text(element, {"title"}))
        link = _entry_link(element)
        identifier = _first_text(element, {"guid", "id"}) or link or title
        summary = clean_text(_first_text(element, {"description", "summary", "content", "encoded"}))
        published = parse_date(_first_text(element, {"pubdate", "published", "updated", "date"}))
        if not title or not link:
            continue
        entries.append(Entry(identifier, title, normalized_url(link), summary[:4000], published))
    return entries


class LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            values = dict(attrs)
            self._href = values.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            title = clean_text(" ".join(self._text))
            self.links.append((urljoin(self.base_url, self._href), title))
            self._href = None
            self._text = []


def _decode_html(body: bytes) -> str:
    head = body[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", head, re.IGNORECASE)
    candidates = [match.group(1)] if match else []
    candidates.extend(["utf-8", "gb18030"])
    for encoding in candidates:
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace")


def parse_html_links(source: Source, body: bytes) -> list[Entry]:
    parser = LinkParser(source.url)
    parser.feed(_decode_html(body))
    config = source.config
    min_length = int(config.get("min_title_length", 8))
    max_entries = int(config.get("max_entries", 50))
    include_parts = [str(item) for item in config.get("url_contains", [])]
    exclude_parts = [str(item) for item in config.get("exclude_url_contains", [])]
    source_host = urlsplit(source.url).netloc.lower()
    seen: set[str] = set()
    entries: list[Entry] = []
    for link, title in parser.links:
        normalized = normalized_url(link)
        if not title or len(title) < min_length or normalized in seen:
            continue
        if config.get("same_domain", True) and urlsplit(normalized).netloc.lower() != source_host:
            continue
        if include_parts and not any(part in normalized for part in include_parts):
            continue
        if any(part in normalized for part in exclude_parts):
            continue
        if normalized.startswith(("javascript:", "mailto:")):
            continue
        seen.add(normalized)
        entries.append(Entry(normalized, title[:500], normalized, "", None))
        if len(entries) >= max_entries:
            break
    return entries


def collect_source(source: Source) -> tuple[FetchResult, list[Entry], str, str]:
    result = fetch(source)
    if result.status == 304:
        return result, [], "", ""
    digest, raw_path = store_raw(source, result)
    if source.kind == "rss":
        entries = parse_feed(result.body, int(source.config.get("max_entries", 50)))
    elif source.kind == "html_links":
        entries = parse_html_links(source, result.body)
    else:
        raise ValueError(f"Unsupported source kind: {source.kind}")
    return result, entries, digest, raw_path
