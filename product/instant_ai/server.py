from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .ai_provider import provider_status, queue_analysis
from .database import create_backup, initialize, run_restore_drill, seed_sources
from .paths import STATIC_ROOT, ensure_layout
from .service import (
    backfill_notifications,
    dismiss_notification,
    export_csv,
    get_item,
    list_notifications,
    list_sources,
    query_items,
    raw_evidence,
    recent_runs,
    run_collection,
    set_item_flag,
    stats,
    toggle_source,
)


HOST = "127.0.0.1"
PORT = 18765
COLLECTION_LOCK = threading.Lock()
COLLECTION_STATE: dict[str, object] = {"running": False, "last_result": None}
SCHEDULER_INTERVAL_SECONDS = 30 * 60


def _collect_in_background() -> None:
    if not COLLECTION_LOCK.acquire(blocking=False):
        return
    COLLECTION_STATE["running"] = True
    try:
        COLLECTION_STATE["last_result"] = run_collection()
    except Exception as error:  # keep the desktop service alive
        COLLECTION_STATE["last_result"] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    finally:
        COLLECTION_STATE["running"] = False
        COLLECTION_LOCK.release()


def _scheduler_loop() -> None:
    while True:
        threading.Event().wait(SCHEDULER_INTERVAL_SECONDS)
        if not COLLECTION_STATE["running"]:
            threading.Thread(target=_collect_in_background, name="instant-ai-scheduled-collector", daemon=True).start()


class InstantAIHandler(BaseHTTPRequestHandler):
    server_version = f"InstantAI/{__version__}"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        target = (STATIC_ROOT / relative).resolve()
        static_root = STATIC_ROOT.resolve()
        if static_root != target and static_root not in target.parents:
            self._not_found()
            return
        if not target.is_file():
            target = STATIC_ROOT / "index.html"
        content = target.read_bytes()
        mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if mime_type.startswith("text/") else mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._json({"error": "invalid_host"}, HTTPStatus.BAD_REQUEST)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            self._json({"ok": True, "version": __version__})
        elif path == "/api/status":
            self._json({**stats(), "collection": COLLECTION_STATE})
        elif path == "/api/items":
            self._json(
                query_items(
                    topic=query.get("topic", [""])[0],
                    query=query.get("q", [""])[0],
                    saved=query.get("saved", ["0"])[0] == "1",
                    limit=int(query.get("limit", ["100"])[0]),
                    offset=int(query.get("offset", ["0"])[0]),
                )
            )
        elif path.startswith("/api/items/"):
            try:
                item_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self._not_found()
                return
            item = get_item(item_id)
            self._json(item) if item else self._not_found()
        elif path == "/api/sources":
            self._json(list_sources())
        elif path == "/api/runs":
            self._json(recent_runs())
        elif path == "/api/notifications":
            self._json(list_notifications())
        elif path == "/api/ai/status":
            self._json(provider_status())
        elif path.startswith("/api/evidence/") and path.endswith("/raw"):
            evidence_id = path.split("/")[3]
            record = raw_evidence(evidence_id)
            if not record:
                self._not_found()
                return
            target, mime_type = record
            if not target.is_file():
                self._not_found()
                return
            content = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            safe_type = "text/plain; charset=utf-8" if (mime_type or "").startswith("text/") or "xml" in (mime_type or "") else "application/octet-stream"
            self.send_header("Content-Type", safe_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox; default-src 'none'")
            self.end_headers()
            self.wfile.write(content)
        elif path == "/api/export":
            target = export_csv()
            self._json({"ok": True, "path": str(target)})
        elif path.startswith("/api/"):
            self._not_found()
        else:
            self._serve_static(path)

    def do_POST(self) -> None:
        if not self._host_allowed() or self.headers.get("X-Instant-AI") != "1":
            self._json({"error": "request_rejected"}, HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        length = min(int(self.headers.get("Content-Length", "0") or "0"), 64 * 1024)
        payload = json.loads(self.rfile.read(length) or b"{}")

        if path == "/api/collect":
            if COLLECTION_STATE["running"]:
                self._json({"ok": True, "running": True}, HTTPStatus.ACCEPTED)
                return
            threading.Thread(target=_collect_in_background, name="instant-ai-collector", daemon=True).start()
            self._json({"ok": True, "running": True}, HTTPStatus.ACCEPTED)
            return

        if path == "/api/backup":
            target = create_backup(force=True)
            self._json({"ok": True, "path": str(target) if target else None})
            return

        if path == "/api/restore-drill":
            self._json({"ok": True, "result": run_restore_drill()})
            return

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "items"] and parts[3] in {"save", "read"}:
            try:
                item_id = int(parts[2])
            except ValueError:
                self._not_found()
                return
            field = "is_saved" if parts[3] == "save" else "is_read"
            ok = set_item_flag(item_id, field, bool(payload.get("value", True)))
            self._json({"ok": ok})
            return

        if len(parts) == 4 and parts[:2] == ["api", "items"] and parts[3] == "analyze":
            try:
                item_id = int(parts[2])
            except ValueError:
                self._not_found()
                return
            result = queue_analysis(item_id)
            self._json(result) if result else self._not_found()
            return

        if len(parts) == 4 and parts[:2] == ["api", "notifications"] and parts[3] == "dismiss":
            try:
                notification_id = int(parts[2])
            except ValueError:
                self._not_found()
                return
            self._json({"ok": dismiss_notification(notification_id)})
            return

        if len(parts) == 4 and parts[:2] == ["api", "sources"] and parts[3] == "toggle":
            try:
                source_id = int(parts[2])
            except ValueError:
                self._not_found()
                return
            ok = toggle_source(source_id, bool(payload.get("enabled", True)))
            self._json({"ok": ok})
            return

        self._not_found()


def create_server() -> ThreadingHTTPServer:
    ensure_layout()
    create_backup()
    initialize()
    seed_sources()
    backfill_notifications()
    return ThreadingHTTPServer((HOST, PORT), InstantAIHandler)


def run_server(collect_if_empty: bool = True) -> None:
    server = create_server()
    threading.Thread(target=_scheduler_loop, name="instant-ai-scheduler", daemon=True).start()
    if collect_if_empty and stats()["items"]["total"] == 0:
        threading.Thread(target=_collect_in_background, name="instant-ai-initial-collector", daemon=True).start()
    server.serve_forever(poll_interval=0.5)
