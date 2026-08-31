from __future__ import annotations

import json
import mimetypes
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .ai_provider import provider_status, queue_analysis
from .auth import AUTH
from .database import create_backup, initialize, run_restore_drill, seed_sources
from .paths import STATIC_ROOT, ensure_layout
from .service import (
    backfill_notifications,
    dismiss_notification,
    get_item,
    list_notifications,
    list_sources,
    query_hot_items,
    query_items,
    raw_evidence,
    reclassify_items,
    recent_runs,
    run_collection,
    set_item_flag,
    stats,
    toggle_source,
)
from .retention import run_retention_cleanup
from .reader_translation import translate_reader_item
from .translation import translate_items, translation_status
from .thumbnails import backfill_thumbnail_candidates, get_thumbnail
from .watch_events import list_watch_events, refresh_watch_events
from .model_mr import MODEL_MR, ModelMrUnavailable
from .blogger_http import BloggerTransferHTTP
from .blogger_library import BLOGGER_LIBRARY


HOST = "127.0.0.1"
PORT = 18765
COLLECTION_LOCK = threading.Lock()
SCHEDULER_INTERVAL_SECONDS = 5 * 60
REQUEST_READ_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_REQUESTS = 32
COLLECTION_STATE: dict[str, object] = {
    "running": False,
    "last_result": None,
    "mode": "automatic",
    "interval_seconds": SCHEDULER_INTERVAL_SECONDS,
}


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Shared HTTP server with an idle read timeout and a hard thread ceiling."""

    daemon_threads = True
    request_queue_size = MAX_CONCURRENT_REQUESTS

    def __init__(
        self,
        server_address,
        request_handler_class,
        *,
        max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS,
    ) -> None:
        self._request_slots = threading.BoundedSemaphore(max(1, int(max_concurrent_requests)))
        super().__init__(server_address, request_handler_class)

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(REQUEST_READ_TIMEOUT_SECONDS)
        return request, client_address

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            try:
                body = b'{"error":"server_overloaded"}'
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode("ascii")
                    + b"Cache-Control: no-store\r\n"
                    + b"Connection: close\r\n\r\n"
                    + body
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return

        worker = threading.Thread(
            target=self._process_request_with_slot,
            args=(request, client_address),
            name="instant-ai-http-request",
            daemon=True,
        )
        try:
            worker.start()
        except Exception:
            self._request_slots.release()
            self.shutdown_request(request)
            raise

    def _process_request_with_slot(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def _collect_in_background() -> None:
    if not COLLECTION_LOCK.acquire(blocking=False):
        return
    COLLECTION_STATE["running"] = True
    try:
        result = run_collection()
        result["watch_events"] = refresh_watch_events()
        COLLECTION_STATE["last_result"] = result
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

    def _json(self, payload: object, status: int = 200, headers: Mapping[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _secure_request(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().casefold() == "https"

    def _client_key(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        return forwarded or self.client_address[0]

    def _require_auth(self) -> bool:
        if not AUTH.required:
            return True
        if AUTH.setup_required:
            self._json({"error": "auth_setup_required"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return False
        if AUTH.session(self.headers.get("Cookie", "")) is None:
            self._json({"error": "authentication_required"}, HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _not_found(self) -> None:
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def _handle_blogger_transfer(self) -> bool:
        if not BloggerTransferHTTP.is_candidate(self.path):
            return False
        self.close_connection = True
        application = getattr(self.server, "blogger_transfer", None)
        if application is None:
            self._json(
                {"error_code": "blogger_receiver_unavailable"},
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers={"Connection": "close"},
            )
            return True
        response = application.handle(self.command, self.path, self.headers, self.rfile)
        self._json(
            response.payload,
            response.status,
            headers={"Connection": "close"},
        )
        return True

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
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _serve_private_file(self, target: Path, mime_type: str) -> None:
        if not target.is_file():
            self._not_found()
            return
        file_size = target.stat().st_size
        start = 0
        end = max(file_size - 1, 0)
        status = HTTPStatus.OK
        range_header = self.headers.get("Range", "").strip()
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or file_size == 0:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            start_text, end_text = match.groups()
            if not start_text and not end_text:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            if start_text:
                start = int(start_text)
                end = min(int(end_text), file_size - 1) if end_text else file_size - 1
            else:
                suffix_length = min(int(end_text), file_size)
                start = file_size - suffix_length
                end = file_size - 1
            if start >= file_size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        content_length = max(end - start + 1, 0)
        self.send_response(status)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with target.open("rb") as stream:
                stream.seek(start)
                remaining = content_length
                while remaining:
                    chunk = stream.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _serve_model_mr_video(self, work_id: int) -> None:
        local = MODEL_MR.video_path(work_id)
        if local is not None:
            self._serve_private_file(*local)
            return
        try:
            upstream = MODEL_MR.open_live_video(work_id, self.headers.get("Range", ""))
        except (ValueError, ModelMrUnavailable) as error:
            self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            return
        try:
            status = int(getattr(upstream, "status", HTTPStatus.OK))
            self.send_response(status)
            for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()
            if self.command != "HEAD":
                while True:
                    chunk = upstream.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        finally:
            upstream.close()

    def do_HEAD(self) -> None:
        if self._handle_blogger_transfer():
            return
        if not self._host_allowed():
            self._json({"error": "invalid_host"}, HTTPStatus.BAD_REQUEST)
            return
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"ok": True, "version": __version__, "auth_required": AUTH.required})
        elif path == "/api/auth/status":
            self._json(AUTH.status(self.headers.get("Cookie", "")))
        elif path.startswith("/api/") and not self._require_auth():
            return
        elif re.fullmatch(r"/api/model-mr/works/\d+/video", path):
            self._serve_model_mr_video(int(path.split("/")[4]))
        elif path.startswith("/api/"):
            self._not_found()
        else:
            self._serve_static(path)

    def do_GET(self) -> None:
        if self._handle_blogger_transfer():
            return
        if not self._host_allowed():
            self._json({"error": "invalid_host"}, HTTPStatus.BAD_REQUEST)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/health":
            self._json({"ok": True, "version": __version__, "auth_required": AUTH.required})
        elif path == "/api/auth/status":
            self._json(AUTH.status(self.headers.get("Cookie", "")))
        elif path.startswith("/api/") and not self._require_auth():
            return
        elif path == "/api/blogger-library/status":
            self._json(BLOGGER_LIBRARY.status())
        elif path == "/api/blogger-library/creators":
            self._json(BLOGGER_LIBRARY.creators())
        elif re.fullmatch(r"/api/blogger-library/creators/[0-9a-f-]{36}/works", path):
            result = BLOGGER_LIBRARY.creator_works(path.split("/")[4])
            self._json(result) if result is not None else self._not_found()
        elif re.fullmatch(r"/api/blogger-library/works/[0-9a-f]{64}", path):
            result = BLOGGER_LIBRARY.work_detail(path.rsplit("/", 1)[-1])
            self._json(result) if result is not None else self._not_found()
        elif path == "/api/model-mr/status":
            self._json(MODEL_MR.status())
        elif path == "/api/model-mr/works":
            try:
                limit = int(query.get("limit", ["40"])[0])
                self._json(MODEL_MR.works(limit=limit))
            except (ValueError, ModelMrUnavailable) as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
        elif re.fullmatch(r"/api/model-mr/works/\d+/video", path):
            self._serve_model_mr_video(int(path.split("/")[4]))
        elif re.fullmatch(r"/api/model-mr/works/\d+", path):
            try:
                self._json(MODEL_MR.work_detail(int(path.rsplit("/", 1)[-1])))
            except (ValueError, ModelMrUnavailable) as error:
                self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        elif path == "/api/model-mr/thoughts":
            try:
                self._json(MODEL_MR.thoughts())
            except ModelMrUnavailable as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
        elif path == "/api/model-mr/chat/config":
            try:
                self._json(MODEL_MR.chat_config())
            except ModelMrUnavailable as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
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
        elif path == "/api/hot":
            self._json(query_hot_items(limit=int(query.get("limit", ["40"])[0])))
        elif path == "/api/watch-events":
            self._json(list_watch_events())
        elif path.startswith("/api/items/") and path.endswith("/thumbnail"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                self._not_found()
                return
            try:
                item_id = int(parts[2])
            except ValueError:
                self._not_found()
                return
            thumbnail = get_thumbnail(item_id)
            if thumbnail is None:
                self._not_found()
                return
            etag = f'"{thumbnail.etag}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", f"public, max-age={thumbnail.cache_seconds}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", thumbnail.mime_type)
            self.send_header("Content-Length", str(len(thumbnail.content)))
            self.send_header("Cache-Control", f"public, max-age={thumbnail.cache_seconds}")
            self.send_header("ETag", etag)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Instant-AI-Thumbnail", thumbnail.kind)
            self.end_headers()
            self.wfile.write(thumbnail.content)
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
        elif path == "/api/translation/status":
            self._json(translation_status())
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
        elif path.startswith("/api/"):
            self._not_found()
        else:
            self._serve_static(path)

    def do_POST(self) -> None:
        if self._handle_blogger_transfer():
            return
        if not self._host_allowed() or self.headers.get("X-Instant-AI") != "1":
            self._json({"error": "request_rejected"}, HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        max_length = 768 * 1024 if re.fullmatch(r"/api/model-mr/works/\d+/video-text", path) else 64 * 1024
        if content_length > max_length:
            self._json({"error": "request_too_large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        length = content_length
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/auth/login":
            if not AUTH.required:
                self._json({"ok": True, "required": False})
                return
            if AUTH.setup_required:
                self._json({"error": "auth_setup_required"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            client_key = self._client_key()
            if not AUTH.login_allowed(client_key):
                self._json({"error": "too_many_attempts"}, HTTPStatus.TOO_MANY_REQUESTS)
                return
            username = str(payload.get("username") or "")
            password = str(payload.get("password") or "")
            if not AUTH.authenticate(username, password):
                AUTH.record_failed_login(client_key)
                self._json({"error": "invalid_credentials"}, HTTPStatus.UNAUTHORIZED)
                return
            AUTH.clear_failed_logins(client_key)
            token, session = AUTH.create_session(username)
            self._json(
                {"ok": True, "username": session.username, "expires_at": session.expires_at},
                headers={"Set-Cookie": AUTH.session_cookie(token, secure=self._secure_request())},
            )
            return

        if path == "/api/auth/logout":
            self._json(
                {"ok": True},
                headers={"Set-Cookie": AUTH.expired_cookie(secure=self._secure_request())},
            )
            return

        if not self._require_auth():
            return

        if path == "/api/model-mr/chat":
            try:
                messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
                self._json(MODEL_MR.chat(messages, str(payload.get("model") or "")))
            except ValueError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except ModelMrUnavailable as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return

        model_work_match = re.fullmatch(r"/api/model-mr/works/(\d+)/(title|video-text|transcribe|doubao-transcribe)", path)
        if model_work_match:
            work_id = int(model_work_match.group(1))
            action = model_work_match.group(2)
            try:
                if action == "title":
                    self._json(MODEL_MR.save_title(work_id, str(payload.get("title") or "")))
                elif action == "video-text":
                    self._json(MODEL_MR.save_video_text(work_id, str(payload.get("text") or "")))
                else:
                    self._json(MODEL_MR.transcribe(work_id, "doubao" if action == "doubao-transcribe" else "local"))
            except ValueError as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except ModelMrUnavailable as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return

        if path == "/api/backup":
            target = create_backup(force=True)
            self._json({"ok": True, "path": str(target) if target else None})
            return

        if path == "/api/restore-drill":
            self._json({"ok": True, "result": run_restore_drill()})
            return

        if path == "/api/translate":
            raw_ids = payload.get("item_ids", [])
            if not isinstance(raw_ids, list):
                self._json({"error": "item_ids_must_be_a_list"}, HTTPStatus.BAD_REQUEST)
                return
            item_ids: list[int] = []
            for value in raw_ids[:40]:
                try:
                    item_ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            try:
                max_new = int(payload.get("max_new", 12))
            except (TypeError, ValueError):
                max_new = 12
            self._json(translate_items(item_ids, max_new=max_new))
            return

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "items"] and parts[3] == "reader-translation":
            try:
                item_id = int(parts[2])
            except ValueError:
                self._not_found()
                return
            self._json(translate_reader_item(item_id))
            return

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

    def do_PUT(self) -> None:
        if self._handle_blogger_transfer():
            return
        if not self._host_allowed() or self.headers.get("X-Instant-AI") != "1":
            self._json({"error": "request_rejected"}, HTTPStatus.FORBIDDEN)
            return
        if not self._require_auth():
            return
        self._not_found()


def create_server() -> BoundedThreadingHTTPServer:
    ensure_layout()
    create_backup()
    initialize()
    seed_sources()
    run_retention_cleanup()
    reclassify_items()
    backfill_thumbnail_candidates()
    backfill_notifications()
    server = BoundedThreadingHTTPServer((HOST, PORT), InstantAIHandler)
    server.blogger_transfer = BloggerTransferHTTP.from_environment()  # type: ignore[attr-defined]
    return server


def run_server(collect_on_start: bool = True) -> None:
    server = create_server()
    threading.Thread(target=_scheduler_loop, name="instant-ai-scheduler", daemon=True).start()
    if collect_on_start:
        threading.Thread(target=_collect_in_background, name="instant-ai-initial-collector", daemon=True).start()
    else:
        threading.Thread(target=refresh_watch_events, name="instant-ai-initial-watch-events", daemon=True).start()
    server.serve_forever(poll_interval=0.5)
