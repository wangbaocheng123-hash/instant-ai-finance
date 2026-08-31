from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import quote, unquote

from .blogger_ingest import (
    BloggerIngestError,
    BloggerManifestReceiver,
    DEFAULT_BLOGGER_AGENT_ROOT,
    DEFAULT_MANIFEST_PATH,
    HEADER_CONTENT_SHA256,
    HEADER_KEY_ID,
    HEADER_NODE_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    MAX_MANIFEST_BYTES,
    ManifestAuthentication,
    V1_COMMENT_ITEM_FIELDS,
    V1_COMMENT_KINDS,
    V1_COMMENT_SECTIONS,
    V1_MEDIA_MIME_TYPES,
    VerifiedRequest,
)


MAX_COMPLETE_BYTES = 4 * 1024
MAX_NDJSON_LINE_BYTES = 256 * 1024
STREAM_CHUNK_BYTES = 1024 * 1024

_TRANSFER_ID = r"[0-9a-f]{64}"
_MEDIA_PATH = re.compile(
    rf"{re.escape(DEFAULT_MANIFEST_PATH)}/(?P<transfer>{_TRANSFER_ID})/media/(?P<artifact>[^/]+)"
)
_COMMENTS_PATH = re.compile(
    rf"{re.escape(DEFAULT_MANIFEST_PATH)}/(?P<transfer>{_TRANSFER_ID})/comments/(?P<artifact>[^/]+)"
)
_COMPLETE_PATH = re.compile(
    rf"{re.escape(DEFAULT_MANIFEST_PATH)}/(?P<transfer>{_TRANSFER_ID})/complete"
)
_CONTENT_LENGTH = re.compile(r"0|[1-9][0-9]*")
_SAFE_COMMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_COMMENT_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_AUTH_HEADERS = (
    HEADER_NODE_ID,
    HEADER_KEY_ID,
    HEADER_TIMESTAMP,
    HEADER_NONCE,
    HEADER_CONTENT_SHA256,
    HEADER_SIGNATURE,
)


@dataclass(frozen=True)
class BloggerHTTPResponse:
    status: int
    payload: dict[str, Any]


def _header_values(headers: Mapping[str, str], name: str) -> list[str]:
    getter = getattr(headers, "get_all", None)
    if callable(getter):
        values = getter(name)
        return [str(value) for value in (values or [])]
    return [
        str(value)
        for key, value in headers.items()
        if str(key).casefold() == name.casefold()
    ]


def _single_header(
    headers: Mapping[str, str],
    name: str,
    *,
    required: bool = True,
) -> str:
    values = _header_values(headers, name)
    if len(values) > 1:
        raise BloggerIngestError("ambiguous_header", f"{name} 不能重复。")
    value = values[0].strip() if values else ""
    if required and not value:
        raise BloggerIngestError("invalid_authentication", f"缺少请求头 {name}。")
    return value


def _authentication(headers: Mapping[str, str]) -> ManifestAuthentication:
    values = {name: _single_header(headers, name) for name in _AUTH_HEADERS}
    return ManifestAuthentication.from_headers(values)


def _content_length(headers: Mapping[str, str]) -> int:
    if any(value.strip() for value in _header_values(headers, "Transfer-Encoding")):
        raise BloggerIngestError("chunked_not_allowed", "传输接口不接受 chunked 正文。")
    value = _single_header(headers, "Content-Length", required=False)
    if not value or not _CONTENT_LENGTH.fullmatch(value):
        raise BloggerIngestError("invalid_content_length", "Content-Length 无效。")
    try:
        return int(value)
    except ValueError as error:  # defensive: the regular expression already bounds syntax
        raise BloggerIngestError("invalid_content_length", "Content-Length 无效。") from error


def _content_type(headers: Mapping[str, str]) -> str:
    return _single_header(headers, "Content-Type", required=False).split(";", 1)[0].strip().casefold()


def _read_exact(stream: BinaryIO, length: int, *, maximum: int) -> bytes:
    if length > maximum:
        raise BloggerIngestError("request_too_large", "请求正文超过上限。")
    remaining = length
    chunks: list[bytes] = []
    while remaining:
        chunk = stream.read(min(STREAM_CHUNK_BYTES, remaining))
        if not chunk:
            raise BloggerIngestError("incomplete_body", "请求正文提前中断。")
        if not isinstance(chunk, bytes):
            chunk = bytes(chunk)
        if len(chunk) > remaining:
            raise BloggerIngestError("invalid_content_length", "请求正文长度无效。")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _decode_complete(body: bytes) -> str:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise BloggerIngestError("invalid_complete", "complete 正文包含重复字段。")
            value[key] = item
        return value

    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BloggerIngestError("invalid_complete", "complete 正文无效。") from error
    if not isinstance(payload, dict) or set(payload) != {"transfer_id"}:
        raise BloggerIngestError("invalid_complete", "complete 正文结构无效。")
    transfer_id = payload.get("transfer_id")
    if not isinstance(transfer_id, str) or not re.fullmatch(_TRANSFER_ID, transfer_id):
        raise BloggerIngestError("invalid_complete", "complete transfer_id 无效。")
    return transfer_id


def _comment_text(
    value: object,
    field: str,
    *,
    maximum: int,
    required: bool = False,
) -> str:
    if not isinstance(value, str):
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 必须是字符串。")
    cleaned = value.strip()
    if (
        cleaned != value
        or (required and not cleaned)
        or len(cleaned) > maximum
        or _COMMENT_CONTROL_CHARACTER.search(cleaned)
    ):
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 长度或字符无效。")
    return cleaned


def _comment_id(value: object, field: str, *, required: bool = True) -> str:
    text = _comment_text(value, field, maximum=256, required=required)
    if not text and not required:
        return ""
    if not _SAFE_COMMENT_ID.fullmatch(text):
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 格式无效。")
    return text


def _comment_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 数值无效。")
    return value


def _comment_boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 必须是布尔值。")
    return value


def _comment_timestamp(value: object, field: str, *, required: bool) -> str:
    text = _comment_text(value, field, maximum=64, required=required)
    if not text and not required:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 时间无效。") from error
    if parsed.tzinfo is None or parsed.isoformat() != text:
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 必须是规范带时区时间。")
    return text


def _validate_comment_item(item: Mapping[str, Any], *, index: int) -> tuple[int, str, str, str]:
    field = f"comments[{index}]"
    if set(item) != V1_COMMENT_ITEM_FIELDS:
        raise BloggerIngestError(
            "invalid_comment_bundle",
            f"{field} 字段不符合 blogger-comments/v1 白名单。",
        )
    source_id = _comment_id(item["source_comment_id"], f"{field}.source_comment_id")
    parent_id = _comment_id(
        item["parent_source_comment_id"],
        f"{field}.parent_source_comment_id",
        required=False,
    )
    root_id = _comment_id(
        item["root_source_comment_id"],
        f"{field}.root_source_comment_id",
        required=False,
    )
    reply_to_id = _comment_id(
        item["reply_to_comment_id"],
        f"{field}.reply_to_comment_id",
        required=False,
    )
    if parent_id == source_id or reply_to_id == source_id:
        raise BloggerIngestError("invalid_comment_bundle", f"{field} 不能回复自身。")

    _comment_text(item["author"], f"{field}.author", maximum=160)
    _comment_boolean(item["is_creator"], f"{field}.is_creator")
    _comment_text(item["text"], f"{field}.text", maximum=20_000, required=True)
    _comment_integer(item["like_count"], f"{field}.like_count")
    _comment_integer(item["reply_count"], f"{field}.reply_count")
    _comment_timestamp(item["published_at"], f"{field}.published_at", required=False)
    _comment_timestamp(item["captured_at"], f"{field}.captured_at", required=True)
    kind = _comment_text(item["kind"], f"{field}.kind", maximum=32, required=True)
    if kind not in V1_COMMENT_KINDS:
        raise BloggerIngestError("invalid_comment_bundle", f"{field}.kind 无效。")
    section = _comment_text(item["section"], f"{field}.section", maximum=32, required=True)
    if section not in V1_COMMENT_SECTIONS:
        raise BloggerIngestError("invalid_comment_bundle", f"{field}.section 无效。")
    _comment_text(item["sentiment"], f"{field}.sentiment", maximum=32, required=True)
    _comment_text(item["risk_level"], f"{field}.risk_level", maximum=32, required=True)
    author_liked = item["author_liked"]
    if author_liked is not None:
        _comment_boolean(author_liked, f"{field}.author_liked")
    _comment_boolean(item["low_value"], f"{field}.low_value")
    _comment_text(item["ip_label"], f"{field}.ip_label", maximum=80)
    _comment_text(item["public_label"], f"{field}.public_label", maximum=160)
    _comment_text(item["actual_reply_user"], f"{field}.actual_reply_user", maximum=160)
    display_order = _comment_integer(item["display_order"], f"{field}.display_order")
    return (display_order, root_id or source_id, parent_id, source_id)


class BloggerTransferHTTP:
    """Strict machine-only HTTP application for Beijing transfer requests."""

    def __init__(
        self,
        *,
        root: Path = DEFAULT_BLOGGER_AGENT_ROOT,
        secrets: Mapping[tuple[str, str], bytes],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.receiver = BloggerManifestReceiver(root=root, secrets=secrets, clock=clock)
        self.store = self.receiver.store
        self.clock = clock
        self._commit_lock = threading.Lock()
        self._ensure_artifact_layout()

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> BloggerTransferHTTP | None:
        values = os.environ if environ is None else environ
        node_id = str(values.get("INSTANT_AI_BLOGGER_NODE_ID", "")).strip()
        key_id = str(values.get("INSTANT_AI_BLOGGER_KEY_ID", "")).strip()
        secret_hex = str(values.get("INSTANT_AI_BLOGGER_HMAC_SECRET_HEX", "")).strip()
        if not any((node_id, key_id, secret_hex)):
            return None
        if not all((node_id, key_id, secret_hex)):
            raise BloggerIngestError(
                "blogger_configuration_invalid",
                "北京传输对等配置不完整。",
            )
        try:
            secret = bytes.fromhex(secret_hex)
        except ValueError as error:
            raise BloggerIngestError(
                "blogger_configuration_invalid",
                "北京传输密钥格式无效。",
            ) from error
        if len(secret) < 32:
            raise BloggerIngestError(
                "blogger_configuration_invalid",
                "北京传输密钥长度不足。",
            )
        configured_root = str(values.get("INSTANT_AI_BLOGGER_ROOT", "")).strip()
        root = Path(configured_root) if configured_root else DEFAULT_BLOGGER_AGENT_ROOT
        return cls(root=root, secrets={(node_id, key_id): secret}, clock=clock)

    @staticmethod
    def is_candidate(raw_target: str) -> bool:
        target = str(raw_target or "")
        path = target.split("?", 1)[0]
        return path == DEFAULT_MANIFEST_PATH or path.startswith(DEFAULT_MANIFEST_PATH + "/")

    def handle(
        self,
        method: str,
        raw_target: str,
        headers: Mapping[str, str],
        stream: BinaryIO,
    ) -> BloggerHTTPResponse:
        request_id = uuid.uuid4().hex
        try:
            normalized_method = str(method or "").upper()
            target = str(raw_target or "")
            if "?" in target or "#" in target:
                raise BloggerIngestError("query_not_allowed", "传输接口不接受 query。")
            if normalized_method == "POST" and target == DEFAULT_MANIFEST_PATH:
                return self._manifest(headers, stream)
            media = _MEDIA_PATH.fullmatch(target)
            if normalized_method == "PUT" and media:
                return self._artifact(
                    headers,
                    stream,
                    path=target,
                    transfer_id=media.group("transfer"),
                    artifact_kind="media",
                    encoded_artifact_id=media.group("artifact"),
                )
            comments = _COMMENTS_PATH.fullmatch(target)
            if normalized_method == "PUT" and comments:
                return self._artifact(
                    headers,
                    stream,
                    path=target,
                    transfer_id=comments.group("transfer"),
                    artifact_kind="comment_bundle",
                    encoded_artifact_id=comments.group("artifact"),
                )
            complete = _COMPLETE_PATH.fullmatch(target)
            if normalized_method == "POST" and complete:
                return self._complete(
                    headers,
                    stream,
                    path=target,
                    transfer_id=complete.group("transfer"),
                )
            raise BloggerIngestError("route_not_found", "传输接口不存在。")
        except BloggerIngestError as error:
            return BloggerHTTPResponse(
                status=self._error_status(error.code),
                payload={"error_code": error.code, "request_id": request_id},
            )
        except Exception:
            return BloggerHTTPResponse(
                status=503,
                payload={"error_code": "receiver_unavailable", "request_id": request_id},
            )

    def _manifest(
        self,
        headers: Mapping[str, str],
        stream: BinaryIO,
    ) -> BloggerHTTPResponse:
        length = _content_length(headers)
        if length > MAX_MANIFEST_BYTES:
            raise BloggerIngestError("manifest_too_large", "清单超过 1 MiB 上限。")
        if _content_type(headers) != "application/json":
            raise BloggerIngestError("unsupported_content_type", "清单必须是 JSON。")
        authentication = _authentication(headers)
        received_at = int(self.clock())
        verified = self.receiver.verifier.verify_declared(
            authentication,
            method="POST",
            path=DEFAULT_MANIFEST_PATH,
            now=received_at,
        )
        body = _read_exact(stream, length, maximum=MAX_MANIFEST_BYTES)
        receipt = self.receiver.receive_verified(
            body,
            verified,
            method="POST",
            path=DEFAULT_MANIFEST_PATH,
            received_at=received_at,
        )
        return BloggerHTTPResponse(
            status=202 if receipt.status == "accepted" else 200,
            payload=receipt.as_dict(),
        )

    def _artifact(
        self,
        headers: Mapping[str, str],
        stream: BinaryIO,
        *,
        path: str,
        transfer_id: str,
        artifact_kind: str,
        encoded_artifact_id: str,
    ) -> BloggerHTTPResponse:
        try:
            artifact_id = unquote(encoded_artifact_id, encoding="utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise BloggerIngestError("invalid_request_target", "artifact_id 编码无效。") from error
        if quote(artifact_id, safe="") != encoded_artifact_id:
            raise BloggerIngestError("invalid_request_target", "artifact_id 路径不是规范编码。")

        length = _content_length(headers)
        authentication = _authentication(headers)
        verified = self.receiver.verifier.verify_declared(
            authentication,
            method="PUT",
            path=path,
            now=int(self.clock()),
        )
        descriptor = self.store.get_artifact(
            transfer_id=transfer_id,
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
        )
        if descriptor is None:
            raise BloggerIngestError("artifact_not_declared", "artifact 未在清单中声明。")
        self._require_transfer_identity(descriptor, verified)
        if not descriptor["is_current"] or descriptor["transfer_state"] != "accepted":
            raise BloggerIngestError("stale_transfer", "非当前 transfer 不接受 artifact。")
        expected_length = int(descriptor["expected_size_bytes"])
        expected_sha256 = str(descriptor["expected_sha256"])
        if length != expected_length:
            raise BloggerIngestError("artifact_length_mismatch", "artifact 长度与清单不一致。")
        if not hmac.compare_digest(verified.content_sha256, expected_sha256):
            raise BloggerIngestError("artifact_conflict", "artifact 摘要与清单不一致。")
        if _content_type(headers) != str(descriptor["mime_type"]).casefold():
            raise BloggerIngestError("unsupported_content_type", "artifact MIME 与清单不一致。")

        stage = self._new_stage_path()
        actual_sha256 = ""
        prefix = b""
        tail = b""
        try:
            digest = hashlib.sha256()
            remaining = expected_length
            with stage.open("wb") as output:
                while remaining:
                    chunk = stream.read(min(STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise BloggerIngestError("incomplete_body", "artifact 上传提前中断。")
                    if not isinstance(chunk, bytes):
                        chunk = bytes(chunk)
                    if len(chunk) > remaining:
                        raise BloggerIngestError("invalid_content_length", "artifact 正文长度无效。")
                    if len(prefix) < 32:
                        prefix += chunk[: 32 - len(prefix)]
                    tail = (tail + chunk)[-32:]
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            actual_sha256 = digest.hexdigest()
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise BloggerIngestError("artifact_hash_mismatch", "artifact 正文摘要不一致。")
            if artifact_kind == "media":
                self._validate_media(
                    role=str(descriptor["media_role"]),
                    mime_type=str(descriptor["mime_type"]),
                    size_bytes=expected_length,
                    prefix=prefix,
                    tail=tail,
                )
            else:
                self._validate_comment_bundle(stage, descriptor)

            self._consume_nonce(verified)
            with self._commit_lock:
                status, relative = self._commit_artifact(
                    stage,
                    descriptor,
                    transfer_id=transfer_id,
                    artifact_kind=artifact_kind,
                    actual_sha256=actual_sha256,
                )
            return BloggerHTTPResponse(
                status=201 if status == "verified" else 200,
                payload={
                    "status": status,
                    "receipt_id": transfer_id,
                    "transfer_id": transfer_id,
                    "artifact_id": artifact_id,
                    "artifact_kind": artifact_kind,
                    "size_bytes": expected_length,
                    "sha256": actual_sha256,
                },
            )
        finally:
            try:
                stage.unlink(missing_ok=True)
            except OSError:
                pass

    def _complete(
        self,
        headers: Mapping[str, str],
        stream: BinaryIO,
        *,
        path: str,
        transfer_id: str,
    ) -> BloggerHTTPResponse:
        length = _content_length(headers)
        if _content_type(headers) != "application/json":
            raise BloggerIngestError("unsupported_content_type", "complete 必须是 JSON。")
        authentication = _authentication(headers)
        verified = self.receiver.verifier.verify_declared(
            authentication,
            method="POST",
            path=path,
            now=int(self.clock()),
        )
        body = _read_exact(stream, length, maximum=MAX_COMPLETE_BYTES)
        actual_sha256 = hashlib.sha256(body).hexdigest()
        if not hmac.compare_digest(verified.content_sha256, actual_sha256):
            raise BloggerIngestError("content_digest_mismatch", "请求正文摘要不一致。")
        body_transfer_id = _decode_complete(body)
        if not hmac.compare_digest(body_transfer_id, transfer_id):
            raise BloggerIngestError("transfer_conflict", "complete transfer_id 不匹配。")
        transfer = self.store.get_transfer(transfer_id)
        if transfer is None:
            raise BloggerIngestError("transfer_not_found", "transfer 不存在。")
        self._require_transfer_identity(transfer, verified)
        self._consume_nonce(verified)
        receipt = self.store.complete_transfer(
            transfer_id,
            node_id=verified.node_id,
            key_id=verified.key_id,
            completed_at=int(self.clock()),
        )
        return BloggerHTTPResponse(status=200, payload=receipt)

    def _consume_nonce(self, verified: VerifiedRequest) -> None:
        now = int(self.clock())
        self.store.consume_nonce(
            node_id=verified.node_id,
            key_id=verified.key_id,
            nonce=verified.nonce,
            request_timestamp=verified.timestamp,
            request_sha256=verified.content_sha256,
            seen_at=now,
            expires_at=now + self.receiver.max_clock_skew_seconds,
        )

    @staticmethod
    def _require_transfer_identity(
        transfer: Mapping[str, Any],
        verified: VerifiedRequest,
    ) -> None:
        if not hmac.compare_digest(str(transfer["collector_node_id"]), verified.node_id) or not hmac.compare_digest(
            str(transfer["collector_key_id"]), verified.key_id
        ):
            raise BloggerIngestError("collector_auth_mismatch", "请求节点与 transfer 不一致。")

    def _ensure_artifact_layout(self) -> None:
        for path in (self.store.staging_root, self.store.artifact_root):
            path.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(path, 0o750)
        if self.store.staging_root.stat().st_dev != self.store.artifact_root.stat().st_dev:
            raise BloggerIngestError(
                "artifact_storage_error",
                "staging 与 artifact 必须位于同一文件系统。",
            )

    def _new_stage_path(self) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix="incoming-",
            suffix=".part",
            dir=self.store.staging_root,
        )
        os.close(descriptor)
        return Path(name)

    @staticmethod
    def _validate_media(
        *,
        role: str,
        mime_type: str,
        size_bytes: int,
        prefix: bytes,
        tail: bytes,
    ) -> None:
        mime = mime_type.casefold()
        if mime not in V1_MEDIA_MIME_TYPES.get(role, frozenset()):
            raise BloggerIngestError("unsupported_media_type", "媒体 role 与格式不受 v1 支持。")
        valid = False
        if mime == "video/mp4":
            box_size = int.from_bytes(prefix[:4], "big") if len(prefix) >= 4 else 0
            valid = (
                size_bytes >= 12
                and len(prefix) >= 8
                and prefix[4:8] == b"ftyp"
                and 8 <= box_size <= size_bytes
            )
        elif mime == "image/jpeg":
            valid = prefix.startswith(b"\xff\xd8\xff") and tail.endswith(b"\xff\xd9")
        elif mime == "image/png":
            valid = prefix.startswith(b"\x89PNG\r\n\x1a\n")
        elif mime == "image/webp":
            valid = (
                size_bytes >= 12
                and prefix.startswith(b"RIFF")
                and prefix[8:12] == b"WEBP"
                and int.from_bytes(prefix[4:8], "little") + 8 == size_bytes
            )
        else:
            raise BloggerIngestError("unsupported_media_type", "媒体格式不受支持。")
        if not valid:
            raise BloggerIngestError("invalid_media_signature", "媒体真实格式与清单不一致。")

    @staticmethod
    def _validate_comment_bundle(stage: Path, descriptor: Mapping[str, Any]) -> None:
        expected_size = int(descriptor["uncompressed_size_bytes"])
        expected_hash = str(descriptor["uncompressed_sha256"])
        expected_items = int(descriptor["item_count"])
        digest = hashlib.sha256()
        actual_size = 0
        item_count = 0
        identifiers: set[str] = set()
        prior_sort_key: tuple[int, str, str, str] | None = None

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise BloggerIngestError(
                        "invalid_comment_bundle",
                        "评论 NDJSON 项包含重复字段。",
                    )
                value[key] = item
            return value

        def invalid_constant(_: str) -> None:
            raise BloggerIngestError(
                "invalid_comment_bundle",
                "评论 NDJSON 项包含 NaN 或 Infinity。",
            )

        try:
            with stage.open("rb") as compressed, gzip.GzipFile(fileobj=compressed, mode="rb") as plain:
                while True:
                    line = plain.readline(MAX_NDJSON_LINE_BYTES + 1)
                    if not line:
                        break
                    if len(line) > MAX_NDJSON_LINE_BYTES:
                        raise BloggerIngestError("invalid_comment_bundle", "评论 NDJSON 单行过长。")
                    actual_size += len(line)
                    if actual_size > expected_size:
                        raise BloggerIngestError("comment_length_mismatch", "评论包解压长度不一致。")
                    digest.update(line)
                    if not line.endswith(b"\n") or line == b"\n":
                        raise BloggerIngestError("invalid_comment_bundle", "评论包不是规范 NDJSON。")
                    try:
                        item = json.loads(
                            line[:-1].decode("utf-8"),
                            object_pairs_hook=unique_object,
                            parse_constant=invalid_constant,
                        )
                    except BloggerIngestError:
                        raise
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise BloggerIngestError("invalid_comment_bundle", "评论 NDJSON 项无效。") from error
                    if not isinstance(item, dict):
                        raise BloggerIngestError("invalid_comment_bundle", "评论 NDJSON 项必须是对象。")
                    sort_key = _validate_comment_item(item, index=item_count)
                    source_id = sort_key[3]
                    if source_id in identifiers:
                        raise BloggerIngestError(
                            "invalid_comment_bundle",
                            "评论包包含重复 source_comment_id。",
                        )
                    if prior_sort_key is not None and sort_key < prior_sort_key:
                        raise BloggerIngestError(
                            "invalid_comment_bundle",
                            "评论 NDJSON 未按 blogger-comments/v1 稳定排序。",
                        )
                    identifiers.add(source_id)
                    prior_sort_key = sort_key
                    item_count += 1
                    if item_count > expected_items:
                        raise BloggerIngestError("comment_count_mismatch", "评论 NDJSON 条数不一致。")
        except (gzip.BadGzipFile, EOFError, OSError) as error:
            raise BloggerIngestError("invalid_comment_gzip", "评论包不是有效 gzip。") from error
        if actual_size != expected_size:
            raise BloggerIngestError("comment_length_mismatch", "评论包解压长度不一致。")
        if not hmac.compare_digest(digest.hexdigest(), expected_hash):
            raise BloggerIngestError("comment_hash_mismatch", "评论包解压摘要不一致。")
        if item_count != expected_items:
            raise BloggerIngestError("comment_count_mismatch", "评论 NDJSON 条数不一致。")

    def _destination(
        self,
        descriptor: Mapping[str, Any],
        *,
        transfer_id: str,
        artifact_kind: str,
    ) -> tuple[Path, str]:
        if artifact_kind == "comment_bundle":
            filename = f"comments-{descriptor['expected_sha256']}.ndjson.gz"
        else:
            suffix = {
                "video/mp4": ".mp4",
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }.get(str(descriptor["mime_type"]).casefold())
            if suffix is None:
                raise BloggerIngestError("unsupported_media_type", "媒体格式不受支持。")
            filename = f"media-{descriptor['expected_sha256']}{suffix}"
        relative = f"artifacts/{transfer_id}/{filename}"
        destination = self.store.root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(destination.parent, 0o750)
        return destination, relative

    def _commit_artifact(
        self,
        stage: Path,
        descriptor: Mapping[str, Any],
        *,
        transfer_id: str,
        artifact_kind: str,
        actual_sha256: str,
    ) -> tuple[str, str]:
        destination, relative = self._destination(
            descriptor,
            transfer_id=transfer_id,
            artifact_kind=artifact_kind,
        )
        current = self.store.get_artifact(
            transfer_id=transfer_id,
            artifact_kind=artifact_kind,
            artifact_id=str(descriptor["artifact_id"]),
        )
        if current is None:
            raise BloggerIngestError("artifact_not_declared", "artifact 未在清单中声明。")
        if destination.exists() or destination.is_symlink():
            self._require_existing_matches(
                destination,
                size_bytes=int(descriptor["expected_size_bytes"]),
                sha256=actual_sha256,
            )
        else:
            os.replace(stage, destination)
            if os.name != "nt":
                os.chmod(destination, 0o600)
            self._fsync_directory(destination.parent)
        status = self.store.mark_artifact_verified(
            transfer_id=transfer_id,
            artifact_kind=artifact_kind,
            artifact_id=str(descriptor["artifact_id"]),
            size_bytes=int(descriptor["expected_size_bytes"]),
            sha256=actual_sha256,
            stored_relative_path=relative,
            verified_at=int(self.clock()),
        )
        return status, relative

    @staticmethod
    def _require_existing_matches(target: Path, *, size_bytes: int, sha256: str) -> None:
        try:
            metadata = os.lstat(target)
        except OSError as error:
            raise BloggerIngestError("artifact_storage_error", "artifact 保存结果不可读。") from error
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size_bytes:
            raise BloggerIngestError("artifact_conflict", "artifact 保存目标已存在不同内容。")
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            while True:
                chunk = stream.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), sha256):
            raise BloggerIngestError("artifact_conflict", "artifact 保存目标已存在不同内容。")

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _error_status(code: str) -> int:
        if code in {
            "invalid_authentication",
            "invalid_signature",
            "expired_request",
            "replayed_nonce",
            "content_digest_mismatch",
            "collector_auth_mismatch",
        }:
            return 401
        if code in {"manifest_too_large", "request_too_large"}:
            return 413
        if code in {"unsupported_content_type", "unsupported_media_type"}:
            return 415
        if code in {"route_not_found", "transfer_not_found", "artifact_not_declared"}:
            return 404
        if code in {
            "artifact_conflict",
            "artifact_length_mismatch",
            "artifacts_missing",
            "processing_conflict",
            "revision_conflict",
            "stale_transfer",
            "transfer_conflict",
        }:
            return 409
        if code in {
            "artifact_hash_mismatch",
            "comment_count_mismatch",
            "comment_hash_mismatch",
            "comment_length_mismatch",
            "invalid_comment_bundle",
            "invalid_comment_gzip",
            "invalid_complete",
            "invalid_manifest",
            "invalid_media_signature",
            "revision_digest_mismatch",
            "transfer_id_mismatch",
            "unsupported_schema",
        }:
            return 422
        if code in {"artifact_storage_error", "blogger_configuration_invalid"}:
            return 503
        return 400


__all__ = [
    "BloggerHTTPResponse",
    "BloggerTransferHTTP",
    "MAX_COMPLETE_BYTES",
]
