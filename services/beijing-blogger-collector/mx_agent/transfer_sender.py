from __future__ import annotations

import http.client
import hashlib
import json
import os
import re
import socket
import ssl
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import quote, urlsplit

from .transfer_contract import (
    canonical_json_bytes,
    request_signature,
    signed_headers,
)
from .transfer_outbox import TransferOutbox, TransferOutboxError


MANIFEST_PATH = "/internal/v1/transfers"
MAX_RESPONSE_BYTES = 1024 * 1024
TRANSIENT_HTTP_STATUSES = {408, 425, 429}
SAFE_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,128}")
SAFE_RECEIPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True)
class TransportResponse:
    status: int
    payload: dict[str, Any]


class TransferTransport(Protocol):
    def send_bytes(
        self,
        method: str,
        path: str,
        body: bytes,
        *,
        content_type: str,
    ) -> TransportResponse: ...

    def send_file(
        self,
        method: str,
        path: str,
        local_path: Path,
        *,
        size_bytes: int,
        sha256: str,
        content_type: str,
    ) -> TransportResponse: ...


class TransferDeliveryError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "transfer_error")[:128]
        self.http_status = http_status
        self.transient = bool(transient)


class HTTPSCollectorTransport:
    """Small HTTPS client that signs every request and streams artifacts."""

    def __init__(
        self,
        base_url: str,
        *,
        node_id: str,
        key_id: str,
        secret: bytes,
        timeout_seconds: int = 60,
        connection_factory: Any | None = None,
    ) -> None:
        parsed = urlsplit(str(base_url or "").strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("新加坡接收地址必须是 HTTPS。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("新加坡接收地址不能包含凭据、查询参数或片段。")
        if parsed.path not in {"", "/"}:
            raise ValueError("新加坡接收地址只能配置站点根地址。")
        if not node_id or not key_id or len(secret) < 32:
            raise ValueError("传输节点、key_id 和至少 32 字节的签名密钥必须完整配置。")
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.node_id = str(node_id)
        self.key_id = str(key_id)
        self.secret = bytes(secret)
        self.timeout_seconds = max(5, int(timeout_seconds))
        self._connection_factory = connection_factory

    def _connection(self) -> http.client.HTTPSConnection:
        if self._connection_factory is not None:
            return self._connection_factory()
        return http.client.HTTPSConnection(
            self.host,
            self.port,
            timeout=self.timeout_seconds,
            context=ssl.create_default_context(),
        )

    @staticmethod
    def _read_response(response: http.client.HTTPResponse) -> TransportResponse:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransferDeliveryError(
                "response_too_large",
                "新加坡回执超过大小限制。",
                http_status=int(response.status),
            )
        if not raw:
            payload: dict[str, Any] = {}
        else:
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TransferDeliveryError(
                    "invalid_response",
                    "新加坡返回了无效 JSON 回执。",
                    http_status=int(response.status),
                    transient=int(response.status) >= 500,
                ) from exc
            if not isinstance(decoded, dict):
                raise TransferDeliveryError(
                    "invalid_response",
                    "新加坡回执必须是 JSON 对象。",
                    http_status=int(response.status),
                )
            payload = decoded
        return TransportResponse(status=int(response.status), payload=payload)

    def send_bytes(
        self,
        method: str,
        path: str,
        body: bytes,
        *,
        content_type: str,
    ) -> TransportResponse:
        headers = signed_headers(
            self.secret,
            body,
            method=method,
            path=path,
            node_id=self.node_id,
            key_id=self.key_id,
        )
        headers.update(
            {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
                "Accept": "application/json",
                "User-Agent": "blogger-agent-beijing/1",
            }
        )
        connection: http.client.HTTPSConnection | None = None
        try:
            connection = self._connection()
            connection.request(method.upper(), path, body=body, headers=headers)
            return self._read_response(connection.getresponse())
        except ssl.SSLCertVerificationError as exc:
            raise TransferDeliveryError(
                "tls_verification_failed",
                "新加坡接收端 TLS 证书验证失败。",
                transient=False,
            ) from exc
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            raise TransferDeliveryError(
                "network_error",
                "连接新加坡接收端失败。",
                transient=True,
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    def send_file(
        self,
        method: str,
        path: str,
        local_path: Path,
        *,
        size_bytes: int,
        sha256: str,
        content_type: str,
    ) -> TransportResponse:
        target = Path(local_path)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_BINARY", 0)
        if os.name == "posix":
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags)
        except OSError as exc:
            raise TransferDeliveryError(
                "artifact_missing",
                "待传输文件不存在或不可读。",
            ) from exc
        try:
            stream_handle = os.fdopen(descriptor, "rb", closefd=True)
        except OSError as exc:
            os.close(descriptor)
            raise TransferDeliveryError(
                "artifact_unreadable",
                "待传输文件不可读取。",
            ) from exc
        connection: http.client.HTTPSConnection | None = None
        with stream_handle as stream:
            try:
                initial_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(initial_stat.st_mode):
                    raise TransferDeliveryError(
                        "artifact_unsafe",
                        "待传输 artifact 不是安全的普通文件。",
                    )
                if initial_stat.st_size != int(size_bytes):
                    raise TransferDeliveryError(
                        "artifact_changed",
                        "待传输文件在进入队列后发生变化。",
                    )
                initial_hash = hashlib.sha256()
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    initial_hash.update(chunk)
                if initial_hash.hexdigest() != str(sha256):
                    raise TransferDeliveryError(
                        "artifact_changed",
                        "待传输文件在进入队列后发生变化。",
                    )
                stream.seek(0)
            except TransferDeliveryError:
                raise
            except OSError as exc:
                raise TransferDeliveryError(
                    "artifact_unreadable",
                    "待传输文件不可读取。",
                ) from exc

            timestamp = str(int(time.time()))
            nonce = uuid.uuid4().hex
            signature = request_signature(
                self.secret,
                method=method,
                path=path,
                node_id=self.node_id,
                key_id=self.key_id,
                timestamp=timestamp,
                nonce=nonce,
                body_sha256=sha256,
            )
            headers = {
                "X-Blogger-Node-Id": self.node_id,
                "X-Blogger-Key-Id": self.key_id,
                "X-Blogger-Timestamp": timestamp,
                "X-Blogger-Nonce": nonce,
                "X-Blogger-Content-SHA256": sha256,
                "X-Blogger-Signature": signature,
                "Content-Type": content_type,
                "Content-Length": str(size_bytes),
                "Accept": "application/json",
                "User-Agent": "blogger-agent-beijing/1",
            }
            try:
                connection = self._connection()
                connection.putrequest(method.upper(), path)
                for name, value in headers.items():
                    connection.putheader(name, value)
                connection.endheaders()
                sent_hash = hashlib.sha256()
                sent_bytes = 0
                remaining = int(size_bytes)
                while remaining:
                    try:
                        chunk = stream.read(min(1024 * 1024, remaining))
                    except OSError as exc:
                        raise TransferDeliveryError(
                            "artifact_unreadable",
                            "待传输文件读取失败。",
                        ) from exc
                    if not chunk:
                        raise TransferDeliveryError(
                            "artifact_changed",
                            "待传输文件在发送期间发生变化。",
                        )
                    connection.send(chunk)
                    sent_hash.update(chunk)
                    sent_bytes += len(chunk)
                    remaining -= len(chunk)
                try:
                    trailing = stream.read(1)
                    stream.seek(0)
                    final_hash = hashlib.sha256()
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        final_hash.update(chunk)
                    final_stat = os.fstat(stream.fileno())
                except OSError as exc:
                    raise TransferDeliveryError(
                        "artifact_unreadable",
                        "待传输文件结束复核失败。",
                    ) from exc
                unchanged_identity = (
                    final_stat.st_dev == initial_stat.st_dev
                    and final_stat.st_ino == initial_stat.st_ino
                    and final_stat.st_size == initial_stat.st_size
                    and final_stat.st_mtime_ns == initial_stat.st_mtime_ns
                )
                if (
                    trailing
                    or sent_bytes != int(size_bytes)
                    or sent_hash.hexdigest() != str(sha256)
                    or final_hash.hexdigest() != str(sha256)
                    or not unchanged_identity
                ):
                    raise TransferDeliveryError(
                        "artifact_changed",
                        "待传输文件在发送期间发生变化。",
                    )
                return self._read_response(connection.getresponse())
            except ssl.SSLCertVerificationError as exc:
                raise TransferDeliveryError(
                    "tls_verification_failed",
                    "新加坡接收端 TLS 证书验证失败。",
                    transient=False,
                ) from exc
            except TransferDeliveryError:
                raise
            except (OSError, socket.timeout, http.client.HTTPException) as exc:
                raise TransferDeliveryError(
                    "network_error",
                    "上传作品文件时连接中断。",
                    transient=True,
                ) from exc
            finally:
                if connection is not None:
                    connection.close()


def _artifact_path(transfer_id: str, artifact: Mapping[str, Any]) -> str:
    transfer = quote(str(transfer_id), safe="")
    artifact_id = quote(str(artifact["artifact_id"]), safe="")
    if artifact["artifact_kind"] == "media":
        return f"{MANIFEST_PATH}/{transfer}/media/{artifact_id}"
    if artifact["artifact_kind"] == "comment_bundle":
        return f"{MANIFEST_PATH}/{transfer}/comments/{artifact_id}"
    raise TransferDeliveryError("invalid_artifact", "outbox 包含未知 artifact 类型。")


def _response_error(response: TransportResponse) -> TransferDeliveryError:
    status = int(response.status)
    supplied_code = str(response.payload.get("error_code") or "")
    code = supplied_code if SAFE_ERROR_CODE.fullmatch(supplied_code) else f"http_{status}"
    message = "新加坡接收端拒绝了传输。"
    transient = status in TRANSIENT_HTTP_STATUSES or status >= 500
    return TransferDeliveryError(
        code,
        message,
        http_status=status,
        transient=transient,
    )


def _require_success(response: TransportResponse) -> dict[str, Any]:
    if not 200 <= int(response.status) < 300:
        raise _response_error(response)
    if not isinstance(response.payload, dict):
        raise TransferDeliveryError(
            "invalid_receipt",
            "新加坡 2xx 回执必须是 JSON 对象。",
        )
    return response.payload


def _invalid_receipt(message: str) -> TransferDeliveryError:
    return TransferDeliveryError("invalid_receipt", message, transient=False)


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_receipt(f"2xx 回执缺少有效 {field}。")
    return value


def _required_receipt_id(payload: Mapping[str, Any]) -> str:
    value = _required_string(payload, "receipt_id")
    if not SAFE_RECEIPT_ID.fullmatch(value):
        raise _invalid_receipt("2xx 回执 receipt_id 含非法字符或超过 128 字符。")
    return value


def _required_int(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_receipt(f"2xx 回执缺少有效 {field}。")
    return value


def _require_manifest_receipt(
    response: TransportResponse,
    queued: Mapping[str, Any],
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _require_success(response)
    transfer_id = str(queued["transfer_id"])
    manifest = queued["manifest"]
    receipt_id = _required_receipt_id(payload)
    if _required_string(payload, "transfer_id") != transfer_id:
        raise _invalid_receipt("manifest 回执 transfer_id 不匹配。")
    status = _required_string(payload, "status")
    if status not in {"accepted", "duplicate", "stale"}:
        raise _invalid_receipt("manifest 回执 status 无效。")
    if _required_string(payload, "revision_sha256") != str(manifest["revision_sha256"]):
        raise _invalid_receipt("manifest 回执 revision_sha256 不匹配。")
    work_revision = int(manifest["work"]["revision"])
    if _required_int(payload, "work_revision") != work_revision:
        raise _invalid_receipt("manifest 回执 work_revision 不匹配。")
    current_revision = _required_int(payload, "current_revision")
    if status == "stale":
        if current_revision <= work_revision:
            raise _invalid_receipt("stale 回执 current_revision 无效。")
    elif current_revision != work_revision:
        raise _invalid_receipt("manifest 回执 current_revision 不匹配。")

    missing = payload.get("missing_artifacts")
    if not isinstance(missing, list):
        raise _invalid_receipt("manifest 回执 missing_artifacts 必须是数组。")
    expected = {str(item["artifact_id"]): item for item in artifacts}
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in missing:
        if not isinstance(item, dict) or set(item) != {"artifact_id", "artifact_kind"}:
            raise _invalid_receipt("missing_artifacts 项结构无效。")
        artifact_id = _required_string(item, "artifact_id")
        artifact_kind = _required_string(item, "artifact_kind")
        descriptor = expected.get(artifact_id)
        if descriptor is None or artifact_kind != str(descriptor["artifact_kind"]):
            raise _invalid_receipt("missing_artifacts 与本地 artifact 不匹配。")
        if artifact_id in seen:
            raise _invalid_receipt("missing_artifacts 包含重复 artifact。")
        seen.add(artifact_id)
        normalized.append(
            {"artifact_id": artifact_id, "artifact_kind": artifact_kind}
        )
    if normalized != sorted(
        normalized,
        key=lambda item: (
            0 if item["artifact_kind"] == "media" else 1,
            item["artifact_id"],
        ),
    ):
        raise _invalid_receipt("missing_artifacts 未按协议稳定排序。")
    if status == "stale" and normalized:
        raise _invalid_receipt("stale 回执不能声明缺失 artifact。")
    return {
        "receipt_id": receipt_id,
        "status": status,
        "missing_artifact_ids": {item["artifact_id"] for item in normalized},
    }


def _require_artifact_receipt(
    response: TransportResponse,
    *,
    transfer_id: str,
    receipt_id: str,
    artifact: Mapping[str, Any],
) -> None:
    payload = _require_success(response)
    if _required_receipt_id(payload) != receipt_id:
        raise _invalid_receipt("artifact 回执 receipt_id 不匹配。")
    if _required_string(payload, "transfer_id") != transfer_id:
        raise _invalid_receipt("artifact 回执 transfer_id 不匹配。")
    if _required_string(payload, "artifact_id") != str(artifact["artifact_id"]):
        raise _invalid_receipt("artifact 回执 artifact_id 不匹配。")
    if _required_string(payload, "artifact_kind") != str(artifact["artifact_kind"]):
        raise _invalid_receipt("artifact 回执 artifact_kind 不匹配。")
    if _required_string(payload, "status") not in {"verified", "duplicate"}:
        raise _invalid_receipt("artifact 回执 status 无效。")
    if _required_int(payload, "size_bytes") != int(artifact["size_bytes"]):
        raise _invalid_receipt("artifact 回执 size_bytes 不匹配。")
    if _required_string(payload, "sha256") != str(artifact["sha256"]):
        raise _invalid_receipt("artifact 回执 sha256 不匹配。")


def _require_complete_receipt(
    response: TransportResponse,
    *,
    transfer_id: str,
    receipt_id: str,
) -> None:
    payload = _require_success(response)
    if _required_receipt_id(payload) != receipt_id:
        raise _invalid_receipt("complete 回执 receipt_id 不匹配。")
    if _required_string(payload, "transfer_id") != transfer_id:
        raise _invalid_receipt("complete 回执 transfer_id 不匹配。")
    if _required_string(payload, "status") != "completed":
        raise _invalid_receipt("complete 回执 status 无效。")
    if payload.get("transport_completed") is not True:
        raise _invalid_receipt("complete 回执未确认 transport_completed。")
    if payload.get("artifacts_verified") is not True:
        raise _invalid_receipt("complete 回执未确认 artifacts_verified。")
    if _required_string(payload, "intelligence_status") != "awaiting_asr_approval":
        raise _invalid_receipt("complete 回执 intelligence_status 无效。")


class TransferSender:
    """Drive leased rows with content-addressed, whole-file retries.

    Protocol v1 intentionally has no byte-range or breakpoint resume. If an
    upload acknowledgement is not durably recorded, the complete file is sent
    again and the receiver's content hash makes that retry idempotent.
    """

    def __init__(
        self,
        outbox: TransferOutbox,
        transport: TransferTransport,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.outbox = outbox
        self.transport = transport
        self.worker_id = worker_id or f"sender-{uuid.uuid4().hex}"

    def run_once(self, *, limit: int = 10) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for _ in range(max(1, min(1000, int(limit)))):
            claimed = self.outbox.claim_ready(self.worker_id, limit=1)
            if not claimed:
                break
            queued = claimed[0]
            transfer_id = str(queued["transfer_id"])
            try:
                if queued["status"] == "retry_wait":
                    queued = self.outbox.resume_due(
                        transfer_id, lease_owner=self.worker_id
                    )
                results.append(self._deliver(queued))
            except TransferDeliveryError as exc:
                results.append(self._record_failure(transfer_id, exc))
            except Exception as exc:  # isolate one transfer without leaking paths
                safe = TransferDeliveryError(
                    "unexpected_sender_error",
                    f"传输处理发生已脱敏异常（{type(exc).__name__}）。",
                    transient=True,
                )
                results.append(self._record_failure(transfer_id, safe))
        return results

    def _record_failure(
        self,
        transfer_id: str,
        error: TransferDeliveryError,
    ) -> dict[str, Any]:
        try:
            if error.transient:
                return self.outbox.mark_retry(
                    transfer_id,
                    error_code=error.code,
                    error_message=str(error),
                    http_status=error.http_status,
                    lease_owner=self.worker_id,
                )
            return self.outbox.mark_dead_letter(
                transfer_id,
                error_code=error.code,
                error_message=str(error),
                lease_owner=self.worker_id,
            )
        except TransferOutboxError:
            self.outbox.release_lease(transfer_id, self.worker_id)
            current = self.outbox.get(transfer_id)
            return current or {
                "transfer_id": transfer_id,
                "status": "sender_error",
                "last_error_code": "outbox_cas_failed",
                "last_error_message": "outbox 状态更新失败。",
            }

    def _deliver(self, queued: Mapping[str, Any]) -> dict[str, Any]:
        transfer_id = str(queued["transfer_id"])
        current = self.outbox.get(transfer_id)
        if current is None:
            raise TransferDeliveryError("outbox_missing", "outbox 记录不存在。")

        if current["status"] == "pending":
            artifacts = self.outbox.artifacts_for(transfer_id)
            self.outbox.renew_lease(transfer_id, self.worker_id)
            body = canonical_json_bytes(current["manifest"])
            response = self.transport.send_bytes(
                "POST",
                MANIFEST_PATH,
                body,
                content_type="application/json; charset=utf-8",
            )
            receipt = _require_manifest_receipt(response, current, artifacts)
            if receipt["status"] == "stale":
                return self.outbox.mark_stale_delivered(
                    transfer_id,
                    receipt_id=receipt["receipt_id"],
                    http_status=response.status,
                    lease_owner=self.worker_id,
                )
            missing_ids = receipt["missing_artifact_ids"]
            for artifact in artifacts:
                if str(artifact["artifact_id"]) not in missing_ids:
                    self.outbox.update_artifact_progress(
                        transfer_id,
                        str(artifact["artifact_id"]),
                        status="verified",
                        uploaded_bytes=int(artifact["size_bytes"]),
                        lease_owner=self.worker_id,
                    )
            current = self.outbox.transition(
                transfer_id,
                "manifest_accepted",
                receipt_id=receipt["receipt_id"],
                http_status=response.status,
                expected_status="pending",
                lease_owner=self.worker_id,
            )

        if current["status"] == "manifest_accepted":
            artifacts = self.outbox.artifacts_for(transfer_id)
            target = (
                "finalizing"
                if all(item["status"] == "verified" for item in artifacts)
                else "media_uploading"
            )
            current = self.outbox.transition(
                transfer_id,
                target,
                expected_status="manifest_accepted",
                lease_owner=self.worker_id,
            )

        if current["status"] == "media_uploading":
            for artifact in self.outbox.artifacts_for(transfer_id):
                if artifact["status"] == "verified":
                    continue
                if artifact["status"] == "pending":
                    artifact = self.outbox.update_artifact_progress(
                        transfer_id,
                        str(artifact["artifact_id"]),
                        status="uploading",
                        uploaded_bytes=0,
                        lease_owner=self.worker_id,
                    )
                try:
                    local_path = self.outbox.checked_artifact_path(artifact)
                except TransferOutboxError as exc:
                    raise TransferDeliveryError(
                        "artifact_unavailable",
                        "待传输 artifact 不存在、已变化或不在允许目录。",
                    ) from exc
                self.outbox.renew_lease(transfer_id, self.worker_id)
                response = self.transport.send_file(
                    "PUT",
                    _artifact_path(transfer_id, artifact),
                    local_path,
                    size_bytes=int(artifact["size_bytes"]),
                    sha256=str(artifact["sha256"]),
                    content_type=str(artifact["mime_type"]),
                )
                _require_artifact_receipt(
                    response,
                    transfer_id=transfer_id,
                    receipt_id=str(current["receipt_id"]),
                    artifact=artifact,
                )
                self.outbox.update_artifact_progress(
                    transfer_id,
                    str(artifact["artifact_id"]),
                    status="verified",
                    uploaded_bytes=int(artifact["size_bytes"]),
                    lease_owner=self.worker_id,
                )
            current = self.outbox.transition(
                transfer_id,
                "finalizing",
                expected_status="media_uploading",
                lease_owner=self.worker_id,
            )

        if current["status"] == "finalizing":
            self.outbox.renew_lease(transfer_id, self.worker_id)
            path = f"{MANIFEST_PATH}/{quote(transfer_id, safe='')}/complete"
            body = canonical_json_bytes({"transfer_id": transfer_id})
            response = self.transport.send_bytes(
                "POST",
                path,
                body,
                content_type="application/json; charset=utf-8",
            )
            _require_complete_receipt(
                response,
                transfer_id=transfer_id,
                receipt_id=str(current["receipt_id"]),
            )
            current = self.outbox.transition(
                transfer_id,
                "delivered",
                receipt_id=str(current["receipt_id"]),
                http_status=response.status,
                expected_status="finalizing",
                lease_owner=self.worker_id,
            )
        return current


__all__ = [
    "HTTPSCollectorTransport",
    "MANIFEST_PATH",
    "TransferDeliveryError",
    "TransferSender",
    "TransportResponse",
]
