from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on minimal Python installs.
    CRYPTOGRAPHY_AVAILABLE = False


ALLOWED_PUSH_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}
ALLOWED_PUSH_HOST_SUFFIXES = (
    ".notify.windows.com",
    ".push.services.mozilla.com",
)
MAX_NOTIFICATION_BYTES = 2800


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        (value + padding).encode("ascii"),
        altchars=b"-_",
        validate=True,
    )


def _hkdf_extract(salt: bytes, value: bytes) -> bytes:
    from cryptography.hazmat.primitives import hmac

    digest = hmac.HMAC(salt, hashes.SHA256())
    digest.update(value)
    return digest.finalize()


def _hkdf_expand(value: bytes, info: bytes, length: int) -> bytes:
    return HKDFExpand(algorithm=hashes.SHA256(), length=length, info=info).derive(value)


def validate_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    parsed = urlsplit(endpoint)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("手机推送地址无效。")
    if parsed.port not in {None, 443}:
        raise ValueError("手机推送地址端口不受支持。")
    if hostname not in ALLOWED_PUSH_HOSTS and not any(
        hostname.endswith(suffix) for suffix in ALLOWED_PUSH_HOST_SUFFIXES
    ):
        raise ValueError("不是受支持的手机推送服务。")
    return endpoint


def validate_subscription(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("手机推送订阅格式无效。")
    endpoint = validate_endpoint(str(payload.get("endpoint") or ""))
    keys = payload.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("手机推送订阅缺少加密密钥。")
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    try:
        public_key = base64url_decode(p256dh)
        auth_secret = base64url_decode(auth)
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError("手机推送订阅密钥无效。") from error
    if len(public_key) != 65 or public_key[0] != 4 or len(auth_secret) != 16:
        raise ValueError("手机推送订阅密钥长度无效。")
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


@dataclass(frozen=True)
class PushResult:
    ok: bool
    status: int | None
    expired: bool
    error: str


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class WebPushSender:
    """Small RFC 8291/RFC 8292 sender with a Git-outside VAPID key."""

    def __init__(
        self,
        key_path: Path,
        *,
        subject: str = "https://grandpaamu.com/",
        timeout: int = 20,
    ) -> None:
        self.key_path = key_path
        self.subject = subject
        self.timeout = timeout
        self._key_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return CRYPTOGRAPHY_AVAILABLE

    def _private_key(self):
        if not self.available:
            raise RuntimeError("服务器缺少手机推送加密组件。")
        with self._key_lock:
            if self.key_path.is_file():
                return serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
            self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_key = ec.generate_private_key(ec.SECP256R1())
            encoded = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            temporary = self.key_path.with_name(f".{self.key_path.name}.{os.getpid()}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.key_path)
                os.chmod(self.key_path, 0o600)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return private_key

    def public_key(self) -> str:
        private_key = self._private_key()
        encoded = private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return base64url_encode(encoded)

    def _authorization(self, endpoint: str, private_key) -> str:
        parsed = urlsplit(endpoint)
        audience = f"{parsed.scheme}://{parsed.netloc}"
        header = base64url_encode(b'{"typ":"JWT","alg":"ES256"}')
        claims = base64url_encode(
            json.dumps(
                {"aud": audience, "exp": int(time.time()) + 12 * 60 * 60, "sub": self.subject},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        unsigned = f"{header}.{claims}".encode("ascii")
        der_signature = private_key.sign(unsigned, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_signature)
        token = f"{header}.{claims}.{base64url_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return f"vapid t={token}, k={base64url_encode(public_key)}"

    @staticmethod
    def _encrypt(payload: bytes, user_public: bytes, auth_secret: bytes) -> bytes:
        if len(payload) > MAX_NOTIFICATION_BYTES:
            raise ValueError("手机通知正文过长。")
        user_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), user_public)
        server_private = ec.generate_private_key(ec.SECP256R1())
        server_public = server_private.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        shared_secret = server_private.exchange(ec.ECDH(), user_key)
        pseudo_random_key = _hkdf_extract(auth_secret, shared_secret)
        key_info = b"WebPush: info\x00" + user_public + server_public
        input_key_material = _hkdf_expand(pseudo_random_key, key_info, 32)
        salt = os.urandom(16)
        content_prk = _hkdf_extract(salt, input_key_material)
        content_key = _hkdf_expand(content_prk, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = _hkdf_expand(content_prk, b"Content-Encoding: nonce\x00", 12)
        ciphertext = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)
        record_size = 4096
        return salt + record_size.to_bytes(4, "big") + bytes((len(server_public),)) + server_public + ciphertext

    def send(self, subscription: dict[str, str], notification: dict[str, object]) -> PushResult:
        if not self.available:
            return PushResult(False, None, False, "服务器缺少手机推送加密组件。")
        try:
            checked = validate_subscription(
                {
                    "endpoint": subscription.get("endpoint"),
                    "keys": {"p256dh": subscription.get("p256dh"), "auth": subscription.get("auth")},
                }
            )
            private_key = self._private_key()
            body = self._encrypt(
                json.dumps(notification, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                base64url_decode(checked["p256dh"]),
                base64url_decode(checked["auth"]),
            )
            request = urllib.request.Request(
                checked["endpoint"],
                data=body,
                method="POST",
                headers={
                    "Authorization": self._authorization(checked["endpoint"], private_key),
                    "Content-Encoding": "aes128gcm",
                    "Content-Type": "application/octet-stream",
                    "TTL": "86400",
                    "Urgency": "high",
                },
            )
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                status = int(response.status)
            return PushResult(status in {201, 202}, status, False, "" if status in {201, 202} else f"HTTP {status}")
        except urllib.error.HTTPError as error:
            expired = error.code in {404, 410}
            return PushResult(False, error.code, expired, f"HTTP {error.code}")
        except (OSError, ValueError, RuntimeError) as error:
            return PushResult(False, None, False, f"{type(error).__name__}: {error}"[:500])
