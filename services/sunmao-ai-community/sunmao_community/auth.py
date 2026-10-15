from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
import time


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
ADMIN_SESSION_SECONDS = 12 * 60 * 60


def create_password_hash(password: str, *, salt: bytes | None = None) -> str:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        raise ValueError("admin password must contain 12 to 256 characters")
    password_salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), password_salt, PASSWORD_ITERATIONS
    )
    encoded_salt = base64.urlsafe_b64encode(password_salt).decode("ascii").rstrip("=")
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${encoded_salt}${encoded_digest}"


def _decode_base64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def _password_hash_parts(encoded: str) -> tuple[int, bytes, bytes] | None:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        iterations = int(iterations_text)
        salt = _decode_base64(salt_text)
        expected = _decode_base64(digest_text)
    except (ValueError, TypeError, binascii.Error):
        return None
    if (
        scheme != PASSWORD_SCHEME
        or iterations != PASSWORD_ITERATIONS
        or not (16 <= len(salt) <= 64)
        or len(expected) != 32
    ):
        return None
    return iterations, salt, expected


def verify_password(password: str, encoded: str) -> bool:
    parsed = _password_hash_parts(encoded)
    if parsed is None or not isinstance(password, str) or not (12 <= len(password) <= 256):
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual, expected)


class AdminSessionManager:
    """In-memory admin sessions; restarts intentionally require a fresh login."""

    def __init__(
        self,
        password_hash: str = "",
        *,
        session_seconds: int = ADMIN_SESSION_SECONDS,
    ) -> None:
        self.password_hash = str(password_hash or "").strip()
        self.session_seconds = max(300, int(session_seconds))
        self._lock = threading.Lock()
        self._sessions: dict[str, int] = {}

    @property
    def configured(self) -> bool:
        return _password_hash_parts(self.password_hash) is not None

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def login(self, password: str, *, now_epoch: int | None = None) -> str | None:
        if not self.configured or not verify_password(password, self.password_hash):
            return None
        current = int(time.time()) if now_epoch is None else int(now_epoch)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_locked(current)
            self._sessions[self._token_hash(token)] = current + self.session_seconds
        return token

    def authenticated(self, token: str, *, now_epoch: int | None = None) -> bool:
        if not token:
            return False
        current = int(time.time()) if now_epoch is None else int(now_epoch)
        try:
            token_hash = self._token_hash(token)
        except (UnicodeEncodeError, AttributeError):
            return False
        with self._lock:
            self._purge_locked(current)
            return self._sessions.get(token_hash, 0) > current

    def logout(self, token: str) -> None:
        if not token:
            return
        try:
            token_hash = self._token_hash(token)
        except (UnicodeEncodeError, AttributeError):
            return
        with self._lock:
            self._sessions.pop(token_hash, None)

    def _purge_locked(self, current_epoch: int) -> None:
        for token_hash, expires_at in list(self._sessions.items()):
            if expires_at <= current_epoch:
                self._sessions.pop(token_hash, None)
