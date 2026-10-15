from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import sqlite3
from http.cookies import CookieError, SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .analytics import CommunityAnalyticsStore, VALID_ANALYTICS_VIEWS
from .auth import ADMIN_SESSION_SECONDS, AdminSessionManager
from .store import CommunityMessageStore, CommunityRateLimiter, validate_community_message


ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18808
MAX_REQUEST_BODY_BYTES = 8 * 1024
PUBLIC_HOSTS = frozenset({"amuyeye.com", "xn--4pv19va7008a.com"})
VISITOR_COOKIE = "sunmao_visitor"
VISIT_SESSION_COOKIE = "sunmao_visit_session"
ADMIN_COOKIE = "sunmao_admin_session"
COOKIE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{24,96}")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/videos.json": ("videos.json", "application/json; charset=utf-8"),
    "/admin/": ("admin/index.html", "text/html; charset=utf-8"),
    "/admin/index.html": ("admin/index.html", "text/html; charset=utf-8"),
    "/admin/admin.js": ("admin/admin.js", "text/javascript; charset=utf-8"),
    "/admin/admin.css": ("admin/admin.css", "text/css; charset=utf-8"),
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


def _configured_admin_password_hash() -> str:
    environment_value = str(os.getenv("SUNMAO_ADMIN_PASSWORD_HASH") or "").strip()
    if environment_value:
        return environment_value[:1024]
    configured_path = os.getenv(
        "SUNMAO_ADMIN_PASSWORD_HASH_FILE",
        "/var/lib/sunmao-community/admin-password.hash",
    )
    try:
        return Path(configured_path).read_text(encoding="utf-8").strip()[:1024]
    except OSError:
        return ""


class CommunityHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        store: CommunityMessageStore,
        analytics_store: CommunityAnalyticsStore,
        admin_auth: AdminSessionManager,
    ):
        self.store = store
        self.analytics_store = analytics_store
        self.admin_auth = admin_auth
        self.rate_limiter = CommunityRateLimiter()
        self.admin_rate_limiter = CommunityRateLimiter(cooldown_seconds=2, hourly_limit=12)
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
        cookies: list[str] | None = None,
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
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(int(HTTPStatus.PERMANENT_REDIRECT))
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

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
        if path.startswith("/admin/"):
            self.send_header("X-Robots-Tag", "noindex, nofollow")
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

    def _read_json_body(self) -> Any | None:
        if str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
            self._json({"error": "json_content_type_required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return None
        if self.headers.get("Transfer-Encoding"):
            self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return None
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if content_length < 1 or content_length > MAX_REQUEST_BODY_BYTES:
            self._json({"error": "invalid_request_size"}, HTTPStatus.BAD_REQUEST)
            return None
        try:
            return json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return None

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
        if parsed.hostname in PUBLIC_HOSTS:
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

    def _cookie_value(self, name: str) -> str:
        try:
            cookies = SimpleCookie(str(self.headers.get("Cookie") or ""))
        except CookieError:
            return ""
        morsel = cookies.get(name)
        if morsel is None or not COOKIE_TOKEN_PATTERN.fullmatch(morsel.value):
            return ""
        return morsel.value

    def _is_https(self) -> bool:
        forwarded = str(self.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0]
        return forwarded.strip().lower() == "https"

    def _cookie_header(
        self,
        name: str,
        value: str,
        *,
        max_age: int,
        same_site: str = "Lax",
    ) -> str:
        parts = [
            f"{name}={value}",
            "Path=/",
            f"Max-Age={max(0, int(max_age))}",
            "HttpOnly",
            f"SameSite={same_site}",
        ]
        if self._is_https():
            parts.append("Secure")
        return "; ".join(parts)

    def _admin_authenticated(self) -> bool:
        return self.server.admin_auth.authenticated(self._cookie_value(ADMIN_COOKIE))

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/admin":
            self._redirect("/admin/")
            return
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
        if parsed.path == "/api/admin/session":
            if not self.server.admin_auth.configured:
                self._json(
                    {"authenticated": False, "configured": False},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            self._json(
                {
                    "authenticated": self._admin_authenticated(),
                    "configured": True,
                }
            )
            return
        if parsed.path == "/api/admin/analytics":
            if not self._admin_authenticated():
                self._json({"error": "admin_auth_required"}, HTTPStatus.UNAUTHORIZED)
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                days = max(1, min(90, int((query.get("days") or ["14"])[0])))
                recent_limit = max(1, min(100, int((query.get("limit") or ["50"])[0])))
            except (TypeError, ValueError):
                days, recent_limit = 14, 50
            try:
                payload = self.server.analytics_store.dashboard(
                    days=days, recent_limit=recent_limit
                )
            except (OSError, sqlite3.Error):
                self._json({"error": "analytics_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._json(payload)
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/messages":
            self._post_message()
            return
        if path == "/api/analytics/pageview":
            self._post_pageview()
            return
        if path == "/api/admin/login":
            self._post_admin_login()
            return
        if path == "/api/admin/logout":
            self._post_admin_logout()
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def _post_message(self) -> None:
        if not self._origin_allowed():
            self._json({"error": "same_origin_required"}, HTTPStatus.FORBIDDEN)
            return
        payload = self._read_json_body()
        if payload is None:
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

    def _post_pageview(self) -> None:
        if not self._origin_allowed():
            self._json({"error": "same_origin_required"}, HTTPStatus.FORBIDDEN)
            return
        payload = self._read_json_body()
        if payload is None:
            return
        if (
            not isinstance(payload, dict)
            or not set(payload).issubset({"view", "referrer"})
            or "view" not in payload
            or payload.get("view") not in VALID_ANALYTICS_VIEWS
            or not isinstance(payload.get("referrer", ""), str)
            or len(payload.get("referrer", "")) > 2048
        ):
            self._json({"error": "invalid_analytics_event"}, HTTPStatus.BAD_REQUEST)
            return
        visitor_token = self._cookie_value(VISITOR_COOKIE)
        session_token = self._cookie_value(VISIT_SESSION_COOKIE)
        cookies = []
        if not visitor_token:
            visitor_token = secrets.token_urlsafe(24)
            cookies.append(
                self._cookie_header(
                    VISITOR_COOKIE, visitor_token, max_age=180 * 24 * 60 * 60
                )
            )
        if not session_token:
            session_token = secrets.token_urlsafe(24)
        cookies.append(
            self._cookie_header(
                VISIT_SESSION_COOKIE, session_token, max_age=30 * 60
            )
        )
        try:
            self.server.analytics_store.record_pageview(
                visitor_token=visitor_token,
                session_token=session_token,
                view=payload["view"],
                referrer=payload.get("referrer", ""),
                user_agent=self.headers.get("User-Agent", ""),
            )
        except (OSError, sqlite3.Error):
            self._json({"error": "analytics_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._json({"accepted": True}, HTTPStatus.ACCEPTED, cookies=cookies)

    def _post_admin_login(self) -> None:
        if not self.server.admin_auth.configured:
            self._json({"error": "admin_not_configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if not self._origin_allowed():
            self._json({"error": "same_origin_required"}, HTTPStatus.FORBIDDEN)
            return
        payload = self._read_json_body()
        if payload is None:
            return
        if (
            not isinstance(payload, dict)
            or set(payload) != {"password"}
            or not isinstance(payload.get("password"), str)
            or len(payload["password"]) > 256
        ):
            self._json({"error": "invalid_request"}, HTTPStatus.BAD_REQUEST)
            return
        allowed, retry_after = self.server.admin_rate_limiter.allow(self._client_key())
        if not allowed:
            self._json(
                {"error": "rate_limited", "retry_after": retry_after},
                HTTPStatus.TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
            )
            return
        session_token = self.server.admin_auth.login(payload["password"])
        if session_token is None:
            self._json({"error": "invalid_credentials"}, HTTPStatus.UNAUTHORIZED)
            return
        self._json(
            {"authenticated": True},
            cookies=[
                self._cookie_header(
                    ADMIN_COOKIE,
                    session_token,
                    max_age=ADMIN_SESSION_SECONDS,
                    same_site="Strict",
                )
            ],
        )

    def _post_admin_logout(self) -> None:
        if not self._origin_allowed():
            self._json({"error": "same_origin_required"}, HTTPStatus.FORBIDDEN)
            return
        self.server.admin_auth.logout(self._cookie_value(ADMIN_COOKIE))
        self._json(
            {"authenticated": False},
            cookies=[
                self._cookie_header(
                    ADMIN_COOKIE, "", max_age=0, same_site="Strict"
                )
            ],
        )


def create_http_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    database_path: Path | None = None,
    admin_password_hash: str | None = None,
) -> CommunityHTTPServer:
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("community server must bind to loopback")
    except ValueError as exc:
        raise ValueError("community server must bind to a numeric loopback address") from exc
    database = database_path or Path(
        os.getenv("SUNMAO_COMMUNITY_DATABASE_PATH", "/var/lib/sunmao-community/community.sqlite3")
    )
    return CommunityHTTPServer(
        (host, int(port)),
        CommunityMessageStore(database),
        CommunityAnalyticsStore(database),
        AdminSessionManager(
            _configured_admin_password_hash()
            if admin_password_hash is None
            else admin_password_hash
        ),
    )


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
