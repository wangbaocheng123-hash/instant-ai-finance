from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from .database import utc_now
from .date_hints import infer_embedded_published_at
from .paths import RAW_ROOT
from .publishers import resolve_publisher_identity
from .rules import clean_text, normalized_url


USER_AGENT = "InstantAI/0.5 (+local personal research client)"


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
    image_url: str = ""
    publisher: str = ""
    publisher_url: str = ""


@dataclass(frozen=True)
class FetchResult:
    status: int
    content_type: str
    body: bytes
    etag: str | None
    last_modified: str | None


def fetch(source: Source, timeout: int = 30) -> FetchResult:
    accept = (
        "application/json"
        if source.kind == "stockplus_breaking_json"
        else "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.5"
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
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
    if "json" in result.content_type or source.kind.endswith("_json"):
        suffix = ".json"
    elif "xml" in result.content_type or source.kind in {"rss", "bing_news_rss"}:
        suffix = ".xml"
    else:
        suffix = ".html"
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


def _usable_image_url(value: str, base_url: str) -> str:
    candidate = urljoin(base_url, value.strip())
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate


def _entry_image(element: ET.Element, link: str) -> str:
    """Read image metadata without downloading the image during collection."""

    candidates: list[str] = []
    for child in element.iter():
        name = _local_name(child.tag)
        attributes = {str(key).lower(): str(value) for key, value in child.attrib.items()}
        media_type = attributes.get("type", "").lower()
        medium = attributes.get("medium", "").lower()
        if name in {"thumbnail", "image", "imageurl"}:
            candidates.append(attributes.get("url") or attributes.get("href") or (child.text or ""))
        elif name in {"content", "enclosure"} and (medium == "image" or media_type.startswith("image/")):
            candidates.append(attributes.get("url") or attributes.get("href") or "")

        if name in {"description", "summary", "content", "encoded"} and child.text:
            match = re.search(
                r"<img\b[^>]+(?:src|data-src)\s*=\s*['\"]([^'\"]+)",
                child.text,
                re.IGNORECASE,
            )
            if match:
                candidates.append(match.group(1))

    for candidate in candidates:
        if candidate:
            image_url = _usable_image_url(candidate, link)
            if image_url:
                return image_url
    return ""


def _entry_publisher(element: ET.Element) -> tuple[str, str]:
    for child in element.iter():
        if _local_name(child.tag) != "source":
            continue
        name = clean_text(child.text or "")
        if not name:
            name = clean_text(_first_text(child, {"title", "name"}))
        url = str(child.attrib.get("url") or child.attrib.get("href") or "").strip()
        if name or url:
            return name, url
    return "", ""


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
        if not published:
            published = infer_embedded_published_at(title, summary)
        if not title or not link:
            continue
        normalized_link = normalized_url(link)
        publisher, publisher_url = _entry_publisher(element)
        entries.append(
            Entry(
                identifier,
                title,
                normalized_link,
                summary[:4000],
                published,
                _entry_image(element, normalized_link),
                publisher,
                publisher_url,
            )
        )
    return entries


class LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._image_url = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            values = dict(attrs)
            self._href = values.get("href")
            self._image_url = ""
            self._text = []
        elif tag.lower() == "img" and self._href is not None and not self._image_url:
            values = dict(attrs)
            candidate = values.get("src") or values.get("data-src") or ""
            self._image_url = _usable_image_url(candidate, self.base_url) if candidate else ""

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            title = clean_text(" ".join(self._text))
            self.links.append((urljoin(self.base_url, self._href), title, self._image_url))
            self._href = None
            self._image_url = ""
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
    for link, title, image_url in parser.links:
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
        entries.append(
            Entry(
                normalized,
                title[:500],
                normalized,
                "",
                infer_embedded_published_at(title),
                image_url,
            )
        )
        if len(entries) >= max_entries:
            break
    return entries


class WechatPublicIndexParser(HTMLParser):
    """Read title/date/link metadata from a public account index page.

    This intentionally does not fetch or copy WeChat article bodies.  The
    adapter is a discovery fallback for accounts that do not expose an
    official public feed.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.entries: list[tuple[str, str, str]] = []
        self.page_text: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._title_text: list[str] = []
        self._in_pretty_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag.lower() == "a" and "ae-container-2" in classes:
            self._href = values.get("href")
            self._text = []
            self._title_text = []
            self._in_pretty_title = False
        elif self._href is not None and tag.lower() == "span" and "pretty" in classes:
            self._in_pretty_title = True

    def handle_data(self, data: str) -> None:
        self.page_text.append(data)
        if self._href is None:
            return
        self._text.append(data)
        if self._in_pretty_title:
            self._title_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "span" and self._in_pretty_title:
            self._in_pretty_title = False
        if lowered == "a" and self._href is not None:
            link = urljoin(self.base_url, self._href)
            title = clean_text(" ".join(self._title_text))
            date_hint = clean_text(" ".join(self._text))
            self.entries.append((link, title, date_hint))
            self._href = None
            self._text = []
            self._title_text = []
            self._in_pretty_title = False


_MONTH_DAY_HINT = re.compile(r"(?<!\d)(?P<month>0?[1-9]|1[0-2])\s*/\s*(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)")
_CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _parse_month_day_hint(value: str, now: datetime | None = None) -> str | None:
    match = _MONTH_DAY_HINT.search(value)
    if not match:
        return None
    current = (now or datetime.now(UTC)).astimezone(_CHINA_TIMEZONE)
    try:
        candidate = datetime(
            current.year,
            int(match.group("month")),
            int(match.group("day")),
            tzinfo=_CHINA_TIMEZONE,
        )
        if candidate > current + timedelta(days=1):
            candidate = candidate.replace(year=current.year - 1)
    except ValueError:
        return None
    return candidate.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_wechat_public_index(
    source: Source,
    body: bytes,
    *,
    now: datetime | None = None,
) -> list[Entry]:
    """Parse a no-login public index as low-trust, title-only discovery."""

    parser = WechatPublicIndexParser(source.url)
    document = _decode_html(body)
    parser.feed(document)
    expected_account = clean_text(str(source.config.get("expected_account", "")))
    expected_biz = str(source.config.get("wechat_biz", "")).strip()
    page_text = clean_text(" ".join(parser.page_text))
    if expected_account and expected_account not in page_text:
        raise ValueError(f"Public index account mismatch: expected {expected_account}")
    if expected_biz and expected_biz not in document:
        raise ValueError("Public index account identifier mismatch")

    max_entries = int(source.config.get("max_entries", 30))
    seen: set[str] = set()
    entries: list[Entry] = []
    for link, title, date_hint in parser.entries:
        normalized = normalized_url(link)
        published = _parse_month_day_hint(date_hint, now)
        if not title or not published or normalized in seen:
            continue
        seen.add(normalized)
        entries.append(
            Entry(
                normalized.rsplit("/", 1)[-1],
                title[:500],
                normalized,
                "",
                published,
            )
        )
        if len(entries) >= max_entries:
            break
    if not entries:
        raise ValueError("Public account index returned no dated article entries")
    return entries


class KBResearchTodayParser(HTMLParser):
    """Read dated segment titles from KB Securities' public morning page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.date_text: list[str] = []
        self.segments: list[tuple[str, str, str]] = []
        self._date_depth = 0
        self._content_depth = 0
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._pending_anchor: tuple[str, str] | None = None
        self._pending_title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        values = dict(attrs)
        if lowered == "div":
            classes = set((values.get("class") or "").split())
            if self._date_depth:
                self._date_depth += 1
            elif "ytb-tit" in classes:
                self._date_depth = 1
            if self._content_depth:
                self._content_depth += 1
            elif values.get("id") == "ytb-cont":
                self._content_depth = 1
        elif lowered == "a" and self._content_depth:
            self._finish_pending()
            self._anchor_href = values.get("href") or ""
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._date_depth:
            self.date_text.append(data)
        if not self._content_depth:
            return
        if self._anchor_href is not None:
            self._anchor_text.append(data)
        elif self._pending_anchor is not None:
            self._pending_title.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a" and self._content_depth and self._anchor_href is not None:
            self._pending_anchor = (self._anchor_href, clean_text(" ".join(self._anchor_text)))
            self._anchor_href = None
            self._anchor_text = []
        if lowered != "div":
            return
        if self._content_depth:
            self._content_depth -= 1
            if not self._content_depth:
                self._finish_pending()
        if self._date_depth:
            self._date_depth -= 1

    def close(self) -> None:
        super().close()
        self._finish_pending()

    def _finish_pending(self) -> None:
        if self._pending_anchor is None:
            return
        href, marker = self._pending_anchor
        self.segments.append((href, marker, clean_text(" ".join(self._pending_title))))
        self._pending_anchor = None
        self._pending_title = []


_KB_RESEARCH_DATE = re.compile(
    r"(?P<year>20\d{2})\s*년\s*(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일"
)
_KOREA_TIMEZONE = timezone(timedelta(hours=9))


def parse_kb_research_today(source: Source, body: bytes) -> list[Entry]:
    """Parse KB's own dated morning-video agenda without copying report prose."""

    parser = KBResearchTodayParser()
    parser.feed(_decode_html(body))
    parser.close()
    match = _KB_RESEARCH_DATE.search(clean_text(" ".join(parser.date_text)))
    if not match:
        raise ValueError("KB research page returned no dated morning agenda")
    published = datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        tzinfo=_KOREA_TIMEZONE,
    ).astimezone(UTC).isoformat()

    max_entries = int(source.config.get("max_entries", 30))
    seen: set[str] = set()
    entries: list[Entry] = []
    for href, marker, raw_title in parser.segments:
        parsed = urlsplit(href)
        if parsed.hostname not in {"youtube.com", "www.youtube.com", "youtu.be"}:
            continue
        title = re.sub(r"^\[\s*|\s*\]$", "", raw_title).strip()
        link = normalized_url(href)
        if not title or not link or link in seen:
            continue
        seen.add(link)
        identifier = hashlib.sha256(f"{published}|{link}|{title}".encode("utf-8")).hexdigest()
        display_title = f"KB证券晨会：{title}"
        if marker:
            display_title += f"（{marker}）"
        entries.append(Entry(identifier, display_title[:500], link, "", published))
        if len(entries) >= max_entries:
            break
    if not entries:
        raise ValueError("KB research page returned no public morning segments")
    return entries


def parse_stockplus_breaking(source: Source, body: bytes) -> list[Entry]:
    """Read the public Stockplus breaking-news index without retaining summaries."""

    payload = json.loads(body.decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("breakingNews") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("Stockplus breaking-news response has no item list")

    max_entries = int(source.config.get("max_entries", 70))
    entries: list[Entry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        title = clean_text(str(item.get("title") or ""))
        published_ms = item.get("publishedAt")
        if (
            not isinstance(item_id, int)
            or item_id <= 0
            or not title
            or not isinstance(published_ms, (int, float))
            or published_ms <= 0
        ):
            continue
        try:
            published = datetime.fromtimestamp(published_ms / 1000, UTC).replace(microsecond=0).isoformat()
        except (OSError, OverflowError, ValueError):
            continue
        link = f"https://newsroom.stockplus.com/breaking-news/{item_id}"
        entries.append(Entry(f"stockplus-breaking-{item_id}", title[:500], link, "", published))
        if len(entries) >= max_entries:
            break
    if not entries:
        raise ValueError("Stockplus breaking-news response returned no usable public titles")
    return entries


def parse_bing_news_feed(source: Source, body: bytes) -> list[Entry]:
    """Unwrap Bing News discovery links and enforce the configured publisher domains."""

    allowed_domains = {
        str(domain).lower().lstrip(".")
        for domain in source.config.get("allowed_domains", [])
        if str(domain).strip()
    }
    if not allowed_domains:
        raise ValueError("Bing News source has no allowed publisher domain")
    required_keywords = [
        str(keyword).casefold()
        for keyword in source.config.get("required_title_keywords", [])
        if str(keyword).strip()
    ]

    entries: list[Entry] = []
    for entry in parse_feed(body, int(source.config.get("max_entries", 70))):
        title_folded = entry.title.casefold()
        if required_keywords and not any(keyword in title_folded for keyword in required_keywords):
            continue
        parsed = urlsplit(entry.url)
        candidate = entry.url
        if parsed.hostname and (parsed.hostname == "bing.com" or parsed.hostname.endswith(".bing.com")):
            candidate = (parse_qs(parsed.query).get("url") or [""])[0]
        direct = urlsplit(candidate)
        hostname = (direct.hostname or "").lower()
        if direct.scheme != "https" or not any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains
        ):
            continue
        link = normalized_url(candidate)
        identifier = hashlib.sha256(
            f"{entry.published_at or ''}|{link}|{entry.title}".encode("utf-8")
        ).hexdigest()
        entries.append(
            Entry(
                identifier,
                entry.title,
                link,
                "",
                entry.published_at,
                publisher=entry.publisher,
                publisher_url=entry.publisher_url,
            )
        )
    if not entries:
        raise ValueError("Bing News response returned no links from the configured publisher domains")
    return entries


def collect_source(source: Source) -> tuple[FetchResult, list[Entry], str, str]:
    result = fetch(source)
    if result.status == 304:
        return result, [], "", ""
    digest, raw_path = store_raw(source, result)
    if source.kind == "rss":
        entries = parse_feed(result.body, int(source.config.get("max_entries", 50)))
    elif source.kind == "bing_news_rss":
        entries = parse_bing_news_feed(source, result.body)
    elif source.kind == "html_links":
        entries = parse_html_links(source, result.body)
    elif source.kind == "wechat_public_index":
        entries = parse_wechat_public_index(source, result.body)
    elif source.kind == "kb_research_today":
        entries = parse_kb_research_today(source, result.body)
    elif source.kind == "stockplus_breaking_json":
        entries = parse_stockplus_breaking(source, result.body)
    else:
        raise ValueError(f"Unsupported source kind: {source.kind}")
    if source.config.get("title_link_only"):
        entries = [
            replace(entry, summary="")
            for entry in entries
        ]
    enriched: list[Entry] = []
    for entry in entries:
        publisher = resolve_publisher_identity(
            explicit_name=entry.publisher,
            explicit_url=entry.publisher_url,
            article_url=entry.url,
            title=entry.title,
            source_name=source.name,
            source_url=source.url,
            source_config=source.config,
        )
        enriched.append(
            replace(entry, publisher=publisher.name, publisher_url=publisher.url)
        )
    return result, enriched, digest, raw_path
