from __future__ import annotations

import html
import json
import os
import re
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
    label: str = ""
    configured: bool = True


def headline_provider() -> TranslationProvider:
    from .headline_translation import DAILY_CHARACTER_LIMIT, MODEL, configured, translate_headline
    return TranslationProvider(name=MODEL, external=True, daily_limit=DAILY_CHARACTER_LIMIT,
                               translate=translate_headline, label="豆包标题翻译", configured=configured())


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


def split_utf8_chunks(text: str, max_bytes: int = MAX_TITLE_BYTES) -> list[str]:
    """Split prose without exceeding the smallest supported provider request."""

    normalized = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    chunks: list[str] = []
    remaining = normalized
    while remaining:
        candidate = utf8_prefix(remaining, max_bytes)
        if not candidate:
            break
        if len(candidate) < len(remaining):
            minimum_break = max(60, len(candidate) // 2)
            break_at = -1
            for marker in ("\n\n", ". ", "! ", "? ", "; ", ", ", " "):
                position = candidate.rfind(marker)
                if position >= minimum_break:
                    break_at = max(break_at, position + len(marker))
            if break_at > 0:
                candidate = candidate[:break_at]
        candidate = candidate.strip()
        if not candidate:
            break
        chunks.append(candidate)
        remaining = remaining[len(candidate):].lstrip()
    return chunks


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
        headers={"User-Agent": "Instant-AI/0.5 local-personal-client"},
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
        headers={"Content-Type": "application/json", "User-Agent": "Instant-AI/0.5"},
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
    provider = provider or headline_provider()
    used = _used_characters(provider.name, path)
    with connect(path) as connection:
        cached = int(connection.execute("SELECT COUNT(*) FROM item_translations").fetchone()[0])
    limit = provider.daily_limit
    return {
        "enabled": provider.configured,
        "configured": provider.configured,
        "provider": provider.name,
        "provider_label": provider.label or ("MyMemory 免费翻译" if provider.name == MYMEMORY_PROVIDER else "本机 LibreTranslate"),
        "external": provider.external,
        "cached_titles": cached,
        "used_characters_today": used,
        "daily_character_limit": limit,
        "remaining_characters_today": max(0, limit - used) if limit is not None else None,
        "official_public_limit": MYMEMORY_PUBLIC_LIMIT if provider.name == MYMEMORY_PROVIDER else None,
        "target_language": TARGET_LANGUAGE,
    }


def translate_text(
    text: str,
    *,
    path: Path | str | None = None,
    provider: TranslationProvider | None = None,
) -> dict[str, object]:
    """Translate ordered prose chunks and account for the shared daily quota."""

    selected_provider = provider or active_provider()
    chunks = split_utf8_chunks(text)
    translated_chunks: list[str] = []
    translated_source_characters = 0
    requested_characters = 0
    quota_exhausted = False
    errors: list[str] = []

    with TRANSLATION_LOCK:
        used = _used_characters(selected_provider.name, path)
        for chunk in chunks:
            cost = len(chunk)
            if selected_provider.daily_limit is not None and used + requested_characters + cost > selected_provider.daily_limit:
                quota_exhausted = True
                break
            requested_characters += cost
            try:
                translated = selected_provider.translate(chunk).strip()
                if translated:
                    translated_chunks.append(translated)
                    translated_source_characters += cost
            except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
                errors.append(f"{type(error).__name__}: {error}"[:300])
                break

        if requested_characters:
            now = utc_now()
            with transaction(path) as connection:
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

    return {
        "translated_text": "\n\n".join(translated_chunks),
        "provider": selected_provider.name,
        "translated_source_characters": translated_source_characters,
        "requested_characters": requested_characters,
        "source_chunk_count": len(chunks),
        "translated_chunk_count": len(translated_chunks),
        "partial": len(translated_chunks) < len(chunks),
        "quota_exhausted": quota_exhausted,
        "errors": errors,
        "status": translation_status(path, selected_provider),
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
    selected_provider = provider or headline_provider()
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
            titles = list(dict.fromkeys(str(row["title"]) for row in rows))
            cached_by_title: dict[str, tuple[str, str]] = {}
            if titles:
                placeholders = ",".join("?" for _ in titles)
                for cached in connection.execute(
                    f"SELECT original_title,translated_title,provider FROM item_translations "
                    f"WHERE target_language=? AND original_title IN ({placeholders}) ORDER BY updated_at DESC",
                    (TARGET_LANGUAGE, *titles),
                ):
                    cached_by_title.setdefault(str(cached["original_title"]),
                                               (str(cached["translated_title"]), str(cached["provider"])))

        translations: dict[str, str] = {}
        original_titles = {str(row["id"]): str(row["title"]) for row in rows}
        providers: dict[str, str] = {}
        candidates: list[tuple[int, str]] = []
        copies: dict[int, tuple[str, str, str]] = {}
        skipped = 0
        for row in rows:
            item_id = int(row["id"])
            if row["translated_title"]:
                translations[str(item_id)] = str(row["translated_title"])
                providers[str(item_id)] = str(row["provider"])
            elif needs_translation(str(row["title"])):
                title = str(row["title"])
                cached = cached_by_title.get(title)
                if cached:
                    translations[str(item_id)], providers[str(item_id)] = cached
                    copies[item_id] = (title, *cached)
                else:
                    candidates.append((item_id, title))
            else:
                skipped += 1

        used = _used_characters(selected_provider.name, path)
        reserved = 0
        chosen: list[tuple[int, str]] = []
        quota_exhausted = False
        picked_titles: set[str] = set()
        for item_id, title in candidates:
            if len(chosen) >= max_new:
                break
            if title in picked_titles:
                continue
            cost = len(title) if selected_provider.label == "豆包标题翻译" else len(utf8_prefix(title))
            if selected_provider.daily_limit is not None and used + reserved + cost > selected_provider.daily_limit:
                quota_exhausted = True
                continue
            chosen.append((item_id, title))
            picked_titles.add(title)
            reserved += cost

        results: dict[int, str] = {}
        errors: list[str] = []
        if chosen and selected_provider.configured:
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
                        errors.append(f"{item_id}: 翻译未确认完成，已保留原题。")
        elif chosen:
            errors.append("豆包标题翻译尚未配置方舟 API Key。")
            chosen = []

        requested_characters = sum(len(title) if selected_provider.label == "豆包标题翻译"
                                   else len(utf8_prefix(title)) for _, title in chosen)
        translated_by_title = {original_titles[str(item_id)]: value for item_id, value in results.items()}
        for item_id, title in candidates:
            if item_id not in results and title in translated_by_title:
                results[item_id] = translated_by_title[title]
        writes = dict(copies)
        writes.update({item_id: (original_titles[str(item_id)], value, selected_provider.name)
                       for item_id, value in results.items()})
        if writes or requested_characters:
            now = utc_now()
            with transaction(path) as connection:
                for item_id, (original, translated, provider_name) in writes.items():
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
                        (item_id, TARGET_LANGUAGE, original, translated, provider_name, now, now),
                    )
                    translations[str(item_id)] = translated
                    providers[str(item_id)] = provider_name
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
            "original_titles": original_titles,
            "providers": providers,
            "translated_count": len(results),
            "cached_count": len(translations) - len(results),
            "skipped_count": skipped,
            "pending_count": max(0, len(candidates) - len(chosen)),
            "quota_exhausted": quota_exhausted,
            "errors": errors,
            "status": status,
        }
