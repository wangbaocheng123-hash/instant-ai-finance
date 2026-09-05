from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


SCHEMA_VERSION = "blogger-transfer/v1"
COMMENT_BUNDLE_FORMAT = "blogger-comments/v1+ndjson"
COMMENT_BUNDLE_ENCODING = "gzip"
MAX_COMMENT_ITEMS = 50_000
MAX_MANIFEST_MEDIA = 100
MAX_MEDIA_BYTES = 10 * 1024 * 1024 * 1024
MAX_COMMENT_BUNDLE_BYTES = 512 * 1024 * 1024

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
SAFE_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
HTTP_METHOD_PATTERN = re.compile(r"^[A-Z]{3,16}$")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
ALLOWED_WORK_TYPES = {"video", "image", "gallery"}
V1_MEDIA_MIME_TYPES = {
    "video": frozenset({"video/mp4"}),
    "image": frozenset({"image/jpeg", "image/png", "image/webp"}),
    "cover": frozenset({"image/jpeg", "image/png", "image/webp"}),
}
ALLOWED_MEDIA_ROLES = frozenset(V1_MEDIA_MIME_TYPES)
ALLOWED_COMMENT_KINDS = {
    "user_comment",
    "user_reply",
    "author_comment",
    "author_reply",
}
ALLOWED_COMMENT_SECTIONS = {"fan_comment", "author_interaction"}
COMMENT_ITEM_FIELDS = (
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
)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class TransferContractError(ValueError):
    pass


NonceClaim = Callable[[str, str, int], bool]


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_text(
    value: Any,
    *,
    limit: int,
    field: str,
    required: bool = False,
) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise TransferContractError(f"{field} 不能为空。")
    if len(text) > limit:
        raise TransferContractError(f"{field} 超过长度限制。")
    if CONTROL_CHARACTER_PATTERN.search(text):
        raise TransferContractError(f"{field} 包含控制字符。")
    return text


def _safe_id(value: Any, *, field: str, required: bool = True) -> str:
    text = _safe_text(value, limit=256, field=field, required=required)
    if not text and not required:
        return ""
    if not SAFE_ID_PATTERN.fullmatch(text):
        raise TransferContractError(f"{field} 格式无效。")
    return text


def _safe_uuid(value: Any, *, field: str) -> str:
    text = _safe_text(value, limit=64, field=field, required=True)
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise TransferContractError(f"{field} 必须是 UUID。") from exc
    return str(parsed)


def _safe_int(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
) -> int:
    if isinstance(value, bool):
        raise TransferContractError(f"{field} 必须是整数。")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TransferContractError(f"{field} 必须是整数。") from exc
    if parsed < minimum or parsed > maximum:
        raise TransferContractError(f"{field} 超出允许范围。")
    return parsed


def _safe_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise TransferContractError(f"{field} 必须是布尔值。")
    return value


def _safe_optional_bool(value: Any, *, field: str) -> bool | None:
    if value is None:
        return None
    return _safe_bool(value, field=field)


def _safe_iso_datetime(
    value: Any,
    *,
    field: str,
    required: bool = True,
) -> str:
    text = _safe_text(value, limit=64, field=field, required=required)
    if not text and not required:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TransferContractError(f"{field} 不是 ISO 时间。") from exc
    if parsed.tzinfo is None:
        raise TransferContractError(f"{field} 必须包含时区。")
    return parsed.isoformat()


def safe_cross_platform_filename(value: Any, *, field: str = "filename") -> str:
    """Validate a display filename identically on Windows and Linux.

    The receiver must still generate its own storage path. This value is only
    retained as the source/display name.
    """

    text = _safe_text(value, limit=255, field=field, required=True)
    if "/" in text or "\\" in text or text in {".", ".."}:
        raise TransferContractError(f"{field} 不能包含路径。")
    if text.endswith((" ", ".")):
        raise TransferContractError(f"{field} 不能以空格或句点结尾。")
    stem = text.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        raise TransferContractError(f"{field} 是系统保留名称。")
    return text


def _safe_sha256(value: Any, *, field: str) -> str:
    text = _safe_text(value, limit=64, field=field, required=True).lower()
    if not SHA256_PATTERN.fullmatch(text):
        raise TransferContractError(f"{field} 无效。")
    return text


def sanitize_comment_item(value: Mapping[str, Any], *, index: int = 0) -> dict[str, Any]:
    field = f"comments[{index}]"
    source_comment_id = _safe_id(
        value.get("source_comment_id"),
        field=f"{field}.source_comment_id",
    )
    parent_id = _safe_id(
        value.get("parent_source_comment_id"),
        field=f"{field}.parent_source_comment_id",
        required=False,
    )
    root_id = _safe_id(
        value.get("root_source_comment_id"),
        field=f"{field}.root_source_comment_id",
        required=False,
    )
    reply_to_id = _safe_id(
        value.get("reply_to_comment_id"),
        field=f"{field}.reply_to_comment_id",
        required=False,
    )
    if parent_id == source_comment_id or reply_to_id == source_comment_id:
        raise TransferContractError(f"{field} 不能回复自身。")

    kind = _safe_text(
        value.get("kind") or "user_comment",
        limit=32,
        field=f"{field}.kind",
        required=True,
    )
    if kind not in ALLOWED_COMMENT_KINDS:
        raise TransferContractError(f"{field}.kind 无效。")
    section = _safe_text(
        value.get("section") or "fan_comment",
        limit=32,
        field=f"{field}.section",
        required=True,
    )
    if section not in ALLOWED_COMMENT_SECTIONS:
        raise TransferContractError(f"{field}.section 无效。")

    sanitized = {
        "source_comment_id": source_comment_id,
        "parent_source_comment_id": parent_id,
        "root_source_comment_id": root_id,
        "reply_to_comment_id": reply_to_id,
        "author": _safe_text(value.get("author"), limit=160, field=f"{field}.author"),
        "is_creator": _safe_bool(
            value.get("is_creator", False),
            field=f"{field}.is_creator",
        ),
        "text": _safe_text(
            value.get("text"),
            limit=20_000,
            field=f"{field}.text",
            required=True,
        ),
        "like_count": _safe_int(value.get("like_count", 0), field=f"{field}.like_count"),
        "reply_count": _safe_int(value.get("reply_count", 0), field=f"{field}.reply_count"),
        "published_at": _safe_iso_datetime(
            value.get("published_at"),
            field=f"{field}.published_at",
            required=False,
        ),
        "captured_at": _safe_iso_datetime(
            value.get("captured_at"),
            field=f"{field}.captured_at",
            required=True,
        ),
        "kind": kind,
        "section": section,
        "sentiment": _safe_text(
            value.get("sentiment") or "neutral",
            limit=32,
            field=f"{field}.sentiment",
        ),
        "risk_level": _safe_text(
            value.get("risk_level") or "normal",
            limit=32,
            field=f"{field}.risk_level",
        ),
        "author_liked": _safe_optional_bool(
            value.get("author_liked"),
            field=f"{field}.author_liked",
        ),
        "low_value": _safe_bool(
            value.get("low_value", False),
            field=f"{field}.low_value",
        ),
        "ip_label": _safe_text(value.get("ip_label"), limit=80, field=f"{field}.ip_label"),
        "public_label": _safe_text(
            value.get("public_label"),
            limit=160,
            field=f"{field}.public_label",
        ),
        "actual_reply_user": _safe_text(
            value.get("actual_reply_user"),
            limit=160,
            field=f"{field}.actual_reply_user",
        ),
        "display_order": _safe_int(
            value.get("display_order", 0),
            field=f"{field}.display_order",
        ),
    }
    return {name: sanitized[name] for name in COMMENT_ITEM_FIELDS}


def sanitize_comment_items(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items = [sanitize_comment_item(value, index=index) for index, value in enumerate(values)]
    if len(items) > MAX_COMMENT_ITEMS:
        raise TransferContractError(f"评论数量不能超过 {MAX_COMMENT_ITEMS} 条。")
    identifiers = [item["source_comment_id"] for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise TransferContractError("评论包包含重复 source_comment_id。")
    items.sort(
        key=lambda item: (
            item["display_order"],
            item["root_source_comment_id"] or item["source_comment_id"],
            item["parent_source_comment_id"],
            item["source_comment_id"],
        )
    )
    return items


def canonical_comment_ndjson_bytes(values: Iterable[Mapping[str, Any]]) -> bytes:
    items = sanitize_comment_items(values)
    return b"".join(canonical_json_bytes(item) + b"\n" for item in items)


def build_comment_bundle(
    values: Iterable[Mapping[str, Any]],
) -> tuple[bytes, dict[str, Any]]:
    items = sanitize_comment_items(values)
    plain = b"".join(canonical_json_bytes(item) + b"\n" for item in items)
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as handle:
        handle.write(plain)
    compressed = output.getvalue()
    descriptor = {
        "bundle_id": sha256_bytes(compressed),
        "format": COMMENT_BUNDLE_FORMAT,
        "content_encoding": COMMENT_BUNDLE_ENCODING,
        "item_count": len(items),
        "size_bytes": len(compressed),
        "sha256": sha256_bytes(compressed),
        "uncompressed_size_bytes": len(plain),
        "uncompressed_sha256": sha256_bytes(plain),
    }
    return compressed, descriptor


def _sanitize_media(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise TransferContractError("media 必须是数组。")
    if len(values) > MAX_MANIFEST_MEDIA:
        raise TransferContractError("作品媒体数量超过协议限制。")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            raise TransferContractError(f"media[{index}] 必须是对象。")
        role = _safe_text(
            item.get("role"),
            limit=16,
            field=f"media[{index}].role",
            required=True,
        )
        if role not in ALLOWED_MEDIA_ROLES:
            raise TransferContractError(f"media[{index}].role 无效。")
        mime_type = _safe_text(
            item.get("mime_type"),
            limit=100,
            field=f"media[{index}].mime_type",
            required=True,
        ).lower()
        if mime_type not in V1_MEDIA_MIME_TYPES[role]:
            raise TransferContractError(
                f"media[{index}] 的 role 与 mime_type 不受 v1 支持。"
            )
        result.append(
            {
                "media_id": _safe_id(item.get("media_id"), field=f"media[{index}].media_id"),
                "role": role,
                "filename": safe_cross_platform_filename(
                    item.get("filename"),
                    field=f"media[{index}].filename",
                ),
                "mime_type": mime_type,
                "size_bytes": _safe_int(
                    item.get("size_bytes"),
                    field=f"media[{index}].size_bytes",
                    minimum=1,
                    maximum=MAX_MEDIA_BYTES,
                ),
                "sha256": _safe_sha256(
                    item.get("sha256"),
                    field=f"media[{index}].sha256",
                ),
                "ordinal": _safe_int(
                    item.get("ordinal", 0),
                    field=f"media[{index}].ordinal",
                    maximum=10_000,
                ),
            }
        )
    media_ids = [item["media_id"] for item in result]
    if len(media_ids) != len(set(media_ids)):
        raise TransferContractError("media 包含重复 media_id。")
    result.sort(key=lambda item: (item["ordinal"], item["role"], item["media_id"]))
    return result


def _sanitize_comment_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TransferContractError("comment_snapshot 必须是对象。")
    bundle = value.get("bundle")
    if not isinstance(bundle, Mapping):
        raise TransferContractError("comment_snapshot.bundle 必须是对象。")
    item_count = _safe_int(
        bundle.get("item_count"),
        field="comment_snapshot.bundle.item_count",
        maximum=MAX_COMMENT_ITEMS,
    )
    captured_count = _safe_int(
        value.get("captured_count"),
        field="comment_snapshot.captured_count",
        maximum=MAX_COMMENT_ITEMS,
    )
    if item_count != captured_count:
        raise TransferContractError("评论包条数与 captured_count 不一致。")
    bundle_format = _safe_text(
        bundle.get("format"),
        limit=64,
        field="comment_snapshot.bundle.format",
        required=True,
    )
    if bundle_format != COMMENT_BUNDLE_FORMAT:
        raise TransferContractError("comment_snapshot.bundle.format 无效。")
    content_encoding = _safe_text(
        bundle.get("content_encoding"),
        limit=16,
        field="comment_snapshot.bundle.content_encoding",
        required=True,
    )
    if content_encoding != COMMENT_BUNDLE_ENCODING:
        raise TransferContractError("comment_snapshot.bundle.content_encoding 无效。")
    return {
        "snapshot_id": _safe_id(
            value.get("snapshot_id"),
            field="comment_snapshot.snapshot_id",
        ),
        "captured_at": _safe_iso_datetime(
            value.get("captured_at"),
            field="comment_snapshot.captured_at",
        ),
        "complete": _safe_bool(
            value.get("complete"),
            field="comment_snapshot.complete",
        ),
        "expected_total": _safe_int(
            value.get("expected_total", 0),
            field="comment_snapshot.expected_total",
            maximum=MAX_COMMENT_ITEMS,
        ),
        "captured_count": captured_count,
        "top_level_count": _safe_int(
            value.get("top_level_count", 0),
            field="comment_snapshot.top_level_count",
            maximum=MAX_COMMENT_ITEMS,
        ),
        "reply_groups": _safe_int(
            value.get("reply_groups", 0),
            field="comment_snapshot.reply_groups",
            maximum=MAX_COMMENT_ITEMS,
        ),
        "reply_groups_incomplete": _safe_int(
            value.get("reply_groups_incomplete", 0),
            field="comment_snapshot.reply_groups_incomplete",
            maximum=MAX_COMMENT_ITEMS,
        ),
        "missing_replies": _safe_int(
            value.get("missing_replies", 0),
            field="comment_snapshot.missing_replies",
            maximum=MAX_COMMENT_ITEMS,
        ),
        "orphan_replies": _safe_int(
            value.get("orphan_replies", 0),
            field="comment_snapshot.orphan_replies",
            maximum=MAX_COMMENT_ITEMS,
        ),
        "rules_version": _safe_text(
            value.get("rules_version") or "comment-rules/v1",
            limit=64,
            field="comment_snapshot.rules_version",
            required=True,
        ),
        "bundle": {
            "bundle_id": _safe_id(
                bundle.get("bundle_id"),
                field="comment_snapshot.bundle.bundle_id",
            ),
            "format": bundle_format,
            "content_encoding": content_encoding,
            "item_count": item_count,
            "size_bytes": _safe_int(
                bundle.get("size_bytes"),
                field="comment_snapshot.bundle.size_bytes",
                minimum=1,
                maximum=MAX_COMMENT_BUNDLE_BYTES,
            ),
            "sha256": _safe_sha256(
                bundle.get("sha256"),
                field="comment_snapshot.bundle.sha256",
            ),
            "uncompressed_size_bytes": _safe_int(
                bundle.get("uncompressed_size_bytes", 0),
                field="comment_snapshot.bundle.uncompressed_size_bytes",
                maximum=2 * 1024 * 1024 * 1024,
            ),
            "uncompressed_sha256": _safe_sha256(
                bundle.get("uncompressed_sha256"),
                field="comment_snapshot.bundle.uncompressed_sha256",
            ),
        },
    }


def sanitize_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if str(value.get("schema_version") or "") != SCHEMA_VERSION:
        raise TransferContractError("不支持的传输协议版本。")

    collector = value.get("collector")
    creator = value.get("creator")
    work = value.get("work")
    if not isinstance(collector, Mapping):
        raise TransferContractError("collector 必须是对象。")
    if not isinstance(creator, Mapping):
        raise TransferContractError("creator 必须是对象。")
    if not isinstance(work, Mapping):
        raise TransferContractError("work 必须是对象。")

    work_type = _safe_text(
        work.get("work_type"),
        limit=16,
        field="work.work_type",
        required=True,
    )
    if work_type not in ALLOWED_WORK_TYPES:
        raise TransferContractError("work.work_type 无效。")

    sanitized = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": _safe_iso_datetime(value.get("captured_at"), field="captured_at"),
        "collector": {
            "node_id": _safe_id(collector.get("node_id"), field="collector.node_id"),
            "key_id": _safe_id(collector.get("key_id"), field="collector.key_id"),
            "version": _safe_text(
                collector.get("version"),
                limit=64,
                field="collector.version",
                required=True,
            ),
            "source_sequence": _safe_int(
                collector.get("source_sequence"),
                field="collector.source_sequence",
                minimum=1,
            ),
        },
        "creator": {
            "creator_id": _safe_uuid(creator.get("creator_id"), field="creator.creator_id"),
            "display_name": _safe_text(
                creator.get("display_name"),
                limit=160,
                field="creator.display_name",
                required=True,
            ),
            "platform": _safe_text(
                creator.get("platform") or "douyin",
                limit=32,
                field="creator.platform",
                required=True,
            ),
            "platform_user_id": _safe_id(
                creator.get("platform_user_id"),
                field="creator.platform_user_id",
            ),
        },
        "work": {
            "platform": _safe_text(
                work.get("platform") or "douyin",
                limit=32,
                field="work.platform",
                required=True,
            ),
            "source_work_id": _safe_id(
                work.get("source_work_id"),
                field="work.source_work_id",
            ),
            "revision": _safe_int(
                work.get("revision"),
                field="work.revision",
                minimum=1,
            ),
            "work_type": work_type,
            "title": _safe_text(work.get("title"), limit=1000, field="work.title"),
            "description": _safe_text(
                work.get("description"),
                limit=20_000,
                field="work.description",
            ),
            "source_url": _safe_text(
                work.get("source_url"),
                limit=4000,
                field="work.source_url",
            ),
            "cover_url": _safe_text(
                work.get("cover_url"),
                limit=4000,
                field="work.cover_url",
            ),
            "published_at": _safe_iso_datetime(
                work.get("published_at"),
                field="work.published_at",
                required=False,
            ),
        },
        "media": _sanitize_media(value.get("media")),
        "comment_snapshot": _sanitize_comment_snapshot(value.get("comment_snapshot")),
    }
    revision_sha256 = sha256_bytes(canonical_json_bytes(sanitized))
    identity = "\n".join(
        (
            sanitized["collector"]["node_id"],
            sanitized["creator"]["creator_id"],
            sanitized["work"]["platform"],
            sanitized["work"]["source_work_id"],
            str(sanitized["work"]["revision"]),
            revision_sha256,
        )
    ).encode("utf-8")
    sanitized["revision_sha256"] = revision_sha256
    sanitized["transfer_id"] = sha256_bytes(identity)
    return sanitized


def new_manifest(
    *,
    collector_node_id: str,
    collector_key_id: str,
    collector_version: str,
    source_sequence: int,
    creator: Mapping[str, Any],
    work: Mapping[str, Any],
    work_revision: int,
    media: list[Mapping[str, Any]],
    comment_snapshot: Mapping[str, Any],
    captured_at: str | None = None,
) -> dict[str, Any]:
    return sanitize_manifest(
        {
            "schema_version": SCHEMA_VERSION,
            "captured_at": captured_at or datetime.now(UTC).isoformat(),
            "collector": {
                "node_id": collector_node_id,
                "key_id": collector_key_id,
                "version": collector_version,
                "source_sequence": source_sequence,
            },
            "creator": dict(creator),
            "work": {**dict(work), "revision": work_revision},
            "media": [dict(item) for item in media],
            "comment_snapshot": dict(comment_snapshot),
        }
    )


def revision_decision(
    *,
    existing_revision: int | None,
    existing_sha256: str | None,
    incoming_revision: int,
    incoming_sha256: str,
) -> str:
    """Return apply, duplicate or stale; reject one revision with two bodies."""

    incoming = _safe_int(incoming_revision, field="incoming_revision", minimum=1)
    incoming_hash = _safe_sha256(incoming_sha256, field="incoming_sha256")
    if existing_revision is None:
        return "apply"
    existing = _safe_int(existing_revision, field="existing_revision", minimum=1)
    existing_hash = _safe_sha256(existing_sha256, field="existing_sha256")
    if incoming < existing:
        return "stale"
    if incoming > existing:
        return "apply"
    if hmac.compare_digest(incoming_hash, existing_hash):
        return "duplicate"
    raise TransferContractError("同一作品 revision 对应了不同正文。")


def canonical_request_bytes(
    *,
    method: str,
    path: str,
    node_id: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
) -> bytes:
    normalized_method = str(method or "").upper()
    if not HTTP_METHOD_PATTERN.fullmatch(normalized_method):
        raise TransferContractError("HTTP method 无效。")
    normalized_path = str(path or "")
    if not normalized_path.startswith("/") or "\n" in normalized_path or "\r" in normalized_path:
        raise TransferContractError("请求路径无效。")
    normalized_node = _safe_id(node_id, field="node_id")
    normalized_key = _safe_id(key_id, field="key_id")
    normalized_timestamp = str(_safe_int(timestamp, field="timestamp", minimum=1))
    normalized_nonce = _safe_text(nonce, limit=128, field="nonce", required=True)
    if not SAFE_NONCE_PATTERN.fullmatch(normalized_nonce):
        raise TransferContractError("nonce 无效。")
    normalized_hash = _safe_sha256(body_sha256, field="body_sha256")
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


def request_signature(
    secret: bytes,
    *,
    method: str,
    path: str,
    node_id: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
) -> str:
    if not secret:
        raise TransferContractError("传输签名密钥不能为空。")
    message = canonical_request_bytes(
        method=method,
        path=path,
        node_id=node_id,
        key_id=key_id,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=body_sha256,
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def signed_headers(
    secret: bytes,
    body: bytes,
    *,
    method: str,
    path: str,
    node_id: str,
    key_id: str,
    now: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = str(int(time.time() if now is None else now))
    request_nonce = nonce or uuid.uuid4().hex
    body_sha256 = sha256_bytes(body)
    signature = request_signature(
        secret,
        method=method,
        path=path,
        node_id=node_id,
        key_id=key_id,
        timestamp=timestamp,
        nonce=request_nonce,
        body_sha256=body_sha256,
    )
    return {
        "X-Blogger-Node-Id": node_id,
        "X-Blogger-Key-Id": key_id,
        "X-Blogger-Timestamp": timestamp,
        "X-Blogger-Nonce": request_nonce,
        "X-Blogger-Content-SHA256": body_sha256,
        "X-Blogger-Signature": signature,
    }


def verify_request_signature(
    secret: bytes,
    *,
    method: str,
    path: str,
    node_id: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    body_sha256: str,
    signature: str,
    now: int | None = None,
    max_age_seconds: int = 300,
    nonce_claim: NonceClaim | None = None,
) -> None:
    request_epoch = _safe_int(timestamp, field="timestamp", minimum=1)
    current_epoch = int(time.time() if now is None else now)
    if abs(current_epoch - request_epoch) > max_age_seconds:
        raise TransferContractError("传输请求已过期。")
    actual_body_sha256 = sha256_bytes(body)
    supplied_hash = _safe_sha256(body_sha256, field="body_sha256")
    if not hmac.compare_digest(actual_body_sha256, supplied_hash):
        raise TransferContractError("传输正文校验失败。")
    expected = request_signature(
        secret,
        method=method,
        path=path,
        node_id=node_id,
        key_id=key_id,
        timestamp=str(request_epoch),
        nonce=nonce,
        body_sha256=supplied_hash,
    )
    supplied_signature = _safe_sha256(signature, field="signature")
    if not hmac.compare_digest(expected, supplied_signature):
        raise TransferContractError("传输签名校验失败。")
    if nonce_claim is not None and not nonce_claim(
        _safe_id(node_id, field="node_id"),
        _safe_text(nonce, limit=128, field="nonce", required=True),
        request_epoch + max_age_seconds,
    ):
        raise TransferContractError("传输请求 nonce 已使用。")
