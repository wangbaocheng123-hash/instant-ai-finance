from __future__ import annotations

import ctypes
import json
import os
import re
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://grandpaamu.com/api/mcp/blogger"
TOKEN_ENV = "INSTANT_AI_BLOGGER_MCP_TOKEN"
TOKEN_FILE_ENV = "BLOGGER_AGENT_CLOUD_TOKEN_FILE"
TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _default_token_file() -> Path | None:
    configured = str(os.getenv(TOKEN_FILE_ENV, "") or "").strip()
    if configured:
        return Path(configured)
    local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
    return Path(local_app_data) / "BloggerAgentMcp" / "instant-ai-cloud-token.bin" if local_app_data else None


def _dpapi_unprotect(payload: bytes) -> bytes:
    if os.name != "nt" or not payload:
        return b""
    buffer = ctypes.create_string_buffer(payload, len(payload))
    input_blob = _DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    ):
        return b""
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def load_token() -> str:
    environment_value = str(os.getenv(TOKEN_ENV, "") or "").strip()
    if TOKEN_PATTERN.fullmatch(environment_value):
        return environment_value
    path = _default_token_file()
    if path is None or not path.is_file():
        return ""
    try:
        value = _dpapi_unprotect(path.read_bytes()).decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return value if TOKEN_PATTERN.fullmatch(value) else ""


class CloudBloggerReader:
    """Fail-closed client for the Singapore read-only blogger-text projection."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 8.0,
        token_loader: Callable[[], str] = load_token,
    ) -> None:
        self.base_url = str(base_url or os.getenv("INSTANT_AI_BLOGGER_MCP_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = max(1.0, min(float(timeout), 30.0))
        self.token_loader = token_loader

    def _post(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self.token_loader()
        if not token:
            raise RuntimeError("cloud_blogger_not_configured")
        request = Request(
            f"{self.base_url}/{action}",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise RuntimeError(f"cloud_blogger_http_{error.code}") from error
        except (OSError, URLError) as error:
            raise RuntimeError("cloud_blogger_unreachable") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("cloud_blogger_response_too_large")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("cloud_blogger_invalid_response") from error
        if not isinstance(value, dict):
            raise RuntimeError("cloud_blogger_invalid_response")
        return value

    def search(self, question: str, limit: int = 10) -> dict[str, Any]:
        try:
            result = self._post("search", {"question": str(question or ""), "limit": max(1, min(int(limit), 30))})
        except (RuntimeError, TypeError, ValueError) as error:
            return {"available": False, "count": 0, "items": [], "error_code": str(error)}
        result["available"] = True
        return result

    def get(self, record_id: str) -> dict[str, Any]:
        try:
            return self._post("get", {"record_id": str(record_id or "")})
        except RuntimeError as error:
            return {"found": False, "record_id": str(record_id or ""), "error_code": str(error)}


def merge_cloud_search_result(
    local: dict[str, Any],
    cloud: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    """Merge cloud evidence into the existing local result contract."""
    safe_limit = max(1, min(int(limit), 30))
    result = dict(local)
    cloud_items = cloud.get("items") if isinstance(cloud.get("items"), list) else []
    if cloud_items:
        combined = [*cloud_items, *result.get("items", [])]
        if result.get("query_mode") == "latest" or cloud.get("query_mode") == "latest":
            combined.sort(
                key=lambda item: (
                    str(item.get("published_at") or item.get("captured_at") or ""),
                    float(item.get("relevance_score") or 0),
                ),
                reverse=True,
            )
        else:
            combined.sort(
                key=lambda item: (
                    float(item.get("relevance_score") or 0),
                    str(item.get("published_at") or item.get("captured_at") or ""),
                ),
                reverse=True,
            )
        result["items"] = combined[:safe_limit]
        result["count"] = len(result["items"])
        result["retrieval"] = f"{result.get('retrieval') or 'local'}+instant_ai_cloud_blogger"
        result["evidence_note"] = str(result.get("evidence_note") or "") + (
            " 云端记录以 cloud-video: 开头，可继续读取完整正式原文。"
        )
    result["cloud_blogger"] = {
        "available": bool(cloud.get("available")),
        "count": len(cloud_items),
        "error_code": str(cloud.get("error_code") or ""),
    }
    return result


__all__ = ["CloudBloggerReader", "DEFAULT_BASE_URL", "load_token", "merge_cloud_search_result"]
