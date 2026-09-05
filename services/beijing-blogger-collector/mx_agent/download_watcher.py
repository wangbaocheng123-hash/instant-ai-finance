from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .analysis import VideoIntelligenceAgent
from .importer import DownloadImportService, MEDIA_EXTENSIONS, PARTIAL_EXTENSIONS, published_at_from_name
from .settings import Settings
from .storage import Storage, now_iso


class DownloadWatcher:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        importer: DownloadImportService,
        analyzer: VideoIntelligenceAgent,
        on_video_imported: Callable[[int], Any] | None = None,
    ):
        self.settings = settings
        self.storage = storage
        self.importer = importer
        self.analyzer = analyzer
        self.on_video_imported = on_video_imported
        self.enabled = os.getenv("MX_AGENT_AUTO_WATCH_DOWNLOADS", "1") != "0"
        self.interval_seconds = int(os.getenv("MX_AGENT_WATCH_INTERVAL_SECONDS", "10"))
        self.stable_seconds = int(os.getenv("MX_AGENT_WATCH_STABLE_SECONDS", "8"))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._known: dict[str, tuple[int, float]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=30)
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        self._snapshot_existing_files()
        self._thread = threading.Thread(target=self._loop, name="download-watcher", daemon=True)
        self._thread.start()
        self._record_event("started", "下载目录自动监听已启动。")

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "stable_seconds": self.stable_seconds,
                "folders": [str(folder) for folder in self._watch_dirs()],
                "manual_download_folders": [str(folder) for folder in self.importer.download_dirs()],
                "pending": len(self._pending),
                "known": len(self._known),
                "events": list(self._events),
            }

    def scan_once(self) -> dict[str, Any]:
        if not self.enabled:
            return {"imported": 0, "skipped": 0, "message": "自动监听未启用。"}
        imported = 0
        skipped = 0
        for path in self._candidate_paths():
            result = self._observe_path(path)
            if result == "imported":
                imported += 1
            elif result == "skipped":
                skipped += 1
        return {"imported": imported, "skipped": skipped}

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                self._record_event("error", str(exc))
            self._stop.wait(self.interval_seconds)

    def _snapshot_existing_files(self) -> None:
        import_existing_dirs = self._import_existing_dirs()
        known_source_paths = self.storage.known_asset_source_paths()
        for path in self._candidate_paths():
            path_key = str(path.resolve()).casefold()
            if (
                self._is_inside_any(path, import_existing_dirs)
                and published_at_from_name(path.stem)
                and path_key not in known_source_paths
            ):
                # A dated file that is genuinely new must still be imported
                # when the app starts after the user copied it into the folder.
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            self._known[str(path)] = (stat.st_size, stat.st_mtime)

    def _candidate_paths(self) -> list[Path]:
        paths: list[Path] = []
        seen_paths: set[str] = set()
        for folder in self._watch_dirs():
            if not folder.exists() or not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if not path.is_file():
                    continue
                if path.stem.casefold().endswith("_ocr"):
                    continue
                path_key = str(path.resolve()).casefold()
                if path_key in seen_paths:
                    continue
                suffix = path.suffix.lower()
                if suffix in PARTIAL_EXTENSIONS:
                    continue
                if suffix not in MEDIA_EXTENSIONS:
                    continue
                if self._asset_type(path) not in {"video", "audio", "screenshot"}:
                    continue
                seen_paths.add(path_key)
                paths.append(path.resolve())
        import_existing_dirs = self._import_existing_dirs()

        def import_priority(path: Path) -> tuple[int, float, float]:
            filename_time = published_at_from_name(path.stem)
            has_filename_time = bool(filename_time)
            is_model_drop = self._is_inside_any(path, import_existing_dirs)
            if is_model_drop and has_filename_time:
                group = 0
            elif has_filename_time:
                group = 1
            else:
                group = 2
            try:
                modified = -path.stat().st_mtime
            except OSError:
                modified = 0.0
            try:
                published = -datetime.fromisoformat(str(filename_time)).timestamp()
            except (TypeError, ValueError):
                published = 0.0
            return group, published, modified

        # A dated file in 模型视频 must win the race against its generic
        # source file. Otherwise the generic image is briefly imported
        # with today's file mtime and appears at the top of the works list.
        paths.sort(key=import_priority)
        return paths

    def _watch_dirs(self) -> list[Path]:
        """Return folders authorized for unattended imports.

        MX_AGENT_DOWNLOAD_DIRS may include a user's general Downloads folder
        for explicit/manual file selection.  Unattended watching must be
        narrower: when MX_AGENT_IMPORT_EXISTING_DIRS is configured, only those
        dedicated model-content folders are eligible for automatic imports.
        """
        dedicated = self._import_existing_dirs()
        return dedicated or self.importer.download_dirs()

    def _observe_path(self, path: Path) -> str:
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            return "skipped"
        size = stat.st_size
        mtime = stat.st_mtime
        now = time.time()

        # Creator sync registers its downloaded file before the stability
        # window expires.  Recheck the live asset index here so the watcher
        # does not import the same file again or trigger OCR side effects.
        path_key = str(path.resolve()).casefold()
        if path_key in self.storage.known_asset_source_paths():
            self._known[key] = (size, mtime)
            self._pending.pop(key, None)
            return "skipped"

        if key in self._known and self._known[key] == (size, mtime):
            return "skipped"

        pending = self._pending.get(key)
        if not pending or pending["size"] != size or pending["mtime"] != mtime:
            self._pending[key] = {
                "size": size,
                "mtime": mtime,
                "stable_since": now,
            }
            return "skipped"

        if now - float(pending["stable_since"]) < self.stable_seconds:
            return "skipped"

        import_existing_dirs = self._import_existing_dirs()
        is_account_drop = self._is_inside_any(path, import_existing_dirs)
        account_author = self._account_for_path(path) if is_account_drop else self._default_account_author()
        source = "datatool-watch"
        if is_account_drop:
            source = "model-video-drop" if account_author == self._default_account_author() else "model-world-video-drop"
        result = self.importer.import_file_as_video(path, source=source, author=account_author)
        self._known[key] = (size, mtime)
        self._pending.pop(key, None)
        video_id = int(result["video_id"])
        analysis_id = None
        if result.get("created") and self.settings.auto_analyze_new_videos:
            try:
                analysis = self.analyzer.analyze_video(video_id)
                analysis_id = analysis.get("analysis_id")
            except Exception as exc:
                self._record_event("analysis_error", f"{path.name}: {exc}", video_id=video_id)
        if result.get("created") and self.on_video_imported:
            try:
                self.on_video_imported(video_id)
            except Exception as exc:
                self._record_event(
                    "auto_transcription_error",
                    f"{path.name}: {exc}",
                    video_id=video_id,
                )
        self._record_event(
            "imported" if result.get("created") else "deduped",
            result.get("message", "已处理下载文件。"),
            video_id=video_id,
            analysis_id=analysis_id,
            file=str(path),
        )
        return "imported"

    def _record_event(self, event_type: str, message: str, **extra: Any) -> None:
        event = {"type": event_type, "message": message, "at": now_iso(), **extra}
        with self._lock:
            self._events.appendleft(event)

    def _import_existing_dirs(self) -> list[Path]:
        configured = os.getenv("MX_AGENT_IMPORT_EXISTING_DIRS", "").strip()
        if not configured:
            return []
        folders: list[Path] = []
        for item in configured.split(";"):
            item = item.strip()
            if item:
                folders.append(Path(item).expanduser().resolve())
        return folders

    def _default_account_author(self) -> str:
        return str(getattr(self.settings, "source_account_name", "新博主") or "新博主")

    def _account_for_path(self, path: Path) -> str:
        resolved = path.resolve()
        for folder in self._import_existing_dirs():
            try:
                relative = resolved.relative_to(folder)
            except ValueError:
                continue
            # The per-creator library is laid out as:
            # 博主数据/<creator>/视频|图片/<file>.  Reading the creator from
            # the relative path keeps future folders automatic and prevents
            # unattended imports from falling back to the default account.
            if (
                folder.name == "博主数据"
                and len(relative.parts) >= 3
                and relative.parts[1] in {"视频", "图片"}
            ):
                return relative.parts[0]
            if "模型哥" in folder.name:
                return "模型哥看世界"
            return self._default_account_author()
        return self._default_account_author()

    def _is_inside_any(self, path: Path, folders: list[Path]) -> bool:
        resolved = path.resolve()
        for folder in folders:
            try:
                resolved.relative_to(folder)
                return True
            except ValueError:
                continue
        return False

    def _asset_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".flv", ".wmv"}:
            return "video"
        if suffix in {".mp3", ".m4a", ".wav", ".aac"}:
            return "audio"
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            return "screenshot"
        return "other"
