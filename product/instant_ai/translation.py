from __future__ import annotations

import html
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .database import connect, transaction, utc_now


TARGET_LANGUAGE = "zh-CN"
MYMEMORY_PROVIDER = "mymemory-anonymous"
MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"
MYMEMORY_PUBLIC_LIMIT = 5_000
MYMEMORY_SAFE_LIMIT = 4_500
MAX_TITLE_BYTES = 480
MAX_BATCH_ITEMS = 40
MAX_NEW_PER_BATCH = 12
TRANSLATION_LOCK = threading.Lock()


@dataclass(frozen=True)
class TranslationProvider:
    name: str
    external: bool
    daily_limit: int | None
    translate: Callable[[str], str]


def needs_translation(text: str) -> bool:
    """Translate Latin-script headlines while leaving Chinese or numeric titles alone."""

    cjk_count = sum("\u3400" <= char <= "\u9fff" for char in text)
    latin_count = sum(char.isascii() and char.isalpha() for char in text)
    return cjk_count == 0 and latin_count >= 6


def utf8_prefix(text: str, max_bytes: int = MAX_TITLE_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def _request_json(request: urllib.request.Request, timeout: float = 12) -> dict[str, object]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("translation provider returned an invalid response")
    return payload


def _translate_with_mymemory(text: str) -> str:
    query = urllib.parse.urlencode(
        {
            "q": utf8_prefix(text),
            "langpair": "en|zh-CN",
            "mt": "1",
        }
    )
    request = urllib.request.Request(
        f"{MYMEMORY_ENDPOINT}?{query}",
        headers={"User-Agent": "Instant-AI/0.4 local-personal-client"},
    )
    payload = _request_json(request)
    response_data = payload.get("responseData")
    if payload.get("responseStatus") != 200 or not isinstance(response_data, dict):
        raise RuntimeError(str(payload.get("responseDetails") or "MyMemory translation failed"))
    translated = html.unescape(str(response_data.get("translatedText") or "")).strip()
    if not translated:
        raise RuntimeError("MyMemory returned an empty translation")
    return translated


def _translate_with_libretranslate(text: str, endpoint: str, api_key: str) -> str:
    body: dict[str, object] = {
        "q": text,
        "source": "en",
        "target": "zh",
        "format": "text",
    }
    if api_key:
        body["api_key"] = api_key
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/translate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Instant-AI/0.4"},
        method="POST",
    )
    payload = _request_json(request)
    translated = html.unescape(str(payload.get("translatedText") or "")).strip()
    if not translated:
        raise RuntimeError("LibreTranslate returned an empty translation")
    return translated


def active_provider() -> TranslationProvider:
    endpoint = os.getenv("INSTANT_AI_LIBRETRANSLATE_URL", "").strip()
    if endpoint:
        api_key = os.getenv("INSTANT_AI_TRANSLATION_API_KEY", "").strip()
        return TranslationProvider(
            name="libretranslate-local",
            external=False,
            daily_limit=None,
            translate=lambda text: _translate_with_libretranslate(text, endpoint, api_key),
        )
    return TranslationProvider(
        name=MYMEMORY_PROVIDER,
        external=True,
        daily_limit=MYMEMORY_SAFE_LIMIT,
        translate=_translate_with_mymemory,
    )


def _usage_date() -> str:
    return utc_now()[:10]


def _used_characters(provider: str, path: Path | str | None = None) -> int:
    with connect(path) as connection:
        row = connection.execute(
            "SELECT character_count FROM translation_usage WHERE usage_date=? AND provider=?",
            (_usage_date(), provider),
        ).fetchone()
    return int(row[0]) if row else 0


def translation_status(
    path: Path | str | None = None,
    provider: TranslationProvider | None = None,
) -> dict[str, object]:
    provider = provider or active_provider()
    used = _used_characters(provider.name, path)
    with connect(path) as connection:
        cached = int(connection.execute("SELECT COUNT(*) FROM item_translations").fetchone()[0])
    limit = provider.daily_limit
    return {
        "enabled": True,
        "provider": provider.name,
        "provider_label": "MyMemory 免费翻译" if provider.name == MYMEMORY_PROVIDER else "本机 LibreTranslate",
        "external": provider.external,
        "cached_titles": cached,
        "used_characters_today": used,
        "daily_character_limit": limit,
        "remaining_characters_today": max(0, limit - used) if limit is not None else None,
        "official_public_limit": MYMEMORY_PUBLIC_LIMIT if provider.name == MYMEMORY_PROVIDER else None,
        "target_language": TARGET_LANGUAGE,
    }


def translate_items(
    item_ids: list[int],
    *,
    max_new: int = MAX_NEW_PER_BATCH,
    path: Path | str | None = None,
    provider: TranslationProvider | None = None,
) -> dict[str, object]:
    normalized_ids: list[int] = []
    for value in item_ids:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0:
            normalized_ids.append(item_id)
    unique_ids = list(dict.fromkeys(normalized_ids))[:MAX_BATCH_ITEMS]
    selected_provider = provider or active_provider()
    max_new = min(max(int(max_new), 1), MAX_NEW_PER_BATCH)

    with TRANSLATION_LOCK:
        with connect(path) as connection:
            if unique_ids:
                placeholders = ",".join("?" for _ in unique_ids)
                rows = connection.execute(
                    f"""
                    SELECT i.id, i.title, t.translated_title, t.provider
                    FROM items i
                    LEFT JOIN item_translations t
                      ON t.item_id=i.id AND t.target_language=? AND t.original_title=i.title
                    WHERE i.id IN ({placeholders})
                    """,
                    (TARGET_LANGUAGE, *unique_ids),
                ).fetchall()
            else:
                rows = []

        translations: dict[str, str] = {}
        providers: dict[str, str] = {}
        candidates: list[tuple[int, str]] = []
        skipped = 0
        for row in rows:
            item_id = int(row["id"])
            if row["translated_title"]:
                translations[str(item_id)] = str(row["translated_title"])
                providers[str(item_id)] = str(row["provider"])
            elif needs_translation(str(row["title"])):
                candidates.append((item_id, str(row["title"])))
            else:
                skipped += 1

        used = _used_characters(selected_provider.name, path)
        reserved = 0
        chosen: list[tuple[int, str]] = []
        quota_exhausted = False
        for item_id, title in candidates:
            if len(chosen) >= max_new:
                break
            cost = len(utf8_prefix(title))
            if selected_provider.daily_limit is not None and used + reserved + cost > selected_provider.daily_limit:
                quota_exhausted = True
                continue
            chosen.append((item_id, title))
            reserved += cost

        results: dict[int, str] = {}
        errors: list[str] = []
        if chosen:
            worker_count = min(3, len(chosen))
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="instant-ai-translate") as executor:
                futures = {
                    executor.submit(selected_provider.translate, title): (item_id, title)
                    for item_id, title in chosen
                }
                for future in as_completed(futures):
                    item_id, title = futures[future]
                    try:
                        translated = future.result().strip()
                        if translated and translated.casefold() != title.casefold():
                            results[item_id] = translated
                    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
                        errors.append(f"{item_id}: {type(error).__name__}: {error}"[:300])

        requested_characters = sum(len(utf8_prefix(title)) for _, title in chosen)
        if results or requested_characters:
            now = utc_now()
            with transaction(path) as connection:
                for item_id, translated in results.items():
                    original = next(title for candidate_id, title in chosen if candidate_id == item_id)
                    connection.execute(
                        """
                        INSERT INTO item_translations(
                            item_id, target_language, original_title, translated_title,
                            provider, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(item_id, target_language) DO UPDATE SET
                            original_title=excluded.original_title,
                            translated_title=excluded.translated_title,
                            provider=excluded.provider,
                            updated_at=excluded.updated_at
                        """,
                        (item_id, TARGET_LANGUAGE, original, translated, selected_provider.name, now, now),
                    )
                    translations[str(item_id)] = translated
                    providers[str(item_id)] = selected_provider.name
                connection.execute(
                    """
                    INSERT INTO translation_usage(usage_date, provider, character_count, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(usage_date, provider) DO UPDATE SET
                        character_count=translation_usage.character_count + excluded.character_count,
                        updated_at=excluded.updated_at
                    """,
                    (_usage_date(), selected_provider.name, requested_characters, now),
                )

        status = translation_status(path, selected_provider)
        return {
            "ok": not errors,
            "translations": translations,
            "providers": providers,
            "translated_count": len(results),
            "cached_count": len(translations) - len(results),
            "skipped_count": skipped,
            "pending_count": max(0, len(candidates) - len(chosen)),
            "quota_exhausted": quota_exhausted,
            "errors": errors,
            "status": status,
        }
