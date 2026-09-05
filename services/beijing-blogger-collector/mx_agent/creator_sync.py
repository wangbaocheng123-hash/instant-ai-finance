from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .comment_sync import classify_comment_sections
from .creator_paths import CREATOR_DATA_ROOT, ensure_creator_directories
from .douyin import comment_signal
from .downloader_engine.comment_collector import (
    CommentCollectError,
    CommentCollector,
)
from .downloader_engine.douyin_core import (
    DouyinResolver,
    ParseError,
    download_images,
    download_video,
    inspect_mp4,
)
from .downloader_engine.profile_monitor import (
    ProfileScanner,
    ProfileVideo,
    launch_dedicated_login_browser,
    refine_created_at_from_title,
    video_created_at,
)
from .settings import DATA_DIR, ROOT_DIR, Settings
from .single_video import VideoLinkError, normalize_video_link, resolve_video_link
from .storage import Storage, from_json, now_iso


CHINA_TZ = ZoneInfo("Asia/Shanghai")
RUNTIME_DIR = DATA_DIR / "creator_sync"
PROFILE_DIR = RUNTIME_DIR / "chrome-profile"
STATE_PATH = RUNTIME_DIR / "state.json"
FFMPEG_DIR = ROOT_DIR / "tools"
REALTIME_SCAN_LIMIT = 50
COUNT_SCAN_MIN_LIMIT = 50
COUNT_SCAN_MULTIPLIER = 5


def _china_now() -> datetime:
    return datetime.now(CHINA_TZ)


def _profile_uid(url: str) -> str:
    match = re.search(r"/user/([^/?#]+)", str(url or ""))
    return match.group(1) if match else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CreatorSyncService:
    """Download one creator's works and merge comments into the agent DB."""

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        *,
        runtime_key: str = "primary",
        content_root: Path | None = None,
        execution_lock: Any | None = None,
        managed_schedule: bool = False,
        on_content_ready: Callable[[int, str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.content_root = (content_root or CREATOR_DATA_ROOT).resolve()
        self._execution_lock = execution_lock
        self._managed_schedule = bool(managed_schedule)
        self._on_content_ready = on_content_ready
        safe_runtime_key = re.sub(r"[^A-Za-z0-9_-]+", "-", runtime_key).strip("-") or "creator"
        self.runtime_key = safe_runtime_key
        # One owner logs in to Douyin once for the whole Beijing collector.
        # Creator tasks are globally serialized, so every creator can safely
        # reuse the same persistent browser session without copying or reading
        # cookies.  Works, settings and cursors remain isolated below.
        self.profile_dir = PROFILE_DIR
        if safe_runtime_key == "primary":
            # Keep the original BK sync cursor in place for backward compatibility.
            self.runtime_dir = RUNTIME_DIR
            self.state_path = STATE_PATH
        else:
            self.runtime_dir = RUNTIME_DIR / "creators" / safe_runtime_key
            self.state_path = self.runtime_dir / "state.json"
        self._content_directories()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        local_ffmpeg = FFMPEG_DIR / "ffmpeg.exe"
        if local_ffmpeg.is_file():
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            if str(FFMPEG_DIR) not in path_parts:
                os.environ["PATH"] = str(FFMPEG_DIR) + os.pathsep + os.environ.get("PATH", "")

        self._lock = threading.RLock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual_requested = False
        self._manual_force_comments = False
        self._manual_start_date: date | None = None
        self._manual_videos_only = False
        self._manual_video_url = ""
        self._next_run_epoch = 0.0
        self._runtime: dict[str, Any] = {
            "running": False,
            "busy": False,
            "phase": "waiting",
            "message": "请先在设置中填写抖音博主主页链接。",
            "current_aweme_id": "",
            "last_started_at": "",
            "last_finished_at": "",
            "last_error": "",
            "works_seen": 0,
            "works_selected": 0,
            "new_downloads": 0,
            "new_image_posts": 0,
            "image_files_downloaded": 0,
            "comments_seen": 0,
            "comments_created": 0,
            "comments_updated": 0,
            "content_ready_callbacks": 0,
            "content_ready_callback_failures": 0,
            "last_content_ready_video_id": 0,
            "last_content_ready_at": "",
            "last_content_ready_error": "",
            "events": [],
        }
        self._state = self._load_state()

    def _content_directories(self) -> dict[str, Path]:
        return ensure_creator_directories(
            str(self.settings.source_account_name or "新博主"),
            data_root=self.content_root,
        )

    @property
    def video_dir(self) -> Path:
        return self._content_directories()["videos"]

    @property
    def image_dir(self) -> Path:
        return self._content_directories()["images"]

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        value.setdefault("videos", {})
        return value

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def _notify_content_ready(
        self,
        local_video_id: int,
        aweme_id: str,
    ) -> bool | None:
        """Notify a downstream boundary after local media/comments are durable.

        The callback is deliberately non-transactional: collection data has
        already been committed before it runs. A callback exception is saved
        as a retryable diagnostic and never escapes into the collection loop.
        """

        if self._on_content_ready is None:
            return None
        attempted_at = now_iso()
        error = ""
        try:
            self._on_content_ready(int(local_video_id), self.runtime_key)
        except Exception as exc:
            error = str(exc).strip()[:500] or type(exc).__name__

        entry = self._state.setdefault("videos", {}).setdefault(str(aweme_id), {})
        entry.update(
            {
                "local_video_id": int(local_video_id),
                "content_ready_pending": bool(error),
                "last_content_ready_attempt_at": attempted_at,
                "last_content_ready_error": error,
            }
        )
        if not error:
            entry["content_ready_notified_at"] = attempted_at
        with self._lock:
            self._runtime["content_ready_callbacks"] = int(
                self._runtime.get("content_ready_callbacks") or 0
            ) + 1
            self._runtime["last_content_ready_video_id"] = int(local_video_id)
            self._runtime["last_content_ready_at"] = attempted_at
            self._runtime["last_content_ready_error"] = error
            if error:
                self._runtime["content_ready_callback_failures"] = int(
                    self._runtime.get("content_ready_callback_failures") or 0
                ) + 1
        try:
            self._save_state()
        except Exception as exc:
            state_error = str(exc).strip()[:500] or type(exc).__name__
            with self._lock:
                self._runtime["last_content_ready_error"] = (
                    f"{error}; 状态保存失败：{state_error}"
                    if error
                    else f"状态保存失败：{state_error}"
                )
            self._event(
                f"作品 {aweme_id} 后续处理状态保存失败：{state_error}",
                level="error",
            )
            return False
        if error:
            self._event(
                f"作品 {aweme_id} 已完成本地采集，但后续处理回调失败，"
                f"采集数据已保留并标记待重试：{error}",
                level="warning",
            )
            return False
        return True

    def _event(
        self,
        message: str,
        *args: object,
        level: str = "info",
    ) -> None:
        if args:
            try:
                message = message % args
            except (TypeError, ValueError):
                message = " ".join([str(message), *(str(item) for item in args)])
        entry = {
            "at": now_iso(),
            "level": level,
            "message": str(message),
        }
        with self._lock:
            events = list(self._runtime.get("events") or [])
            events.append(entry)
            self._runtime["events"] = events[-40:]
            self._runtime["message"] = str(message)
        print(f"[creator-sync] {message}")

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="creator-sync",
                daemon=True,
            )
            self._runtime["running"] = True
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def wake(self) -> None:
        with self._lock:
            self._next_run_epoch = 0.0
        self._wake_event.set()

    def run_now(
        self,
        *,
        force_comments: bool = False,
        start_date: str | None = None,
        videos_only: bool = False,
        video_url: str = "",
    ) -> dict[str, Any]:
        if not self.settings.creator_profile_url:
            raise ValueError("请先在设置中填写抖音博主主页链接。")
        parsed_start_date: date | None = None
        target_url = normalize_video_link(video_url) if video_url else ""
        if target_url and start_date:
            raise ValueError("指定视频不能同时设置日期范围。")
        if str(start_date or "").strip():
            try:
                parsed_start_date = date.fromisoformat(str(start_date).strip())
            except ValueError as exc:
                raise ValueError("开始日期必须是 YYYY-MM-DD 格式。") from exc
            if parsed_start_date > _china_now().date():
                raise ValueError("开始日期不能晚于今天。")
        with self._lock:
            if self._runtime.get("busy") or self._manual_requested:
                if target_url:
                    raise VideoLinkError("当前博主已有采集任务，请完成后再抓取指定视频。")
                return self.status()
            self._manual_requested = True
            self._manual_force_comments = bool(force_comments)
            self._manual_start_date = parsed_start_date
            self._manual_videos_only = bool(videos_only)
            self._manual_video_url = target_url
            self._runtime["message"] = (
                "已加入指定视频任务，只处理这一条视频。"
                if target_url else
                f"已经加入 {parsed_start_date.isoformat()} 至今的全部作品抓取任务。"
                if parsed_start_date
                else "已经加入立即同步任务。"
            )
        self._wake_event.set()
        return self.status()

    def open_login_browser(self) -> dict[str, Any]:
        url = str(self.settings.creator_profile_url or "").strip()
        if not url:
            raise ValueError("请先填写并保存抖音博主主页链接。")
        process = launch_dedicated_login_browser(url, self.profile_dir)
        self._event("已打开智能体专用抖音登录浏览器；登录完成后请将该窗口完全关闭。")
        return {
            "opened": True,
            "process_id": int(process.pid),
            "profile_dir": str(self.profile_dir),
            "message": "登录完成后请完全关闭专用浏览器，再点击立即同步。",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            runtime = json.loads(json.dumps(self._runtime, ensure_ascii=False))
            next_run = self._next_run_epoch
        runtime.update(
            {
                "sync_mode": self._sync_mode(),
                "enabled": self._sync_mode() == "realtime",
                "profile_configured": bool(self.settings.creator_profile_url),
                "profile_url": self.settings.creator_profile_url,
                "comments_enabled": bool(self.settings.creator_comments_enabled),
                "interval_minutes": int(self.settings.creator_sync_interval_minutes),
                "history_limit": int(self.settings.creator_sync_history_limit),
                "comment_limit": int(self.settings.creator_comment_limit),
                "comment_refresh_minutes": int(
                    self.settings.creator_comment_refresh_minutes
                ),
                "comment_tracking_hours": int(
                    self.settings.creator_comment_tracking_hours
                ),
                "video_dir": str(self.video_dir),
                "image_dir": str(self.image_dir),
                "profile_dir": str(self.profile_dir),
                "next_run_at": (
                    datetime.fromtimestamp(next_run, CHINA_TZ).isoformat()
                    if next_run > time.time()
                    else ""
                ),
                "tracked_videos": len(self._state.get("videos") or {}),
                "realtime_baseline_ready": self._realtime_baseline_ready(),
                "queued": self._manual_requested,
                "managed_schedule": self._managed_schedule,
            }
        )
        return runtime

    def _sync_mode(self) -> str:
        mode = str(getattr(self.settings, "creator_sync_mode", "") or "").lower()
        if mode in {"count", "realtime"}:
            return mode
        return "realtime" if bool(self.settings.creator_sync_enabled) else "count"

    def _realtime_profile_key(self) -> str:
        profile_url = str(self.settings.creator_profile_url or "").strip()
        return _profile_uid(profile_url) or hashlib.sha256(
            profile_url.encode("utf-8")
        ).hexdigest()[:20]

    def _realtime_baseline_ready(self) -> bool:
        if not self.settings.creator_profile_url:
            return False
        profiles = self._state.get("realtime_profiles") or {}
        profile = profiles.get(self._realtime_profile_key()) or {}
        return bool(profile.get("latest_video_id"))

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            manual = False
            force_comments = False
            start_date: date | None = None
            videos_only = False
            video_url = ""
            should_run = False
            with self._lock:
                if self._manual_requested:
                    manual = True
                    force_comments = self._manual_force_comments
                    start_date = self._manual_start_date
                    videos_only = self._manual_videos_only
                    video_url = self._manual_video_url
                    self._manual_requested = False
                    self._manual_force_comments = False
                    self._manual_start_date = None
                    self._manual_videos_only = False
                    self._manual_video_url = ""
                    should_run = True
                elif (
                    not self._managed_schedule
                    and
                    self._sync_mode() == "realtime"
                    and self.settings.creator_profile_url
                    and time.time() >= self._next_run_epoch
                ):
                    should_run = True
            if should_run:
                self._perform_cycle(
                    manual=manual,
                    force_comments=force_comments,
                    start_date=start_date,
                    videos_only=videos_only,
                    video_url=video_url,
                )
                with self._lock:
                    self._next_run_epoch = time.time() + (
                        max(3, int(self.settings.creator_sync_interval_minutes)) * 60
                    )
            self._wake_event.wait(5)
            self._wake_event.clear()
        with self._lock:
            self._runtime["running"] = False

    def _perform_cycle(
        self,
        *,
        manual: bool,
        force_comments: bool,
        start_date: date | None = None,
        videos_only: bool = False,
        video_url: str = "",
    ) -> None:
        if self._execution_lock is None:
            self._perform_cycle_unlocked(
                manual=manual,
                force_comments=force_comments,
                start_date=start_date,
                videos_only=videos_only,
                video_url=video_url,
            )
            return
        with self._execution_lock:
            self._perform_cycle_unlocked(
                manual=manual,
                force_comments=force_comments,
                start_date=start_date,
                videos_only=videos_only,
                video_url=video_url,
            )

    def _perform_cycle_unlocked(
        self,
        *,
        manual: bool,
        force_comments: bool,
        start_date: date | None = None,
        videos_only: bool = False,
        video_url: str = "",
    ) -> None:
        with self._lock:
            self._runtime.update(
                {
                    "busy": True,
                    "phase": "profile",
                    "last_started_at": now_iso(),
                    "last_error": "",
                    "works_seen": 0,
                    "works_selected": 0,
                    "new_downloads": 0,
                    "new_image_posts": 0,
                    "image_files_downloaded": 0,
                    "comments_seen": 0,
                    "comments_created": 0,
                    "comments_updated": 0,
                    "content_ready_callbacks": 0,
                    "content_ready_callback_failures": 0,
                    "requested_start_date": (
                        start_date.isoformat() if start_date else ""
                    ),
                    "videos_only": bool(videos_only),
                    "single_video": bool(video_url),
                }
            )
        profile_url = str(self.settings.creator_profile_url or "").strip()
        sync_mode = self._sync_mode()
        try:
            if video_url:
                self._event("正在识别指定视频，只抓取这一条，不扫描博主主页。")
            elif start_date:
                self._event(
                    f"正在扫描博主完整主页，只抓取 {start_date.isoformat()} 至今的视频和图文。"
                )
            elif sync_mode == "realtime":
                self._event("正在检查博主是否发布了新作品。")
            else:
                self._event(
                    f"正在扫描博主主页，准备抓取最近 {int(self.settings.creator_sync_history_limit)} 条视频。"
                )
            count_target = int(self.settings.creator_sync_history_limit)
            tracked_count = len(self._state.get("videos") or {})
            count_scan_limit = min(
                1000,
                max(
                    COUNT_SCAN_MIN_LIMIT,
                    count_target * COUNT_SCAN_MULTIPLIER,
                    (count_target + tracked_count) * 3,
                ),
            )
            if video_url:
                target = resolve_video_link(video_url)
                creator = str(self.settings.source_account_name or "新博主")
                videos = [ProfileVideo(
                    video_id=target["video_id"], url=target["video_url"],
                    title=f"抖音作品_{target['video_id']}",
                    created_at=video_created_at(target["video_id"]),
                )]
            else:
                scanner = ProfileScanner(self.profile_dir, log=self._event)
                try:
                    creator, videos = scanner.scan(
                        profile_url,
                        timeout=240 if start_date else 120,
                        limit=(1000 if start_date else (
                            REALTIME_SCAN_LIMIT if sync_mode == "realtime" else count_scan_limit
                        )),
                    )
                finally:
                    scanner.close()
            scanned_count = len(videos)
            with self._lock:
                self._runtime["works_seen"] = scanned_count

            if sync_mode == "count" and not start_date and not video_url:
                scanned_images = sum(1 for video in videos if video.work_type == "image")
                selected_videos: list[ProfileVideo] = []
                skipped_existing = 0
                for video in videos:
                    if video.work_type == "image":
                        continue
                    if self._tracked_local_video_id(video):
                        skipped_existing += 1
                        continue
                    selected_videos.append(video)
                    if len(selected_videos) >= count_target:
                        break
                videos = selected_videos
                self._event(
                    f"按数量抓取已选中 {len(videos)} 条尚未下载的视频；"
                    f"已按作品 ID 跳过 {skipped_existing} 条本地已有视频，"
                    f"扫描到的 {scanned_images} 个图文不计入数量，也不会下载。"
                )
                if len(videos) < count_target:
                    self._event(
                        f"本轮已扫描 {scanned_count} 个主页作品，只找到 "
                        f"{len(videos)} 条未下载视频；不会用重复视频凑满 {count_target} 条。",
                        level="warning",
                    )

            if start_date:
                videos = [
                    video
                    for video in videos
                    if video.created_at.date() >= start_date
                ]

            realtime_profile: dict[str, Any] | None = None
            realtime_watermark = 0
            latest_scanned_id = max(
                (int(video.video_id) for video in videos),
                default=0,
            )
            if sync_mode == "realtime" and not start_date and not video_url:
                profiles = self._state.setdefault("realtime_profiles", {})
                profile_key = self._realtime_profile_key()
                existing_profile = profiles.get(profile_key)
                if not isinstance(existing_profile, dict) or not existing_profile.get(
                    "latest_video_id"
                ):
                    profiles[profile_key] = {
                        "profile_url": profile_url,
                        "latest_video_id": str(latest_scanned_id),
                        "initialized_at": now_iso(),
                        "last_checked_at": now_iso(),
                    }
                    self._state["last_cycle"] = {
                        "at": now_iso(),
                        "manual": bool(manual),
                        "sync_mode": sync_mode,
                        "baseline_initialized": True,
                        "works_seen": len(videos),
                        "new_downloads": 0,
                        "comments_created": 0,
                        "comments_updated": 0,
                        "failures": 0,
                    }
                    self._save_state()
                    self._event(
                        f"实时更新已开始：已记录当前最新位置（扫描到 {len(videos)} 条），"
                        "没有下载历史作品；以后只抓新发布的作品。"
                    )
                    return
                realtime_profile = existing_profile
                realtime_watermark = int(existing_profile.get("latest_video_id") or 0)

            work_items: list[tuple[ProfileVideo, bool]] = []
            if sync_mode == "count" or start_date or video_url:
                work_items = [
                    (video, not bool(self._tracked_local_video_id(video)))
                    for video in videos
                ]
            else:
                tracked = self._state.get("videos") or {}
                for video in videos:
                    is_new = int(video.video_id) > realtime_watermark
                    has_local_copy = bool(
                        (tracked.get(video.video_id) or {}).get("local_video_id")
                    )
                    if is_new or (self.settings.creator_comments_enabled and has_local_copy):
                        work_items.append((video, is_new))
            with self._lock:
                self._runtime["works_selected"] = sum(
                    1 for _video, should_download in work_items if should_download
                )
            already_downloaded = len(work_items) - int(self._runtime["works_selected"])
            if already_downloaded:
                self._event(
                    f"已按作品 ID 跳过 {already_downloaded} 条本地已有内容；"
                    f"本轮只需下载 {int(self._runtime['works_selected'])} 条缺失视频。"
                )

            downloaded = 0
            image_posts_downloaded = 0
            image_files_downloaded = 0
            comment_seen = 0
            comment_created = 0
            comment_updated = 0
            content_ready_callbacks = 0
            content_ready_callback_failures = 0
            failures = 0
            realtime_download_failed = False
            for index, (video, should_download) in enumerate(work_items, start=1):
                if self._stop_event.is_set():
                    break
                with self._lock:
                    self._runtime["current_aweme_id"] = video.video_id
                    self._runtime["phase"] = "download"
                if should_download or video_url:
                    try:
                        local_video_id, was_downloaded = self._ensure_video(
                            video,
                            creator=creator,
                        )
                        if was_downloaded:
                            downloaded += 1
                            if video.work_type == "image":
                                image_posts_downloaded += 1
                                image_files_downloaded += int(
                                    (
                                        (self._state.get("videos") or {}).get(
                                            video.video_id
                                        )
                                        or {}
                                    ).get("image_file_count")
                                    or 0
                                )
                    except Exception as exc:
                        failures += 1
                        if video_url:
                            with self._lock:
                                self._runtime["last_error"] = str(exc)
                        if sync_mode == "realtime":
                            realtime_download_failed = True
                        self._remember_video_error(video.video_id, str(exc))
                        self._event(
                            f"作品 {video.video_id} 下载或入库失败：{exc}",
                            level="error",
                        )
                        continue
                else:
                    tracked_entry = (self._state.get("videos") or {}).get(
                        video.video_id
                    ) or {}
                    local_video_id = int(tracked_entry.get("local_video_id") or 0)
                    was_downloaded = False
                    if not local_video_id:
                        continue

                if (
                    self.settings.creator_comments_enabled
                    and not videos_only
                    and self._comments_due(
                        video,
                        newly_downloaded=was_downloaded,
                        force=force_comments or bool(video_url),
                    )
                ):
                    with self._lock:
                        self._runtime["phase"] = "comments"
                    try:
                        result = self._collect_and_merge_comments(
                            local_video_id,
                            video,
                            creator=creator,
                            profile_url=profile_url,
                        )
                        comment_seen += int(result["seen"])
                        comment_created += int(result["created"])
                        comment_updated += int(result["updated"])
                    except Exception as exc:
                        failures += 1
                        if video_url:
                            with self._lock:
                                self._runtime["last_error"] = str(exc)
                        self._remember_video_error(video.video_id, str(exc))
                        self._event(
                            f"作品 {video.video_id} 评论同步失败：{exc}",
                            level="error",
                        )
                with self._lock:
                    self._runtime["phase"] = "content_ready"
                callback_result = self._notify_content_ready(
                    local_video_id,
                    video.video_id,
                )
                if callback_result is not None:
                    content_ready_callbacks += 1
                    if not callback_result:
                        content_ready_callback_failures += 1
                with self._lock:
                    self._runtime.update(
                        {
                            "new_downloads": downloaded,
                            "new_image_posts": image_posts_downloaded,
                            "image_files_downloaded": image_files_downloaded,
                            "comments_seen": comment_seen,
                            "comments_created": comment_created,
                            "comments_updated": comment_updated,
                            "content_ready_callbacks": content_ready_callbacks,
                            "content_ready_callback_failures": (
                                content_ready_callback_failures
                            ),
                            "message": f"正在处理第 {index}/{len(work_items)} 个作品。",
                        }
                    )

            if video_url:
                summary = (
                    f"指定视频 {videos[0].video_id} 处理结束：新下载 {downloaded} 条，"
                    f"评论新增 {comment_created} 条、更新 {comment_updated} 条。"
                )
            elif start_date:
                summary = (
                    f"日期范围抓取完成：{start_date.isoformat()} 至今共匹配 "
                    f"{len(videos)} 个作品，新下载 {downloaded} 个"
                    f"（其中图文 {image_posts_downloaded} 个、原图 {image_files_downloaded} 张）。"
                )
            elif sync_mode == "realtime":
                new_count = sum(
                    1 for _video, should_download in work_items if should_download
                )
                summary = (
                    f"实时检查完成：发现 {new_count} 个新作品，新下载 {downloaded} 个；"
                    f"评论新增 {comment_created} 条、更新 {comment_updated} 条。"
                )
                if realtime_profile is not None:
                    realtime_profile["last_checked_at"] = now_iso()
                    if not realtime_download_failed and latest_scanned_id:
                        realtime_profile["latest_video_id"] = str(latest_scanned_id)
            else:
                summary = (
                    f"按数量抓取完成：扫描主页 {scanned_count} 个作品，"
                    f"选中 {len(videos)} 条未下载视频，实际新下载 {downloaded} 条；"
                    f"评论新增 {comment_created} 条、更新 {comment_updated} 条。"
                )
            if failures:
                summary += f" 有 {failures} 个步骤等待下次重试。"
            if content_ready_callback_failures:
                summary += (
                    f" 有 {content_ready_callback_failures} 个后续处理通知失败，"
                    "本地采集数据已保留并标记待重试。"
                )
            self._state["last_cycle"] = {
                "at": now_iso(),
                "manual": bool(manual),
                "sync_mode": sync_mode,
                "start_date": start_date.isoformat() if start_date else "",
                "videos_only": bool(videos_only),
                "single_video": bool(video_url),
                "works_seen": scanned_count,
                "works_selected": sum(
                    1 for _video, should_download in work_items if should_download
                ),
                "new_downloads": downloaded,
                "new_image_posts": image_posts_downloaded,
                "image_files_downloaded": image_files_downloaded,
                "comments_created": comment_created,
                "comments_updated": comment_updated,
                "content_ready_callbacks": content_ready_callbacks,
                "content_ready_callback_failures": content_ready_callback_failures,
                "failures": failures,
            }
            self._save_state()
            self._event(summary, level="warning" if failures else "info")
        except Exception as exc:
            with self._lock:
                self._runtime["last_error"] = str(exc)
            self._event(f"同步失败：{exc}", level="error")
        finally:
            with self._lock:
                self._runtime.update(
                    {
                        "busy": False,
                        "phase": "waiting",
                        "current_aweme_id": "",
                        "last_finished_at": now_iso(),
                    }
                )

    def _existing_video_file(self, aweme_id: str) -> Path | None:
        matches = sorted(self.video_dir.glob(f"*_{aweme_id}.mp4"))
        for path in matches:
            if path.is_file() and path.stat().st_size > 1024:
                return path
        return None

    def _tracked_local_video_id(self, video: ProfileVideo) -> int:
        entry = (self._state.get("videos") or {}).get(video.video_id) or {}
        local_video_id = int(entry.get("local_video_id") or 0)
        if video.work_type == "image":
            outputs = self._existing_image_files(video.video_id)
            expected_count = int(entry.get("image_file_count") or 0)
            if outputs and (not expected_count or len(outputs) >= expected_count):
                return local_video_id or -1
            return 0
        return (local_video_id or -1) if self._existing_video_file(video.video_id) else 0

    def _ensure_video(
        self,
        video: ProfileVideo,
        *,
        creator: str,
    ) -> tuple[int, bool]:
        if video.work_type == "image":
            return self._ensure_image_work(video, creator=creator)
        output = self._existing_video_file(video.video_id)
        title = video.title
        downloaded = False
        if output is None:
            self._event(f"发现作品 {video.video_id}，正在下载到当前博主的独立视频文件夹。")
            resolver = DouyinResolver(
                log=self._event,
                profile_dir=self.profile_dir,
            )
            try:
                result = resolver.resolve(video.url, timeout=55)
            finally:
                resolver.close()
            if result.video_id != video.video_id:
                raise ParseError("解析到的视频与指定作品 ID 不一致，已停止下载，请重新复制该视频链接。")
            if video.title and not video.title.startswith("抖音作品_"):
                result.title = video.title
            title = result.title or video.title
            refined_created_at = refine_created_at_from_title(
                video.created_at,
                title,
            )
            if refined_created_at.date() != video.created_at.date():
                self._event(
                    f"作品 {video.video_id} 的公开标题显示发布日期为 "
                    f"{refined_created_at:%Y-%m-%d}，已替代作品 ID 生成日期。"
                )
                video = replace(video, created_at=refined_created_at)
            filename = f"{video.created_at:%Y%m%d_%H%M}_{video.video_id}"
            output = download_video(
                result,
                self.video_dir,
                custom_filename=filename,
            )
            inspection = inspect_mp4(output)
            if not inspection.get("has_mdat") or not inspection.get("has_video"):
                output.unlink(missing_ok=True)
                raise ParseError("下载文件缺少有效视频轨道，已删除并等待重试。")
            downloaded = True
            self._event(f"作品 {video.video_id} 已保存：{output.name}")
        local_video_id = self._register_video(
            video,
            output=output,
            creator=creator,
            title=title,
        )
        entry = self._state.setdefault("videos", {}).setdefault(video.video_id, {})
        entry.update(
            {
                "file_path": str(output),
                "local_video_id": local_video_id,
                "last_seen_at": now_iso(),
                "last_error": "",
            }
        )
        self._save_state()
        return local_video_id, downloaded

    def _existing_image_files(self, aweme_id: str) -> list[Path]:
        matches: list[Path] = []
        for extension in ("jpg", "jpeg", "png", "webp"):
            matches.extend(self.image_dir.glob(f"*_{aweme_id}_*.{extension}"))
        return sorted(
            path
            for path in matches
            if (
                path.is_file()
                and path.stat().st_size > 1024
                and not path.stem.casefold().endswith("_ocr")
            )
        )

    def _ensure_image_work(
        self,
        work: ProfileVideo,
        *,
        creator: str,
    ) -> tuple[int, bool]:
        outputs = self._existing_image_files(work.video_id)
        entry = (self._state.get("videos") or {}).get(work.video_id) or {}
        expected_count = int(entry.get("image_file_count") or 0)
        title = work.title
        downloaded = False
        if not outputs or not expected_count or len(outputs) < expected_count:
            self._event(f"发现图文作品 {work.video_id}，正在读取并下载全部原图。")
            resolver = DouyinResolver(log=self._event, profile_dir=self.profile_dir)
            try:
                result = resolver.resolve_images(work.url, timeout=45)
            finally:
                resolver.close()
            if work.title and not work.title.startswith("抖音图文_"):
                result.title = work.title
            title = result.title or work.title
            refined_created_at = refine_created_at_from_title(
                work.created_at,
                title,
            )
            if refined_created_at.date() != work.created_at.date():
                self._event(
                    f"图文作品 {work.video_id} 的公开标题显示发布日期为 "
                    f"{refined_created_at:%Y-%m-%d}，已替代作品 ID 生成日期。"
                )
                work = replace(work, created_at=refined_created_at)
            prefix = f"{work.created_at:%Y%m%d_%H%M}_{work.video_id}"
            before_paths = {str(path.resolve()).casefold() for path in outputs}
            outputs = download_images(result, self.image_dir, prefix)
            downloaded = any(
                str(path.resolve()).casefold() not in before_paths for path in outputs
            )
            self._event(
                f"图文作品 {work.video_id} 已保存：共 {len(outputs)} 张原图。"
            )
        if not outputs:
            raise ParseError("图文作品没有可用原图，已等待下次重试。")
        local_video_id = self._register_image_work(
            work,
            outputs=outputs,
            creator=creator,
            title=title,
        )
        state_entry = self._state.setdefault("videos", {}).setdefault(
            work.video_id, {}
        )
        state_entry.update(
            {
                "file_paths": [str(path) for path in outputs],
                "image_file_count": len(outputs),
                "work_type": "image",
                "local_video_id": local_video_id,
                "last_seen_at": now_iso(),
                "last_error": "",
            }
        )
        self._save_state()
        return local_video_id, downloaded

    def _register_image_work(
        self,
        work: ProfileVideo,
        *,
        outputs: list[Path],
        creator: str,
        title: str,
    ) -> int:
        # The tab name is the stable account key used by the UI and database
        # filters. Douyin display names may contain changing suffixes or emoji.
        author = str(self.settings.source_account_name or creator or "新博主")
        existing_video = self._find_video_by_aweme(work.video_id)
        raw = from_json((existing_video or {}).get("raw_json"), {})
        if not isinstance(raw, dict):
            raw = {}
        previous_date_source = str(raw.get("published_at_source") or "")
        strong_existing_date = previous_date_source in {
            "manual_verified",
            "douyin-title-date",
            "douyin-page-create-time",
        }
        incoming_date_source = (
            "douyin-title-date"
            if work.created_at.date() != video_created_at(work.video_id).date()
            else "douyin-aweme-id"
        )
        published_at = (
            str((existing_video or {}).get("published_at") or "")
            if strong_existing_date
            else work.created_at.replace(tzinfo=CHINA_TZ).isoformat()
        )
        resolved_title = title or work.title or f"抖音图文_{work.video_id}"
        if (
            resolved_title.startswith("抖音图文_")
            and str((existing_video or {}).get("title") or "").strip()
        ):
            resolved_title = str(existing_video["title"])
        raw.update(
            {
                "source_path": str(outputs[0]),
                "source_paths": [str(path) for path in outputs],
                "douyin_aweme_id": work.video_id,
                "douyin_url": work.url,
                "douyin_work_type": "image",
                "image_count": len(outputs),
                "imported_at": now_iso(),
                "import_method": "creator-image-auto",
                "account_author": author,
                "published_at_source": (
                    previous_date_source
                    if strong_existing_date
                    else incoming_date_source
                ),
            }
        )
        local_video_id, _created = self.storage.upsert_video(
            {
                "source": str((existing_video or {}).get("source") or "douyin-image-auto"),
                "source_video_id": str(
                    (existing_video or {}).get("source_video_id") or work.video_id
                ),
                "author": author,
                "title": resolved_title,
                "description": f"由智能体自动下载并归档的图文作品，共 {len(outputs)} 张原图。",
                "url": work.url,
                "cover_url": "",
                "published_at": published_at,
                "status": "new",
                "raw_json": raw,
            }
        )
        existing_hashes = {
            str(asset.get("sha256") or "")
            for asset in self.storage.list_assets(local_video_id)
        }
        for index, output in enumerate(outputs, start=1):
            digest = _sha256(output)
            if digest in existing_hashes:
                continue
            mime_type = mimetypes.guess_type(output.name)[0] or "image/jpeg"
            self.storage.save_asset(
                {
                    "video_id": local_video_id,
                    "asset_type": "image",
                    "storage_mode": "source_file",
                    "original_name": output.name,
                    "local_path": str(output),
                    "mime_type": mime_type,
                    "size_bytes": output.stat().st_size,
                    "sha256": digest,
                    "source": "creator-image-auto",
                    "status": "stored",
                    "raw_json": {
                        "source_path": str(output),
                        "douyin_aweme_id": work.video_id,
                        "source_url": work.url,
                        "image_index": index,
                        "image_count": len(outputs),
                        "imported_at": now_iso(),
                    },
                }
            )
        self.storage.bind_video_remote_source(
            local_video_id,
            url=work.url,
            aweme_id=work.video_id,
            match_source="creator-image-auto",
            match_confidence=1.0,
            metadata={"file_paths": [str(path) for path in outputs]},
        )
        return local_video_id

    def _register_video(
        self,
        video: ProfileVideo,
        *,
        output: Path,
        creator: str,
        title: str,
    ) -> int:
        digest = _sha256(output)
        author = str(self.settings.source_account_name or creator or "新博主")
        existing_asset = self.storage.find_asset_by_sha(digest, author=author)
        existing_video = (
            self.storage.get_video(int(existing_asset["video_id"]))
            if existing_asset
            else self._find_video_by_aweme(video.video_id)
        )
        source = str((existing_video or {}).get("source") or "douyin-auto")
        source_video_id = str(
            (existing_video or {}).get("source_video_id") or video.video_id
        )
        raw = from_json((existing_video or {}).get("raw_json"), {})
        if not isinstance(raw, dict):
            raw = {}
        previous_date_source = str(raw.get("published_at_source") or "")
        strong_existing_date = previous_date_source in {
            "manual_verified",
            "douyin-title-date",
            "douyin-page-create-time",
        }
        incoming_date_source = (
            "douyin-title-date"
            if video.created_at.date() != video_created_at(video.video_id).date()
            else "douyin-aweme-id"
        )
        published_at = (
            str((existing_video or {}).get("published_at") or "")
            if strong_existing_date
            else video.created_at.replace(tzinfo=CHINA_TZ).isoformat()
        )
        resolved_title = title or video.title or f"抖音作品_{video.video_id}"
        if (
            resolved_title.startswith("抖音作品_")
            and str((existing_video or {}).get("title") or "").strip()
        ):
            resolved_title = str(existing_video["title"])
        raw.update(
            {
                "source_path": str(output),
                "sha256": digest,
                "douyin_aweme_id": video.video_id,
                "douyin_url": video.url,
                "imported_at": now_iso(),
                "import_method": "creator-downloader-auto",
                "account_author": author,
                "published_at_source": (
                    previous_date_source
                    if strong_existing_date
                    else incoming_date_source
                ),
            }
        )
        local_video_id, _created = self.storage.upsert_video(
            {
                "source": source,
                "source_video_id": source_video_id,
                "author": author,
                "title": resolved_title,
                "description": "由智能体内置下载器自动下载并归档。",
                "url": video.url,
                "cover_url": "",
                "published_at": published_at,
                "status": "new",
                "raw_json": raw,
            }
        )
        if not existing_asset:
            self.storage.save_asset(
                {
                    "video_id": local_video_id,
                    "asset_type": "video",
                    "storage_mode": "source_file",
                    "original_name": output.name,
                    "local_path": str(output),
                    "mime_type": "video/mp4",
                    "size_bytes": output.stat().st_size,
                    "sha256": digest,
                    "source": "creator-downloader-auto",
                    "status": "stored",
                    "raw_json": {
                        "source_path": str(output),
                        "douyin_aweme_id": video.video_id,
                        "source_url": video.url,
                        "imported_at": now_iso(),
                    },
                }
            )
        self.storage.bind_video_remote_source(
            local_video_id,
            url=video.url,
            aweme_id=video.video_id,
            match_source="creator-downloader-auto",
            match_confidence=1.0,
            metadata={"file_path": str(output)},
        )
        return local_video_id

    def _find_video_by_aweme(self, aweme_id: str) -> dict[str, Any] | None:
        with self.storage.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM videos
                WHERE (source = 'douyin-auto' AND source_video_id = ?)
                   OR url LIKE ?
                   OR raw_json LIKE ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (aweme_id, f"%/{aweme_id}%", f'%"douyin_aweme_id": "{aweme_id}"%'),
            ).fetchone()
        return dict(row) if row else None

    def _comments_due(
        self,
        video: ProfileVideo,
        *,
        newly_downloaded: bool,
        force: bool,
    ) -> bool:
        if force or newly_downloaded:
            return True
        age = _china_now() - video.created_at.replace(tzinfo=CHINA_TZ)
        if age < timedelta(0) or age > timedelta(
            hours=int(self.settings.creator_comment_tracking_hours)
        ):
            return False
        entry = self._state.setdefault("videos", {}).setdefault(video.video_id, {})
        previous_raw = str(entry.get("last_comment_sync_at") or "")
        if not previous_raw:
            return True
        try:
            previous = datetime.fromisoformat(previous_raw)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=CHINA_TZ)
        except ValueError:
            return True
        return _china_now() - previous >= timedelta(
            minutes=int(self.settings.creator_comment_refresh_minutes)
        )

    def _collect_and_merge_comments(
        self,
        local_video_id: int,
        video: ProfileVideo,
        *,
        creator: str,
        profile_url: str,
    ) -> dict[str, int]:
        self._event(f"正在同步作品 {video.video_id} 的公开评论。")
        collector = CommentCollector(self.profile_dir, log=self._event)
        try:
            rows = collector.collect(
                video.url,
                timeout=180,
                limit=int(self.settings.creator_comment_limit),
                creator_uid=_profile_uid(profile_url),
            )
            summary = dict(collector.last_summary)
        except CommentCollectError:
            raise
        finally:
            collector.close()
        normalized: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            comment_id = str(row.get("comment_id") or "").strip()
            text = str(row.get("text") or "").strip()
            if not comment_id or not text:
                continue
            is_creator = bool(row.get("is_creator"))
            normalized.append(
                {
                    "source_comment_id": comment_id,
                    "parent_source_comment_id": str(
                        row.get("parent_comment_id") or ""
                    ),
                    "thread_root_source_comment_id": str(
                        row.get("parent_comment_id") or ""
                    ),
                    "author": (
                        creator
                        if is_creator
                        else str(row.get("author_name") or "抖音用户")
                    ),
                    "author_uid": str(row.get("author_uid") or ""),
                    "text": text,
                    "like_count": int(row.get("digg_count") or 0),
                    "reply_count": int(row.get("reply_count") or 0),
                    "published_at": str(row.get("created_at") or ""),
                    "captured_at": now_iso(),
                    "kind": "author_reply" if is_creator else "user_comment",
                    "reply_depth": 1 if row.get("parent_comment_id") else 0,
                    "author_liked": row.get("is_author_digged") is True,
                    "author_like_status": row.get("is_author_digged"),
                    "ip_label": str(row.get("ip_label") or ""),
                    "public_label": str(row.get("label_text") or ""),
                    "reply_to_comment_id": str(
                        row.get("reply_to_comment_id") or ""
                    ),
                    "actual_reply_user": str(
                        row.get("reply_to_user_name") or ""
                    ),
                    "display_order": index + 1,
                }
            )
        normalized = classify_comment_sections(normalized, creator)
        by_id = {
            str(item["source_comment_id"]): item
            for item in normalized
        }
        created = 0
        updated = 0
        for item in normalized:
            reply_target = str(item.get("reply_to_comment_id") or "")
            parent_target = str(item.get("parent_source_comment_id") or "")
            replied = by_id.get(reply_target) or by_id.get(parent_target) or {}
            signal = comment_signal(str(item["text"]))
            _comment_id, was_created = self.storage.upsert_comment(
                {
                    "video_id": local_video_id,
                    "source": "douyin-web",
                    "source_comment_id": str(item["source_comment_id"]),
                    "author": str(item["author"]),
                    "text": str(item["text"]),
                    "like_count": int(item.get("like_count") or 0),
                    "reply_count": int(item.get("reply_count") or 0),
                    "sentiment": signal["sentiment"],
                    "risk_level": signal["risk_level"],
                    "published_at": str(item.get("published_at") or ""),
                    "captured_at": str(item.get("captured_at") or now_iso()),
                    "raw_json": {
                        "kind": str(item.get("kind") or "user_comment"),
                        "section": str(item.get("section") or "fan_comment"),
                        "reply_depth": int(item.get("reply_depth") or 0),
                        "parent_source_comment_id": parent_target,
                        "root_source_comment_id": str(
                            item.get("root_source_comment_id") or ""
                        ),
                        "author_uid": str(item.get("author_uid") or ""),
                        "author_liked": bool(item.get("author_liked")),
                        "author_like_status": item.get("author_like_status"),
                        "ip_label": str(item.get("ip_label") or ""),
                        "low_value": bool(item.get("low_value")),
                        "aweme_id": video.video_id,
                        "source_url": video.url,
                        "confirmed_by_user": False,
                        "automatic_sync": True,
                        "display_order": int(item.get("display_order") or 0),
                        "public_label": str(item.get("public_label") or ""),
                        "actual_reply_user": str(
                            item.get("actual_reply_user") or ""
                        ),
                        "replied_original_comment": str(
                            replied.get("text") or ""
                        ),
                        "reply_object": str(replied.get("author") or ""),
                        "import_method": "creator-downloader-auto",
                        "collection_summary": summary,
                    },
                }
            )
            if was_created:
                created += 1
            else:
                updated += 1
        entry = self._state.setdefault("videos", {}).setdefault(video.video_id, {})
        entry.update(
            {
                "last_comment_sync_at": _china_now().isoformat(),
                "comment_count": len(normalized),
                "last_comment_summary": summary,
                "last_error": "",
            }
        )
        self._save_state()
        self._event(
            f"作品 {video.video_id} 评论已合并：新增 {created} 条，更新 {updated} 条。"
        )
        return {
            "seen": len(normalized),
            "created": created,
            "updated": updated,
        }

    def _remember_video_error(self, aweme_id: str, error: str) -> None:
        entry = self._state.setdefault("videos", {}).setdefault(aweme_id, {})
        entry.update({"last_error": str(error), "last_error_at": now_iso()})
        self._save_state()
