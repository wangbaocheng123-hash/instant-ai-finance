from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .paths import LIBRARY_ROOT


DEFAULT_ORIGIN = "http://127.0.0.1:8787"
SNAPSHOT_VERSION = 1


def _default_snapshot_path() -> Path:
    return Path(
        os.environ.get(
            "INSTANT_AI_MODEL_MR_SNAPSHOT",
            str(LIBRARY_ROOT / "model-mr" / "public-snapshot.json"),
        )
    )


class ModelMrUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelMrClient:
    origin: str = os.environ.get("INSTANT_AI_MODEL_MR_ORIGIN", DEFAULT_ORIGIN).rstrip("/")
    snapshot_path: Path = field(default_factory=_default_snapshot_path)

    def __post_init__(self) -> None:
        parsed = urlparse(self.origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("模型先生服务地址无效。")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("INSTANT_AI_MODEL_MR_ALLOW_REMOTE") != "1":
            raise ValueError("模型先生服务默认只允许服务器回环地址。")

    def status(self) -> dict[str, Any]:
        try:
            source = self._json("/api/status", timeout=3)
            chat = self.chat_config()
        except ModelMrUnavailable as error:
            snapshot = self._snapshot()
            if snapshot is not None:
                works = snapshot.get("works") if isinstance(snapshot.get("works"), list) else []
                thoughts = snapshot.get("thoughts") if isinstance(snapshot.get("thoughts"), list) else []
                return {
                    "available": True,
                    "module": "模型先生",
                    "mode": "sanitized-snapshot",
                    "message": "模型先生精简资料已连接；视频、评论和管理数据未上传。",
                    "features": ["作品浏览", "投资思路"],
                    "counts": {"works": len(works), "transcripts": 0, "analyses": len(thoughts)},
                    "chat_enabled": False,
                    "snapshot_updated_at": str(snapshot.get("exported_at") or ""),
                }
            return {
                "available": False,
                "module": "模型先生",
                "mode": "independent-readonly",
                "message": str(error),
                "features": ["作品浏览", "投资思路", "智能问答"],
            }
        counts = source.get("counts") if isinstance(source.get("counts"), dict) else {}
        return {
            "available": True,
            "module": "模型先生",
            "mode": "independent-readonly",
            "message": "模型先生精简模块已连接。",
            "features": ["作品浏览", "投资思路", "智能问答"],
            "counts": {
                "works": int(counts.get("videos") or 0),
                "transcripts": int(counts.get("transcripts") or 0),
                "analyses": int(counts.get("analyses") or 0),
            },
            "chat_enabled": bool(chat.get("enabled")),
        }

    def works(self, *, limit: int = 40) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        try:
            source = self._json(f"/api/videos?{urlencode({'limit': safe_limit, 'account': '模型先生'})}")
        except ModelMrUnavailable:
            snapshot = self._require_snapshot()
            items = snapshot.get("works") if isinstance(snapshot.get("works"), list) else []
            cleaned = [self._clean_snapshot_work(item) for item in items[:safe_limit] if isinstance(item, dict)]
            return {"items": cleaned, "count": len(cleaned), "mode": "sanitized-snapshot"}
        items = source.get("items") if isinstance(source.get("items"), list) else []
        return {"items": [self._clean_work(item) for item in items if isinstance(item, dict)], "count": len(items)}

    def thoughts(self, *, limit: int = 200) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 300))
        try:
            source = self._json(f"/api/investment-thoughts?{urlencode({'limit': safe_limit})}")
        except ModelMrUnavailable:
            snapshot = self._require_snapshot()
            categories = snapshot.get("thoughts") if isinstance(snapshot.get("thoughts"), list) else []
            cleaned = [self._clean_thought(item) for item in categories[:safe_limit] if isinstance(item, dict)]
            return {
                "categories": [item for item in cleaned if item.get("name")],
                "count": len(cleaned),
                "purpose": "模型先生投资思路只读索引",
                "mode": "sanitized-snapshot",
            }
        categories = source.get("categories") if isinstance(source.get("categories"), list) else []
        cleaned = [self._clean_thought(item) for item in categories if isinstance(item, dict) and item.get("name")]
        return {"categories": cleaned, "count": len(cleaned), "purpose": "模型先生投资思路只读索引"}

    def chat_config(self) -> dict[str, Any]:
        try:
            source = self._json("/api/chat/config", timeout=3)
        except ModelMrUnavailable:
            if self._snapshot() is None:
                raise
            return {
                "enabled": False,
                "default_model": "",
                "models": [],
                "message": "云端当前使用精简只读资料；智能问答需单独连接模型服务后启用。",
            }
        models = source.get("models") if isinstance(source.get("models"), list) else []
        return {
            "enabled": bool(source.get("enabled")),
            "default_model": str(source.get("default_model") or ""),
            "models": [
                {
                    "id": str(item.get("id") or ""),
                    "label": str(item.get("label") or item.get("id") or ""),
                    "description": str(item.get("description") or ""),
                }
                for item in models
                if isinstance(item, dict) and item.get("id")
            ],
            "message": "模型先生智能问答已启用。" if source.get("enabled") else "模型先生智能问答当前未开启。",
        }

    def chat(self, messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
        cleaned: list[dict[str, str]] = []
        for item in messages[-12:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                cleaned.append({"role": role, "content": content[:6000]})
        if not cleaned:
            raise ValueError("聊天内容不能为空。")
        return self._json(
            "/api/chat",
            method="POST",
            payload={"messages": cleaned, "model": model, "account": "模型先生"},
            timeout=90,
        )

    def _json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 10,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.origin}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(4 * 1024 * 1024)
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise ModelMrUnavailable("模型先生本机服务未连接。") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ModelMrUnavailable("模型先生服务返回了无效数据。") from error
        if not isinstance(value, dict):
            raise ModelMrUnavailable("模型先生服务返回格式不正确。")
        if value.get("error"):
            raise ModelMrUnavailable(str(value["error"]))
        return value

    def write_public_snapshot(self, output: Path, *, works_limit: int = 500, thoughts_limit: int = 300) -> dict[str, Any]:
        works = self.works(limit=works_limit).get("items", [])
        thoughts = self.thoughts(limit=thoughts_limit).get("categories", [])
        payload = {
            "version": SNAPSHOT_VERSION,
            "exported_at": int(time.time()),
            "scope": "works-and-investment-thoughts-only",
            "excluded": ["videos", "comments", "fans", "admin", "api_keys", "local_paths", "raw_database"],
            "works": works,
            "thoughts": thoughts,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=".model-mr-", suffix=".json", dir=output.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": str(output), "works": len(works), "thoughts": len(thoughts)}

    def _snapshot(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or int(value.get("version") or 0) != SNAPSHOT_VERSION:
            return None
        if not isinstance(value.get("works"), list) or not isinstance(value.get("thoughts"), list):
            return None
        return value

    def _require_snapshot(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        if snapshot is None:
            raise ModelMrUnavailable("模型先生本机服务未连接，精简资料也尚未部署。")
        return snapshot

    @staticmethod
    def _clean_work(item: dict[str, Any]) -> dict[str, Any]:
        keyword_info = item.get("keyword_info") if isinstance(item.get("keyword_info"), dict) else {}
        keywords = keyword_info.get("keywords") if isinstance(keyword_info.get("keywords"), list) else []
        description = str(item.get("description") or "")
        if description.startswith("由 model-"):
            description = ""
        return {
            "id": int(item.get("id") or 0),
            "title": str(item.get("active_title") or item.get("title") or "未命名作品"),
            "description": description,
            "url": str(item.get("url") or ""),
            "published_at": str(item.get("published_at") or item.get("discovered_at") or ""),
            "has_video_text": bool(item.get("has_video_text")),
            "has_interpretation": bool(item.get("has_interpretation")),
            "keywords": [str(value) for value in keywords[:8] if str(value).strip()],
        }

    @staticmethod
    def _clean_snapshot_work(item: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "id": int(item.get("id") or 0),
            "title": str(item.get("title") or "未命名作品"),
            "description": str(item.get("description") or ""),
            "url": str(item.get("url") or ""),
            "published_at": str(item.get("published_at") or ""),
            "has_video_text": bool(item.get("has_video_text")),
            "has_interpretation": bool(item.get("has_interpretation")),
            "keywords": [str(value) for value in item.get("keywords", [])[:8] if str(value).strip()]
            if isinstance(item.get("keywords"), list)
            else [],
        }
        return allowed

    @staticmethod
    def _clean_thought(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(item.get("id") or 0),
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or ""),
            "level": int(item.get("level") or 1),
            "parent_id": int(item["parent_id"]) if item.get("parent_id") is not None else None,
            "video_count": int(item.get("video_count") or 0),
        }


MODEL_MR = ModelMrClient()
