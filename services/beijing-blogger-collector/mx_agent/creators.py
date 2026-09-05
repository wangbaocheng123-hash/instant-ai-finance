from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .creator_sync import CreatorSyncService
from .settings import CONFIG_DIR, Settings
from .storage import Storage


CREATORS_PATH = CONFIG_DIR / "creators.json"
DOUYIN_PROFILE_RE = re.compile(
    r"https?://(?:[A-Za-z0-9-]+\.)?douyin\.com/[^\s，。！？；]*",
    re.IGNORECASE,
)
DOUYIN_USER_PATH_RE = re.compile(r"/user/([^/?#]+)", re.IGNORECASE)
INT_FIELDS = {
    "creator_sync_interval_minutes": (3, 1440),
    "creator_sync_history_limit": (1, 1000),
    "creator_comment_limit": (20, 50000),
    "creator_comment_refresh_minutes": (30, 1440),
    "creator_comment_tracking_hours": (1, 720),
}


def _clean(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _clean_profile_url(value: Any) -> str:
    profile_url = _clean(value, 1000)
    if not profile_url:
        return ""
    match = DOUYIN_PROFILE_RE.search(profile_url)
    if not match:
        raise ValueError("请填写抖音博主主页链接或完整分享文案。")
    return match.group(0).rstrip(",.;:!?)]}，。；：！？）")


def _creator_uuid(value: Any = "") -> str:
    try:
        return str(uuid.UUID(_clean(value, 80)))
    except (ValueError, AttributeError):
        return str(uuid.uuid4())


def _platform_identity(profile_url: str) -> tuple[str, str]:
    """Derive stable Douyin account identifiers without opening the profile."""

    if not profile_url:
        return "", ""
    parsed = urlparse(profile_url)
    query = {
        str(key).casefold(): [str(item).strip() for item in values if str(item).strip()]
        for key, values in parse_qs(parsed.query, keep_blank_values=False).items()
    }

    def first(*keys: str) -> str:
        for key in keys:
            values = query.get(key.casefold()) or []
            if values:
                return _clean(unquote(values[0]), 240)
        return ""

    path_match = DOUYIN_USER_PATH_RE.search(parsed.path)
    path_identity = (
        _clean(unquote(path_match.group(1)), 240)
        if path_match
        else ""
    )
    sec_uid = first("sec_uid", "sec_user_id") or path_identity
    platform_user_id = first("user_id", "uid", "author_id") or path_identity or sec_uid
    return platform_user_id, sec_uid


class CreatorRegistry:
    """Persist the creators shown as tabs without moving existing video data."""

    def __init__(self, base_settings: Settings, path: Path = CREATORS_PATH) -> None:
        self.base_settings = base_settings
        self.path = path
        self._lock = threading.RLock()
        self._creators = self._load_or_migrate()

    def _legacy_creator(self) -> dict[str, Any]:
        settings = self.base_settings
        return {
            "id": "primary",
            "name": _clean(settings.source_account_name, 60) or "新博主",
            "profile_url": _clean(settings.creator_profile_url, 1000),
            "creator_sync_mode": settings.creator_sync_mode,
            "creator_sync_enabled": bool(settings.creator_sync_enabled),
            "creator_sync_interval_minutes": int(settings.creator_sync_interval_minutes),
            "creator_sync_history_limit": int(settings.creator_sync_history_limit),
            "creator_comments_enabled": bool(settings.creator_comments_enabled),
            "creator_comment_limit": int(settings.creator_comment_limit),
            "creator_comment_refresh_minutes": int(settings.creator_comment_refresh_minutes),
            "creator_comment_tracking_hours": int(settings.creator_comment_tracking_hours),
        }

    def _load_or_migrate(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("creators") if isinstance(payload, dict) else None
        except (OSError, ValueError, TypeError):
            rows = None
        if not isinstance(rows, list) or not rows:
            rows = [self._legacy_creator()]
            self._write(rows)
        creators: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        seen_uuids: set[str] = set()
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict):
                continue
            creator = self._normalize(raw, fallback_id="primary" if index == 0 else "")
            if creator["id"] in seen_ids or creator["name"].casefold() in seen_names:
                continue
            if creator["creator_uuid"] in seen_uuids:
                creator["creator_uuid"] = _creator_uuid()
            seen_ids.add(creator["id"])
            seen_names.add(creator["name"].casefold())
            seen_uuids.add(creator["creator_uuid"])
            creators.append(creator)
        if not creators:
            creators = [self._legacy_creator()]
        if creators[0]["id"] != "primary" and "primary" not in seen_ids:
            creators[0]["id"] = "primary"
        self._write(creators)
        return creators

    def _normalize(self, raw: dict[str, Any], fallback_id: str = "") -> dict[str, Any]:
        creator_id = _clean(raw.get("id"), 80) or fallback_id or f"creator-{uuid.uuid4().hex[:12]}"
        creator_id = re.sub(r"[^A-Za-z0-9_-]+", "-", creator_id).strip("-")
        name = _clean(raw.get("name") or raw.get("source_account_name"), 60) or "新博主"
        mode = _clean(raw.get("creator_sync_mode"), 20).lower()
        if mode not in {"count", "realtime"}:
            mode = "realtime" if bool(raw.get("creator_sync_enabled")) else "count"
        profile_url = _clean_profile_url(
            raw.get("profile_url") or raw.get("creator_profile_url")
        )
        derived_platform_user_id, derived_sec_uid = _platform_identity(profile_url)
        creator = {
            "id": creator_id,
            "creator_uuid": _creator_uuid(raw.get("creator_uuid")),
            "name": name,
            "platform": "douyin",
            "profile_url": profile_url,
            "platform_user_id": (
                derived_platform_user_id
                or _clean(raw.get("platform_user_id"), 240)
            ),
            "sec_uid": derived_sec_uid or _clean(raw.get("sec_uid"), 240),
            "creator_sync_mode": mode,
            "creator_sync_enabled": mode == "realtime",
            "creator_comments_enabled": bool(raw.get("creator_comments_enabled", True)),
        }
        defaults = self._legacy_creator()
        for field, (minimum, maximum) in INT_FIELDS.items():
            try:
                value = int(raw.get(field, defaults[field]))
            except (TypeError, ValueError):
                value = int(defaults[field])
            creator[field] = max(minimum, min(maximum, value))
        return creator

    def _write(self, creators: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"version": 2, "creators": creators}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._creators, ensure_ascii=False))

    def get(self, creator_id: str | None) -> dict[str, Any]:
        target = _clean(creator_id, 80) or "primary"
        with self._lock:
            for creator in self._creators:
                if creator["id"] == target:
                    return dict(creator)
        raise ValueError("博主不存在，请刷新页面后重试。")

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = _clean(payload.get("name") or payload.get("source_account_name"), 60)
        if not name:
            raise ValueError("请填写博主名称。")
        with self._lock:
            if any(item["name"].casefold() == name.casefold() for item in self._creators):
                raise ValueError("已经存在同名博主。")
            raw = {
                **self._legacy_creator(),
                "id": f"creator-{uuid.uuid4().hex[:12]}",
                "name": name,
                "profile_url": payload.get("profile_url") or payload.get("creator_profile_url") or "",
                "creator_sync_mode": payload.get("creator_sync_mode") or "count",
                "creator_sync_enabled": False,
            }
            creator = self._normalize(raw)
            if not creator["profile_url"]:
                raise ValueError("请填写抖音博主主页链接。保存后抓取时会自动使用。")
            self._creators.append(creator)
            self._write(self._creators)
            return dict(creator)

    def update(self, creator_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        with self._lock:
            index = next(
                (index for index, item in enumerate(self._creators) if item["id"] == creator_id),
                -1,
            )
            if index < 0:
                raise ValueError("博主不存在，请刷新页面后重试。")
            current = self._creators[index]
            raw = dict(current)
            if "source_account_name" in payload or "name" in payload:
                raw["name"] = _clean(payload.get("name") or payload.get("source_account_name"), 60)
                if not raw["name"]:
                    raise ValueError("请填写博主名称。")
                if any(
                    item["id"] != creator_id and item["name"].casefold() == raw["name"].casefold()
                    for item in self._creators
                ):
                    raise ValueError("已经存在同名博主。")
            if "creator_profile_url" in payload or "profile_url" in payload:
                raw["profile_url"] = payload.get("profile_url", payload.get("creator_profile_url"))
            for field in (
                "creator_sync_mode",
                "creator_sync_enabled",
                "creator_comments_enabled",
                *INT_FIELDS.keys(),
            ):
                if field in payload:
                    raw[field] = payload[field]
            updated = self._normalize(raw, fallback_id=creator_id)
            if updated["creator_sync_mode"] == "realtime" and not updated["profile_url"]:
                raise ValueError("选择实时更新前，请先填写抖音博主主页链接。")
            old_name = current["name"]
            self._creators[index] = updated
            self._write(self._creators)
            return dict(updated), old_name

    def settings_for(self, creator_id: str | None) -> Settings:
        creator = self.get(creator_id)
        return replace(
            self.base_settings,
            source_account_name=creator["name"],
            creator_profile_url=creator["profile_url"],
            creator_sync_mode=creator["creator_sync_mode"],
            creator_sync_enabled=creator["creator_sync_enabled"],
            creator_sync_interval_minutes=creator["creator_sync_interval_minutes"],
            creator_sync_history_limit=creator["creator_sync_history_limit"],
            creator_comments_enabled=creator["creator_comments_enabled"],
            creator_comment_limit=creator["creator_comment_limit"],
            creator_comment_refresh_minutes=creator["creator_comment_refresh_minutes"],
            creator_comment_tracking_hours=creator["creator_comment_tracking_hours"],
        )


class CreatorSyncManager:
    def __init__(
        self,
        registry: CreatorRegistry,
        storage: Storage,
        *,
        execution_lock: Any | None = None,
        on_content_ready: Callable[[int, str], Any] | None = None,
        automatic_rotation: bool = True,
    ) -> None:
        self.registry = registry
        self.storage = storage
        self._lock = threading.RLock()
        self._execution_lock = (
            execution_lock if execution_lock is not None else threading.Lock()
        )
        self._on_content_ready = on_content_ready
        self._automatic_rotation = bool(automatic_rotation)
        self._services: dict[str, CreatorSyncService] = {}
        self._rotation_stop = threading.Event()
        self._rotation_wake = threading.Event()
        self._rotation_thread: threading.Thread | None = None
        self._rotation_index = 0
        self._rotation_slot_seconds = max(
            60,
            int(os.getenv("BLOGGER_AGENT_ROTATION_SLOT_SECONDS", "600")),
        )
        self._rotation_runtime: dict[str, Any] = {
            "running": False,
            "current_creator_id": "",
            "last_dispatched_at": "",
            "message": "等待启用轮替更新的博主。",
        }

    def start(self) -> None:
        for creator in self.registry.list():
            self.ensure(creator["id"])
        if not self._automatic_rotation:
            with self._lock:
                self._rotation_runtime.update(
                    running=False,
                    current_creator_id="",
                    message="仅手动采集；自动轮替已关闭。",
                )
            return
        with self._lock:
            if self._rotation_thread and self._rotation_thread.is_alive():
                return
            self._rotation_stop.clear()
            self._rotation_thread = threading.Thread(
                target=self._rotation_loop,
                name="creator-round-robin",
                daemon=True,
            )
            self._rotation_thread.start()

    def ensure(self, creator_id: str) -> CreatorSyncService:
        with self._lock:
            service = self._services.get(creator_id)
            settings = self.registry.settings_for(creator_id)
            if service is None:
                service = CreatorSyncService(
                    settings,
                    self.storage,
                    runtime_key=creator_id,
                    execution_lock=self._execution_lock,
                    managed_schedule=True,
                    on_content_ready=self._on_content_ready,
                )
                self._services[creator_id] = service
                service.start()
            else:
                service.settings = settings
                service.wake()
            return service

    def status(self, creator_id: str | None) -> dict[str, Any]:
        creator = self.registry.get(creator_id)
        result = self.ensure(creator["id"]).status()
        result["creator_identity"] = {
            "creator_uuid": creator["creator_uuid"],
            "runtime_key": creator["id"],
            "platform": creator["platform"],
            "platform_user_id": creator["platform_user_id"],
            "sec_uid": creator["sec_uid"],
        }
        result["rotation"] = self.rotation_status(creator["id"])
        return result

    def run_now(self, creator_id: str | None, **kwargs: Any) -> dict[str, Any]:
        creator = self.registry.get(creator_id)
        return self.ensure(creator["id"]).run_now(**kwargs)

    def open_login_browser(self, creator_id: str | None) -> dict[str, Any]:
        creator = self.registry.get(creator_id)
        return self.ensure(creator["id"]).open_login_browser()

    def rotation_status(self, creator_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            runtime = dict(self._rotation_runtime)
        eligible = self._eligible_creators()
        current_index = next(
            (index for index, item in enumerate(eligible) if item["id"] == creator_id),
            -1,
        )
        runtime.update(
            {
                "automatic": self._automatic_rotation,
                "slot_seconds": self._rotation_slot_seconds,
                "eligible_count": len(eligible),
                "creator_position": current_index + 1 if current_index >= 0 else 0,
                "single_browser": True,
            }
        )
        return runtime

    def _eligible_creators(self) -> list[dict[str, Any]]:
        if not self._automatic_rotation:
            return []
        return [
            creator
            for creator in self.registry.list()
            if creator.get("profile_url")
            and creator.get("creator_sync_mode") == "realtime"
        ]

    def _any_browser_job_pending(self) -> bool:
        if self._execution_lock.locked():
            return True
        with self._lock:
            services = list(self._services.values())
        return any(
            status.get("busy") or status.get("queued")
            for status in (service.status() for service in services)
        )

    def _rotation_loop(self) -> None:
        with self._lock:
            self._rotation_runtime.update(
                running=True,
                message="轮替调度器已启动。",
            )
        while not self._rotation_stop.is_set():
            eligible = self._eligible_creators()
            if not eligible:
                with self._lock:
                    self._rotation_runtime.update(
                        current_creator_id="",
                        message="等待启用轮替更新的博主。",
                    )
                self._rotation_wake.wait(30)
                self._rotation_wake.clear()
                continue
            if self._any_browser_job_pending():
                self._rotation_wake.wait(5)
                self._rotation_wake.clear()
                continue
            creator = eligible[self._rotation_index % len(eligible)]
            self._rotation_index = (self._rotation_index + 1) % len(eligible)
            service = self.ensure(creator["id"])
            service.run_now()
            with self._lock:
                self._rotation_runtime.update(
                    current_creator_id=creator["id"],
                    last_dispatched_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    message=f"已轮到 {creator['name']}，下一位将在当前任务完成后按时隙启动。",
                )
            self._rotation_wake.wait(self._rotation_slot_seconds)
            self._rotation_wake.clear()
        with self._lock:
            self._rotation_runtime["running"] = False
