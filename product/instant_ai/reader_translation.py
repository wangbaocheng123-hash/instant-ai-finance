from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .database import connect, transaction, utc_now
from .translation import (
    TARGET_LANGUAGE,
    TranslationProvider,
    active_provider,
    translate_text,
    translation_status,
)


def _clean_prose(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _item_fingerprint(url: str, summary: str) -> str:
    # The policy marker invalidates legacy article-excerpt caches.  From 0.9.1
    # onward this endpoint is deliberately summary-only; the original page is
    # translated by the user's browser and is never downloaded by Instant AI.
    return hashlib.sha256(f"summary-only-v1\n{url}\n{summary}".encode("utf-8")).hexdigest()


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
) -> dict[str, object]:
    """Translate only the stored feed summary; never download the original article."""

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
    source_text = _clean_prose(str(item["summary"] or ""))
    source_kind = "summary"
    source_truncated = False
    if not source_text:
        return {
            "ok": False,
            "item_id": int(item_id),
            "error": "no_public_text",
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
    return response
