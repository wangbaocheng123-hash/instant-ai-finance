from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .model_mr import MODEL_MR, SUPPORTED_SNAPSHOT_VERSIONS


MCP_WORK_PREFIX = "model-mr-work:"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_DETAIL_BYTES = 16 * 1024 * 1024
MAX_WORKS = 1_000
MAX_AUTHOR_REPLY_THREADS = 100
MAX_AUTHOR_MESSAGES_PER_THREAD = 30
AUTHOR_KINDS = {"author", "author_comment", "author_reply"}


class ModelMrMcpUnavailable(RuntimeError):
    pass


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _search_text(value: object) -> str:
    return re.sub(r"[\W_]+", "", _text(value).casefold())


def _safe_url(value: object) -> str:
    raw = _text(value).strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return raw


def _time_order(value: object) -> float:
    raw = _text(value).strip()
    if not raw:
        return float("-inf")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (OSError, OverflowError, ValueError):
        return float("-inf")


_WORK_QUERY_NOISE = (
    "模型先生",
    "请帮我",
    "帮我",
    "请",
    "查询",
    "查找",
    "搜索",
    "看看",
    "看一下",
    "一下",
    "最新的",
    "最近的",
    "最新",
    "最近",
    "今天",
    "视频原文",
    "正式原文",
    "识别文字",
    "视频文字",
    "投资解读",
    "解读",
    "原文",
    "文字",
    "视频",
    "作品",
    "内容",
    "关于",
    "一条",
    "一篇",
    "这条",
    "这篇",
    "这个",
    "有哪些",
    "有什么",
    "怎么说",
    "怎么看",
    "说了什么",
    "是什么",
    "的",
    "了",
    "吗",
)


def _query_terms(question: str, *, thoughts: bool = False) -> list[str]:
    value = _search_text(question)
    noise = (
        "投资思路",
        "思路分类",
        "思路",
        "分类",
        "列表",
        *_WORK_QUERY_NOISE,
    ) if thoughts else _WORK_QUERY_NOISE
    for phrase in noise:
        value = value.replace(_search_text(phrase), "")
    return [value] if len(value) >= 2 else []


@dataclass(frozen=True)
class ModelMrMcpLibrary:
    """Read-only MCP projection over the Git-external Model Mr library.

    This class deliberately reads only the exported snapshot and per-work JSON.
    It never calls the live Model Mr sidecar, media endpoints, ASR, AI, or any
    owner write method.
    """

    snapshot_path: Path = field(default_factory=lambda: MODEL_MR.snapshot_path)

    @property
    def details_root(self) -> Path:
        return self.snapshot_path.parent / "details"

    def search_works_for_mcp(self, question: str, limit: int = 10) -> dict[str, Any]:
        value = str(question or "").strip()
        if not value:
            raise ValueError("question_required")
        safe_limit = max(1, min(int(limit), 30))
        snapshot = self._snapshot()
        works = [self._clean_work(item) for item in snapshot["works"][:MAX_WORKS] if isinstance(item, dict)]
        works = [work for work in works if work["id"] > 0]
        latest_requested = any(marker in value for marker in ("最新", "最近", "刚刚", "今天"))
        terms = _query_terms(value)

        candidates: list[dict[str, Any]] = []
        for work in works:
            detail = self._detail(work["id"])
            original = self._video_original(detail)
            interpretation = self._interpretation(detail)
            fields = {
                "title": _search_text(work["title"]),
                "description": _search_text(work["description"]),
                "keywords": _search_text(" ".join(work["keywords"])),
                "video_original": _search_text(original["text"]),
                "interpretation": _search_text(interpretation["text"]),
            }
            matched_in = [name for name, searchable in fields.items() if any(term in searchable for term in terms)]
            if terms and not matched_in:
                continue
            score = float(len(matched_in) * 30)
            if "title" in matched_in:
                score += 40
            if latest_requested:
                score += 10
            candidates.append(
                {
                    "record_id": f"{MCP_WORK_PREFIX}{work['id']}",
                    "source": "instant-ai-model-mr",
                    "title": work["title"],
                    "description": work["description"],
                    "published_at": work["published_at"],
                    "source_url": work["source_url"],
                    "keywords": work["keywords"],
                    "original_status": original["status"],
                    "original_excerpt": original["text"][:360],
                    "interpretation_excerpt": interpretation["text"][:360],
                    "matched_in": matched_in or ["recency"],
                    "relevance_score": score,
                    "_work_id": work["id"],
                }
            )

        if latest_requested or not terms:
            candidates.sort(
                key=lambda item: (_time_order(item["published_at"]), int(item["_work_id"])),
                reverse=True,
            )
            query_mode = "latest"
        else:
            candidates.sort(
                key=lambda item: (
                    float(item["relevance_score"]),
                    _time_order(item["published_at"]),
                    int(item["_work_id"]),
                ),
                reverse=True,
            )
            query_mode = "relevance"
        selected = candidates[:safe_limit]
        for item in selected:
            item.pop("_work_id", None)
        return {
            "available": True,
            "query": value,
            "query_mode": query_mode,
            "count": len(selected),
            "items": selected,
            "evidence_note": (
                "模型先生资料为单主人只读投影；official 可直接引用，"
                "video_text_unconfirmed 或 transcript_unconfirmed 需先核对。"
            ),
        }

    def get_work_for_mcp(self, record_id: str) -> dict[str, Any]:
        value = str(record_id or "").strip()
        match = re.fullmatch(r"model-mr-work:([1-9][0-9]{0,11})", value)
        if match is None:
            return {"found": False, "record_id": value}
        work_id = int(match.group(1))
        snapshot = self._snapshot()
        raw_work = next(
            (
                item
                for item in snapshot["works"][:MAX_WORKS]
                if isinstance(item, dict) and self._positive_int(item.get("id")) == work_id
            ),
            None,
        )
        if raw_work is None:
            return {"found": False, "record_id": value}
        work = self._clean_work(raw_work)
        detail = self._detail(work_id)
        original = self._video_original(detail)
        interpretation = self._interpretation(detail)
        return {
            "found": True,
            "record_id": f"{MCP_WORK_PREFIX}{work_id}",
            "source": "instant-ai-model-mr",
            "work": work,
            "video_original": original,
            "interpretation": interpretation,
            "evidence_note": (
                "这是主人已保存并标记为正式的视频原文。"
                if original["status"] == "official"
                else "这是尚未确认为正式原文的保存文字，引用前需要核对。"
                if original["text"]
                else "这条作品尚无可读取的视频文字。"
            ),
        }

    def get_author_replies_for_mcp(
        self,
        record_id: str,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return only source-marked Model Mr messages with their root question.

        The regular work-text tool deliberately continues to exclude comments. This
        separate projection is bounded by work and thread so a GPT can request the
        author's replies without receiving an entire comment area or fan profiles.
        """
        value = str(record_id or "").strip()
        match = re.fullmatch(r"model-mr-work:([1-9][0-9]{0,11})", value)
        if match is None:
            return {"found": False, "record_id": value}
        safe_limit = max(1, min(int(limit), MAX_AUTHOR_REPLY_THREADS))
        safe_offset = max(0, min(int(offset), 100_000))
        work_id = int(match.group(1))
        snapshot = self._snapshot()
        raw_work = next(
            (
                item
                for item in snapshot["works"][:MAX_WORKS]
                if isinstance(item, dict) and self._positive_int(item.get("id")) == work_id
            ),
            None,
        )
        if raw_work is None:
            return {"found": False, "record_id": value}
        work = self._clean_work(raw_work)
        detail = self._detail(work_id)
        source = detail.get("comments") if isinstance(detail.get("comments"), list) else []
        grouped: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(source, start=1):
            if not isinstance(raw, dict):
                continue
            text = _text(raw.get("text")).strip()[:20_000]
            if not text:
                continue
            kind = _text(raw.get("kind")).strip()
            depth = max(0, min(self._integer(raw.get("reply_depth")), 8))
            raw_key = _text(raw.get("thread_key")).strip().lower()
            thread_key = raw_key if re.fullmatch(r"[a-f0-9]{8,64}", raw_key) else f"row-{index}"
            thread = grouped.setdefault(thread_key, {"root": None, "author_messages": []})
            cleaned = {
                "text": text,
                "published_at": _text(raw.get("published_at")).strip()[:80],
                "like_count": max(0, self._integer(raw.get("like_count"))),
                "reply_count": max(0, self._integer(raw.get("reply_count"))),
                "reply_depth": depth,
                "kind": kind,
            }
            if depth == 0 and thread["root"] is None:
                thread["root"] = cleaned
            if kind in AUTHOR_KINDS:
                author_message = dict(cleaned)
                author_message["author"] = _text(raw.get("author")).strip()[:80] or "模型先生"
                thread["author_messages"].append(author_message)

        threads: list[dict[str, Any]] = []
        for thread in grouped.values():
            messages = thread["author_messages"]
            if not messages:
                continue
            root = thread["root"]
            question = None
            if isinstance(root, dict) and root.get("kind") not in AUTHOR_KINDS:
                question = {
                    "text": root["text"],
                    "published_at": root["published_at"],
                    "like_count": root["like_count"],
                    "reply_count": root["reply_count"],
                }
            threads.append(
                {
                    "question": question,
                    "author_messages": messages[:MAX_AUTHOR_MESSAGES_PER_THREAD],
                    "author_message_count": len(messages),
                    "context_status": "root_question_included" if question else "root_question_unavailable",
                }
            )
        total = len(threads)
        page = threads[safe_offset:safe_offset + safe_limit]
        return {
            "found": True,
            "record_id": f"{MCP_WORK_PREFIX}{work_id}",
            "source": "instant-ai-model-mr",
            "work": {
                "id": work["id"],
                "title": work["title"],
                "published_at": work["published_at"],
                "source_url": work["source_url"],
            },
            "count": len(page),
            "total": total,
            "offset": safe_offset,
            "has_more": safe_offset + len(page) < total,
            "items": page,
            "evidence_note": (
                "只返回来源明确标记为 author、author_comment 或 author_reply 的模型先生本人发言；"
                "昵称相同不作为作者身份依据。原提问仅保留正文、时间和计数，不返回粉丝账号或主页。"
            ),
        }

    def list_thoughts_for_mcp(self, query: str = "", limit: int = 100) -> dict[str, Any]:
        value = str(query or "").strip()
        safe_limit = max(1, min(int(limit), 300))
        snapshot = self._snapshot()
        terms = _query_terms(value, thoughts=True) if value else []
        items: list[dict[str, Any]] = []
        for raw in snapshot["thoughts"][:MAX_WORKS]:
            if not isinstance(raw, dict):
                continue
            item = {
                "id": self._positive_int(raw.get("id")),
                "name": _text(raw.get("name")).strip()[:160],
                "description": _text(raw.get("description")).strip()[:2_000],
                "level": max(1, min(self._positive_int(raw.get("level")) or 1, 20)),
                "parent_id": self._positive_int(raw.get("parent_id")) or None,
                "video_count": max(0, self._integer(raw.get("video_count"))),
            }
            if not item["name"]:
                continue
            searchable = _search_text(f"{item['name']} {item['description']}")
            if terms and not all(term in searchable for term in terms):
                continue
            items.append(item)
        items.sort(key=lambda item: (item["level"], item["id"], item["name"]))
        selected = items[:safe_limit]
        return {
            "available": True,
            "query": value,
            "count": len(selected),
            "items": selected,
            "purpose": "模型先生投资思路只读索引；分类不代表即时买卖建议。",
        }

    def _snapshot(self) -> dict[str, Any]:
        value = self._read_json(self.snapshot_path, MAX_SNAPSHOT_BYTES)
        if (
            not isinstance(value, dict)
            or self._integer(value.get("version")) not in SUPPORTED_SNAPSHOT_VERSIONS
            or not isinstance(value.get("works"), list)
            or not isinstance(value.get("thoughts"), list)
        ):
            raise ModelMrMcpUnavailable("model_mr_library_unavailable")
        return value

    def _detail(self, work_id: int) -> dict[str, Any]:
        path = self.details_root / f"{work_id}.json"
        try:
            path.resolve().relative_to(self.details_root.resolve())
        except (OSError, ValueError):
            return {}
        try:
            value = self._read_json(path, MAX_DETAIL_BYTES)
        except ModelMrMcpUnavailable:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _read_json(path: Path, maximum: int) -> object:
        try:
            if not path.is_file() or path.stat().st_size > maximum:
                raise ModelMrMcpUnavailable("model_mr_library_unavailable")
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelMrMcpUnavailable("model_mr_library_unavailable") from error

    @classmethod
    def _clean_work(cls, item: dict[str, Any]) -> dict[str, Any]:
        keywords = item.get("keywords") if isinstance(item.get("keywords"), list) else []
        return {
            "id": cls._positive_int(item.get("id")),
            "title": _text(item.get("title")).strip()[:240] or "未命名作品",
            "description": _text(item.get("description")).strip()[:4_000],
            "source_url": _safe_url(item.get("url")),
            "published_at": _text(item.get("published_at")).strip()[:80],
            "keywords": [_text(keyword).strip()[:80] for keyword in keywords[:12] if _text(keyword).strip()],
        }

    @staticmethod
    def _video_original(detail: dict[str, Any]) -> dict[str, Any]:
        note = detail.get("video_text") if isinstance(detail.get("video_text"), dict) else {}
        text = _text(note.get("text")).strip()[:200_000]
        if text:
            verified = bool(note.get("official")) and note.get("source") != "doubao-auto-unreviewed"
            return {
                "text": text,
                "verified": verified,
                "status": "official" if verified else "video_text_unconfirmed",
                "source": _text(note.get("source")).strip()[:120],
                "updated_at": _text(note.get("updated_at")).strip()[:80],
            }
        transcripts = detail.get("transcripts") if isinstance(detail.get("transcripts"), list) else []
        for raw in transcripts:
            if not isinstance(raw, dict):
                continue
            transcript = _text(raw.get("text")).strip()[:200_000]
            if transcript:
                return {
                    "text": transcript,
                    "verified": False,
                    "status": "transcript_unconfirmed",
                    "source": _text(raw.get("source")).strip()[:120],
                    "updated_at": _text(raw.get("created_at")).strip()[:80],
                }
        return {"text": "", "verified": False, "status": "missing", "source": "", "updated_at": ""}

    @staticmethod
    def _interpretation(detail: dict[str, Any]) -> dict[str, Any]:
        note = detail.get("interpretation") if isinstance(detail.get("interpretation"), dict) else {}
        return {
            "text": _text(note.get("text")).strip()[:200_000],
            "updated_at": _text(note.get("updated_at")).strip()[:80],
        }

    @staticmethod
    def _integer(value: object) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _positive_int(cls, value: object) -> int:
        return max(0, cls._integer(value))


MODEL_MR_MCP = ModelMrMcpLibrary()


__all__ = ["MODEL_MR_MCP", "MCP_WORK_PREFIX", "ModelMrMcpLibrary", "ModelMrMcpUnavailable"]
