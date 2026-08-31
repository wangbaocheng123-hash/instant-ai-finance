from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping


DEFAULT_BLOGGER_AGENT_ROOT = Path("/var/lib/instant-ai/blogger-agent")
DEFAULT_MANIFEST_PATH = "/internal/v1/transfers"
MANIFEST_SCHEMA_VERSION = "blogger-transfer/v1"
COMMENT_BUNDLE_FORMAT = "blogger-comments/v1+ndjson"
COMMENT_BUNDLE_ENCODING = "gzip"
STORE_SCHEMA_VERSION = 3

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_CLOCK_SKEW_SECONDS = 5 * 60
MAX_MANIFEST_MEDIA = 100
MAX_MEDIA_BYTES = 10 * 1024 * 1024 * 1024
MAX_COMMENT_ITEMS = 50_000
MAX_COMMENT_BUNDLE_BYTES = 512 * 1024 * 1024

HEADER_NODE_ID = "X-Blogger-Node-Id"
HEADER_KEY_ID = "X-Blogger-Key-Id"
HEADER_TIMESTAMP = "X-Blogger-Timestamp"
HEADER_NONCE = "X-Blogger-Nonce"
HEADER_CONTENT_SHA256 = "X-Blogger-Content-SHA256"
HEADER_SIGNATURE = "X-Blogger-Signature"

_HEX_64 = re.compile(r"[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_SAFE_NONCE = re.compile(r"[A-Za-z0-9._:-]{16,128}")
_HTTP_METHOD = re.compile(r"[A-Z]{3,16}")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_ALLOWED_WORK_TYPES = {"video", "image", "gallery"}
V1_MEDIA_MIME_TYPES = {
    "video": frozenset({"video/mp4"}),
    "image": frozenset({"image/jpeg", "image/png", "image/webp"}),
    "cover": frozenset({"image/jpeg", "image/png", "image/webp"}),
}
_ALLOWED_MEDIA_ROLES = frozenset(V1_MEDIA_MIME_TYPES)
V1_COMMENT_KINDS = frozenset(
    {"user_comment", "user_reply", "author_comment", "author_reply"}
)
V1_COMMENT_SECTIONS = frozenset({"fan_comment", "author_interaction"})
V1_COMMENT_ITEM_FIELDS = frozenset(
    {
        "source_comment_id",
        "parent_source_comment_id",
        "root_source_comment_id",
        "reply_to_comment_id",
        "author",
        "is_creator",
        "text",
        "like_count",
        "reply_count",
        "published_at",
        "captured_at",
        "kind",
        "section",
        "sentiment",
        "risk_level",
        "author_liked",
        "low_value",
        "ip_label",
        "public_label",
        "actual_reply_user",
        "display_order",
    }
)
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class BloggerIngestError(RuntimeError):
    """An error safe to return without exposing authentication material."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManifestAuthentication:
    node_id: str
    key_id: str
    timestamp: str
    nonce: str
    content_sha256: str
    signature: str

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> ManifestAuthentication:
        normalized = {str(key).casefold(): str(value) for key, value in headers.items()}

        def required(name: str) -> str:
            value = normalized.get(name.casefold(), "").strip()
            if not value:
                raise BloggerIngestError("invalid_authentication", f"缺少请求头 {name}。")
            return value

        return cls(
            node_id=required(HEADER_NODE_ID),
            key_id=required(HEADER_KEY_ID),
            timestamp=required(HEADER_TIMESTAMP),
            nonce=required(HEADER_NONCE),
            content_sha256=required(HEADER_CONTENT_SHA256),
            signature=required(HEADER_SIGNATURE),
        )

    def as_headers(self) -> dict[str, str]:
        return {
            HEADER_NODE_ID: self.node_id,
            HEADER_KEY_ID: self.key_id,
            HEADER_TIMESTAMP: self.timestamp,
            HEADER_NONCE: self.nonce,
            HEADER_CONTENT_SHA256: self.content_sha256,
            HEADER_SIGNATURE: self.signature,
        }


@dataclass(frozen=True)
class ValidatedManifest:
    transfer_id: str
    revision_sha256: str
    collector_node_id: str
    collector_key_id: str
    collector_source_sequence: int
    creator_id: str
    creator_display_name: str
    creator_platform: str
    platform_user_id: str
    work_platform: str
    source_work_id: str
    source_revision: int
    normalized: dict[str, Any]
    canonical_json: str


@dataclass(frozen=True)
class IngestReceipt:
    status: str
    transfer_id: str
    receipt_id: str
    revision_sha256: str
    work_revision: int
    current_revision: int
    missing_artifacts: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "transfer_id": self.transfer_id,
            "receipt_id": self.receipt_id,
            "revision_sha256": self.revision_sha256,
            "work_revision": self.work_revision,
            "current_revision": self.current_revision,
            "missing_artifacts": [dict(item) for item in self.missing_artifacts],
        }


@dataclass(frozen=True)
class VerifiedRequest:
    node_id: str
    key_id: str
    timestamp: int
    nonce: str
    content_sha256: str


def _missing_artifacts(manifest: ValidatedManifest) -> tuple[dict[str, Any], ...]:
    artifacts = [
        {
            "artifact_id": item["media_id"],
            "artifact_kind": "media",
        }
        for item in manifest.normalized["media"]
    ]
    bundle = manifest.normalized["comment_snapshot"]["bundle"]
    artifacts.append(
        {
            "artifact_id": bundle["bundle_id"],
            "artifact_kind": "comment_bundle",
        }
    )
    artifacts.sort(
        key=lambda item: (
            0 if item["artifact_kind"] == "media" else 1,
            item["artifact_id"],
        )
    )
    return tuple(artifacts)


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def opaque_work_key(work_platform: str, creator_id: str, source_work_id: str) -> str:
    identity = "\n".join(
        ("blogger-work/v1", str(work_platform), str(creator_id), str(source_work_id))
    ).encode("utf-8")
    return _body_digest(identity)


def canonical_request_bytes(
    *,
    method: str,
    path: str,
    node_id: str,
    key_id: str,
    timestamp: str | int,
    nonce: str,
    content_sha256: str,
) -> bytes:
    normalized_method = str(method or "").upper()
    if not _HTTP_METHOD.fullmatch(normalized_method):
        raise BloggerIngestError("invalid_authentication", "HTTP method 无效。")
    normalized_path = str(path or "")
    if not normalized_path.startswith("/") or "\n" in normalized_path or "\r" in normalized_path:
        raise BloggerIngestError("invalid_authentication", "请求路径无效。")
    normalized_node = _safe_identifier(node_id, "node_id")
    normalized_key = _safe_identifier(key_id, "key_id")
    try:
        normalized_timestamp = str(int(timestamp))
    except (TypeError, ValueError) as error:
        raise BloggerIngestError("invalid_authentication", "请求时间无效。") from error
    if int(normalized_timestamp) < 1:
        raise BloggerIngestError("invalid_authentication", "请求时间无效。")
    normalized_nonce = _text(nonce, "nonce", maximum=128)
    if not _SAFE_NONCE.fullmatch(normalized_nonce):
        raise BloggerIngestError("invalid_authentication", "nonce 无效。")
    normalized_hash = _sha256(content_sha256, "content_sha256")
    return "\n".join(
        (
            normalized_method,
            normalized_path,
            normalized_node,
            normalized_key,
            normalized_timestamp,
            normalized_nonce,
            normalized_hash,
        )
    ).encode("utf-8")


def sign_manifest(
    secret: bytes,
    body: bytes,
    *,
    node_id: str,
    key_id: str,
    timestamp: str | int,
    nonce: str,
    method: str = "POST",
    path: str = DEFAULT_MANIFEST_PATH,
) -> ManifestAuthentication:
    """Build the same signed envelope as the Beijing sender contract."""

    if not secret:
        raise ValueError("清单签名密钥不能为空。")
    digest = _body_digest(body)
    normalized_timestamp = str(int(timestamp))
    signature = hmac.new(
        secret,
        canonical_request_bytes(
            method=method,
            path=path,
            node_id=node_id,
            key_id=key_id,
            timestamp=normalized_timestamp,
            nonce=nonce,
            content_sha256=digest,
        ),
        hashlib.sha256,
    ).hexdigest()
    return ManifestAuthentication(
        node_id=node_id,
        key_id=key_id,
        timestamp=normalized_timestamp,
        nonce=nonce,
        content_sha256=digest,
        signature=signature,
    )


class BloggerManifestVerifier:
    def __init__(
        self,
        secrets: Mapping[tuple[str, str], bytes],
        *,
        max_clock_skew_seconds: int = MAX_CLOCK_SKEW_SECONDS,
    ) -> None:
        self._secrets = dict(secrets)
        self.max_clock_skew_seconds = max(1, int(max_clock_skew_seconds))

    def verify(
        self,
        body: bytes,
        authentication: ManifestAuthentication,
        *,
        method: str = "POST",
        path: str = DEFAULT_MANIFEST_PATH,
        now: int | None = None,
    ) -> str:
        if len(body) > MAX_MANIFEST_BYTES:
            raise BloggerIngestError("manifest_too_large", "清单超过 1 MiB 上限。")
        verified = self.verify_declared(
            authentication,
            method=method,
            path=path,
            now=now,
        )
        actual_digest = _body_digest(body)
        if not hmac.compare_digest(verified.content_sha256, actual_digest):
            raise BloggerIngestError("content_digest_mismatch", "请求正文摘要不一致。")
        return actual_digest

    def verify_declared(
        self,
        authentication: ManifestAuthentication,
        *,
        method: str,
        path: str,
        now: int | None = None,
    ) -> VerifiedRequest:
        """Verify the seven-line envelope before a potentially large body is read."""

        node_id = _safe_identifier(authentication.node_id, "node_id", authentication=True)
        key_id = _safe_identifier(authentication.key_id, "key_id", authentication=True)
        nonce = _text(authentication.nonce, "nonce", maximum=128, authentication=True)
        if not _SAFE_NONCE.fullmatch(nonce):
            raise BloggerIngestError("invalid_authentication", "请求认证信息无效。")
        try:
            timestamp = int(authentication.timestamp)
        except (TypeError, ValueError) as error:
            raise BloggerIngestError("invalid_authentication", "请求认证信息无效。") from error
        if timestamp < 1:
            raise BloggerIngestError("invalid_authentication", "请求认证信息无效。")
        current = int(time.time() if now is None else now)
        if abs(current - timestamp) > self.max_clock_skew_seconds:
            raise BloggerIngestError("expired_request", "请求时间已超出允许窗口。")

        supplied_digest = str(authentication.content_sha256 or "").lower()
        if not _HEX_64.fullmatch(supplied_digest):
            raise BloggerIngestError("invalid_authentication", "请求正文摘要无效。")

        secret = self._secrets.get((node_id, key_id))
        usable_secret = secret if secret else b"\x00"
        expected = hmac.new(
            usable_secret,
            canonical_request_bytes(
                method=method,
                path=path,
                node_id=node_id,
                key_id=key_id,
                timestamp=str(timestamp),
                nonce=nonce,
                content_sha256=supplied_digest,
            ),
            hashlib.sha256,
        ).hexdigest()
        supplied_signature = str(authentication.signature or "").lower()
        valid = bool(_HEX_64.fullmatch(supplied_signature)) and hmac.compare_digest(
            supplied_signature,
            expected,
        )
        if not secret or not valid:
            raise BloggerIngestError("invalid_signature", "请求签名验证失败。")
        return VerifiedRequest(
            node_id=node_id,
            key_id=key_id,
            timestamp=timestamp,
            nonce=nonce,
            content_sha256=supplied_digest,
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BloggerIngestError("invalid_manifest", "清单包含重复字段。")
        value[key] = item
    return value


def _invalid_constant(_: str) -> None:
    raise BloggerIngestError("invalid_manifest", "清单包含无效数字。")


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BloggerIngestError("invalid_manifest", f"{name} 必须是对象。")
    return value


def _allowed(value: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(value) - fields:
        raise BloggerIngestError("invalid_manifest", f"{name} 包含未允许字段。")


def _text(
    value: object,
    name: str,
    *,
    maximum: int,
    required: bool = True,
    authentication: bool = False,
) -> str:
    code = "invalid_authentication" if authentication else "invalid_manifest"
    if not isinstance(value, str):
        raise BloggerIngestError(code, f"{name} 必须是字符串。")
    cleaned = value.strip()
    if (required and not cleaned) or len(cleaned) > maximum or _CONTROL_CHARACTER.search(cleaned):
        raise BloggerIngestError(code, f"{name} 长度或字符无效。")
    return cleaned


def _safe_identifier(value: object, name: str, *, authentication: bool = False) -> str:
    text = _text(value, name, maximum=256, authentication=authentication)
    if not _SAFE_ID.fullmatch(text):
        code = "invalid_authentication" if authentication else "invalid_manifest"
        raise BloggerIngestError(code, f"{name} 格式无效。")
    return text


def _integer(value: object, name: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BloggerIngestError("invalid_manifest", f"{name} 数值无效。")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise BloggerIngestError("invalid_manifest", f"{name} 必须是布尔值。")
    return value


def _timestamp(value: object, name: str, *, required: bool = True) -> str:
    text = _text(value, name, maximum=64, required=required)
    if not text and not required:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise BloggerIngestError("invalid_manifest", f"{name} 时间格式无效。") from error
    if parsed.tzinfo is None:
        raise BloggerIngestError("invalid_manifest", f"{name} 必须包含时区。")
    return parsed.isoformat()


def _sha256(value: object, name: str) -> str:
    text = _text(value, name, maximum=64).lower()
    if not _HEX_64.fullmatch(text):
        raise BloggerIngestError("invalid_manifest", f"{name} 不是有效 SHA-256。")
    return text


def _uuid(value: object, name: str) -> str:
    text = _text(value, name, maximum=64)
    try:
        return str(uuid.UUID(text))
    except ValueError as error:
        raise BloggerIngestError("invalid_manifest", f"{name} 必须是 UUID。") from error


def _filename(value: object, name: str) -> str:
    text = _text(value, name, maximum=255)
    if "/" in text or "\\" in text or text in {".", ".."} or text.endswith((" ", ".")):
        raise BloggerIngestError("invalid_manifest", f"{name} 不能包含路径或无效结尾。")
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise BloggerIngestError("invalid_manifest", f"{name} 是系统保留名称。")
    return text


def _sanitize_media(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_MANIFEST_MEDIA:
        raise BloggerIngestError("invalid_manifest", "media 必须是协议上限内的数组。")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        media = _mapping(raw, f"media[{index}]")
        _allowed(media, {"media_id", "role", "filename", "mime_type", "size_bytes", "sha256", "ordinal"}, f"media[{index}]")
        role = _text(media.get("role"), f"media[{index}].role", maximum=16)
        if role not in _ALLOWED_MEDIA_ROLES:
            raise BloggerIngestError("invalid_manifest", f"media[{index}].role 无效。")
        mime_type = _text(
            media.get("mime_type"),
            f"media[{index}].mime_type",
            maximum=100,
        ).casefold()
        if mime_type not in V1_MEDIA_MIME_TYPES[role]:
            raise BloggerIngestError(
                "invalid_manifest",
                f"media[{index}] 的 role 与 mime_type 不受 v1 支持。",
            )
        result.append(
            {
                "media_id": _safe_identifier(media.get("media_id"), f"media[{index}].media_id"),
                "role": role,
                "filename": _filename(media.get("filename"), f"media[{index}].filename"),
                "mime_type": mime_type,
                "size_bytes": _integer(
                    media.get("size_bytes"),
                    f"media[{index}].size_bytes",
                    minimum=1,
                    maximum=MAX_MEDIA_BYTES,
                ),
                "sha256": _sha256(media.get("sha256"), f"media[{index}].sha256"),
                "ordinal": _integer(media.get("ordinal"), f"media[{index}].ordinal", maximum=10_000),
            }
        )
    media_ids = [item["media_id"] for item in result]
    if len(media_ids) != len(set(media_ids)):
        raise BloggerIngestError("invalid_manifest", "media 包含重复 media_id。")
    result.sort(key=lambda item: (item["ordinal"], item["role"], item["media_id"]))
    return result


def _sanitize_comment_snapshot(value: object) -> dict[str, Any]:
    snapshot = _mapping(value, "comment_snapshot")
    _allowed(
        snapshot,
        {
            "snapshot_id",
            "captured_at",
            "complete",
            "expected_total",
            "captured_count",
            "top_level_count",
            "reply_groups",
            "reply_groups_incomplete",
            "missing_replies",
            "orphan_replies",
            "rules_version",
            "bundle",
        },
        "comment_snapshot",
    )
    bundle = _mapping(snapshot.get("bundle"), "comment_snapshot.bundle")
    _allowed(
        bundle,
        {
            "bundle_id",
            "format",
            "content_encoding",
            "item_count",
            "size_bytes",
            "sha256",
            "uncompressed_size_bytes",
            "uncompressed_sha256",
        },
        "comment_snapshot.bundle",
    )
    item_count = _integer(
        bundle.get("item_count"),
        "comment_snapshot.bundle.item_count",
        maximum=MAX_COMMENT_ITEMS,
    )
    captured_count = _integer(
        snapshot.get("captured_count"),
        "comment_snapshot.captured_count",
        maximum=MAX_COMMENT_ITEMS,
    )
    if item_count != captured_count:
        raise BloggerIngestError("invalid_manifest", "评论包条数与 captured_count 不一致。")
    bundle_format = _text(bundle.get("format"), "comment_snapshot.bundle.format", maximum=64)
    encoding = _text(bundle.get("content_encoding"), "comment_snapshot.bundle.content_encoding", maximum=16)
    if bundle_format != COMMENT_BUNDLE_FORMAT or encoding != COMMENT_BUNDLE_ENCODING:
        raise BloggerIngestError("invalid_manifest", "评论包格式无效。")
    return {
        "snapshot_id": _safe_identifier(snapshot.get("snapshot_id"), "comment_snapshot.snapshot_id"),
        "captured_at": _timestamp(snapshot.get("captured_at"), "comment_snapshot.captured_at"),
        "complete": _boolean(snapshot.get("complete"), "comment_snapshot.complete"),
        "expected_total": _integer(snapshot.get("expected_total"), "comment_snapshot.expected_total", maximum=MAX_COMMENT_ITEMS),
        "captured_count": captured_count,
        "top_level_count": _integer(snapshot.get("top_level_count"), "comment_snapshot.top_level_count", maximum=MAX_COMMENT_ITEMS),
        "reply_groups": _integer(snapshot.get("reply_groups"), "comment_snapshot.reply_groups", maximum=MAX_COMMENT_ITEMS),
        "reply_groups_incomplete": _integer(snapshot.get("reply_groups_incomplete"), "comment_snapshot.reply_groups_incomplete", maximum=MAX_COMMENT_ITEMS),
        "missing_replies": _integer(snapshot.get("missing_replies"), "comment_snapshot.missing_replies", maximum=MAX_COMMENT_ITEMS),
        "orphan_replies": _integer(snapshot.get("orphan_replies"), "comment_snapshot.orphan_replies", maximum=MAX_COMMENT_ITEMS),
        "rules_version": _text(snapshot.get("rules_version"), "comment_snapshot.rules_version", maximum=64),
        "bundle": {
            "bundle_id": _safe_identifier(bundle.get("bundle_id"), "comment_snapshot.bundle.bundle_id"),
            "format": bundle_format,
            "content_encoding": encoding,
            "item_count": item_count,
            "size_bytes": _integer(
                bundle.get("size_bytes"),
                "comment_snapshot.bundle.size_bytes",
                minimum=1,
                maximum=MAX_COMMENT_BUNDLE_BYTES,
            ),
            "sha256": _sha256(bundle.get("sha256"), "comment_snapshot.bundle.sha256"),
            "uncompressed_size_bytes": _integer(
                bundle.get("uncompressed_size_bytes"),
                "comment_snapshot.bundle.uncompressed_size_bytes",
                maximum=2 * 1024 * 1024 * 1024,
            ),
            "uncompressed_sha256": _sha256(
                bundle.get("uncompressed_sha256"),
                "comment_snapshot.bundle.uncompressed_sha256",
            ),
        },
    }


def decode_manifest(body: bytes) -> ValidatedManifest:
    if len(body) > MAX_MANIFEST_BYTES:
        raise BloggerIngestError("manifest_too_large", "清单超过 1 MiB 上限。")
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except UnicodeDecodeError as error:
        raise BloggerIngestError("invalid_manifest", "清单必须使用 UTF-8。") from error
    except json.JSONDecodeError as error:
        raise BloggerIngestError("invalid_manifest", "清单不是有效 JSON。") from error
    payload = _mapping(payload, "清单")
    _allowed(
        payload,
        {
            "schema_version",
            "captured_at",
            "collector",
            "creator",
            "work",
            "media",
            "comment_snapshot",
            "revision_sha256",
            "transfer_id",
        },
        "清单",
    )
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise BloggerIngestError("unsupported_schema", "清单契约版本不受支持。")

    collector = _mapping(payload.get("collector"), "collector")
    _allowed(collector, {"node_id", "key_id", "version", "source_sequence"}, "collector")
    normalized_collector = {
        "node_id": _safe_identifier(collector.get("node_id"), "collector.node_id"),
        "key_id": _safe_identifier(collector.get("key_id"), "collector.key_id"),
        "version": _text(collector.get("version"), "collector.version", maximum=64),
        "source_sequence": _integer(
            collector.get("source_sequence"),
            "collector.source_sequence",
            minimum=1,
        ),
    }

    creator = _mapping(payload.get("creator"), "creator")
    _allowed(creator, {"creator_id", "display_name", "platform", "platform_user_id"}, "creator")
    normalized_creator = {
        "creator_id": _uuid(creator.get("creator_id"), "creator.creator_id"),
        "display_name": _text(creator.get("display_name"), "creator.display_name", maximum=160),
        "platform": _text(creator.get("platform"), "creator.platform", maximum=32),
        "platform_user_id": _safe_identifier(creator.get("platform_user_id"), "creator.platform_user_id"),
    }

    work = _mapping(payload.get("work"), "work")
    _allowed(
        work,
        {
            "platform",
            "source_work_id",
            "revision",
            "work_type",
            "title",
            "description",
            "source_url",
            "cover_url",
            "published_at",
        },
        "work",
    )
    work_type = _text(work.get("work_type"), "work.work_type", maximum=16)
    if work_type not in _ALLOWED_WORK_TYPES:
        raise BloggerIngestError("invalid_manifest", "work.work_type 无效。")
    normalized_work = {
        "platform": _text(work.get("platform"), "work.platform", maximum=32),
        "source_work_id": _safe_identifier(work.get("source_work_id"), "work.source_work_id"),
        "revision": _integer(work.get("revision"), "work.revision", minimum=1),
        "work_type": work_type,
        "title": _text(work.get("title"), "work.title", maximum=1000, required=False),
        "description": _text(work.get("description"), "work.description", maximum=20_000, required=False),
        "source_url": _text(work.get("source_url"), "work.source_url", maximum=4000, required=False),
        "cover_url": _text(work.get("cover_url"), "work.cover_url", maximum=4000, required=False),
        "published_at": _timestamp(work.get("published_at"), "work.published_at", required=False),
    }

    normalized_media = _sanitize_media(payload.get("media"))
    normalized_comments = _sanitize_comment_snapshot(payload.get("comment_snapshot"))
    if normalized_comments["bundle"]["bundle_id"] in {
        item["media_id"] for item in normalized_media
    }:
        raise BloggerIngestError("invalid_manifest", "artifact_id 在清单中不唯一。")
    without_hashes: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "captured_at": _timestamp(payload.get("captured_at"), "captured_at"),
        "collector": normalized_collector,
        "creator": normalized_creator,
        "work": normalized_work,
        "media": normalized_media,
        "comment_snapshot": normalized_comments,
    }
    expected_revision_sha256 = _body_digest(canonical_json_bytes(without_hashes))
    supplied_revision_sha256 = _sha256(payload.get("revision_sha256"), "revision_sha256")
    if not hmac.compare_digest(expected_revision_sha256, supplied_revision_sha256):
        raise BloggerIngestError("revision_digest_mismatch", "清单修订摘要不一致。")

    identity = "\n".join(
        (
            normalized_collector["node_id"],
            normalized_creator["creator_id"],
            normalized_work["platform"],
            normalized_work["source_work_id"],
            str(normalized_work["revision"]),
            expected_revision_sha256,
        )
    ).encode("utf-8")
    expected_transfer_id = _body_digest(identity)
    supplied_transfer_id = _sha256(payload.get("transfer_id"), "transfer_id")
    if not hmac.compare_digest(expected_transfer_id, supplied_transfer_id):
        raise BloggerIngestError("transfer_id_mismatch", "transfer_id 与清单身份不一致。")

    normalized = dict(without_hashes)
    normalized["revision_sha256"] = expected_revision_sha256
    normalized["transfer_id"] = expected_transfer_id
    canonical_json = canonical_json_bytes(normalized).decode("utf-8")
    return ValidatedManifest(
        transfer_id=expected_transfer_id,
        revision_sha256=expected_revision_sha256,
        collector_node_id=normalized_collector["node_id"],
        collector_key_id=normalized_collector["key_id"],
        collector_source_sequence=normalized_collector["source_sequence"],
        creator_id=normalized_creator["creator_id"],
        creator_display_name=normalized_creator["display_name"],
        creator_platform=normalized_creator["platform"],
        platform_user_id=normalized_creator["platform_user_id"],
        work_platform=normalized_work["platform"],
        source_work_id=normalized_work["source_work_id"],
        source_revision=normalized_work["revision"],
        normalized=normalized,
        canonical_json=canonical_json,
    )


class BloggerIngestStore:
    """A Git-external SQLite ledger isolated from Instant AI news and Model Mr."""

    def __init__(self, root: Path = DEFAULT_BLOGGER_AGENT_ROOT) -> None:
        self.root = Path(root)
        self.database_root = self.root / "database"
        self.database_path = self.database_root / "blogger_ingest.db"
        self.staging_root = self.root / "staging"
        self.artifact_root = self.root / "artifacts"
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.database_path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.root, 0o750)
            os.chmod(self.database_root, 0o750)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS replay_nonces (
                    node_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    request_timestamp INTEGER NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (node_id, nonce)
                );
                CREATE INDEX IF NOT EXISTS idx_replay_nonces_expires_at
                    ON replay_nonces(expires_at);
                CREATE TABLE IF NOT EXISTS transfers (
                    transfer_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    collector_node_id TEXT NOT NULL,
                    collector_key_id TEXT NOT NULL,
                    collector_source_sequence INTEGER NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    revision_sha256 TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    creator_display_name TEXT NOT NULL,
                    creator_platform TEXT NOT NULL,
                    platform_user_id TEXT NOT NULL,
                    work_platform TEXT NOT NULL,
                    source_work_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL CHECK(source_revision > 0),
                    opaque_work_key TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('accepted', 'stale', 'superseded')),
                    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
                    transport_status TEXT NOT NULL
                        CHECK(transport_status IN ('accepted', 'transport_completed')),
                    received_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    manifest_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_transfers_work_revision
                    ON transfers(work_platform, creator_id, source_work_id, source_revision DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_transfers_current_work
                    ON transfers(work_platform, creator_id, source_work_id)
                    WHERE is_current = 1;
                """
            )
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (str(STORE_SCHEMA_VERSION),),
                )
                existing_version = STORE_SCHEMA_VERSION
            else:
                try:
                    existing_version = int(row["value"])
                except (TypeError, ValueError) as error:
                    raise BloggerIngestError(
                        "unsupported_store_schema",
                        "博主接收账本版本不受支持。",
                    ) from error
            if existing_version not in {2, STORE_SCHEMA_VERSION}:
                raise BloggerIngestError("unsupported_store_schema", "博主接收账本版本不受支持。")
            columns = {
                str(item[1]) for item in connection.execute("PRAGMA table_info(transfers)")
            }
            if "opaque_work_key" not in columns:
                connection.execute(
                    "ALTER TABLE transfers ADD COLUMN opaque_work_key TEXT NOT NULL DEFAULT ''"
                )
            if "transport_status" not in columns:
                connection.execute(
                    "ALTER TABLE transfers ADD COLUMN transport_status TEXT NOT NULL DEFAULT 'accepted'"
                )
            if "completed_at" not in columns:
                connection.execute("ALTER TABLE transfers ADD COLUMN completed_at INTEGER")

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    transfer_id TEXT NOT NULL REFERENCES transfers(transfer_id) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL,
                    artifact_kind TEXT NOT NULL
                        CHECK(artifact_kind IN ('media', 'comment_bundle')),
                    expected_size_bytes INTEGER NOT NULL CHECK(expected_size_bytes > 0),
                    expected_sha256 TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    source_filename TEXT NOT NULL DEFAULT '',
                    media_role TEXT NOT NULL DEFAULT '',
                    ordinal INTEGER NOT NULL DEFAULT 0,
                    uncompressed_size_bytes INTEGER,
                    uncompressed_sha256 TEXT,
                    item_count INTEGER,
                    state TEXT NOT NULL CHECK(state IN ('pending', 'verified')),
                    stored_relative_path TEXT,
                    verified_at INTEGER,
                    PRIMARY KEY(transfer_id, artifact_kind, artifact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_transfer_state
                    ON artifacts(transfer_id, state, artifact_kind, artifact_id);
                CREATE TABLE IF NOT EXISTS processing_queue (
                    transfer_id TEXT PRIMARY KEY REFERENCES transfers(transfer_id) ON DELETE CASCADE,
                    opaque_work_key TEXT NOT NULL,
                    processing_status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_processing_queue_status
                    ON processing_queue(processing_status, created_at, transfer_id);
                """
            )
            rows = connection.execute(
                """
                SELECT transfer_id, work_platform, creator_id, source_work_id,
                       is_current, manifest_json
                FROM transfers
                """
            ).fetchall()
            for transfer in rows:
                work_key = opaque_work_key(
                    transfer["work_platform"],
                    transfer["creator_id"],
                    transfer["source_work_id"],
                )
                connection.execute(
                    "UPDATE transfers SET opaque_work_key=? WHERE transfer_id=?",
                    (work_key, transfer["transfer_id"]),
                )
                if bool(transfer["is_current"]):
                    self._insert_manifest_artifacts(
                        connection,
                        transfer["transfer_id"],
                        json.loads(transfer["manifest_json"]),
                    )
            if existing_version != STORE_SCHEMA_VERSION:
                connection.execute(
                    "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                    (str(STORE_SCHEMA_VERSION),),
                )

    @staticmethod
    def _insert_manifest_artifacts(
        connection: sqlite3.Connection,
        transfer_id: str,
        manifest: Mapping[str, Any],
    ) -> None:
        for media in manifest["media"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO artifacts(
                    transfer_id, artifact_id, artifact_kind,
                    expected_size_bytes, expected_sha256, mime_type,
                    source_filename, media_role, ordinal, state
                ) VALUES(?, ?, 'media', ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    transfer_id,
                    media["media_id"],
                    media["size_bytes"],
                    media["sha256"],
                    media["mime_type"],
                    media["filename"],
                    media["role"],
                    media["ordinal"],
                ),
            )
        bundle = manifest["comment_snapshot"]["bundle"]
        connection.execute(
            """
            INSERT OR IGNORE INTO artifacts(
                transfer_id, artifact_id, artifact_kind,
                expected_size_bytes, expected_sha256, mime_type,
                uncompressed_size_bytes, uncompressed_sha256, item_count, state
            ) VALUES(?, ?, 'comment_bundle', ?, ?, 'application/gzip', ?, ?, ?, 'pending')
            """,
            (
                transfer_id,
                bundle["bundle_id"],
                bundle["size_bytes"],
                bundle["sha256"],
                bundle["uncompressed_size_bytes"],
                bundle["uncompressed_sha256"],
                bundle["item_count"],
            ),
        )

    def consume_nonce(
        self,
        *,
        node_id: str,
        key_id: str,
        nonce: str,
        request_timestamp: int,
        request_sha256: str,
        seen_at: int,
        expires_at: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM replay_nonces WHERE expires_at < ?", (seen_at,))
                connection.execute(
                    """
                    INSERT INTO replay_nonces(
                        node_id, nonce, key_id, request_timestamp,
                        request_sha256, seen_at, expires_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node_id,
                        nonce,
                        key_id,
                        request_timestamp,
                        request_sha256,
                        seen_at,
                        expires_at,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise BloggerIngestError("replayed_nonce", "清单请求 nonce 已使用。") from error
            except Exception:
                connection.rollback()
                raise

    def accept_manifest(
        self,
        manifest: ValidatedManifest,
        *,
        request_sha256: str,
        received_at: int,
    ) -> IngestReceipt:
        identity = (manifest.work_platform, manifest.creator_id, manifest.source_work_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prior_transfer = connection.execute(
                    "SELECT * FROM transfers WHERE transfer_id=?",
                    (manifest.transfer_id,),
                ).fetchone()
                if prior_transfer is not None:
                    same_revision = hmac.compare_digest(
                        prior_transfer["revision_sha256"],
                        manifest.revision_sha256,
                    )
                    same_manifest = hmac.compare_digest(
                        str(prior_transfer["manifest_json"]).encode("utf-8"),
                        manifest.canonical_json.encode("utf-8"),
                    )
                    if not same_revision or not same_manifest:
                        raise BloggerIngestError("transfer_conflict", "transfer_id 对应了不同清单。")
                    current_revision = self._current_revision(connection, identity)
                    duplicate_status = "duplicate" if bool(prior_transfer["is_current"]) else "stale"
                    connection.commit()
                    return IngestReceipt(
                        status=duplicate_status,
                        transfer_id=manifest.transfer_id,
                        receipt_id=prior_transfer["transfer_id"],
                        revision_sha256=manifest.revision_sha256,
                        work_revision=manifest.source_revision,
                        current_revision=current_revision,
                        missing_artifacts=(
                            self._pending_artifacts(connection, manifest.transfer_id)
                            if bool(prior_transfer["is_current"])
                            else ()
                        ),
                    )

                current = connection.execute(
                    """
                    SELECT * FROM transfers
                    WHERE work_platform=? AND creator_id=? AND source_work_id=? AND is_current=1
                    """,
                    identity,
                ).fetchone()
                if current is not None and manifest.source_revision == int(current["source_revision"]):
                    if hmac.compare_digest(current["revision_sha256"], manifest.revision_sha256):
                        connection.commit()
                        return IngestReceipt(
                            status="duplicate",
                            transfer_id=manifest.transfer_id,
                            receipt_id=current["transfer_id"],
                            revision_sha256=manifest.revision_sha256,
                            work_revision=manifest.source_revision,
                            current_revision=int(current["source_revision"]),
                            missing_artifacts=self._pending_artifacts(
                                connection,
                                current["transfer_id"],
                            ),
                        )
                    raise BloggerIngestError(
                        "revision_conflict",
                        "同一作品 revision 对应了不同清单。",
                    )

                state = "accepted"
                is_current = True
                current_revision = manifest.source_revision
                if current is not None and manifest.source_revision < int(current["source_revision"]):
                    state = "stale"
                    is_current = False
                    current_revision = int(current["source_revision"])
                elif current is not None:
                    connection.execute(
                        "UPDATE transfers SET state='superseded', is_current=0 WHERE transfer_id=?",
                        (current["transfer_id"],),
                    )

                connection.execute(
                    """
                    INSERT INTO transfers(
                        transfer_id, schema_version, collector_node_id, collector_key_id,
                        collector_source_sequence, request_sha256, revision_sha256,
                        creator_id, creator_display_name, creator_platform, platform_user_id,
                        work_platform, source_work_id, source_revision, opaque_work_key,
                        state, is_current, transport_status, received_at, manifest_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.transfer_id,
                        MANIFEST_SCHEMA_VERSION,
                        manifest.collector_node_id,
                        manifest.collector_key_id,
                        manifest.collector_source_sequence,
                        request_sha256,
                        manifest.revision_sha256,
                        manifest.creator_id,
                        manifest.creator_display_name,
                        manifest.creator_platform,
                        manifest.platform_user_id,
                        manifest.work_platform,
                        manifest.source_work_id,
                        manifest.source_revision,
                        opaque_work_key(
                            manifest.work_platform,
                            manifest.creator_id,
                            manifest.source_work_id,
                        ),
                        state,
                        int(is_current),
                        "accepted",
                        received_at,
                        manifest.canonical_json,
                    ),
                )
                if is_current:
                    self._insert_manifest_artifacts(
                        connection,
                        manifest.transfer_id,
                        manifest.normalized,
                    )
                connection.commit()
                return IngestReceipt(
                    status=state,
                    transfer_id=manifest.transfer_id,
                    receipt_id=manifest.transfer_id,
                    revision_sha256=manifest.revision_sha256,
                    work_revision=manifest.source_revision,
                    current_revision=current_revision,
                    missing_artifacts=(
                        self._pending_artifacts(connection, manifest.transfer_id)
                        if is_current
                        else ()
                    ),
                )
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _current_revision(connection: sqlite3.Connection, identity: tuple[str, str, str]) -> int:
        row = connection.execute(
            """
            SELECT source_revision FROM transfers
            WHERE work_platform=? AND creator_id=? AND source_work_id=? AND is_current=1
            """,
            identity,
        ).fetchone()
        return int(row["source_revision"]) if row is not None else 0

    @staticmethod
    def _pending_artifacts(
        connection: sqlite3.Connection,
        transfer_id: str,
    ) -> tuple[dict[str, Any], ...]:
        rows = connection.execute(
            """
            SELECT artifact_id, artifact_kind
            FROM artifacts
            WHERE transfer_id=? AND state='pending'
            ORDER BY CASE artifact_kind WHEN 'media' THEN 0 ELSE 1 END, artifact_id
            """,
            (transfer_id,),
        ).fetchall()
        return tuple(
            {
                "artifact_id": row["artifact_id"],
                "artifact_kind": row["artifact_kind"],
            }
            for row in rows
        )

    def get_artifact(
        self,
        *,
        transfer_id: str,
        artifact_kind: str,
        artifact_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, t.collector_node_id, t.collector_key_id,
                       t.state AS transfer_state, t.is_current, t.transport_status
                FROM artifacts AS a
                JOIN transfers AS t ON t.transfer_id=a.transfer_id
                WHERE a.transfer_id=? AND a.artifact_kind=? AND a.artifact_id=?
                """,
                (transfer_id, artifact_kind, artifact_id),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["is_current"] = bool(value["is_current"])
        return value

    def mark_artifact_verified(
        self,
        *,
        transfer_id: str,
        artifact_kind: str,
        artifact_id: str,
        size_bytes: int,
        sha256: str,
        stored_relative_path: str,
        verified_at: int,
    ) -> str:
        relative = PurePosixPath(stored_relative_path)
        if (
            relative.is_absolute()
            or str(relative) != stored_relative_path
            or not relative.parts
            or relative.parts[0] != "artifacts"
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise BloggerIngestError("artifact_storage_error", "artifact 保存路径无效。")
        relative_text = str(relative)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE transfer_id=? AND artifact_kind=? AND artifact_id=?
                    """,
                    (transfer_id, artifact_kind, artifact_id),
                ).fetchone()
                if row is None:
                    raise BloggerIngestError("artifact_not_declared", "artifact 未在清单中声明。")
                if int(row["expected_size_bytes"]) != int(size_bytes) or not hmac.compare_digest(
                    str(row["expected_sha256"]),
                    str(sha256),
                ):
                    raise BloggerIngestError("artifact_conflict", "artifact 内容与清单不一致。")
                if row["state"] == "verified":
                    same_path = str(row["stored_relative_path"] or "") == relative_text
                    if not same_path:
                        raise BloggerIngestError("artifact_conflict", "artifact 已对应不同保存结果。")
                    connection.commit()
                    return "duplicate"
                connection.execute(
                    """
                    UPDATE artifacts
                    SET state='verified', stored_relative_path=?, verified_at=?
                    WHERE transfer_id=? AND artifact_kind=? AND artifact_id=?
                    """,
                    (
                        relative_text,
                        verified_at,
                        transfer_id,
                        artifact_kind,
                        artifact_id,
                    ),
                )
                connection.commit()
                return "verified"
            except Exception:
                connection.rollback()
                raise

    def complete_transfer(
        self,
        transfer_id: str,
        *,
        node_id: str,
        key_id: str,
        completed_at: int,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                transfer = connection.execute(
                    "SELECT * FROM transfers WHERE transfer_id=?",
                    (transfer_id,),
                ).fetchone()
                if transfer is None:
                    raise BloggerIngestError("transfer_not_found", "transfer 不存在。")
                if not hmac.compare_digest(str(transfer["collector_node_id"]), node_id) or not hmac.compare_digest(
                    str(transfer["collector_key_id"]), key_id
                ):
                    raise BloggerIngestError("collector_auth_mismatch", "请求节点与 transfer 不一致。")
                if not bool(transfer["is_current"]) or transfer["state"] != "accepted":
                    raise BloggerIngestError("stale_transfer", "非当前 transfer 不能完成。")
                artifacts = connection.execute(
                    "SELECT * FROM artifacts WHERE transfer_id=? ORDER BY artifact_kind, artifact_id",
                    (transfer_id,),
                ).fetchall()
                if any(item["state"] != "verified" for item in artifacts):
                    raise BloggerIngestError("artifacts_missing", "transfer 仍缺少 artifact。")
                for item in artifacts:
                    self._verify_stored_artifact(item)

                processing_status = "awaiting_asr_approval"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO processing_queue(
                        transfer_id, opaque_work_key, processing_status, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        transfer_id,
                        transfer["opaque_work_key"],
                        processing_status,
                        completed_at,
                        completed_at,
                    ),
                )
                queued = connection.execute(
                    "SELECT processing_status FROM processing_queue WHERE transfer_id=?",
                    (transfer_id,),
                ).fetchone()
                if queued is None or queued["processing_status"] != processing_status:
                    raise BloggerIngestError(
                        "processing_conflict",
                        "transfer 已对应不同处理状态。",
                    )
                connection.execute(
                    """
                    UPDATE transfers
                    SET transport_status='transport_completed', completed_at=COALESCE(completed_at, ?)
                    WHERE transfer_id=?
                    """,
                    (completed_at, transfer_id),
                )
                connection.commit()
                return {
                    "status": "completed",
                    "receipt_id": transfer_id,
                    "transfer_id": transfer_id,
                    "transport_completed": True,
                    "artifacts_verified": True,
                    "intelligence_status": processing_status,
                }
            except Exception:
                connection.rollback()
                raise

    def _stored_artifact_components(
        self,
        stored_relative_path: str,
    ) -> tuple[Path, tuple[str, ...], Path]:
        relative = PurePosixPath(stored_relative_path)
        parts = relative.parts
        if (
            not stored_relative_path
            or relative.is_absolute()
            or str(relative) != stored_relative_path
            or len(parts) < 3
            or parts[0] != "artifacts"
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise BloggerIngestError("artifact_storage_error", "artifact 保存路径无效。")
        root = Path(os.path.abspath(self.root))
        target = root.joinpath(*parts)
        try:
            target.relative_to(root)
        except ValueError as error:
            raise BloggerIngestError("artifact_storage_error", "artifact 保存路径无效。") from error
        return root, parts, target

    @staticmethod
    def _is_symlink_like(metadata: os.stat_result) -> bool:
        if stat.S_ISLNK(metadata.st_mode):
            return True
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(reparse_flag and getattr(metadata, "st_file_attributes", 0) & reparse_flag)

    def _open_stored_artifact(self, stored_relative_path: str) -> int:
        root, parts, target = self._stored_artifact_components(stored_relative_path)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        cloexec = getattr(os, "O_CLOEXEC", 0)
        binary = getattr(os, "O_BINARY", 0)

        if nofollow and directory_flag and os.open in os.supports_dir_fd:
            directory_descriptor = -1
            try:
                directory_descriptor = os.open(
                    root,
                    os.O_RDONLY | directory_flag | nofollow | cloexec,
                )
                if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                    raise BloggerIngestError(
                        "artifact_storage_error",
                        "artifact 根目录不是普通目录。",
                    )
                for component in parts[:-1]:
                    next_descriptor = os.open(
                        component,
                        os.O_RDONLY | directory_flag | nofollow | cloexec,
                        dir_fd=directory_descriptor,
                    )
                    os.close(directory_descriptor)
                    directory_descriptor = next_descriptor
                    if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                        raise BloggerIngestError(
                            "artifact_storage_error",
                            "artifact 父链不是普通目录。",
                        )
                return os.open(
                    parts[-1],
                    os.O_RDONLY | binary | nofollow | cloexec,
                    dir_fd=directory_descriptor,
                )
            except FileNotFoundError as error:
                raise BloggerIngestError("artifacts_missing", "已验证 artifact 文件缺失。") from error
            except BloggerIngestError:
                raise
            except OSError as error:
                raise BloggerIngestError(
                    "artifact_storage_error",
                    "artifact 不能通过安全路径打开。",
                ) from error
            finally:
                if directory_descriptor >= 0:
                    os.close(directory_descriptor)

        current = root
        try:
            for component in parts[:-1]:
                metadata = os.lstat(current)
                if self._is_symlink_like(metadata) or not stat.S_ISDIR(metadata.st_mode):
                    raise BloggerIngestError(
                        "artifact_storage_error",
                        "artifact 父链包含链接或非目录节点。",
                    )
                current = current / component
            parent_metadata = os.lstat(current)
            if self._is_symlink_like(parent_metadata) or not stat.S_ISDIR(parent_metadata.st_mode):
                raise BloggerIngestError(
                    "artifact_storage_error",
                    "artifact 父链包含链接或非目录节点。",
                )
            target_metadata = os.lstat(target)
            if self._is_symlink_like(target_metadata) or not stat.S_ISREG(target_metadata.st_mode):
                raise BloggerIngestError(
                    "artifact_storage_error",
                    "artifact 不是安全普通文件。",
                )
            descriptor = os.open(target, os.O_RDONLY | binary | nofollow | cloexec)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != target_metadata.st_dev
                or opened.st_ino != target_metadata.st_ino
            ):
                os.close(descriptor)
                raise BloggerIngestError(
                    "artifact_storage_error",
                    "artifact 打开期间发生替换。",
                )
            return descriptor
        except FileNotFoundError as error:
            raise BloggerIngestError("artifacts_missing", "已验证 artifact 文件缺失。") from error
        except BloggerIngestError:
            raise
        except OSError as error:
            raise BloggerIngestError(
                "artifact_storage_error",
                "artifact 不能通过安全路径打开。",
            ) from error

    def _verify_stored_artifact(self, item: Mapping[str, Any]) -> None:
        expected_size = int(item["expected_size_bytes"])
        expected_sha256 = str(item["expected_sha256"])
        descriptor = self._open_stored_artifact(str(item["stored_relative_path"] or ""))
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise BloggerIngestError("artifact_storage_error", "artifact 不是普通文件。")
            if before.st_size != expected_size:
                raise BloggerIngestError("artifact_conflict", "已验证 artifact 文件长度异常。")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
            after = os.fstat(descriptor)
            if (
                total != expected_size
                or after.st_size != before.st_size
                or after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or getattr(after, "st_mtime_ns", None) != getattr(before, "st_mtime_ns", None)
                or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
            ):
                raise BloggerIngestError("artifact_conflict", "已验证 artifact 文件内容异常。")
        except OSError as error:
            raise BloggerIngestError("artifact_storage_error", "artifact 完整性复核失败。") from error
        finally:
            os.close(descriptor)

    def processing_jobs(self, transfer_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if transfer_id is None:
                rows = connection.execute(
                    "SELECT * FROM processing_queue ORDER BY created_at, transfer_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM processing_queue WHERE transfer_id=?",
                    (transfer_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_transfer(self, transfer_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM transfers WHERE transfer_id=?",
                (transfer_id,),
            ).fetchone()
        return self._row_payload(row)

    def get_current(
        self,
        *,
        work_platform: str,
        creator_id: str,
        source_work_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM transfers
                WHERE work_platform=? AND creator_id=? AND source_work_id=? AND is_current=1
                """,
                (work_platform, creator_id, source_work_id),
            ).fetchone()
        return self._row_payload(row)

    def _row_payload(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["is_current"] = bool(value["is_current"])
        value["manifest"] = json.loads(value.pop("manifest_json"))
        with self._connect() as connection:
            artifacts = connection.execute(
                """
                SELECT artifact_id, artifact_kind, expected_size_bytes, expected_sha256,
                       mime_type, state, stored_relative_path, verified_at
                FROM artifacts
                WHERE transfer_id=?
                ORDER BY CASE artifact_kind WHEN 'media' THEN 0 ELSE 1 END, artifact_id
                """,
                (value["transfer_id"],),
            ).fetchall()
            processing = connection.execute(
                "SELECT processing_status FROM processing_queue WHERE transfer_id=?",
                (value["transfer_id"],),
            ).fetchone()
        value["artifacts"] = [dict(item) for item in artifacts]
        value["processing_status"] = (
            str(processing["processing_status"]) if processing is not None else None
        )
        return value


class BloggerManifestReceiver:
    """Verifies and stores manifest metadata; it never fetches media or calls AI."""

    def __init__(
        self,
        *,
        root: Path = DEFAULT_BLOGGER_AGENT_ROOT,
        secrets: Mapping[tuple[str, str], bytes],
        clock: Callable[[], float] = time.time,
        max_clock_skew_seconds: int = MAX_CLOCK_SKEW_SECONDS,
    ) -> None:
        self.store = BloggerIngestStore(root)
        self.verifier = BloggerManifestVerifier(
            secrets,
            max_clock_skew_seconds=max_clock_skew_seconds,
        )
        self.clock = clock
        self.max_clock_skew_seconds = max(1, int(max_clock_skew_seconds))

    def receive(
        self,
        body: bytes,
        authentication: ManifestAuthentication,
        *,
        method: str = "POST",
        path: str = DEFAULT_MANIFEST_PATH,
    ) -> IngestReceipt:
        if method.upper() != "POST" or path != DEFAULT_MANIFEST_PATH:
            raise BloggerIngestError("invalid_request_target", "清单请求目标不受支持。")
        now = int(self.clock())
        verified = self.verifier.verify_declared(
            authentication,
            method=method,
            path=path,
            now=now,
        )
        return self.receive_verified(
            body,
            verified,
            method=method,
            path=path,
            received_at=now,
        )

    def receive_verified(
        self,
        body: bytes,
        verified: VerifiedRequest,
        *,
        method: str = "POST",
        path: str = DEFAULT_MANIFEST_PATH,
        received_at: int | None = None,
    ) -> IngestReceipt:
        """Consume a body only after its seven-line envelope was authenticated."""

        if method.upper() != "POST" or path != DEFAULT_MANIFEST_PATH:
            raise BloggerIngestError("invalid_request_target", "清单请求目标不受支持。")
        if len(body) > MAX_MANIFEST_BYTES:
            raise BloggerIngestError("manifest_too_large", "清单超过 1 MiB 上限。")
        request_sha256 = _body_digest(body)
        if not hmac.compare_digest(verified.content_sha256, request_sha256):
            raise BloggerIngestError("content_digest_mismatch", "请求正文摘要不一致。")
        now = int(self.clock()) if received_at is None else int(received_at)
        manifest = decode_manifest(body)
        if not hmac.compare_digest(verified.node_id, manifest.collector_node_id):
            raise BloggerIngestError("collector_auth_mismatch", "请求节点与清单采集节点不一致。")
        if not hmac.compare_digest(verified.key_id, manifest.collector_key_id):
            raise BloggerIngestError("collector_auth_mismatch", "请求 key 与清单采集 key 不一致。")
        self.store.consume_nonce(
            node_id=verified.node_id,
            key_id=verified.key_id,
            nonce=verified.nonce,
            request_timestamp=verified.timestamp,
            request_sha256=request_sha256,
            seen_at=now,
            expires_at=now + self.max_clock_skew_seconds,
        )
        return self.store.accept_manifest(
            manifest,
            request_sha256=request_sha256,
            received_at=now,
        )


__all__ = [
    "BloggerIngestError",
    "BloggerIngestStore",
    "BloggerManifestReceiver",
    "BloggerManifestVerifier",
    "COMMENT_BUNDLE_ENCODING",
    "COMMENT_BUNDLE_FORMAT",
    "DEFAULT_BLOGGER_AGENT_ROOT",
    "DEFAULT_MANIFEST_PATH",
    "HEADER_CONTENT_SHA256",
    "HEADER_KEY_ID",
    "HEADER_NODE_ID",
    "HEADER_NONCE",
    "HEADER_SIGNATURE",
    "HEADER_TIMESTAMP",
    "IngestReceipt",
    "MANIFEST_SCHEMA_VERSION",
    "MAX_MANIFEST_BYTES",
    "ManifestAuthentication",
    "V1_COMMENT_ITEM_FIELDS",
    "V1_COMMENT_KINDS",
    "V1_COMMENT_SECTIONS",
    "V1_MEDIA_MIME_TYPES",
    "ValidatedManifest",
    "VerifiedRequest",
    "canonical_json_bytes",
    "canonical_request_bytes",
    "decode_manifest",
    "opaque_work_key",
    "sign_manifest",
]
