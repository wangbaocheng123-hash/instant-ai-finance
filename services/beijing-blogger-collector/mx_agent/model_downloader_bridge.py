from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .transfer_contract import build_comment_bundle, new_manifest, sha256_file
from .transfer_outbox import TransferOutbox


MODEL_MR_TRANSFER_CREATOR_ID = "732ceafb-2bb3-5042-b303-967bdcf4312d"
DEFAULT_DATABASE = Path("/var/lib/model-downloader/library.sqlite3")
DEFAULT_MEDIA_ROOT = Path("/srv/model-downloader/videos")
WIRE_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")


def _enabled(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "off", "no"}


def _is_readable_file(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def _wire_text(value: object, limit: int) -> str:
    return WIRE_CONTROL_CHARACTERS.sub(" ", str(value or "")).strip()[:limit]


def _iso(value: object, *, fallback: str | None = None) -> str:
    text = str(value or fallback or "").strip()
    if not text:
        return ""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


class ModelDownloaderBridge:
    """Read the independent downloader library and enqueue signed transfers."""

    def __init__(
        self,
        *,
        outbox: TransferOutbox,
        artifact_dir: Path,
        collector_node_id: str,
        collector_key_id: str,
        collector_version: str,
        database_path: Path = DEFAULT_DATABASE,
        media_root: Path = DEFAULT_MEDIA_ROOT,
        state_path: Path | None = None,
        interval_seconds: int = 15,
        enabled: bool = True,
    ) -> None:
        self.outbox = outbox
        self.artifact_dir = Path(artifact_dir)
        self.collector_node_id = collector_node_id
        self.collector_key_id = collector_key_id
        self.collector_version = collector_version
        self.database_path = Path(database_path)
        self.media_root = Path(media_root)
        self.state_path = Path(state_path or self.artifact_dir.parent / "model-mr-bridge-state.json")
        self.interval_seconds = max(5, min(int(interval_seconds), 3600))
        self.enabled = bool(enabled)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state = self._load_state()
        self._last_run_at = ""
        self._last_error_code = ""
        self._last_enqueued = 0

    @classmethod
    def from_environment(
        cls,
        *,
        outbox: TransferOutbox,
        artifact_dir: Path,
        collector_node_id: str,
        collector_key_id: str,
        collector_version: str,
    ) -> "ModelDownloaderBridge":
        database = Path(os.getenv("MODEL_DOWNLOADER_DATABASE", str(DEFAULT_DATABASE)))
        media_root = Path(os.getenv("MODEL_DOWNLOADER_MEDIA_ROOT", str(DEFAULT_MEDIA_ROOT)))
        return cls(
            outbox=outbox,
            artifact_dir=artifact_dir,
            collector_node_id=collector_node_id,
            collector_key_id=collector_key_id,
            collector_version=collector_version,
            database_path=database,
            media_root=media_root,
            state_path=Path(
                os.getenv(
                    "MODEL_DOWNLOADER_BRIDGE_STATE_PATH",
                    str(Path(artifact_dir).parent / "model-mr-bridge-state.json"),
                )
            ),
            interval_seconds=int(os.getenv("MODEL_DOWNLOADER_BRIDGE_INTERVAL_SECONDS", "15")),
            enabled=_enabled(
                os.getenv("MODEL_DOWNLOADER_BRIDGE_ENABLED"),
                _is_readable_file(database),
            ),
        )

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="model-downloader-transfer-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "database_available": _is_readable_file(self.database_path),
            "last_run_at": self._last_run_at,
            "last_error_code": self._last_error_code,
            "last_enqueued": self._last_enqueued,
            "interval_seconds": self.interval_seconds,
        }

    def scan_once(self) -> dict[str, int]:
        if not self.enabled:
            return {"enqueued": 0, "unchanged": 0, "skipped": 0}
        if not self._lock.acquire(blocking=False):
            return {"enqueued": 0, "unchanged": 0, "skipped": 0}
        try:
            if not self.database_path.is_file():
                raise FileNotFoundError("model_downloader_database_missing")
            enqueued = unchanged = skipped = 0
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT video_id, creator, title, source_url, published_at,
                           discovered_at, downloaded_at, file_path, file_size,
                           duration_seconds, download_status,
                           comments_collected_at, comment_count, updated_at
                    FROM videos
                    WHERE file_path IS NOT NULL AND TRIM(file_path) != ''
                      AND download_status IN ('downloaded','repair_requested','repair_failed')
                    ORDER BY published_at ASC, video_id ASC
                    """
                ).fetchall()
                for raw in rows:
                    row = dict(raw)
                    source_id = str(row.get("video_id") or "").strip()
                    try:
                        signature = self._signature(row)
                    except (OSError, ValueError):
                        skipped += 1
                        continue
                    previous = self._state.get(source_id)
                    if isinstance(previous, dict) and previous.get("signature") == signature:
                        unchanged += 1
                        continue
                    try:
                        queued = self._enqueue(connection, row)
                    except (OSError, ValueError, sqlite3.Error):
                        skipped += 1
                        continue
                    self._state[source_id] = {
                        "signature": signature,
                        "transfer_id": str(queued.get("transfer_id") or ""),
                        "queued_at": datetime.now(UTC).isoformat(),
                    }
                    self._save_state()
                    enqueued += int(queued.get("action") in {"inserted", "duplicate"})
            self._last_run_at = datetime.now(UTC).isoformat()
            self._last_error_code = ""
            self._last_enqueued = enqueued
            return {"enqueued": enqueued, "unchanged": unchanged, "skipped": skipped}
        finally:
            self._lock.release()

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _safe_media_path(self, value: object) -> Path:
        root = self.media_root.resolve(strict=True)
        target = Path(str(value or "")).resolve(strict=True)
        if target != root and root not in target.parents:
            raise ValueError("model_downloader_media_outside_root")
        if not target.is_file():
            raise ValueError("model_downloader_media_missing")
        return target

    def _signature(self, row: Mapping[str, Any]) -> str:
        path = self._safe_media_path(row.get("file_path"))
        stat = path.stat()
        value = "\n".join(
            str(item or "")
            for item in (
                row.get("video_id"),
                row.get("title"),
                row.get("source_url"),
                row.get("published_at"),
                stat.st_size,
                stat.st_mtime_ns,
                row.get("comments_collected_at"),
                row.get("comment_count"),
                row.get("updated_at"),
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _enqueue(self, connection: sqlite3.Connection, row: Mapping[str, Any]) -> dict[str, Any]:
        source_id = str(row.get("video_id") or "").strip()
        if not source_id:
            raise ValueError("model_downloader_work_id_missing")
        captured_at = _iso(row.get("updated_at"), fallback=datetime.now(UTC).isoformat())
        comments = self._comments(connection, source_id, captured_at)
        bundle_bytes, bundle = build_comment_bundle(comments)
        comment_path = self.artifact_dir / "model-mr" / f"comments-{bundle['sha256']}.ndjson.gz"
        self._atomic_bytes(comment_path, bundle_bytes)

        source_media = self._safe_media_path(row.get("file_path"))
        media_sha = sha256_file(source_media)
        media_path = self.artifact_dir / "model-mr" / f"video-{media_sha}.mp4"
        self._stage_media(source_media, media_path, media_sha)
        media_size = media_path.stat().st_size
        media_id = media_sha
        reservation = self.outbox.reserve(
            node_id=self.collector_node_id,
            creator_id=MODEL_MR_TRANSFER_CREATOR_ID,
            platform="douyin",
            source_work_id=source_id,
        )
        manifest = new_manifest(
            collector_node_id=self.collector_node_id,
            collector_key_id=self.collector_key_id,
            collector_version=self.collector_version,
            source_sequence=reservation.source_sequence,
            creator={
                "creator_id": MODEL_MR_TRANSFER_CREATOR_ID,
                "display_name": "模型先生",
                "platform": "douyin",
                "platform_user_id": "model-mr",
            },
            work={
                "platform": "douyin",
                "source_work_id": source_id,
                "work_type": "video",
                "title": str(row.get("title") or "")[:1000],
                "description": "",
                "source_url": str(row.get("source_url") or f"https://www.douyin.com/video/{source_id}"),
                "cover_url": "",
                "published_at": _iso(row.get("published_at")) or None,
            },
            work_revision=reservation.work_revision,
            media=[
                {
                    "media_id": media_id,
                    "role": "video",
                    "filename": f"model-mr-{source_id}.mp4",
                    "mime_type": "video/mp4",
                    "size_bytes": media_size,
                    "sha256": media_sha,
                    "ordinal": 0,
                }
            ],
            comment_snapshot={
                "snapshot_id": hashlib.sha256(f"{source_id}:{bundle['sha256']}".encode()).hexdigest(),
                "captured_at": captured_at,
                "complete": len(comments) >= int(row.get("comment_count") or 0),
                "expected_total": max(len(comments), int(row.get("comment_count") or 0)),
                "captured_count": len(comments),
                "top_level_count": sum(1 for item in comments if not item["parent_source_comment_id"]),
                "reply_groups": 0,
                "reply_groups_incomplete": 0,
                "missing_replies": 0,
                "orphan_replies": 0,
                "rules_version": "model-downloader-comments/v1",
                "bundle": bundle,
            },
            captured_at=captured_at,
        )
        return self.outbox.enqueue(
            manifest,
            artifacts=[
                {
                    "artifact_id": media_id,
                    "artifact_kind": "media",
                    "local_path": str(media_path),
                    "size_bytes": media_size,
                    "sha256": media_sha,
                    "mime_type": "video/mp4",
                },
                {
                    "artifact_id": bundle["bundle_id"],
                    "artifact_kind": "comment_bundle",
                    "local_path": str(comment_path),
                    "size_bytes": len(bundle_bytes),
                    "sha256": bundle["sha256"],
                    "mime_type": "application/gzip",
                },
            ],
        )

    @staticmethod
    def _comments(connection: sqlite3.Connection, video_id: str, captured_at: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT comment_id, parent_comment_id, author_name, text, created_at,
                   digg_count, reply_count, ip_label, is_creator,
                   is_author_digged, reply_to_comment_id, reply_to_user_name,
                   label_text, collected_at
            FROM comments WHERE video_id=?
            ORDER BY created_at ASC, comment_id ASC
            """,
            (video_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for index, raw in enumerate(rows):
            row = dict(raw)
            comment_id = str(row.get("comment_id") or "").strip()
            text = _wire_text(row.get("text"), 20_000)
            if not comment_id or not text:
                continue
            parent = str(row.get("parent_comment_id") or "").strip()
            is_creator = bool(row.get("is_creator"))
            result.append(
                {
                    "source_comment_id": comment_id,
                    "parent_source_comment_id": parent,
                    "root_source_comment_id": parent or comment_id,
                    "reply_to_comment_id": str(row.get("reply_to_comment_id") or ""),
                    "author": _wire_text(row.get("author_name"), 160),
                    "is_creator": is_creator,
                    "text": text,
                    "like_count": max(0, int(row.get("digg_count") or 0)),
                    "reply_count": max(0, int(row.get("reply_count") or 0)),
                    "published_at": _iso(row.get("created_at")) or None,
                    "captured_at": _iso(row.get("collected_at"), fallback=captured_at),
                    "kind": (
                        "author_reply"
                        if is_creator and parent
                        else "author_comment"
                        if is_creator
                        else "user_reply"
                        if parent
                        else "user_comment"
                    ),
                    "section": "author_interaction" if is_creator else "fan_comment",
                    "sentiment": "neutral",
                    "risk_level": "normal",
                    "author_liked": (
                        None
                        if row.get("is_author_digged") is None
                        else bool(row.get("is_author_digged"))
                    ),
                    "low_value": False,
                    "ip_label": _wire_text(row.get("ip_label"), 80),
                    "public_label": _wire_text(row.get("label_text"), 160),
                    "actual_reply_user": _wire_text(row.get("reply_to_user_name"), 160),
                    "display_order": index,
                }
            )
        return result

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            if path.stat().st_size == len(content) and sha256_file(path) == hashlib.sha256(content).hexdigest():
                return
            raise ValueError("staged_comment_bundle_conflict")
        handle, name = tempfile.mkstemp(prefix=".model-mr-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _stage_media(source: Path, destination: Path, expected_sha: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            if sha256_file(destination) != expected_sha:
                raise ValueError("staged_media_conflict")
            return
        try:
            os.link(source, destination)
            if sha256_file(destination) != expected_sha:
                destination.unlink(missing_ok=True)
                raise ValueError("staged_media_digest_mismatch")
        except OSError:
            handle, name = tempfile.mkstemp(prefix=".model-mr-video-", dir=destination.parent)
            os.close(handle)
            temporary = Path(name)
            try:
                shutil.copyfile(source, temporary)
                if sha256_file(temporary) != expected_sha:
                    raise ValueError("staged_media_digest_mismatch")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save_state(self) -> None:
        payload = json.dumps(self._state, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        self._atomic_replace(self.state_path, payload)

    @staticmethod
    def _atomic_replace(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=f".{path.stem}-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                self._last_error_code = "model_downloader_bridge_scan_failed"
                self._last_run_at = datetime.now(UTC).isoformat()
            self._stop.wait(self.interval_seconds)


__all__ = ["MODEL_MR_TRANSFER_CREATOR_ID", "ModelDownloaderBridge"]
