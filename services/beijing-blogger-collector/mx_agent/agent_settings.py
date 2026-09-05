from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .settings import CONFIG_DIR, ROOT_DIR, Settings


ENV_PATH = Path(
    os.getenv("BLOGGER_AGENT_ENV_FILE", str(ROOT_DIR / ".env"))
).expanduser().resolve()
CONFIG_PATH = CONFIG_DIR / "settings.json"
SECRET_FIELDS = {
    "openai_api_key": ("OPENAI_API_KEY", "openai_api_key"),
    "doubao_ark_api_key": ("DOUBAO_ARK_API_KEY", "doubao_ark_api_key"),
    "doubao_asr_api_key": ("DOUBAO_ASR_API_KEY", "doubao_asr_api_key"),
    "doubao_asr_app_id": ("DOUBAO_ASR_APP_ID", "doubao_asr_app_id"),
    "doubao_asr_access_key": ("DOUBAO_ASR_ACCESS_KEY", "doubao_asr_access_key"),
}
VALUE_FIELDS = {
    "doubao_asr_resource_id": ("DOUBAO_ASR_RESOURCE_ID", "doubao_asr_resource_id"),
    "doubao_text_model": ("DOUBAO_TEXT_MODEL", "doubao_text_model"),
    "doubao_vision_model": ("DOUBAO_VISION_MODEL", "doubao_vision_model"),
    "deep_model": ("MX_AGENT_DEEP_MODEL", "deep_model"),
    "fast_model": ("MX_AGENT_FAST_MODEL", "fast_model"),
}


def _clean(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _read_env(path: Path = ENV_PATH) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return lines, values


def _write_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    lines, values = _read_env(path)
    values.update(updates)
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={value}")
    temp = path.with_suffix(".env.tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temp.replace(path)


def public_agent_settings(settings: Settings) -> dict[str, Any]:
    return {
        "app_name": settings.app_name,
        "source_account_name": settings.source_account_name,
        "openai_configured": bool(settings.openai_api_key),
        "doubao_ark_configured": bool(settings.doubao_ark_api_key),
        "doubao_asr_configured": bool(
            settings.doubao_asr_api_key
            or (settings.doubao_asr_app_id and settings.doubao_asr_access_key)
        ),
        "doubao_text_model": settings.doubao_text_model or "",
        "doubao_vision_model": settings.doubao_vision_model or "",
        "doubao_asr_resource_id": settings.doubao_asr_resource_id or "",
        "deep_model": settings.deep_model,
        "fast_model": settings.fast_model,
        "creator_profile_url": settings.creator_profile_url,
        "creator_sync_mode": settings.creator_sync_mode,
        "creator_sync_enabled": settings.creator_sync_enabled,
        "creator_sync_interval_minutes": settings.creator_sync_interval_minutes,
        "creator_sync_history_limit": settings.creator_sync_history_limit,
        "creator_comments_enabled": settings.creator_comments_enabled,
        "creator_comment_limit": settings.creator_comment_limit,
        "creator_comment_refresh_minutes": settings.creator_comment_refresh_minutes,
        "creator_comment_tracking_hours": settings.creator_comment_tracking_hours,
        "api_keys_are_local": True,
        "restart_required": False,
    }


def update_agent_settings(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    app_name = _clean(payload.get("app_name"), 60) or settings.app_name
    source_name = _clean(payload.get("source_account_name"), 60) or settings.source_account_name
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["app_name"] = app_name
    config["source_account_name"] = source_name
    config_path_temp = CONFIG_PATH.with_suffix(".json.tmp")

    if "creator_profile_url" in payload:
        profile_url = _clean(payload.get("creator_profile_url"), 1000)
        if profile_url:
            match = re.search(
                r"https?://(?:[A-Za-z0-9-]+\.)?douyin\.com/[^\s，。！？；]*",
                profile_url,
                re.IGNORECASE,
            )
            if not match:
                raise ValueError("请填写抖音博主主页链接或完整分享文案。")
            profile_url = match.group(0).rstrip(",.;:!?)]}，。；：！？）")
        config["creator_profile_url"] = profile_url
        settings.creator_profile_url = profile_url

    if "creator_sync_mode" in payload:
        sync_mode = _clean(payload.get("creator_sync_mode"), 20).lower()
        if sync_mode not in {"count", "realtime"}:
            raise ValueError("抓取方式必须选择“按数量抓取”或“实时更新”。")
        config["creator_sync_mode"] = sync_mode
        config["creator_sync_enabled"] = sync_mode == "realtime"
        settings.creator_sync_mode = sync_mode
        settings.creator_sync_enabled = sync_mode == "realtime"
    elif "creator_sync_enabled" in payload:
        sync_mode = "realtime" if bool(payload.get("creator_sync_enabled")) else "count"
        config["creator_sync_mode"] = sync_mode
        config["creator_sync_enabled"] = sync_mode == "realtime"
        settings.creator_sync_mode = sync_mode
        settings.creator_sync_enabled = sync_mode == "realtime"

    bool_fields = ("creator_comments_enabled",)
    for field in bool_fields:
        if field in payload:
            value = bool(payload.get(field))
            config[field] = value
            setattr(settings, field, value)

    int_fields = {
        "creator_sync_interval_minutes": (3, 1440),
        "creator_sync_history_limit": (1, 1000),
        "creator_comment_limit": (20, 50000),
        "creator_comment_refresh_minutes": (30, 1440),
        "creator_comment_tracking_hours": (1, 720),
    }
    for field, (minimum, maximum) in int_fields.items():
        if field not in payload:
            continue
        try:
            value = int(payload.get(field))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 必须是整数。") from exc
        value = max(minimum, min(maximum, value))
        config[field] = value
        setattr(settings, field, value)

    if config.get("creator_sync_mode") == "realtime" and not str(
        config.get("creator_profile_url") or ""
    ).strip():
        raise ValueError("选择实时更新前，请先填写抖音博主主页链接。")

    config_path_temp.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config_path_temp.replace(CONFIG_PATH)
    settings.app_name = app_name
    settings.source_account_name = source_name

    env_updates: dict[str, str] = {}
    for field, (env_key, attribute) in SECRET_FIELDS.items():
        clear = bool(payload.get(f"clear_{field}"))
        supplied = _clean(payload.get(field), 1000)
        if clear or supplied:
            value = "" if clear else supplied
            env_updates[env_key] = value
            os.environ[env_key] = value
            setattr(settings, attribute, value or None)
    for field, (env_key, attribute) in VALUE_FIELDS.items():
        if field not in payload:
            continue
        value = _clean(payload.get(field), 240)
        env_updates[env_key] = value
        os.environ[env_key] = value
        setattr(settings, attribute, value or None)
    if env_updates:
        _write_env(env_updates)
    return public_agent_settings(settings)
