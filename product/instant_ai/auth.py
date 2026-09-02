from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from .paths import LIBRARY_ROOT


SESSION_COOKIE_NAME = "instant_ai_owner_session"
SESSION_DAYS = 30
SESSION_SECONDS = SESSION_DAYS * 24 * 60 * 60
MIN_OWNER_PASSWORD_LENGTH = 9
AUTH_FILE = Path(os.environ.get("INSTANT_AI_AUTH_FILE", str(LIBRARY_ROOT / "auth.json")))


def _enabled(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


def configure_owner(username: str, password: str, path: Path = AUTH_FILE) -> dict[str, Any]:
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("主人账户名称必须为 1—64 个字符。")
    if len(password) < MIN_OWNER_PASSWORD_LENGTH:
        raise ValueError(f"主人密码至少需要 {MIN_OWNER_PASSWORD_LENGTH} 个字符。")

    salt = secrets.token_bytes(16)
    payload = {
        "version": 1,
        "username": username,
        "password_salt": _b64encode(salt),
        "password_hash": _b64encode(_password_hash(password, salt)),
        "session_secret": _b64encode(secrets.token_bytes(32)),
        "session_days": SESSION_DAYS,
        "updated_at": int(time.time()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".auth-", suffix=".json", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return {"username": username, "session_days": SESSION_DAYS, "path": str(path)}


def generate_owner_password() -> str:
    return f"IA-{secrets.token_urlsafe(18)}"


@dataclass(frozen=True)
class Session:
    username: str
    issued_at: int
    expires_at: int


class OwnerAuth:
    def __init__(self, *, required: bool | None = None, path: Path | None = None) -> None:
        self.required = _enabled(os.environ.get("INSTANT_AI_AUTH_REQUIRED")) if required is None else required
        self.path = path or AUTH_FILE
        self._config: dict[str, Any] | None = None
        self._config_mtime_ns = -1
        self._lock = threading.Lock()
        self._failed_attempts: dict[str, list[float]] = {}

    @property
    def setup_required(self) -> bool:
        return self.required and self._load_config() is None

    @property
    def configured_username(self) -> str:
        """Return the single configured owner name for first-party login forms."""
        config = self._load_config()
        return str(config.get("username") or "") if config is not None else ""

    def status(self, cookie_header: str = "") -> dict[str, Any]:
        session = self.session(cookie_header)
        return {
            "required": self.required,
            "authenticated": not self.required or session is not None,
            "setup_required": self.setup_required,
            "username": session.username if session else "",
            "expires_at": session.expires_at if session else None,
            "session_days": SESSION_DAYS,
        }

    def authenticate(self, username: str, password: str) -> bool:
        config = self._load_config()
        if not self.required or config is None:
            return False
        expected_username = str(config.get("username") or "")
        try:
            salt = _b64decode(str(config["password_salt"]))
            expected_hash = _b64decode(str(config["password_hash"]))
            supplied_hash = _password_hash(password, salt)
        except (KeyError, TypeError, ValueError):
            return False
        return hmac.compare_digest(username.strip().encode("utf-8"), expected_username.encode("utf-8")) and hmac.compare_digest(
            supplied_hash, expected_hash
        )

    def create_session(self, username: str, *, now: int | None = None) -> tuple[str, Session]:
        config = self._load_config()
        if config is None or username.strip() != str(config.get("username") or ""):
            raise ValueError("账户配置不可用。")
        issued_at = int(time.time()) if now is None else int(now)
        session = Session(username=username.strip(), issued_at=issued_at, expires_at=issued_at + SESSION_SECONDS)
        payload = _b64encode(
            json.dumps(
                {"u": session.username, "iat": session.issued_at, "exp": session.expires_at},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = _b64encode(hmac.new(self._session_secret(config), payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}", session

    def create_oauth_token(
        self,
        username: str,
        *,
        audience: str,
        scopes: tuple[str, ...],
        now: int | None = None,
    ) -> tuple[str, Session]:
        """Create a signed owner-only MCP token without exposing the auth secret."""
        config = self._load_config()
        if config is None or username.strip() != str(config.get("username") or ""):
            raise ValueError("账户配置不可用。")
        if not audience.startswith("https://") or not scopes:
            raise ValueError("OAuth 令牌范围无效。")
        issued_at = int(time.time()) if now is None else int(now)
        session = Session(
            username=username.strip(),
            issued_at=issued_at,
            expires_at=issued_at + SESSION_SECONDS,
        )
        payload = _b64encode(
            json.dumps(
                {
                    "typ": "mcp_oauth",
                    "u": session.username,
                    "iat": session.issued_at,
                    "exp": session.expires_at,
                    "aud": audience,
                    "scope": " ".join(sorted(set(scopes))),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signing_input = f"mcp-oauth-v1.{payload}".encode("ascii")
        signature = _b64encode(
            hmac.new(self._session_secret(config), signing_input, hashlib.sha256).digest()
        )
        return f"{payload}.{signature}", session

    def oauth_session(
        self,
        token: str,
        *,
        audience: str,
        required_scope: str,
        now: int | None = None,
    ) -> Session | None:
        """Verify a signed MCP token, including audience, expiry and scope."""
        if not self.required:
            return None
        config = self._load_config()
        if config is None:
            return None
        try:
            payload, signature = str(token or "").split(".", 1)
            signing_input = f"mcp-oauth-v1.{payload}".encode("ascii")
            expected = _b64encode(
                hmac.new(self._session_secret(config), signing_input, hashlib.sha256).digest()
            )
            if not hmac.compare_digest(signature, expected):
                return None
            raw = json.loads(_b64decode(payload))
            if raw.get("typ") != "mcp_oauth" or raw.get("aud") != audience:
                return None
            scopes = set(str(raw.get("scope") or "").split())
            if required_scope not in scopes:
                return None
            session = Session(
                username=str(raw["u"]),
                issued_at=int(raw["iat"]),
                expires_at=int(raw["exp"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        current = int(time.time()) if now is None else int(now)
        if (
            session.username != str(config.get("username") or "")
            or session.issued_at > current + 60
            or session.expires_at <= current
            or session.expires_at - session.issued_at != SESSION_SECONDS
        ):
            return None
        return session

    def session(self, cookie_header: str, *, now: int | None = None) -> Session | None:
        if not self.required:
            current = int(time.time()) if now is None else int(now)
            return Session(username="owner", issued_at=current, expires_at=current + SESSION_SECONDS)
        config = self._load_config()
        if config is None:
            return None
        try:
            cookies = SimpleCookie()
            cookies.load(cookie_header or "")
            token = cookies[SESSION_COOKIE_NAME].value
            payload, signature = token.split(".", 1)
            expected = _b64encode(hmac.new(self._session_secret(config), payload.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            raw = json.loads(_b64decode(payload))
            session = Session(username=str(raw["u"]), issued_at=int(raw["iat"]), expires_at=int(raw["exp"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        current = int(time.time()) if now is None else int(now)
        if session.username != str(config.get("username") or "") or session.issued_at > current + 60 or session.expires_at <= current:
            return None
        if session.expires_at - session.issued_at != SESSION_SECONDS:
            return None
        return session

    def login_allowed(self, client_key: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            attempts = [stamp for stamp in self._failed_attempts.get(client_key, []) if current - stamp < 15 * 60]
            self._failed_attempts[client_key] = attempts
            return len(attempts) < 5

    def record_failed_login(self, client_key: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            attempts = [stamp for stamp in self._failed_attempts.get(client_key, []) if current - stamp < 15 * 60]
            attempts.append(current)
            self._failed_attempts[client_key] = attempts

    def clear_failed_logins(self, client_key: str) -> None:
        with self._lock:
            self._failed_attempts.pop(client_key, None)

    @staticmethod
    def session_cookie(token: str, *, secure: bool) -> str:
        attributes = [
            f"{SESSION_COOKIE_NAME}={token}",
            "Path=/",
            f"Max-Age={SESSION_SECONDS}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            attributes.append("Secure")
        return "; ".join(attributes)

    @staticmethod
    def expired_cookie(*, secure: bool) -> str:
        attributes = [
            f"{SESSION_COOKIE_NAME}=",
            "Path=/",
            "Max-Age=0",
            "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            attributes.append("Secure")
        return "; ".join(attributes)

    def _load_config(self) -> dict[str, Any] | None:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._config = None
            self._config_mtime_ns = -1
            return None
        if self._config is not None and self._config_mtime_ns == mtime_ns:
            return self._config
        try:
            config = json.loads(self.path.read_text(encoding="utf-8"))
            if int(config.get("version", 0)) != 1:
                raise ValueError("unsupported auth file")
            self._session_secret(config)
            if not str(config.get("username") or ""):
                raise ValueError("missing username")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._config = None
            self._config_mtime_ns = mtime_ns
            return None
        self._config = config
        self._config_mtime_ns = mtime_ns
        return config

    @staticmethod
    def _session_secret(config: dict[str, Any]) -> bytes:
        secret = _b64decode(str(config["session_secret"]))
        if len(secret) < 32:
            raise ValueError("invalid session secret")
        return secret


AUTH = OwnerAuth()


def main() -> None:
    parser = argparse.ArgumentParser(description="配置即时 AI 的单一主人账户。")
    parser.add_argument("action", nargs="?", default="set-owner", choices=["set-owner"])
    parser.add_argument("--username", default="owner")
    parser.add_argument("--path", type=Path, default=AUTH_FILE)
    parser.add_argument("--generate-password", action="store_true")
    args = parser.parse_args()
    if args.generate_password:
        first = generate_owner_password()
    else:
        first = getpass.getpass(f"请输入主人密码（至少 {MIN_OWNER_PASSWORD_LENGTH} 个字符）：")
        second = getpass.getpass("请再次输入主人密码：")
        if first != second:
            raise SystemExit("两次输入的密码不一致。")
    result = configure_owner(args.username, first, args.path)
    print(f"主人账户已配置：{result['username']}；登录有效期 {result['session_days']} 天。")
    if args.generate_password:
        print(f"一次性显示主人密码：{first}")


if __name__ == "__main__":
    main()
