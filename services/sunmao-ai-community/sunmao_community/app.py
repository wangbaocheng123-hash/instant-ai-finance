from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .store import CommunityMessageStore, CommunityRateLimiter, validate_community_message


ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18808
MAX_REQUEST_BODY_BYTES = 8 * 1024
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/videos.json": ("videos.json", "application/json; charset=utf-8"),
}


def _version() -> str:
    try:
        value = (ROOT_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        value = "unknown"
    return (value or "unknown")[:64]


def _deployment_version() -> dict[str, str]:
    payload = {
        "service": "sunmao-ai-community",
        "status": "ok",
        "version": _version(),
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


class CommunityHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: CommunityMessageStore):
        self.store = store
        self.rate_limiter = CommunityRateLimiter()
        super().__init__(address, CommunityRequestHandler)


class CommunityRequestHandler(BaseHTTPRequestHandler):
    server: CommunityHTTPServer

    def log_message(self, _format: str, *_args: Any) -> None:
        # Public source addresses are used only for in-memory rate limiting.
        return

    def _json(
        self,
        payload: Mapping[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
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
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> bool:
        descriptor = STATIC_FILES.get(path)
        if descriptor is None:
            return False
        filename, content_type = descriptor
        try:
            body = (WEB_DIR / filename).read_bytes()
        except OSError:
            self._json({"error": "community_unavailable"}, HTTPStatus.NOT_FOUND)
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
            "img-src 'self' data: https://*.douyinpic.com; media-src 'none'; "
            "connect-src 'self'; object-src 'none'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)
        return True

    def _origin_allowed(self) -> bool:
        origin = str(self.headers.get("Origin") or "").strip()
        host = str(self.headers.get("Host") or "").strip().lower()
        try:
            parsed = urlsplit(origin)
            origin_port = parsed.port
        except (TypeError, ValueError):
            return False
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.netloc.lower() != host
        ):
            return False
        if parsed.hostname == "xn--4pv19va7008a.com":
            return parsed.scheme == "https" and origin_port in {None, 443}
        try:
            return parsed.scheme == "http" and ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            return parsed.scheme == "http" and parsed.hostname == "localhost"

    def _client_key(self) -> str:
        forwarded = str(self.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
        try:
            return ipaddress.ip_address(forwarded).compressed
        except ValueError:
            return str(self.client_address[0])

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if self._static(parsed.path):
            return
        if parsed.path == "/health":
            self._json({"service": "sunmao-ai-community", "status": "ok"})
            return
        if parsed.path == "/health/version":
            self._json(_deployment_version())
            return
        if parsed.path == "/api/messages":
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                limit = max(1, min(100, int((query.get("limit") or ["80"])[0])))
            except (TypeError, ValueError):
                limit = 80
            try:
                messages = self.server.store.list_messages(limit=limit)
            except (OSError, sqlite3.Error):
                self._json({"error": "community_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._json({"messages": messages})
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/messages":
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._origin_allowed():
            self._json({"error": "same_origin_required"}, HTTPStatus.FORBIDDEN)
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
        if not isinstance(payload, dict) or set(payload) != {"nickname", "body"}:
            self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            nickname, body = validate_community_message(payload["nickname"], payload["body"])
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        allowed, retry_after = self.server.rate_limiter.allow(self._client_key())
        if not allowed:
            self._json(
                {"error": "rate_limited", "retry_after": retry_after},
                HTTPStatus.TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
            )
            return
        try:
            message = self.server.store.create_message(nickname, body)
        except (OSError, sqlite3.Error):
            self._json({"error": "community_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._json({"message": message}, HTTPStatus.CREATED)


def create_http_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    database_path: Path | None = None,
) -> CommunityHTTPServer:
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("community server must bind to loopback")
    except ValueError as exc:
        raise ValueError("community server must bind to a numeric loopback address") from exc
    database = database_path or Path(
        os.getenv("SUNMAO_COMMUNITY_DATABASE_PATH", "/var/lib/sunmao-community/community.sqlite3")
    )
    return CommunityHTTPServer((host, int(port)), CommunityMessageStore(database))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the independent Sunmao AI Community")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    server = create_http_server(host=args.host, port=args.port, database_path=args.database)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
