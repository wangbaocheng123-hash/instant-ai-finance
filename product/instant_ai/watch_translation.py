"""Foreground-only translation of existing watch titles; no event/report writes."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from .database import connect, transaction, utc_now
from .headline_translation import MAX_INPUT_BYTES
from .translation import (TRANSLATION_LOCK, TARGET_LANGUAGE, _usage_date, _used_characters,
                          headline_provider, needs_translation, translation_status)


def cached_titles(titles, *, path=None, now=None):
    """Read exact-title short-lived caches, including existing news translations."""
    at = now or utc_now()
    unique = list(dict.fromkeys(titles))
    found = {}
    with connect(path) as db:
        for start in range(0, len(unique), 100):
            chunk = unique[start:start+100]
            marks = ','.join('?' for _ in chunk)
            for row in db.execute(f'SELECT original_title,translated_title,provider FROM item_translations WHERE target_language=? AND original_title IN ({marks}) ORDER BY updated_at DESC', (TARGET_LANGUAGE, *chunk)):
                if re.search(r'[\u3400-\u9fff]', row[1]) and row[1].casefold() != row[0].casefold():
                    found.setdefault(row[0], (row[1], row[2]))
            for row in db.execute(f'SELECT original_title,translated_title,provider FROM watch_title_translations WHERE expires_at>? AND original_title IN ({marks})', (at, *chunk)):
                if re.search(r'[\u3400-\u9fff]', row[1]) and row[1].casefold() != row[0].casefold():
                    found.setdefault(row[0], (row[1], row[2]))
    return found


def annotate_watch_titles(payload, *, path=None):
    groups = [payload.get('events', []), payload.get('discovery', {}).get('events', []),
              payload.get('monetaryPolicy', {}).get('signals', [])]
    cache = cached_titles([row['title'] for group in groups for row in group], path=path)
    for group in groups:
        for row in group:
            if row['title'] in cache:
                row['translated_title'], row['translation_provider'] = cache[row['title']]
    return payload


def _title(kind, key, *, path=None, at):
    with connect(path) as db:
        if kind == 'event':
            row = db.execute('SELECT title FROM watch_events WHERE event_key=? AND is_active=1', (key,)).fetchone()
            return row[0] if row else None
        if kind == 'calendar':
            row = db.execute("SELECT payload_json FROM future_calendar_events WHERE event_key=? ORDER BY checked_at DESC LIMIT 1", (key,)).fetchone()
        elif kind == 'policy':
            row = db.execute('SELECT payload_json FROM monetary_policy_signals WHERE signal_key=? AND expires_at>?', (key, at)).fetchone()
        else:
            return None
    if row:
        try:
            return json.loads(row[0]).get('title')
        except (ValueError, AttributeError):
            pass
    return None


def translate_watch_title(kind, key, expected_title, *, path=None, provider=None):
    """Resolve a known source title. Arbitrary client-supplied text is never sent."""
    if not isinstance(kind, str) or kind not in {'event', 'calendar', 'policy'} or not isinstance(key, str) or not 1 <= len(key) <= 500:
        raise ValueError('重点事件标题标识无效')
    provider = provider or headline_provider()
    ref = f'watch:{kind}:{key}'
    with TRANSLATION_LOCK:
        at = utc_now()
        title = _title(kind, key, path=path, at=at)
        if not isinstance(title, str) or title != expected_title:
            raise ValueError('事件标题已更新或不再有效，请刷新后重试')
        if not title.strip() or len(title.encode('utf-8')) > MAX_INPUT_BYTES:
            raise ValueError('事件标题为空或过长，已保留原文')
        cache = cached_titles([title], path=path, now=at).get(title)
        errors = []
        exhausted = False
        translated = ''
        new = 0
        provider_name = provider.name
        if cache:
            translated, provider_name = cache
        elif needs_translation(title):
            if not provider.configured:
                errors.append('标题翻译尚未就绪，已保留原文。')
            elif provider.daily_limit is not None and _used_characters(provider.name, path) + len(title) > provider.daily_limit:
                exhausted = True
            else:
                try:
                    translated = provider.translate(title).strip()
                    if not translated or translated.casefold() == title.casefold():
                        raise ValueError('No confirmed translation')
                    new = 1
                except (OSError, ValueError, RuntimeError):
                    errors.append('事件标题翻译未确认完成，已保留原文；不会自动重试。')
                    translated = ''
                with transaction(path) as db:
                    db.execute('INSERT INTO translation_usage(usage_date,provider,character_count,updated_at) VALUES(?,?,?,?) ON CONFLICT(usage_date,provider) DO UPDATE SET character_count=translation_usage.character_count+excluded.character_count,updated_at=excluded.updated_at', (_usage_date(), provider.name, len(title), at))
                    if translated:
                        expiry = (datetime.fromisoformat(at) + timedelta(hours=72)).astimezone(UTC).isoformat()
                        db.execute('INSERT INTO watch_title_translations VALUES(?,?,?,?,?) ON CONFLICT(original_title) DO UPDATE SET translated_title=excluded.translated_title,provider=excluded.provider,created_at=excluded.created_at,expires_at=excluded.expires_at', (title, translated, provider.name, at, expiry))
        return {'ok': not errors, 'translations': {ref: translated} if translated else {},
                'original_titles': {ref: title}, 'providers': {ref: provider_name} if translated else {},
                'translated_count': new, 'cached_count': int(bool(cache)), 'skipped_count': int(not needs_translation(title)),
                'pending_count': 0, 'quota_exhausted': exhausted, 'errors': errors,
                'status': translation_status(path, provider)}
