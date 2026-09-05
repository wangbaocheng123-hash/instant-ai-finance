from __future__ import annotations

import json
import os
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .settings import DATA_DIR, Settings
from .storage import Storage, now_iso


CHINA_TZ = timezone(timedelta(hours=8))
CANONICAL_STEM = re.compile(r"^\d{8}_\d{4}_[A-Za-z0-9-]+$")


class AutoDoubaoTranscriptionService:
    """Transcribe newly imported videos once and archive their official text.

    The first startup records the current highest video id as a baseline.  This
    is intentional: enabling the feature must not unexpectedly submit the
    user's historical backlog to a paid ASR API.  Later startups reuse the
    persisted baseline and therefore recover new videos that arrived while the
    app was closed.
    """

    def __init__(
        self,
        settings: Settings | object,
        storage: Storage,
        doubao: Any,
        *,
        state_path: Path | None = None,
        interval_seconds: int | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.doubao = doubao
        self.enabled = bool(
            getattr(settings, "auto_doubao_transcribe_enabled", True)
        ) and os.getenv("MX_AGENT_AUTO_DOUBAO_TRANSCRIBE", "1") != "0"
        self.interval_seconds = max(
            5,
            int(
                interval_seconds
                if interval_seconds is not None
                else os.getenv("MX_AGENT_AUTO_TRANSCRIBE_INTERVAL_SECONDS", "15")
            ),
        )
        self.max_per_pass = max(
            1,
            int(os.getenv("MX_AGENT_AUTO_TRANSCRIBE_MAX_PER_PASS", "20")),
        )
        self.max_consecutive_failures = 3
        self.state_path = Path(
            state_path or DATA_DIR / "auto-doubao-transcription.json"
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._queued: set[int] = set()
        self._events: deque[dict[str, Any]] = deque(maxlen=30)
        self._processing = False
        self._current_video_id: int | None = None
        self._last_message = "自动豆包转写尚未启动。"
        self._state = self._load_or_create_state()

    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="auto-doubao-transcription",
            daemon=True,
        )
        self._thread.start()
        self._record_event("started", "新下载视频自动豆包转写已启动。")
        self._wake.set()

    def enqueue(self, video_id: int) -> dict[str, Any]:
        value = int(video_id)
        if not self.enabled:
            return self.status()
        if value <= int(self._state.get("baseline_video_id") or 0):
            return self.status()
        with self._state_lock:
            self._queued.add(value)
        self._wake.set()
        return self.status()

    def trigger(self) -> dict[str, Any]:
        if self.enabled:
            self._wake.set()
            self._last_message = "已检查并唤醒新视频自动转写。"
        result = self.status()
        result["message"] = self._last_message
        return result

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            attempts = dict(self._state.get("attempts") or {})
            saved_count = sum(
                1 for item in attempts.values() if item.get("status") == "saved"
            )
            failed_count = sum(
                1 for item in attempts.values() if item.get("status") == "failed"
            )
            queued_count = len(self._queued)
            state_snapshot = {
                "baseline_video_id": int(
                    self._state.get("baseline_video_id") or 0
                ),
                "created_at": str(self._state.get("created_at") or ""),
                "last_run_at": str(self._state.get("last_run_at") or ""),
            }
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "processing": self._processing,
            "wake_pending": self._wake.is_set(),
            "current_video_id": self._current_video_id,
            "interval_seconds": self.interval_seconds,
            "queued_count": queued_count,
            "saved_count": saved_count,
            "failed_count": failed_count,
            "message": self._last_message,
            "events": list(self._events),
            **state_snapshot,
        }

    def run_once(self) -> dict[str, Any]:
        if not self.enabled:
            return {"processed": 0, "saved": 0, "failed": 0, "message": "自动转写未启用。"}
        if not self._run_lock.acquire(blocking=False):
            return {"processed": 0, "saved": 0, "failed": 0, "message": "自动转写正在运行。"}
        self._processing = True
        processed = 0
        saved = 0
        failed = 0
        skipped = 0
        consecutive_failures = 0
        try:
            if not bool(self.doubao.speech_enabled()):
                self._last_message = "豆包语音识别未开启，已保留新视频等待下次检查。"
                return {
                    "processed": 0,
                    "saved": 0,
                    "failed": 0,
                    "skipped": 0,
                    "message": self._last_message,
                }

            candidate_ids = self._candidate_ids()
            for video_id in candidate_ids:
                if self._stop.is_set():
                    break
                if processed >= self.max_per_pass:
                    break
                self._current_video_id = video_id
                detail = self.storage.get_video_detail(video_id)
                allowed, reason = self._archiveable(detail)
                if not allowed:
                    skipped += 1
                    continue

                current_text = str(
                    ((((detail or {}).get("notes") or {}).get("video_text") or {}).get("text"))
                    or ""
                ).strip()
                if current_text:
                    skipped += 1
                    self._dequeue(video_id)
                    continue

                processed += 1
                paid_call_started = False
                try:
                    paid_call_started = True
                    recognized = self.doubao.transcribe_video_text(video_id)
                    text = str(recognized.get("text") or "").strip()
                    if not text:
                        raise RuntimeError("豆包没有返回可保存文字。")

                    saved_result = self.storage.save_official_original(video_id, text)
                    note = saved_result.get("note") or {}
                    if not bool(note.get("official")) or str(note.get("text") or "").strip() != text:
                        raise RuntimeError("保存接口未返回一致的正式原文。")
                    verified = self.storage.get_official_original_note(video_id) or {}
                    if str(verified.get("text") or "").strip() != text:
                        raise RuntimeError("保存后重新读取的正式原文不一致。")

                    self._record_attempt(
                        video_id,
                        "saved",
                        task_id=str(recognized.get("task_id") or ""),
                        characters=len(text),
                        txt_path=str(saved_result.get("txt_path") or ""),
                    )
                    self._record_event(
                        "saved",
                        f"视频 {video_id} 已自动识别并保存正式原文。",
                        video_id=video_id,
                        characters=len(text),
                    )
                    self._dequeue(video_id)
                    saved += 1
                    consecutive_failures = 0
                except Exception as exc:
                    failed += 1
                    consecutive_failures += 1
                    # Once the ASR call begins, never auto-retry this paid job.
                    if paid_call_started:
                        self._record_attempt(video_id, "failed", error=str(exc))
                        self._dequeue(video_id)
                    self._record_event(
                        "failed",
                        f"视频 {video_id} 自动转写失败：{exc}",
                        video_id=video_id,
                    )
                    if consecutive_failures >= self.max_consecutive_failures:
                        self._record_event(
                            "paused",
                            "连续失败 3 条，本轮自动转写已停止。",
                        )
                        break

            self._last_message = (
                f"本轮完成：保存 {saved} 条，失败 {failed} 条，跳过 {skipped} 条。"
            )
            return {
                "processed": processed,
                "saved": saved,
                "failed": failed,
                "skipped": skipped,
                "message": self._last_message,
            }
        finally:
            self._current_video_id = None
            self._processing = False
            with self._state_lock:
                self._state["last_run_at"] = now_iso()
                self._save_state_locked()
            self._run_lock.release()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.interval_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.run_once()
            except Exception as exc:
                self._last_message = f"自动转写检查失败：{exc}"
                self._record_event("error", self._last_message)

    def _candidate_ids(self) -> list[int]:
        baseline = int(self._state.get("baseline_video_id") or 0)
        with self._state_lock:
            attempted = set((self._state.get("attempts") or {}).keys())
            queued = set(self._queued)
        missing = {
            int(item["id"])
            for item in self.storage.list_missing_video_text_videos(limit=0)
            if int(item["id"]) > baseline
        }
        candidates = queued | missing
        return sorted(
            video_id
            for video_id in candidates
            if video_id > baseline and str(video_id) not in attempted
        )

    @staticmethod
    def _archiveable(detail: dict[str, Any] | None) -> tuple[bool, str]:
        if not detail:
            return False, "作品不存在"
        video = detail.get("video") or {}
        assets = detail.get("assets") or []
        asset = next(
            (
                item
                for item in assets
                if str(item.get("mime_type") or "").startswith("video/")
                or str(item.get("asset_type") or "") == "video"
            ),
            None,
        )
        if not asset:
            return False, "没有本地视频文件"
        local_path = Path(str(asset.get("local_path") or ""))
        if not local_path.is_file():
            return False, "本地视频文件不存在"
        filename = str(asset.get("original_name") or local_path.name)
        stem = Path(filename).stem
        if not CANONICAL_STEM.fullmatch(stem):
            return False, "视频文件名不是可归档的时间格式"
        published_at = str(video.get("published_at") or "").strip()
        if not published_at:
            return False, "缺少真实发布时间"
        try:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            expected = published.astimezone(CHINA_TZ).strftime("%Y%m%d_%H%M_")
        except ValueError:
            return False, "发布时间格式无效"
        if not stem.startswith(expected):
            return False, "视频文件时间与发布时间不一致"
        return True, ""

    def _load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "baseline_video_id" in loaded:
                    loaded.setdefault("attempts", {})
                    return loaded
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        state = {
            "version": 1,
            "baseline_video_id": self.storage.max_video_id(),
            "created_at": now_iso(),
            "last_run_at": "",
            "attempts": {},
        }
        self._state = state
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.state_path)
        return state

    def _record_attempt(self, video_id: int, status: str, **extra: Any) -> None:
        with self._state_lock:
            attempts = self._state.setdefault("attempts", {})
            attempts[str(video_id)] = {
                "status": status,
                "at": now_iso(),
                **extra,
            }
            self._save_state_locked()

    def _save_state_locked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.state_path)

    def _dequeue(self, video_id: int) -> None:
        with self._state_lock:
            self._queued.discard(video_id)

    def _record_event(self, event_type: str, message: str, **extra: Any) -> None:
        self._events.appendleft(
            {"type": event_type, "message": message, "at": now_iso(), **extra}
        )
