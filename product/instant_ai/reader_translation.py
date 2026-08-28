from __future__ import annotations

import hashlib
import re
import urllib.error
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from .database import connect, transaction, utc_now
from .thumbnails import DownloadedHtml, download_public_html
from .translation import (
    TARGET_LANGUAGE,
    TranslationProvider,
    active_provider,
    translate_text,
    translation_status,
)


MAX_READER_HTML_BYTES = 650_000
MAX_READER_SOURCE_CHARACTERS = 3_600
MIN_ARTICLE_CHARACTERS = 280
BLOCKED_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form"}
BOILERPLATE_MARKERS = (
    "subscribe",
    "sign up",
    "newsletter",
    "all rights reserved",
    "accept cookies",
    "privacy policy",
    "terms of use",
    "advertisement",
)


class _ArticleParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._paragraph_parts: list[str] | None = None
        self._paragraph_primary = False
        self.primary: list[str] = []
        self.fallback: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        self._stack.append(normalized)
        if normalized == "p":
            self._paragraph_parts = []
            self._paragraph_primary = any(entry in {"article", "main"} for entry in self._stack)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_data(self, data: str) -> None:
        if self._paragraph_parts is None or any(tag in BLOCKED_TAGS for tag in self._stack):
            return
        self._paragraph_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "p" and self._paragraph_parts is not None:
            paragraph = _clean_prose(" ".join(self._paragraph_parts))
            self._paragraph_parts = None
            if _useful_paragraph(paragraph):
                self.fallback.append(paragraph)
                if self._paragraph_primary:
                    self.primary.append(paragraph)
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index] == normalized:
                del self._stack[index:]
                break


def _clean_prose(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _useful_paragraph(value: str) -> bool:
    if len(value) < 45:
        return False
    lowered = value.casefold()
    if any(marker in lowered for marker in BOILERPLATE_MARKERS) and len(value) < 500:
        return False
    return sum(character.isalpha() for character in value) >= 25


def _deduplicate(paragraphs: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        key = re.sub(r"\W+", "", paragraph).casefold()[:500]
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(paragraph)
    return result


def extract_article_text(
    html_text: str,
    *,
    max_characters: int = MAX_READER_SOURCE_CHARACTERS,
) -> tuple[str, bool]:
    """Extract a bounded article/main paragraph excerpt without page chrome."""

    parser = _ArticleParagraphParser()
    parser.feed(html_text)
    primary = _deduplicate(parser.primary)
    fallback = _deduplicate(parser.fallback)
    paragraphs = primary if len(" ".join(primary)) >= MIN_ARTICLE_CHARACTERS else fallback
    combined = "\n\n".join(paragraphs).strip()
    if len(combined) <= max_characters:
        return combined, False
    excerpt = combined[:max_characters]
    boundary = max(excerpt.rfind("\n\n"), excerpt.rfind(". "), excerpt.rfind("。"))
    if boundary >= max_characters // 2:
        excerpt = excerpt[: boundary + 1]
    return excerpt.strip(), True


def _item_fingerprint(url: str, summary: str) -> str:
    return hashlib.sha256(f"{url}\n{summary}".encode("utf-8")).hexdigest()


def _requires_translation(text: str) -> bool:
    cjk = sum("\u3400" <= character <= "\u9fff" for character in text)
    latin = sum(character.isascii() and character.isalpha() for character in text)
    return latin >= 20 and latin > cjk * 2


def _response_from_row(row: object, *, cached: bool, status: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "item_id": int(row["item_id"]),
        "source_url": str(row["source_url"]),
        "source_kind": str(row["source_kind"]),
        "original_excerpt": str(row["original_excerpt"]),
        "translated_text": str(row["translated_text"]),
        "provider": str(row["provider"]),
        "source_truncated": bool(row["source_truncated"]),
        "translation_partial": bool(row["translation_partial"]),
        "cached": cached,
        "updated_at": str(row["updated_at"]),
        "quota_exhausted": False,
        "errors": [],
        "status": status,
    }


def translate_reader_item(
    item_id: int,
    *,
    path: Path | str | None = None,
    provider: TranslationProvider | None = None,
    fetcher: Callable[[str, int], DownloadedHtml] = download_public_html,
) -> dict[str, object]:
    """Translate a public article excerpt on demand, falling back to its feed summary."""

    selected_provider = provider or active_provider()
    with connect(path) as connection:
        item = connection.execute(
            "SELECT id, title, url, summary FROM items WHERE id=?",
            (int(item_id),),
        ).fetchone()
        if item is None:
            return {"ok": False, "error": "news_not_found"}
        fingerprint = _item_fingerprint(str(item["url"]), str(item["summary"] or ""))
        cached_row = connection.execute(
            "SELECT * FROM reader_translations WHERE item_id=? AND target_language=? AND item_fingerprint=?",
            (int(item_id), TARGET_LANGUAGE, fingerprint),
        ).fetchone()
    if cached_row is not None:
        return _response_from_row(
            cached_row,
            cached=True,
            status=translation_status(path, selected_provider),
        )

    source_url = str(item["url"])
    summary = _clean_prose(str(item["summary"] or ""))
    source_text = ""
    source_kind = "summary"
    source_truncated = False
    fetch_error = ""
    try:
        document = fetcher(source_url, MAX_READER_HTML_BYTES)
        extracted, source_truncated = extract_article_text(document.text)
        if len(extracted) >= MIN_ARTICLE_CHARACTERS:
            source_text = extracted
            source_url = document.final_url
            source_kind = "article_excerpt"
    except (OSError, ValueError, UnicodeError, urllib.error.URLError) as error:
        fetch_error = type(error).__name__

    if not source_text:
        source_text = summary
        source_kind = "summary"
        source_truncated = False
    if not source_text:
        return {
            "ok": False,
            "item_id": int(item_id),
            "error": "no_public_text",
            "fetch_error": fetch_error,
            "status": translation_status(path, selected_provider),
        }

    if _requires_translation(source_text):
        translated = translate_text(source_text, path=path, provider=selected_provider)
        translated_text = str(translated["translated_text"])
        translation_partial = bool(translated["partial"])
        provider_name = str(translated["provider"])
        quota_exhausted = bool(translated["quota_exhausted"])
        errors = list(translated["errors"])
        status = dict(translated["status"])
    else:
        translated_text = source_text
        translation_partial = False
        provider_name = "source-chinese"
        quota_exhausted = False
        errors = []
        status = translation_status(path, selected_provider)

    if not translated_text:
        return {
            "ok": False,
            "item_id": int(item_id),
            "source_url": source_url,
            "source_kind": source_kind,
            "original_excerpt": source_text,
            "source_truncated": source_truncated,
            "translation_partial": True,
            "quota_exhausted": quota_exhausted,
            "errors": errors,
            "error": "translation_unavailable",
            "fetch_error": fetch_error,
            "status": status,
        }

    now = utc_now()
    with transaction(path) as connection:
        connection.execute(
            """
            INSERT INTO reader_translations(
                item_id, target_language, item_fingerprint, source_url, source_kind,
                original_excerpt, translated_text, provider, source_truncated,
                translation_partial, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id, target_language) DO UPDATE SET
                item_fingerprint=excluded.item_fingerprint,
                source_url=excluded.source_url,
                source_kind=excluded.source_kind,
                original_excerpt=excluded.original_excerpt,
                translated_text=excluded.translated_text,
                provider=excluded.provider,
                source_truncated=excluded.source_truncated,
                translation_partial=excluded.translation_partial,
                updated_at=excluded.updated_at
            """,
            (
                int(item_id), TARGET_LANGUAGE, fingerprint, source_url, source_kind,
                source_text, translated_text, provider_name, int(source_truncated),
                int(translation_partial), now, now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM reader_translations WHERE item_id=? AND target_language=?",
            (int(item_id), TARGET_LANGUAGE),
        ).fetchone()
    response = _response_from_row(row, cached=False, status=status)
    response["quota_exhausted"] = quota_exhausted
    response["errors"] = errors
    response["fetch_error"] = fetch_error
    return response
