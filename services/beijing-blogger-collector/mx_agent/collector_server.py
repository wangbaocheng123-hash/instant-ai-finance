from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import os
import re
import signal
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .collector_export import CollectorContentReadyAdapter
from .creators import CreatorRegistry, CreatorSyncManager
from .roles import ApplicationRole, require_role
from .settings import CONFIG_DIR, DATA_DIR, ROOT_DIR, load_settings
from .storage import Storage
from .single_video import VideoLinkError, normalize_video_link, resolve_video_link
from .model_downloader_bridge import ModelDownloaderBridge
from .transfer_outbox import TransferOutbox
from .transfer_sender import HTTPSCollectorTransport, TransferSender


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 18797
MAX_REQUEST_BODY_BYTES = 16 * 1024
SAFE_TRANSFER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
COLLECTOR_WEB_DIR = ROOT_DIR / "collector_web"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/hub": ("hub.html", "text/html; charset=utf-8"),
    "/hub/": ("hub.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/hub.css": ("hub.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json; charset=utf-8"),
    "/service-worker.js": ("service-worker.js", "text/javascript; charset=utf-8"),
    "/app-icon.png": ("north-pole-collector-icon-192.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/favicon-32.png": ("favicon-32.png", "image/png"),
    "/north-pole-collector-icon-1024.png": ("north-pole-collector-icon-1024.png", "image/png"),
    "/north-pole-collector-icon-192.png": ("north-pole-collector-icon-192.png", "image/png"),
    "/north-pole-collector-icon-512.png": ("north-pole-collector-icon-512.png", "image/png"),
}
ROOT_STATIC_FILES: dict[str, tuple[Path, str]] = {}
ASSET_ROUTE = re.compile(r"^/api/collector/assets/([1-9][0-9]*)/content$")
WORK_ROUTE = re.compile(r"^/api/collector/works/([1-9][0-9]*)$")
CREATOR_SETTINGS_ROUTE = re.compile(
    r"^/api/collector/creators/([A-Za-z0-9_-]{1,80})/settings$"
)
CREATOR_INPUT_FIELDS = {
    "name",
    "profile_url",
    "history_limit",
    "comments_enabled",
    "comment_limit",
    "comment_tracking_hours",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _runtime_path(environment_name: str, default: Path) -> Path:
    configured = str(os.getenv(environment_name, "") or "").strip()
    return Path(configured or default).expanduser().resolve()


def _bounded_integer(environment_name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(environment_name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _query_integer(
    values: Mapping[str, list[str]],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int((values.get(name) or [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_text(value: Any, limit: int = 4000) -> str:
    cleaned = "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value or ""))
        if unicodedata.category(character) not in {"Cc", "Cf"}
        or character in {"\n", "\t"}
    )
    return cleaned.strip()[:limit]


def _creator_name_key(value: Any) -> str:
    return re.sub(r"\s+", " ", _safe_text(value, 160)).strip().casefold()


def _strip_collector_prefix(path: str) -> str:
    if path == "/collector":
        return "/"
    if path.startswith("/collector/"):
        return path[len("/collector") :] or "/"
    return path


def _collector_version() -> str:
    configured = str(os.getenv("BLOGGER_AGENT_COLLECTOR_VERSION", "") or "").strip()
    if configured:
        return configured[:64]
    try:
        version = (ROOT_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = "unknown"
    return (version or "unknown")[:64]


def _deployment_version() -> dict[str, str]:
    payload = {
        "service": "blogger-collector",
        "status": "ok",
        "version": _collector_version(),
        "repository_revision": "development",
        "deployed_time": "",
    }
    try:
        value = json.loads((ROOT_DIR / "DEPLOYMENT.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return payload
    revision = str(value.get("repository_revision") or "").strip().lower()
    deployed_time = str(value.get("deployed_time") or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision):
        payload["repository_revision"] = revision
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", deployed_time):
        payload["deployed_time"] = deployed_time
    return payload


def _transfer_secret() -> bytes:
    """Load the HMAC secret without ever formatting it into diagnostics.

    Hex is preferred because its byte length is unambiguous. The legacy text
    variable remains accepted so an existing Git-external environment file can
    be upgraded without an abrupt credential rotation.
    """

    hexadecimal = str(os.getenv("BLOGGER_AGENT_TRANSFER_SECRET_HEX", "") or "").strip()
    if hexadecimal:
        if len(hexadecimal) % 2 or not re.fullmatch(r"[0-9A-Fa-f]+", hexadecimal):
            return b""
        try:
            return bytes.fromhex(hexadecimal)
        except ValueError:
            return b""
    raw = os.getenv("BLOGGER_AGENT_TRANSFER_SECRET", "")
    return str(raw or "").encode("utf-8")


def _valid_https_root(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path in {"", "/"}
    )


@dataclass(frozen=True)
class CollectorConfiguration:
    media_dir: Path
    artifact_dir: Path
    outbox_path: Path
    creators_path: Path
    singapore_base_url: str
    collector_node_id: str
    transfer_key_id: str
    transfer_secret: bytes = field(repr=False)
    collector_version: str = "unknown"
    sender_interval_seconds: float = 15.0
    sender_batch_size: int = 10
    outbox_lease_seconds: int = 30 * 60

    @classmethod
    def from_environment(cls) -> "CollectorConfiguration":
        outbox_path = _runtime_path(
            "BLOGGER_AGENT_OUTBOX_PATH",
            DATA_DIR.parent / "outbox" / "transfer.sqlite3",
        )
        return cls(
            media_dir=_runtime_path(
                "BLOGGER_AGENT_MEDIA_DIR",
                DATA_DIR.parent / "media",
            ),
            artifact_dir=_runtime_path(
                "BLOGGER_AGENT_OUTBOX_ARTIFACT_DIR",
                outbox_path.parent / "artifacts",
            ),
            outbox_path=outbox_path,
            creators_path=_runtime_path(
                "BLOGGER_AGENT_CREATORS_PATH",
                CONFIG_DIR / "creators.json",
            ),
            singapore_base_url=str(
                os.getenv("BLOGGER_AGENT_SINGAPORE_BASE_URL", "") or ""
            ).strip(),
            collector_node_id=str(
                os.getenv("BLOGGER_AGENT_COLLECTOR_NODE_ID", "") or ""
            ).strip(),
            transfer_key_id=str(
                os.getenv("BLOGGER_AGENT_TRANSFER_KEY_ID", "") or ""
            ).strip(),
            transfer_secret=_transfer_secret(),
            collector_version=_collector_version(),
            sender_interval_seconds=float(
                _bounded_integer(
                    "BLOGGER_AGENT_SENDER_INTERVAL_SECONDS", 15, 1, 3600
                )
            ),
            sender_batch_size=_bounded_integer(
                "BLOGGER_AGENT_SENDER_BATCH_SIZE", 10, 1, 1000
            ),
            outbox_lease_seconds=_bounded_integer(
                "BLOGGER_AGENT_OUTBOX_LEASE_SECONDS", 1800, 30, 86400
            ),
        )

    def prepare_directories(self) -> None:
        # Both directories must exist before TransferOutbox resolves its
        # allowlist with strict=True.
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def transfer_configuration_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not _valid_https_root(self.singapore_base_url):
            issues.append("https_endpoint")
        if not SAFE_TRANSFER_ID.fullmatch(self.collector_node_id):
            issues.append("collector_node_id")
        if not SAFE_TRANSFER_ID.fullmatch(self.transfer_key_id):
            issues.append("transfer_key_id")
        if len(self.transfer_secret) < 32:
            issues.append("hmac_secret_32_bytes")
        return tuple(issues)


class CollectorRuntime:
    """Own the Beijing-only collector, outbox, and sender lifecycle."""

    def __init__(
        self,
        *,
        configuration: CollectorConfiguration,
        storage: Any,
        registry: Any,
        outbox: Any,
        adapter: Any,
        sync_manager: Any,
        sender: Any | None,
        model_downloader_bridge: Any | None = None,
        transfer_issues: tuple[str, ...] = (),
    ) -> None:
        self.configuration = configuration
        self.storage = storage
        self.registry = registry
        self.outbox = outbox
        self.adapter = adapter
        self.sync_manager = sync_manager
        self.sender = sender
        self.model_downloader_bridge = model_downloader_bridge
        self.transfer_issues = tuple(transfer_issues)
        self._state_lock = threading.RLock()
        self._sender_stop = threading.Event()
        self._sender_wake = threading.Event()
        self._sender_thread: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._sender_state = "waiting_config" if self.transfer_issues else "ready"
        self._sender_last_run_at = ""
        self._sender_last_error_code = ""
        self._sender_last_processed = 0
        self._sender_last_failed = 0

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
            if self._closed:
                raise RuntimeError("collector runtime 已关闭。")
            self._sender_stop.clear()
            self._sender_wake.clear()
            self._sender_thread = threading.Thread(
                target=self._sender_loop,
                name="collector-transfer-sender",
                daemon=True,
            )
            self._sender_thread.start()
            self._started = True
        try:
            self.sync_manager.start()
            if self.model_downloader_bridge is not None:
                self.model_downloader_bridge.start()
        except Exception:
            self.close()
            raise

    def _sender_loop(self) -> None:
        while not self._sender_stop.is_set():
            if self.sender is None:
                with self._state_lock:
                    self._sender_state = "waiting_config"
                self._wait_for_sender()
                continue
            with self._state_lock:
                self._sender_state = "sending"
            try:
                results = self.sender.run_once(
                    limit=self.configuration.sender_batch_size
                )
                failed = sum(
                    1
                    for result in results
                    if str((result or {}).get("status") or "")
                    in {"retry_wait", "dead_letter", "sender_error"}
                )
                with self._state_lock:
                    self._sender_state = "ready"
                    self._sender_last_run_at = _now_iso()
                    self._sender_last_error_code = ""
                    self._sender_last_processed = len(results)
                    self._sender_last_failed = failed
            except Exception:
                # Sender exceptions can include local paths or endpoint details.
                # Keep only a stable, non-secret diagnostic code here.
                with self._state_lock:
                    self._sender_state = "sender_error"
                    self._sender_last_run_at = _now_iso()
                    self._sender_last_error_code = "sender_run_failed"
                    self._sender_last_processed = 0
                    self._sender_last_failed = 0
            self._wait_for_sender()

    def _wait_for_sender(self) -> None:
        self._sender_wake.wait(self.configuration.sender_interval_seconds)
        self._sender_wake.clear()

    def status(self) -> dict[str, Any]:
        try:
            creator_count = len(self.registry.list())
        except Exception:
            creator_count = 0
        try:
            raw_rotation = self.sync_manager.rotation_status()
        except Exception:
            raw_rotation = {}
        rotation = {
            "running": bool(raw_rotation.get("running")),
            "automatic": bool(raw_rotation.get("automatic")),
            "single_browser": bool(raw_rotation.get("single_browser", True)),
            "eligible_count": max(0, int(raw_rotation.get("eligible_count") or 0)),
            "slot_seconds": max(0, int(raw_rotation.get("slot_seconds") or 0)),
        }
        with self._state_lock:
            transfer = {
                "state": self._sender_state,
                "configured": not self.transfer_issues,
                "waiting_for": list(self.transfer_issues),
                "last_run_at": self._sender_last_run_at,
                "last_error_code": self._sender_last_error_code,
                "last_processed": self._sender_last_processed,
                "last_failed": self._sender_last_failed,
            }
            running = self._started and not self._closed
        return {
            "role": ApplicationRole.COLLECTOR.value,
            "version": self.configuration.collector_version,
            "service": "running" if running else "stopped",
            "listen_scope": "loopback",
            "collection_mode": "manual_only",
            "automatic_collection": False,
            "creator_count": creator_count,
            "scheduler": rotation,
            "transfer": transfer,
            "model_downloader_bridge": (
                self.model_downloader_bridge.status()
                if self.model_downloader_bridge is not None
                else {"enabled": False, "running": False, "database_available": False}
            ),
        }

    def health(self) -> dict[str, Any]:
        status = self.status()
        return {
            "status": "ok" if status["service"] == "running" else "stopped",
            "role": status["role"],
            "transfer_state": status["transfer"]["state"],
        }

    def trigger_once(
        self,
        creator_id: str,
        *,
        force_comments: bool = False,
        videos_only: bool = False,
        video_url: str = "",
    ) -> dict[str, Any]:
        runtime_key = str(creator_id or "").strip()
        if not runtime_key or len(runtime_key) > 80:
            raise ValueError("creator_id 无效。")
        creator = self.registry.get(runtime_key)
        safe_runtime_key = str(creator.get("id") or "").strip()
        options = {"force_comments": bool(force_comments), "videos_only": bool(videos_only)}
        if video_url:
            options["video_url"] = normalize_video_link(video_url)
        self.sync_manager.run_now(safe_runtime_key, **options)
        return {
            "accepted": True,
            "creator_id": safe_runtime_key,
            "state": "queued",
        }

    @staticmethod
    def _creator_settings_payload(creator: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": _safe_text(creator.get("id"), 80),
            "name": _safe_text(creator.get("name"), 120),
            "platform": _safe_text(creator.get("platform"), 40),
            "profile_url": _safe_text(creator.get("profile_url"), 1000),
            "collection_mode": "manual_only",
            "history_limit": max(
                1, min(1000, int(creator.get("creator_sync_history_limit") or 1))
            ),
            "comments_enabled": bool(creator.get("creator_comments_enabled")),
            "comment_limit": max(
                20, min(50000, int(creator.get("creator_comment_limit") or 20))
            ),
            "comment_tracking_hours": max(
                1,
                min(720, int(creator.get("creator_comment_tracking_hours") or 1)),
            ),
        }

    @staticmethod
    def _registry_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "creator_sync_mode": "count",
            "creator_sync_enabled": False,
        }
        if "name" in payload:
            result["name"] = payload.get("name")
        if "profile_url" in payload:
            result["profile_url"] = payload.get("profile_url")
        if "history_limit" in payload:
            result["creator_sync_history_limit"] = payload.get("history_limit")
        if "comments_enabled" in payload:
            result["creator_comments_enabled"] = payload.get("comments_enabled")
        if "comment_limit" in payload:
            result["creator_comment_limit"] = payload.get("comment_limit")
        if "comment_tracking_hours" in payload:
            result["creator_comment_tracking_hours"] = payload.get(
                "comment_tracking_hours"
            )
        return result

    def creator_settings(self, creator_id: str) -> dict[str, Any]:
        return self._creator_settings_payload(self.registry.get(creator_id))

    def create_creator(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        creator = self.registry.create(self._registry_payload(payload))
        self.sync_manager.ensure(str(creator.get("id") or ""))
        return self._creator_settings_payload(creator)

    def update_creator(
        self,
        creator_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        creator, old_name = self.registry.update(
            creator_id,
            self._registry_payload(payload),
        )
        new_name = str(creator.get("name") or "")
        if old_name != new_name:
            self.storage.rename_video_author(old_name, new_name)
        self.sync_manager.ensure(creator_id)
        return self._creator_settings_payload(creator)

    @staticmethod
    def _work_summary(item: Mapping[str, Any]) -> dict[str, Any]:
        asset_id = max(0, int(item.get("primary_asset_id") or 0))
        asset_type = _safe_text(item.get("primary_asset_type"), 40)
        return {
            "id": max(0, int(item.get("id") or 0)),
            "platform": _safe_text(item.get("source"), 40),
            "source_work_id": _safe_text(item.get("source_video_id"), 100),
            "creator": _safe_text(item.get("author"), 120),
            "title": _safe_text(item.get("active_title") or item.get("title"), 500),
            "description": _safe_text(item.get("description"), 2000),
            "published_at": _safe_text(item.get("published_at"), 80),
            "discovered_at": _safe_text(item.get("discovered_at"), 80),
            "status": _safe_text(item.get("status"), 40),
            "asset_count": max(0, int(item.get("asset_count") or 0)),
            "comment_count": max(0, int(item.get("comment_count") or 0)),
            "primary_asset": (
                {
                    "id": asset_id,
                    "type": asset_type,
                    "mime_type": _safe_text(item.get("primary_asset_mime"), 100),
                    "size_bytes": max(0, int(item.get("primary_asset_size") or 0)),
                    "content_url": f"/api/collector/assets/{asset_id}/content",
                }
                if asset_id
                else None
            ),
        }

    def creator_summaries(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for creator in self.registry.list():
            creator_id = _safe_text(creator.get("id"), 80)
            try:
                sync = self.sync_manager.status(creator_id)
            except Exception:
                sync = {}
            busy = bool(sync.get("busy"))
            queued = bool(sync.get("queued"))
            has_error = bool(_safe_text(sync.get("last_error"), 1))
            if busy or queued:
                safe_message = "采集任务正在运行。"
            elif has_error:
                safe_message = "上次采集未正常完成，请查看脱敏诊断状态。"
            elif sync.get("last_finished_at"):
                safe_message = "上次采集已经完成，当前等待手动操作。"
            else:
                safe_message = "当前没有采集任务，等待手动操作。"
            result.append(
                {
                    "id": creator_id,
                    "name": _safe_text(creator.get("name"), 120),
                    "platform": _safe_text(creator.get("platform"), 40),
                    "profile_configured": bool(creator.get("profile_url")),
                    "history_limit": max(
                        1, int(creator.get("creator_sync_history_limit") or 1)
                    ),
                    "comments_enabled": bool(creator.get("creator_comments_enabled")),
                    "sync": {
                        "busy": busy,
                        "queued": queued,
                        "phase": _safe_text(sync.get("phase"), 40),
                        "message": safe_message,
                        "last_started_at": _safe_text(sync.get("last_started_at"), 80),
                        "last_finished_at": _safe_text(sync.get("last_finished_at"), 80),
                        "has_error": has_error,
                        "works_seen": max(0, int(sync.get("works_seen") or 0)),
                        "works_selected": max(0, int(sync.get("works_selected") or 0)),
                        "new_downloads": max(0, int(sync.get("new_downloads") or 0)),
                        "comments_created": max(0, int(sync.get("comments_created") or 0)),
                        "comments_updated": max(0, int(sync.get("comments_updated") or 0)),
                    },
                }
            )
        return result

    def dashboard(self) -> dict[str, Any]:
        counts = self.storage.counts()
        try:
            transfer_counts = self.outbox.status_counts()
            recent_transfers = self.outbox.list_recent(limit=30)
        except Exception:
            transfer_counts = {}
            recent_transfers = []
        transfers = [
            {
                "transfer_id": _safe_text(item.get("transfer_id"), 260),
                "creator_id": _safe_text(item.get("creator_id"), 100),
                "source_work_id": _safe_text(item.get("source_work_id"), 100),
                "status": _safe_text(item.get("status"), 40),
                "attempt_count": max(0, int(item.get("attempt_count") or 0)),
                "last_error_code": _safe_text(item.get("last_error_code"), 100),
                "updated_at": _safe_text(item.get("updated_at"), 80),
                "delivered_at": _safe_text(item.get("delivered_at"), 80),
            }
            for item in recent_transfers
        ]
        return {
            "status": self.status(),
            "counts": {
                "works": max(0, int(counts.get("videos") or 0)),
                "assets": max(0, int(counts.get("assets") or 0)),
                "comments": max(0, int(counts.get("comments") or 0)),
            },
            "creators": self.creator_summaries(),
            "transfer_counts": {
                _safe_text(key, 40): max(0, int(value or 0))
                for key, value in transfer_counts.items()
            },
            "recent_transfers": transfers,
        }

    def list_works(self, *, creator_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        requested_limit = max(1, min(500, int(limit)))
        target_creator_key = ""
        if creator_id:
            target_creator_key = _creator_name_key(
                self.registry.get(creator_id).get("name")
            )
        items = self.storage.list_videos(limit=500, account=None)
        if target_creator_key:
            items = [
                item
                for item in items
                if _creator_name_key(item.get("author")) == target_creator_key
            ]
        return [
            self._work_summary(item)
            for item in items[:requested_limit]
        ]

    def work_detail(self, video_id: int) -> dict[str, Any]:
        detail = self.storage.get_video_detail(int(video_id))
        if not detail:
            raise ValueError("work_not_found")
        video = dict(detail.get("video") or {})
        assets = []
        primary_asset = None
        for asset in detail.get("assets") or []:
            asset_id = max(0, int(asset.get("id") or 0))
            safe_asset = {
                "id": asset_id,
                "type": _safe_text(asset.get("asset_type"), 40),
                "mime_type": _safe_text(asset.get("mime_type"), 100),
                "size_bytes": max(0, int(asset.get("size_bytes") or 0)),
                "status": _safe_text(asset.get("status"), 40),
                "content_url": f"/api/collector/assets/{asset_id}/content",
            }
            assets.append(safe_asset)
            if primary_asset is None and (
                safe_asset["mime_type"].startswith(("video/", "image/"))
                or safe_asset["type"] in {"video", "image"}
            ):
                primary_asset = safe_asset
        video.update(
            {
                "comment_count": int(detail.get("comment_total") or 0),
                "asset_count": len(assets),
                "primary_asset_id": (primary_asset or {}).get("id", 0),
                "primary_asset_type": (primary_asset or {}).get("type", ""),
                "primary_asset_mime": (primary_asset or {}).get("mime_type", ""),
                "primary_asset_size": (primary_asset or {}).get("size_bytes", 0),
            }
        )
        comments = []
        for comment in detail.get("comments") or []:
            raw = comment.get("raw_json") if isinstance(comment.get("raw_json"), Mapping) else {}
            comments.append(
                {
                    "id": max(0, int(comment.get("id") or 0)),
                    "author": _safe_text(comment.get("author"), 160),
                    "text": _safe_text(comment.get("text"), 4000),
                    "like_count": max(0, int(comment.get("like_count") or 0)),
                    "reply_count": max(0, int(comment.get("reply_count") or 0)),
                    "sentiment": _safe_text(comment.get("sentiment"), 40),
                    "risk_level": _safe_text(comment.get("risk_level"), 40),
                    "published_at": _safe_text(comment.get("published_at"), 80),
                    "captured_at": _safe_text(comment.get("captured_at"), 80),
                    "kind": _safe_text(raw.get("kind"), 60),
                    "author_liked": raw.get("author_liked") is True,
                    "reply_depth": max(0, int(raw.get("reply_depth") or 0)),
                }
            )
        return {
            "work": self._work_summary(video),
            "assets": assets,
            "comments": comments,
        }

    def asset_file(self, asset_id: int) -> tuple[Path, str]:
        asset = self.storage.get_asset(int(asset_id))
        if not asset:
            raise ValueError("asset_not_found")
        root = self.configuration.media_dir.resolve(strict=True)
        path = Path(str(asset.get("local_path") or "")).resolve(strict=True)
        if path == root or root not in path.parents or not path.is_file():
            raise ValueError("asset_not_found")
        mime_type = _safe_text(asset.get("mime_type"), 100)
        if not mime_type:
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return path, mime_type

    def retry_transfers(self) -> dict[str, Any]:
        expedited = int(self.outbox.expedite_retries())
        self._sender_wake.set()
        return {"accepted": True, "expedited": max(0, expedited)}

    def close(self, *, join_timeout: float = 10.0) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._sender_stop.set()
            self._sender_wake.set()
            sender_thread = self._sender_thread
        if sender_thread and sender_thread is not threading.current_thread():
            sender_thread.join(timeout=max(0.0, float(join_timeout)))
        if self.model_downloader_bridge is not None:
            self.model_downloader_bridge.stop(timeout=max(0.0, float(join_timeout)))
        self._stop_sync_manager(join_timeout=max(0.0, float(join_timeout)))
        with self._state_lock:
            self._sender_state = (
                "waiting_config" if self.transfer_issues else "stopped"
            )

    def _stop_sync_manager(self, *, join_timeout: float) -> None:
        public_stop = getattr(self.sync_manager, "stop", None)
        if callable(public_stop):
            public_stop()
            return

        # CreatorSyncManager predates a public aggregate stop method. Keep the
        # compatibility cleanup local to this dedicated process boundary.
        rotation_stop = getattr(self.sync_manager, "_rotation_stop", None)
        rotation_wake = getattr(self.sync_manager, "_rotation_wake", None)
        if rotation_stop is not None:
            rotation_stop.set()
        if rotation_wake is not None:
            rotation_wake.set()
        manager_lock = getattr(self.sync_manager, "_lock", None)
        if manager_lock is not None:
            with manager_lock:
                services = list(
                    getattr(self.sync_manager, "_services", {}).values()
                )
        else:
            services = list(getattr(self.sync_manager, "_services", {}).values())
        for service in services:
            stop = getattr(service, "stop", None)
            if callable(stop):
                stop()
        threads = [getattr(self.sync_manager, "_rotation_thread", None)]
        threads.extend(getattr(service, "_thread", None) for service in services)
        for thread in threads:
            if thread and thread is not threading.current_thread():
                thread.join(timeout=join_timeout)


def build_collector_runtime(
    configuration: CollectorConfiguration | None = None,
) -> CollectorRuntime:
    require_role(ApplicationRole.COLLECTOR)
    config = configuration or CollectorConfiguration.from_environment()
    config.prepare_directories()

    settings = load_settings()
    storage = Storage(settings.database_path)
    registry = CreatorRegistry(settings, path=config.creators_path)
    outbox = TransferOutbox(
        config.outbox_path,
        allowed_artifact_roots=(config.media_dir, config.artifact_dir),
        lease_seconds=config.outbox_lease_seconds,
    )
    adapter = CollectorContentReadyAdapter(
        storage,
        registry,
        outbox,
        artifact_dir=config.artifact_dir,
        collector_node_id=config.collector_node_id,
        collector_key_id=config.transfer_key_id,
        collector_version=config.collector_version,
    )
    model_downloader_bridge = ModelDownloaderBridge.from_environment(
        outbox=outbox,
        artifact_dir=config.artifact_dir,
        collector_node_id=config.collector_node_id,
        collector_key_id=config.transfer_key_id,
        collector_version=config.collector_version,
    )
    sync_manager = CreatorSyncManager(
        registry,
        storage,
        execution_lock=threading.Lock(),
        on_content_ready=adapter,
        automatic_rotation=False,
    )

    transfer_issues = config.transfer_configuration_issues()
    sender: TransferSender | None = None
    if not transfer_issues:
        try:
            transport = HTTPSCollectorTransport(
                config.singapore_base_url,
                node_id=config.collector_node_id,
                key_id=config.transfer_key_id,
                secret=config.transfer_secret,
            )
        except ValueError:
            transfer_issues = ("transfer_configuration",)
        else:
            sender = TransferSender(outbox, transport)
    if transfer_issues:
        model_downloader_bridge.enabled = False

    return CollectorRuntime(
        configuration=config,
        storage=storage,
        registry=registry,
        outbox=outbox,
        adapter=adapter,
        sync_manager=sync_manager,
        sender=sender,
        model_downloader_bridge=model_downloader_bridge,
        transfer_issues=transfer_issues,
    )


class CollectorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], runtime: CollectorRuntime):
        self.runtime = runtime
        super().__init__(server_address, CollectorRequestHandler)


class CollectorRequestHandler(BaseHTTPRequestHandler):
    server_version = "BloggerCollector/1"
    sys_version = ""

    @property
    def runtime(self) -> CollectorRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Do not log request targets or bodies; the local API accepts creator IDs.
        return

    def _is_loopback_client(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _json(self, payload: Mapping[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> bool:
        descriptor = STATIC_FILES.get(path)
        root_descriptor = ROOT_STATIC_FILES.get(path)
        if descriptor is None and root_descriptor is None:
            return False
        if root_descriptor is not None:
            source_path, content_type = root_descriptor
        else:
            filename, content_type = descriptor  # type: ignore[misc]
            source_path = COLLECTOR_WEB_DIR / filename
        try:
            body = source_path.read_bytes()
        except OSError:
            self._json({"error": "management_ui_unavailable"}, HTTPStatus.NOT_FOUND)
            return True
        self.send_response(int(HTTPStatus.OK))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)
        return True

    def _stream_asset(self, asset_id: int) -> None:
        try:
            path, mime_type = self.runtime.asset_file(asset_id)
            size = path.stat().st_size
        except (OSError, ValueError):
            self._json({"error": "asset_not_found"}, HTTPStatus.NOT_FOUND)
            return
        if size < 0:
            self._json({"error": "asset_not_found"}, HTTPStatus.NOT_FOUND)
            return

        start = 0
        end = max(0, size - 1)
        response_status = HTTPStatus.OK
        range_header = str(self.headers.get("Range") or "").strip()
        if range_header:
            match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", range_header)
            if not match or (not match.group(1) and not match.group(2)):
                self.send_response(int(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE))
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            try:
                if not match.group(1):
                    suffix = int(match.group(2))
                    if suffix <= 0:
                        raise ValueError
                    start = max(0, size - suffix)
                else:
                    start = int(match.group(1))
                    if match.group(2):
                        end = int(match.group(2))
                if start >= size or start < 0 or end < start:
                    raise ValueError
                end = min(end, size - 1)
            except ValueError:
                self.send_response(int(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE))
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            response_status = HTTPStatus.PARTIAL_CONTENT

        length = 0 if size == 0 else end - start + 1
        self.send_response(int(response_status))
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if response_status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not length:
            return
        remaining = length
        try:
            with path.open("rb") as source:
                source.seek(start)
                while remaining > 0:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _reject_non_loopback(self) -> bool:
        if self._is_loopback_client():
            return False
        self._json({"error": "loopback_only"}, HTTPStatus.FORBIDDEN)
        return True

    def do_GET(self) -> None:
        if self._reject_non_loopback():
            return
        parsed = urlsplit(self.path)
        path = _strip_collector_prefix(parsed.path)
        if self._static(path):
            return
        if path == "/health":
            self._json(self.runtime.health())
            return
        if path == "/health/version":
            self._json(_deployment_version())
            return
        if path == "/api/collector/status":
            self._json(self.runtime.status())
            return
        if path == "/api/collector/dashboard":
            self._json(self.runtime.dashboard())
            return
        creator_settings_match = CREATOR_SETTINGS_ROUTE.fullmatch(path)
        if creator_settings_match:
            try:
                settings = self.runtime.creator_settings(
                    creator_settings_match.group(1)
                )
            except ValueError:
                self._json({"error": "invalid_creator"}, HTTPStatus.NOT_FOUND)
                return
            self._json(settings)
            return
        if path == "/api/collector/works":
            query = parse_qs(parsed.query, keep_blank_values=True)
            creator_id = _safe_text((query.get("creator_id") or [""])[0], 80)
            limit = _query_integer(query, "limit", 100, 1, 500)
            try:
                works = self.runtime.list_works(creator_id=creator_id, limit=limit)
            except ValueError:
                self._json({"error": "invalid_creator"}, HTTPStatus.BAD_REQUEST)
                return
            self._json({"works": works})
            return
        work_match = WORK_ROUTE.fullmatch(path)
        if work_match:
            try:
                detail = self.runtime.work_detail(int(work_match.group(1)))
            except ValueError:
                self._json({"error": "work_not_found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(detail)
            return
        asset_match = ASSET_ROUTE.fullmatch(path)
        if asset_match:
            self._stream_asset(int(asset_match.group(1)))
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self._reject_non_loopback():
            return
        path = _strip_collector_prefix(urlsplit(self.path).path)
        if path not in {
            "/api/collector/run-once",
            "/api/collector/resolve-video",
            "/api/collector/transfers/retry",
            "/api/collector/creators",
        } and not CREATOR_SETTINGS_ROUTE.fullmatch(path):
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
            self._json({"error": "json_content_type_required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        if self.headers.get("Transfer-Encoding"):
            self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if content_length < 1 or content_length > MAX_REQUEST_BODY_BYTES:
            self._json({"error": "invalid_request_size"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(payload, dict):
            self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/collector/resolve-video":
            if set(payload) != {"creator_id", "video_url"} or not all(
                isinstance(payload[key], str) for key in payload
            ):
                self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                self.runtime.registry.get(payload["creator_id"])
                result = resolve_video_link(payload["video_url"])
            except VideoLinkError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            except ValueError:
                self._json({"error": "invalid_creator"}, HTTPStatus.BAD_REQUEST)
                return
            self._json({"creator_id": payload["creator_id"], **result})
            return
        if path == "/api/collector/run-once":
            if not {"creator_id"} <= set(payload) <= {
                "creator_id",
                "force_comments",
                "videos_only",
                "video_url",
            }:
                self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
            if "video_url" in payload and (
                not isinstance(payload["video_url"], str) or not payload["video_url"].strip()
            ):
                self._json({"error": "请填写要抓取的视频链接。"}, HTTPStatus.BAD_REQUEST)
                return
            if any(
                name in payload and not isinstance(payload[name], bool)
                for name in ("force_comments", "videos_only")
            ):
                self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
        elif path == "/api/collector/transfers/retry":
            if payload:
                self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
        elif not set(payload) <= CREATOR_INPUT_FIELDS:
            self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        elif any(
            field in payload and not isinstance(payload[field], str)
            for field in ("name", "profile_url")
        ) or (
            "comments_enabled" in payload
            and not isinstance(payload["comments_enabled"], bool)
        ) or any(
            field in payload
            and (
                not isinstance(payload[field], int)
                or isinstance(payload[field], bool)
            )
            for field in (
                "history_limit",
                "comment_limit",
                "comment_tracking_hours",
            )
        ):
            self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/collector/transfers/retry":
            try:
                result = self.runtime.retry_transfers()
            except Exception:
                self._json({"error": "transfer_retry_failed"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._json(result, HTTPStatus.ACCEPTED)
            return
        if path == "/api/collector/creators":
            if not {"name", "profile_url"} <= set(payload):
                self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                result = self.runtime.create_creator(payload)
            except ValueError:
                self._json({"error": "invalid_creator_settings"}, HTTPStatus.BAD_REQUEST)
                return
            except Exception:
                self._json({"error": "creator_create_failed"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._json(result, HTTPStatus.CREATED)
            return
        creator_settings_match = CREATOR_SETTINGS_ROUTE.fullmatch(path)
        if creator_settings_match:
            if not payload:
                self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                result = self.runtime.update_creator(
                    creator_settings_match.group(1), payload
                )
            except ValueError:
                self._json({"error": "invalid_creator_settings"}, HTTPStatus.BAD_REQUEST)
                return
            except Exception:
                self._json({"error": "creator_update_failed"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._json(result)
            return
        try:
            result = self.runtime.trigger_once(
                str(payload.get("creator_id") or ""),
                force_comments=bool(payload.get("force_comments")),
                videos_only=bool(payload.get("videos_only")),
                video_url=payload.get("video_url", ""),
            )
        except VideoLinkError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError:
            self._json({"error": "invalid_creator"}, HTTPStatus.BAD_REQUEST)
            return
        except Exception:
            self._json({"error": "collector_trigger_failed"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._json(result, HTTPStatus.ACCEPTED)


def create_http_server(
    runtime: CollectorRuntime,
    *,
    host: str = LOOPBACK_HOST,
    port: int = DEFAULT_PORT,
) -> CollectorHTTPServer:
    if str(host).strip() != LOOPBACK_HOST:
        raise ValueError("collector 管理接口只允许监听 127.0.0.1。")
    if int(port) < 0 or int(port) > 65535:
        raise ValueError("端口必须在 0 到 65535 之间。")
    return CollectorHTTPServer((LOOPBACK_HOST, int(port)), runtime)


def main() -> None:
    parser = argparse.ArgumentParser(description="北京 collector 运行入口")
    parser.add_argument("--host", default=LOOPBACK_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    runtime = build_collector_runtime()
    server = create_http_server(runtime, host=args.host, port=args.port)
    runtime.start()

    def request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signal_name in ("SIGINT", "SIGTERM"):
        current_signal = getattr(signal, signal_name, None)
        if current_signal is not None:
            signal.signal(current_signal, request_shutdown)

    print(f"北京 collector 已启动：http://{LOOPBACK_HOST}:{server.server_port}")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        runtime.close()


if __name__ == "__main__":
    main()
