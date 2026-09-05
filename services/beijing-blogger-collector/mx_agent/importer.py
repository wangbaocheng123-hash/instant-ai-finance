from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8+ has zoneinfo.
    ZoneInfo = None

from .storage import Storage, now_iso


MEDIA_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".mkv",
    ".avi",
    ".flv",
    ".wmv",
    ".mp3",
    ".m4a",
    ".wav",
    ".aac",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".txt",
    ".md",
    ".pdf",
}

PARTIAL_EXTENSIONS = {
    ".crdownload",
    ".download",
    ".part",
    ".tmp",
}


def _china_timezone():
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            pass
    return timezone(timedelta(hours=8))


CHINA_TZ = _china_timezone()


def published_at_from_name(name: str) -> str | None:
    parsed = datetime_from_name(name)
    if not parsed:
        return None
    return parsed.astimezone(UTC).isoformat()


def datetime_from_name(name: str) -> datetime | None:
    value = Path(str(name or "")).stem.strip()
    if not value:
        return None

    # Canonical normalized form: 20260709_1653 / 20260709-165328.
    # Keep this before the contiguous form so renamed files remain parseable.
    for match in re.finditer(
        r"(?<!\d)((?:19|20)\d{2})([01]\d)([0-3]\d)[_-]([0-2]\d)([0-5]\d)([0-5]\d)?(?!\d)",
        value,
    ):
        parsed = _build_local_datetime(
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
            match.group(5),
            match.group(6) or "0",
        )
        if parsed:
            return parsed

    # 20260709165328 / 202607091653
    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})([01]\d)([0-3]\d)([0-2]\d)([0-5]\d)([0-5]\d)?(?!\d)", value):
        parsed = _build_local_datetime(
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
            match.group(5),
            match.group(6) or "0",
        )
        if parsed:
            return parsed

    # Tolerate a legacy HMM0 suffix such as 202607107000, meaning 2026-07-10 07:00.
    # Standard YYYYMMDDHHMM and YYYYMMDDHHMMSS forms above always take priority.
    for match in re.finditer(
        r"(?<!\d)((?:19|20)\d{2})([01]\d)([0-3]\d)([0-9])([0-5]\d)0(?!\d)",
        value,
    ):
        parsed = _build_local_datetime(
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
            match.group(5),
            "0",
        )
        if parsed:
            return parsed

    # 2026-07-09 16:53 / 2026年07月09日16点53分
    year_pattern = re.compile(
        r"(?<!\d)((?:19|20)\d{2})\D{0,3}"
        r"([01]?\d)\D{1,3}([0-3]?\d)\D{0,5}"
        r"([0-2]?\d)\D{0,3}([0-5]\d)(?:\D{0,3}([0-5]\d))?"
    )
    for match in year_pattern.finditer(value):
        parsed = _build_local_datetime(*match.groups(default="0"))
        if parsed:
            return parsed

    current_year = str(datetime.now(CHINA_TZ).year)

    # 07091653 / 0709165328
    for match in re.finditer(r"(?<!\d)([01]\d)([0-3]\d)([0-2]\d)([0-5]\d)([0-5]\d)?(?!\d)", value):
        parsed = _build_local_datetime(
            current_year,
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
            match.group(5) or "0",
        )
        if parsed:
            return parsed

    # 07-09 16:53 / 7月9日16点53分
    no_year_pattern = re.compile(
        r"(?<!\d)([01]?\d)\D{1,3}([0-3]?\d)\D{0,5}"
        r"([0-2]?\d)\D{0,3}([0-5]\d)(?:\D{0,3}([0-5]\d))?"
    )
    for match in no_year_pattern.finditer(value):
        parsed = _build_local_datetime(
            current_year,
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
            match.group(5) or "0",
        )
        if parsed:
            return parsed

    return None


def _build_local_datetime(
    year: str | int,
    month: str | int,
    day: str | int,
    hour: str | int,
    minute: str | int,
    second: str | int = 0,
) -> datetime | None:
    try:
        parsed = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=CHINA_TZ,
        )
    except (TypeError, ValueError):
        return None
    if parsed.year < 2000 or parsed.year > 2100:
        return None
    return parsed


@dataclass(frozen=True)
class LocalFileCandidate:
    path: Path
    name: str
    size_bytes: int
    modified_at: str
    mime_type: str
    asset_type: str


class DownloadImportService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def status(self) -> dict[str, Any]:
        folders = self.download_dirs()
        return {
            "folders": [str(folder) for folder in folders],
            "existing_folders": [str(folder) for folder in folders if folder.exists()],
            "supported_extensions": sorted(MEDIA_EXTENSIONS),
        }

    def download_dirs(self) -> list[Path]:
        configured = os.getenv("MX_AGENT_DOWNLOAD_DIRS", "").strip()
        if configured:
            folders = [Path(item.strip()).expanduser() for item in configured.split(";") if item.strip()]
        else:
            downloads = Path.home() / "Downloads"
            folders = [downloads, downloads / "douyin"]
        unique: list[Path] = []
        seen: set[str] = set()
        for folder in folders:
            resolved = folder.resolve()
            key = str(resolved).casefold()
            if key not in seen:
                unique.append(resolved)
                seen.add(key)
        return unique

    def recent_files(self, limit: int = 12, asset_type: str | None = None) -> list[dict[str, Any]]:
        candidates: list[LocalFileCandidate] = []
        seen_paths: set[str] = set()
        for folder in self.download_dirs():
            if not folder.exists() or not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if self._is_candidate(path):
                    path_key = str(path.resolve()).casefold()
                    if path_key in seen_paths:
                        continue
                    seen_paths.add(path_key)
                    candidate = self._candidate(path)
                    if self._asset_type_matches(candidate.asset_type, asset_type):
                        candidates.append(candidate)
        candidates.sort(key=lambda item: item.path.stat().st_mtime, reverse=True)
        return [self._serialize(candidate) for candidate in candidates[:limit]]

    def import_latest(self, video_id: int) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        recent = self.recent_files(limit=1, asset_type="media")
        if not recent:
            raise ValueError("下载目录里没有找到可导入的视频或音频文件")

        source_path = Path(recent[0]["path"]).resolve()
        self._ensure_in_download_dirs(source_path)
        digest = self._sha256_file(source_path)

        for asset in self.storage.list_assets(video_id):
            if asset.get("sha256") == digest:
                item = dict(asset)
                item["already_imported"] = True
                item["file_url"] = f"/api/assets/{item['id']}/file"
                return {
                    "asset": item,
                    "title_recognition": self._recognize_cover_title(video_id),
                    "message": "这个文件已经在当前视频档案里，不需要重复导入。",
                }

        mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        asset_id = self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": self._asset_type(source_path),
                "storage_mode": "source_file",
                "original_name": source_path.name,
                "local_path": str(source_path),
                "mime_type": mime_type,
                "size_bytes": source_path.stat().st_size,
                "sha256": digest,
                "source": "downloads-import",
                "status": "stored",
                "raw_json": {
                    "source_path": str(source_path),
                    "imported_at": now_iso(),
                },
            }
        )
        asset = self.storage.get_asset(asset_id) or {}
        asset["file_url"] = f"/api/assets/{asset_id}/file"
        return {
            "asset": asset,
            "title_recognition": self._recognize_cover_title(video_id),
            "message": "已直接引用原文件入库，没有生成媒体副本。",
        }

    def import_file_as_video(
        self,
        source_path: str | Path,
        source: str = "datatool-watch",
        author: str = "模型先生",
    ) -> dict[str, Any]:
        path = Path(source_path).resolve()
        if not self._is_candidate(path):
            raise ValueError("不是可导入的媒体文件")

        account_author = str(author or "模型先生").strip() or "模型先生"
        is_account_drop = source.endswith("-video-drop")
        parsed_published_at = published_at_from_name(path.stem)
        published_at_source = "model_filename" if is_account_drop and parsed_published_at else (
            "filename" if parsed_published_at else "file_mtime"
        )
        digest = self._sha256_file(path)
        existing = self.storage.find_asset_by_sha(digest, author=account_author)
        if existing:
            corrected = False
            if parsed_published_at:
                corrected = self.storage.update_video_publish_time_from_filename(
                    int(existing["video_id"]),
                    parsed_published_at,
                    title=path.stem if is_account_drop else None,
                    source_path=str(path),
                    source=source,
                    force=is_account_drop,
                )
            existing["file_url"] = f"/api/assets/{existing['id']}/file"
            return {
                "created": False,
                "video_id": int(existing["video_id"]),
                "asset": existing,
                "title_recognition": self._recognize_cover_title(int(existing["video_id"])),
                "message": "文件已经入库，已按文件名修正发布时间。" if corrected else "文件已经入库，已跳过去重。",
                "corrected_published_at": corrected,
            }

        stat = path.stat()
        published_at = parsed_published_at or now_iso_from_timestamp(stat.st_mtime)
        video_id, video_created = self.storage.upsert_video(
            {
                "source": source,
                "source_video_id": digest[:16],
                "author": account_author,
                "title": path.stem,
                "description": f"由 {source} 自动导入的本地下载文件。",
                "url": "",
                "cover_url": "",
                "published_at": published_at,
                "raw_json": {
                    "source_path": str(path),
                    "sha256": digest,
                    "imported_at": now_iso(),
                    "published_at_source": published_at_source,
                    "account_author": account_author,
                },
            }
        )

        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        asset_id = self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": self._asset_type(path),
                "storage_mode": "source_file",
                "original_name": path.name,
                "local_path": str(path),
                "mime_type": mime_type,
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "source": source,
                "status": "stored",
                "raw_json": {
                    "source_path": str(path),
                    "imported_at": now_iso(),
                    "published_at_source": published_at_source,
                    "account_author": account_author,
                },
            }
        )
        asset = self.storage.get_asset(asset_id) or {}
        asset["file_url"] = f"/api/assets/{asset_id}/file"
        return {
            "created": video_created,
            "video_id": video_id,
            "asset": asset,
            "title_recognition": self._recognize_cover_title(video_id),
            "message": "已自动导入下载文件。",
        }

    def _recognize_cover_title(self, video_id: int) -> dict[str, Any]:
        try:
            from .cover_title import CoverTitleRecognizer

            return CoverTitleRecognizer(self.storage).recognize_title(video_id)
        except Exception as exc:
            return {
                "video_id": video_id,
                "recognized": False,
                "skipped": False,
                "error": str(exc),
            }

    def _is_candidate(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.stem.casefold().endswith("_ocr"):
            return False
        if path.suffix.lower() in PARTIAL_EXTENSIONS:
            return False
        if path.name.startswith("."):
            return False
        return path.suffix.lower() in MEDIA_EXTENSIONS

    def _asset_type_matches(self, current: str, expected: str | None) -> bool:
        if not expected or expected == "all":
            return True
        if expected == "media":
            return current in {"video", "audio"}
        return current == expected

    def _candidate(self, path: Path) -> LocalFileCandidate:
        stat = path.stat()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return LocalFileCandidate(
            path=path.resolve(),
            name=path.name,
            size_bytes=stat.st_size,
            modified_at=now_iso_from_timestamp(stat.st_mtime),
            mime_type=mime_type,
            asset_type=self._asset_type(path),
        )

    def _serialize(self, candidate: LocalFileCandidate) -> dict[str, Any]:
        return {
            "path": str(candidate.path),
            "name": candidate.name,
            "size_bytes": candidate.size_bytes,
            "modified_at": candidate.modified_at,
            "mime_type": candidate.mime_type,
            "asset_type": candidate.asset_type,
        }

    def _ensure_in_download_dirs(self, path: Path) -> None:
        resolved = path.resolve()
        for folder in self.download_dirs():
            try:
                resolved.relative_to(folder.resolve())
                return
            except ValueError:
                continue
        raise ValueError("只能导入下载目录里的文件")

    def _asset_type(self, path: Path) -> str:
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        if mime_type.startswith("video/"):
            return "video"
        if mime_type.startswith("audio/"):
            return "audio"
        if mime_type.startswith("image/"):
            return "screenshot"
        return "document"

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def now_iso_from_timestamp(timestamp: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(timestamp, UTC).isoformat()
