from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

from .blogger_ingest import DEFAULT_BLOGGER_AGENT_ROOT, STORE_SCHEMA_VERSION, opaque_work_key
from . import doubao_asr


MODULE_NAME = "blogger-library"
MODULE_MODE = "owner-mobile-library"
MAX_CURRENT_ROWS = 10_000
MAX_PUBLIC_COMMENTS = 10_000
MAX_VIDEO_TEXT_LENGTH = 200_000
MCP_RECORD_PREFIX = "cloud-video:"

_REQUIRED_TABLES = {"schema_meta", "transfers", "artifacts", "processing_queue"}
_PROCESSING_STATUSES = {
    "awaiting_transfer",
    "awaiting_asr_approval",
    "transcribing",
    "ready",
    "failed",
}
_TRANSFER_IN_PROGRESS = {"pending", "manifest_received", "transferring", "verifying"}


class BloggerLibraryUnavailable(RuntimeError):
    pass


def _zero_counts() -> dict[str, int]:
    return {
        "creators": 0,
        "works": 0,
        "transferring": 0,
        "awaiting_asr_approval": 0,
        "ready": 0,
        "failed": 0,
    }


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _timestamp(value: object) -> str | None:
    text = _text(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return text


def _epoch_timestamp(value: object) -> str | None:
    try:
        seconds = int(value)
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _timestamp_order(value: str | None) -> float:
    if not value:
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (OSError, OverflowError, ValueError):
        return float("-inf")


def _safe_source_url(value: object) -> str:
    text = _text(value).strip()
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return text


def _canonical_creator_id(value: object) -> str | None:
    text = _text(value).strip()
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, ValueError):
        return None
    canonical = str(parsed)
    return canonical if text.casefold() == canonical else None


def _search_text(value: object) -> str:
    """Normalize owner-facing text for small, deterministic MCP searches."""
    return re.sub(r"[\W_]+", "", _text(value).casefold()).replace("艾", "爱")


def _creator_aliases(value: object) -> list[str]:
    raw = _text(value).strip().casefold().replace("艾", "爱")
    aliases = {
        _search_text(raw),
        _search_text(re.sub(r"[^\u3400-\u9fff]+", "", raw)),
        _search_text(re.sub(r"[^a-z0-9]+", "", raw)),
    }
    return sorted((alias for alias in aliases if len(alias) >= 2), key=len, reverse=True)


_MCP_QUERY_NOISE = (
    "请帮我", "帮我", "请", "查询", "查找", "搜索", "看看", "看一下", "一下", "尤其是", "我们",
    "最新的", "最近的", "最新", "最近", "刚刚", "今天", "视频原文", "识别文字",
    "正式原文", "视频文字", "原文", "文字", "视频", "博主", "作品", "内容", "关于",
    "一条", "一篇", "一部", "这条", "这篇", "这部", "这个", "抓取", "采集",
    "怎么说", "怎么看", "说了什么", "是什么", "的", "了", "吗",
)


def _mcp_query_terms(question: str, creator_names: list[str]) -> list[str]:
    value = _search_text(question)
    for creator_name in creator_names:
        name = _search_text(creator_name)
        if name:
            value = value.replace(name, "")
    for noise in _MCP_QUERY_NOISE:
        value = value.replace(_search_text(noise), "")
    return [value] if len(value) >= 2 else []


class BloggerLibrary:
    """Owner-only projection over the Git-external ingest ledger.

    This reader deliberately does not use ``BloggerIngestStore`` because that
    write-side type initializes directories and schema. Every connection here
    uses SQLite URI ``mode=ro`` and selects only the fields needed by the owner
    client contract.
    """

    def __init__(self, root: Path = DEFAULT_BLOGGER_AGENT_ROOT) -> None:
        self.root = Path(root)
        self.database_path = self.root / "database" / "blogger_ingest.db"
        self.owner_database_path = self.root / "database" / "blogger_owner.db"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise BloggerLibraryUnavailable("博主资料库尚未创建。")
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error as error:
            raise BloggerLibraryUnavailable("博主资料库暂时无法读取。") from error
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=2000")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not _REQUIRED_TABLES.issubset(tables):
                raise BloggerLibraryUnavailable("博主资料库尚未完成初始化。")
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            try:
                schema_version = int(row[0]) if row is not None else 0
            except (TypeError, ValueError):
                schema_version = 0
            if schema_version != STORE_SCHEMA_VERSION:
                raise BloggerLibraryUnavailable("博主资料库版本暂不受支持。")
            yield connection
        except sqlite3.Error as error:
            raise BloggerLibraryUnavailable("博主资料库暂时无法读取。") from error
        finally:
            connection.close()

    def status(self) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                works = self._current_works(connection)
        except BloggerLibraryUnavailable as error:
            return {
                "available": False,
                "module": MODULE_NAME,
                "mode": MODULE_MODE,
                "message": str(error),
                "counts": _zero_counts(),
            }

        creators = {work["creator_id"] for work in works}
        counts = _zero_counts()
        counts["creators"] = len(creators)
        counts["works"] = len(works)
        for work in works:
            self._increment_status_counts(counts, work)
        return {
            "available": True,
            "module": MODULE_NAME,
            "mode": MODULE_MODE,
            "message": "博主资料库已连接，可查看视频、原文与评论。",
            "counts": counts,
        }

    def creators(self) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                works = self._current_works(connection)
        except BloggerLibraryUnavailable:
            return {"items": [], "count": 0}
        items = self._creator_items(works)
        return {"items": items, "count": len(items)}

    def creator_works(self, creator_id: str) -> dict[str, Any] | None:
        canonical_id = _canonical_creator_id(creator_id)
        if canonical_id is None:
            return None
        try:
            with self._connect() as connection:
                works = self._current_works(connection, creator_id=canonical_id)
        except BloggerLibraryUnavailable:
            return None
        if not works:
            return None
        creator = self._creator_items(works)[0]
        items = [self._public_work(work) for work in self._sort_works(works)]
        return {"creator": creator, "items": items, "count": len(items)}

    def work_detail(self, work_key: str) -> dict[str, Any] | None:
        if not self._valid_work_key(work_key):
            return None
        try:
            with self._connect() as connection:
                works = self._current_works(connection, work_key=work_key)
        except BloggerLibraryUnavailable:
            return None
        if not works:
            return None
        work = works[0]
        detail = self._public_work(work)
        snapshot = work["manifest"].get("comment_snapshot")
        detail["comment_snapshot"] = self._comment_snapshot(snapshot)
        owner = self._owner_content(work_key)
        comments = self._comments_for_work(work)
        transcript_text = _text(owner.get("transcript_text"))
        transcript_source = _text(owner.get("transcript_engine"))
        video_text = _text(owner.get("video_text"))
        media = self.video_path(work_key)
        detail["media_available"] = media is not None
        detail["video_url"] = f"/api/blogger-library/works/{work_key}/video" if media else ""
        transcripts = []
        if transcript_text:
            transcripts.append(
                {
                    "text": transcript_text,
                    "source": transcript_source or "识别结果",
                    "language": _text(owner.get("transcript_language")) or "zh-CN",
                    "created_at": _text(owner.get("transcript_created_at")),
                }
            )
        detail.update(
            {
                "video_text": {
                    "text": video_text,
                    "official": bool(video_text),
                    "source": "主人保存" if video_text else "",
                    "updated_at": _text(owner.get("updated_at")),
                },
                "transcripts": transcripts,
                "comments": comments,
                "comment_total": len(comments),
                "capabilities": {
                    "video": media is not None,
                    "save_title": True,
                    "save_video_text": True,
                    "transcribe_video": bool(transcript_text or video_text),
                    "doubao_asr": media is not None and doubao_asr.is_configured(),
                    "comments": bool(comments),
                },
            }
        )
        return detail

    def search_for_mcp(self, question: str, limit: int = 10) -> dict[str, Any]:
        """Search the current cloud blogger library without exposing private artifacts.

        This projection intentionally excludes comments, media files, filesystem paths,
        transfer identities and the raw manifest. It reads only the current accepted
        work plus its owner-saved official text (or a clearly marked transcript fallback).
        """
        value = str(question or "").strip()
        if not value:
            raise ValueError("question_required")
        safe_limit = max(1, min(int(limit), 30))
        try:
            with self._connect() as connection:
                works = self._sort_works(self._current_works(connection))
        except BloggerLibraryUnavailable:
            raise

        creator_names = sorted(
            {
                alias
                for work in works
                for alias in _creator_aliases(work.get("creator_display_name"))
            },
            key=len,
            reverse=True,
        )
        normalized_question = _search_text(value)
        latest_requested = any(marker in value for marker in ("最新", "最近", "刚刚", "今天"))
        query_terms = _mcp_query_terms(value, creator_names)
        items: list[dict[str, Any]] = []
        for work in works:
            creator = _text(work.get("creator_display_name")).strip()
            creator_aliases = _creator_aliases(creator)
            public = self._public_work(work)
            owner = self._owner_content(public["work_key"])
            official = _text(owner.get("video_text")).strip()
            transcript = _text(owner.get("transcript_text")).strip()
            searchable = _search_text(" ".join((
                creator,
                public["title"],
                public["description"],
                public["source_work_id"],
                official,
                transcript,
            )))
            creator_match = any(alias in normalized_question for alias in creator_aliases)
            term_matches = [term for term in query_terms if term in searchable]
            if query_terms and not term_matches:
                continue
            if not query_terms and not latest_requested and not creator_match:
                continue
            if latest_requested and creator_names and any(
                name in normalized_question for name in creator_names
            ) and not creator_match:
                continue
            text_value = official or transcript
            matched_in: list[str] = []
            if creator_match:
                matched_in.append("creator")
            if any(term in _search_text(public["title"]) for term in term_matches):
                matched_in.append("title")
            if any(term in _search_text(text_value) for term in term_matches):
                matched_in.append("video_original" if official else "transcript")
            score = 100.0 if creator_match else 20.0
            score += 30.0 * len(term_matches)
            if latest_requested:
                score += 10.0
            items.append({
                "record_id": f"{MCP_RECORD_PREFIX}{public['work_key']}",
                "source": "instant-ai-cloud-blogger",
                "creator": creator,
                "title": public["title"],
                "published_at": public["published_at"],
                "captured_at": public["captured_at"],
                "source_work_id": public["source_work_id"],
                "source_url": public["source_url"],
                "processing_status": public["processing_status"],
                "original_status": "official" if official else ("transcript_unconfirmed" if transcript else "missing"),
                "original_excerpt": text_value[:360],
                "matched_in": matched_in or (["recency"] if latest_requested else []),
                "relevance_score": score,
            })

        items.sort(
            key=lambda item: (
                _timestamp_order(item.get("published_at") or item.get("captured_at")),
                float(item.get("relevance_score") or 0),
            ) if latest_requested else (
                float(item.get("relevance_score") or 0),
                _timestamp_order(item.get("published_at") or item.get("captured_at")),
            ),
            reverse=True,
        )
        selected = items[:safe_limit]
        return {
            "available": True,
            "query": value,
            "query_mode": "latest" if latest_requested else "relevance",
            "count": len(selected),
            "items": selected,
            "evidence_note": "云端博主原文为只读证据；official 可直接引用，transcript_unconfirmed 需先核对。",
        }

    def get_for_mcp(self, record_id: str) -> dict[str, Any]:
        """Return one whitelisted cloud record for the read-only MCP bridge."""
        value = str(record_id or "").strip()
        work_key = value[len(MCP_RECORD_PREFIX):] if value.startswith(MCP_RECORD_PREFIX) else value
        if not self._valid_work_key(work_key):
            return {"found": False, "record_id": value}
        try:
            with self._connect() as connection:
                works = self._current_works(connection, work_key=work_key)
        except BloggerLibraryUnavailable:
            raise
        if not works:
            return {"found": False, "record_id": value}
        work = works[0]
        public = self._public_work(work)
        owner = self._owner_content(work_key)
        official = _text(owner.get("video_text")).strip()
        transcript = _text(owner.get("transcript_text")).strip()
        text_value = official or transcript
        return {
            "found": True,
            "record_id": f"{MCP_RECORD_PREFIX}{work_key}",
            "source": "instant-ai-cloud-blogger",
            "work": {
                "creator": _text(work.get("creator_display_name")).strip(),
                "title": public["title"],
                "source_work_id": public["source_work_id"],
                "source_url": public["source_url"],
                "published_at": public["published_at"],
                "captured_at": public["captured_at"],
                "processing_status": public["processing_status"],
            },
            "video_original": {
                "text": text_value,
                "verified": bool(official),
                "status": "official" if official else ("transcript_unconfirmed" if transcript else "missing"),
                "source": "owner_saved" if official else (_text(owner.get("transcript_engine")) or ""),
                "updated_at": _text(owner.get("updated_at")) or _text(owner.get("transcript_created_at")),
            },
            "evidence_note": (
                "这是主人已保存的正式视频原文。" if official else
                "这是尚未确认为正式原文的识别文字，引用前需要核对。" if transcript else
                "这条作品尚无可读取的视频文字。"
            ),
        }

    @staticmethod
    def _valid_work_key(work_key: object) -> bool:
        return (
            isinstance(work_key, str)
            and len(work_key) == 64
            and all(character in "0123456789abcdef" for character in work_key)
        )

    def save_title(self, work_key: str, title: str) -> dict[str, Any]:
        if not self._valid_work_key(work_key) or self.work_detail(work_key) is None:
            raise ValueError("博主作品不存在。")
        value = str(title or "").strip()
        if not value:
            raise ValueError("作品标题不能为空。")
        if len(value) > 120:
            raise ValueError("作品标题不能超过 120 个字符。")
        self._save_owner_content(work_key, title=value)
        return {"ok": True, "title": value, "saved": True, "mode": MODULE_MODE}

    def save_video_text(self, work_key: str, text: str) -> dict[str, Any]:
        if not self._valid_work_key(work_key) or self.work_detail(work_key) is None:
            raise ValueError("博主作品不存在。")
        value = str(text or "").strip()
        if len(value) > MAX_VIDEO_TEXT_LENGTH:
            raise ValueError("视频原文超过保存上限。")
        self._save_owner_content(work_key, video_text=value)
        return {"ok": True, "text": value, "saved": True, "mode": MODULE_MODE}

    def transcribe(self, work_key: str, engine: str) -> dict[str, Any]:
        if not self._valid_work_key(work_key) or self.work_detail(work_key) is None:
            raise ValueError("博主作品不存在。")
        owner = self._owner_content(work_key)
        cached = _text(owner.get("transcript_text")) or _text(owner.get("video_text"))
        if engine != "doubao":
            if not cached:
                raise BloggerLibraryUnavailable("尚无可读取的识别结果；可在确认费用后使用豆包识别。")
            return {
                "text": cached,
                "engine": _text(owner.get("transcript_engine")) or "owner-saved",
                "cached": True,
                "message": "已读取现有识别文字，请核对后保存为视频原文。",
            }
        media = self.video_path(work_key)
        if media is None:
            raise BloggerLibraryUnavailable("这条博主作品没有可识别的视频。")
        try:
            result = doubao_asr.transcribe_video(media[0], int(work_key[:12], 16), scope="blogger")
        except doubao_asr.DoubaoAsrUnavailable as error:
            raise BloggerLibraryUnavailable(str(error)) from error
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._save_owner_content(
            work_key,
            transcript_text=_text(result.get("text")),
            transcript_engine=_text(result.get("engine")) or "doubao-recording-asr-2.0",
            transcript_language="zh-CN",
            transcript_created_at=now,
        )
        return result

    def video_path(self, work_key: str) -> tuple[Path, str] | None:
        if not self._valid_work_key(work_key):
            return None
        try:
            with self._connect() as connection:
                works = self._current_works(connection, work_key=work_key)
                if not works:
                    return None
                descriptor = self._artifact_descriptor(connection, works[0], "media")
        except BloggerLibraryUnavailable:
            return None
        if descriptor is None:
            return None
        target = self._safe_artifact_path(descriptor)
        return (target, _text(descriptor.get("mime_type")) or "video/mp4") if target else None

    def _current_works(
        self,
        connection: sqlite3.Connection,
        *,
        creator_id: str | None = None,
        work_key: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        parameters: list[object] = []
        if creator_id is not None:
            filters.append("t.creator_id=?")
            parameters.append(creator_id)
        extra_where = "" if not filters else " AND " + " AND ".join(filters)
        parameters.append(MAX_CURRENT_ROWS + 1)
        rows = connection.execute(
            f"""
            SELECT
                t.transfer_id,
                t.creator_id,
                t.creator_display_name,
                t.creator_platform,
                t.work_platform,
                t.source_work_id,
                t.source_revision,
                t.transport_status,
                t.received_at,
                t.manifest_json,
                p.processing_status,
                SUM(CASE WHEN a.artifact_kind='media' THEN 1 ELSE 0 END) AS media_expected,
                SUM(CASE WHEN a.artifact_kind='media' AND a.state='verified' THEN 1 ELSE 0 END) AS media_received,
                SUM(CASE WHEN a.artifact_kind='media' AND a.state='verified' AND a.media_role='video' AND a.mime_type='video/mp4' THEN 1 ELSE 0 END) AS video_received,
                SUM(CASE WHEN a.artifact_kind='comment_bundle' THEN 1 ELSE 0 END) AS comments_expected,
                SUM(CASE WHEN a.artifact_kind='comment_bundle' AND a.state='verified' THEN 1 ELSE 0 END) AS comments_received
            FROM transfers AS t
            LEFT JOIN artifacts AS a ON a.transfer_id=t.transfer_id
            LEFT JOIN processing_queue AS p ON p.transfer_id=t.transfer_id
            WHERE t.is_current=1 AND t.state='accepted'
              AND NOT EXISTS (
                  SELECT 1
                  FROM transfers AS newer
                  WHERE newer.work_platform=t.work_platform
                    AND newer.creator_id=t.creator_id
                    AND newer.source_work_id=t.source_work_id
                    AND newer.is_current=1
                    AND newer.state='accepted'
                    AND (
                        newer.source_revision > t.source_revision
                        OR (newer.source_revision=t.source_revision AND newer.received_at > t.received_at)
                        OR (
                            newer.source_revision=t.source_revision
                            AND newer.received_at=t.received_at
                            AND newer.transfer_id > t.transfer_id
                        )
                    )
              )
              {extra_where}
            GROUP BY t.transfer_id
            ORDER BY t.received_at DESC, t.source_revision DESC, t.transfer_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        if len(rows) > MAX_CURRENT_ROWS:
            raise BloggerLibraryUnavailable("博主资料数量超过当前只读查询上限。")

        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                manifest = json.loads(str(row["manifest_json"]))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(manifest, dict):
                continue
            creator = manifest.get("creator")
            work = manifest.get("work")
            if not isinstance(creator, dict) or not isinstance(work, dict):
                continue
            canonical_id = _canonical_creator_id(row["creator_id"])
            if canonical_id is None:
                continue
            calculated_key = opaque_work_key(
                _text(row["work_platform"]),
                canonical_id,
                _text(row["source_work_id"]),
            )
            if work_key is not None and calculated_key != work_key:
                continue
            value = dict(row)
            value["_transfer_id"] = _text(value.get("transfer_id"))
            value.pop("transfer_id", None)
            value.pop("manifest_json", None)
            value["creator_id"] = canonical_id
            value["work_key"] = calculated_key
            value["manifest"] = manifest
            value["processing_status"] = self._processing_status(row["processing_status"])
            value["transfer_status"] = self._transfer_status(value)
            result.append(value)
        return result

    @staticmethod
    def _processing_status(value: object) -> str:
        text = _text(value)
        if not text:
            return "awaiting_transfer"
        return text if text in _PROCESSING_STATUSES else "failed"

    @staticmethod
    def _transfer_status(work: Mapping[str, Any]) -> str:
        media_expected = _integer(work.get("media_expected"))
        media_received = _integer(work.get("media_received"))
        comments_expected = _integer(work.get("comments_expected"))
        comments_received = _integer(work.get("comments_received"))
        expected = media_expected + comments_expected
        received = media_received + comments_received
        completed = _text(work.get("transport_status")) == "transport_completed"
        if received > expected or (completed and received != expected):
            return "failed"
        if completed:
            return "verified"
        if expected == 0:
            return "pending"
        if received == 0:
            return "manifest_received"
        if received < expected:
            return "transferring"
        return "verifying"

    @staticmethod
    def _increment_status_counts(counts: dict[str, int], work: Mapping[str, Any]) -> None:
        transfer_status = _text(work.get("transfer_status"))
        processing_status = _text(work.get("processing_status"))
        if transfer_status in _TRANSFER_IN_PROGRESS:
            counts["transferring"] += 1
        if processing_status == "awaiting_asr_approval":
            counts["awaiting_asr_approval"] += 1
        if processing_status == "ready":
            counts["ready"] += 1
        if transfer_status == "failed" or processing_status == "failed":
            counts["failed"] += 1

    def _creator_items(self, works: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for work in works:
            grouped.setdefault(work["creator_id"], []).append(work)
        result: list[dict[str, Any]] = []
        for creator_id, creator_works in grouped.items():
            latest = max(
                creator_works,
                key=lambda item: (
                    _timestamp_order(self._captured_at(item)),
                    _integer(item.get("received_at")),
                    _integer(item.get("source_revision")),
                ),
            )
            published = [self._published_at(item) for item in creator_works]
            captured = [self._captured_at(item) for item in creator_works]
            status_counts = {
                "works": len(creator_works),
                "transferring": 0,
                "awaiting_asr_approval": 0,
                "ready": 0,
                "failed": 0,
            }
            for work in creator_works:
                self._increment_status_counts(status_counts, work)
            result.append(
                {
                    "creator_id": creator_id,
                    "display_name": _text(latest.get("creator_display_name")),
                    "platform": _text(latest.get("creator_platform")),
                    "work_count": len(creator_works),
                    "latest_published_at": max(published, key=_timestamp_order, default=None),
                    "latest_captured_at": max(captured, key=_timestamp_order, default=None),
                    "status_counts": status_counts,
                }
            )
        result.sort(
            key=lambda item: (
                _timestamp_order(item["latest_published_at"] or item["latest_captured_at"]),
                item["creator_id"],
            ),
            reverse=True,
        )
        return result

    @staticmethod
    def _work_manifest(work: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest = work.get("manifest")
        return manifest if isinstance(manifest, dict) else {}

    def _published_at(self, work: Mapping[str, Any]) -> str | None:
        manifest_work = self._work_manifest(work).get("work")
        return _timestamp(manifest_work.get("published_at")) if isinstance(manifest_work, dict) else None

    def _captured_at(self, work: Mapping[str, Any]) -> str | None:
        return _timestamp(self._work_manifest(work).get("captured_at"))

    def _sort_works(self, works: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            works,
            key=lambda item: (
                _timestamp_order(self._published_at(item) or self._captured_at(item)),
                _integer(item.get("source_revision")),
                item["work_key"],
            ),
            reverse=True,
        )

    def _public_work(self, value: Mapping[str, Any]) -> dict[str, Any]:
        manifest = self._work_manifest(value)
        raw_work = manifest.get("work")
        work = raw_work if isinstance(raw_work, dict) else {}
        captured_at = self._captured_at(value) or _epoch_timestamp(value.get("received_at")) or ""
        work_key = _text(value.get("work_key"))
        owner = self._owner_content(work_key)
        snapshot = self._comment_snapshot(manifest.get("comment_snapshot"))
        media_available = _integer(value.get("video_received")) > 0
        processing_status = _text(value.get("processing_status"))
        if _text(owner.get("video_text")) and processing_status == "awaiting_asr_approval":
            processing_status = "ready"
        return {
            "work_key": work_key,
            "creator_id": _text(value.get("creator_id")),
            "source_work_id": _text(value.get("source_work_id")),
            "platform": _text(value.get("work_platform")),
            "work_type": _text(work.get("work_type")),
            "title": _text(owner.get("title")) or _text(work.get("title")),
            "description": _text(work.get("description")),
            "source_url": _safe_source_url(work.get("source_url")),
            "published_at": self._published_at(value),
            "captured_at": captured_at,
            "transfer": {
                "status": _text(value.get("transfer_status")),
                "source_revision": _integer(value.get("source_revision")),
                "received_at": _epoch_timestamp(value.get("received_at")),
                "media_expected": _integer(value.get("media_expected")),
                "media_received": _integer(value.get("media_received")),
                "comments_expected": _integer(value.get("comments_expected")),
                "comments_received": _integer(value.get("comments_received")),
            },
            "processing_status": processing_status,
            "media_available": media_available,
            "video_url": f"/api/blogger-library/works/{work_key}/video" if media_available else "",
            "has_video_text": bool(_text(owner.get("video_text"))),
            "comment_count": _integer(snapshot.get("captured_count")) if snapshot else 0,
        }

    @staticmethod
    def _artifact_descriptor(
        connection: sqlite3.Connection,
        work: Mapping[str, Any],
        artifact_kind: str,
    ) -> dict[str, Any] | None:
        transfer_id = _text(work.get("_transfer_id"))
        if not transfer_id:
            return None
        conditions = "artifact_kind=? AND state='verified'"
        parameters: list[object] = [transfer_id, artifact_kind]
        if artifact_kind == "media":
            conditions += " AND media_role='video' AND mime_type='video/mp4'"
        row = connection.execute(
            f"""
            SELECT stored_relative_path, expected_size_bytes, expected_sha256,
                   mime_type, uncompressed_size_bytes, uncompressed_sha256, item_count
            FROM artifacts
            WHERE transfer_id=? AND {conditions}
            ORDER BY ordinal ASC, artifact_id ASC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        return dict(row) if row is not None else None

    def _safe_artifact_path(self, descriptor: Mapping[str, Any]) -> Path | None:
        relative = _text(descriptor.get("stored_relative_path")).replace("\\", "/")
        parts = [part for part in relative.split("/") if part]
        if not parts or relative.startswith("/") or any(part in {".", ".."} for part in parts):
            return None
        candidate = self.root.joinpath(*parts)
        try:
            root = self.root.resolve(strict=True)
            artifacts_root = (root / "artifacts").resolve(strict=True)
            target = candidate.resolve(strict=True)
            if artifacts_root != target and artifacts_root not in target.parents:
                return None
            current = root
            for part in parts:
                current = current / part
                if current.is_symlink():
                    return None
            if not target.is_file() or target.stat().st_size != _integer(descriptor.get("expected_size_bytes")):
                return None
        except (OSError, RuntimeError):
            return None
        return target

    def _comments_for_work(self, work: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            with self._connect() as connection:
                descriptor = self._artifact_descriptor(connection, work, "comment_bundle")
        except BloggerLibraryUnavailable:
            return []
        if descriptor is None:
            return []
        target = self._safe_artifact_path(descriptor)
        if target is None:
            return []
        expected_uncompressed = _integer(descriptor.get("uncompressed_size_bytes"))
        expected_items = _integer(descriptor.get("item_count"))
        if expected_items > MAX_PUBLIC_COMMENTS or expected_uncompressed > 128 * 1024 * 1024:
            return []
        comments: list[dict[str, Any]] = []
        digest = hashlib.sha256()
        uncompressed_size = 0
        try:
            with gzip.open(target, "rb") as stream:
                for raw_line in stream:
                    uncompressed_size += len(raw_line)
                    if uncompressed_size > expected_uncompressed or len(comments) >= MAX_PUBLIC_COMMENTS:
                        return []
                    digest.update(raw_line)
                    item = json.loads(raw_line.decode("utf-8"))
                    if not isinstance(item, dict):
                        return []
                    source_id = _text(item.get("source_comment_id"))
                    thread_source = (
                        _text(item.get("root_source_comment_id"))
                        or _text(item.get("parent_source_comment_id"))
                        or source_id
                    )
                    is_creator = item.get("is_creator") is True
                    comments.append(
                        {
                            "id": len(comments) + 1,
                            "author": _text(item.get("author")) or "抖音用户",
                            "text": _text(item.get("text")),
                            "like_count": _integer(item.get("like_count")),
                            "reply_count": _integer(item.get("reply_count")),
                            "published_at": _timestamp(item.get("published_at")) or "",
                            "kind": "author_reply" if is_creator else (_text(item.get("kind")) or "comment"),
                            "reply_depth": 1 if _text(item.get("parent_source_comment_id")) else 0,
                            "thread_key": hashlib.sha256(thread_source.encode("utf-8")).hexdigest()[:16],
                            "author_liked": item.get("author_liked") is True,
                        }
                    )
        except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError, json.JSONDecodeError):
            return []
        if uncompressed_size != expected_uncompressed or len(comments) != expected_items:
            return []
        expected_digest = _text(descriptor.get("uncompressed_sha256"))
        if expected_digest and digest.hexdigest() != expected_digest:
            return []
        return comments

    def _owner_content(self, work_key: str) -> dict[str, Any]:
        if not self._valid_work_key(work_key) or not self.owner_database_path.is_file():
            return {}
        uri = f"{self.owner_database_path.resolve().as_uri()}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM work_content WHERE work_key=?",
                    (work_key,),
                ).fetchone()
        except sqlite3.Error:
            return {}
        return dict(row) if row is not None else {}

    def _save_owner_content(self, work_key: str, **changes: str) -> None:
        self.owner_database_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            with closing(sqlite3.connect(self.owner_database_path, timeout=5.0)) as connection, connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS work_content(
                        work_key TEXT PRIMARY KEY,
                        title TEXT NOT NULL DEFAULT '',
                        video_text TEXT NOT NULL DEFAULT '',
                        transcript_text TEXT NOT NULL DEFAULT '',
                        transcript_engine TEXT NOT NULL DEFAULT '',
                        transcript_language TEXT NOT NULL DEFAULT '',
                        transcript_created_at TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                row = connection.execute(
                    "SELECT * FROM work_content WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                fields = {
                    "title": "",
                    "video_text": "",
                    "transcript_text": "",
                    "transcript_engine": "",
                    "transcript_language": "",
                    "transcript_created_at": "",
                }
                if row is not None:
                    names = [column[0] for column in connection.execute("SELECT * FROM work_content LIMIT 0").description]
                    fields.update(dict(zip(names, row)))
                fields.update({key: _text(value) for key, value in changes.items() if key in fields})
                connection.execute(
                    """
                    INSERT INTO work_content(
                        work_key, title, video_text, transcript_text, transcript_engine,
                        transcript_language, transcript_created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(work_key) DO UPDATE SET
                        title=excluded.title,
                        video_text=excluded.video_text,
                        transcript_text=excluded.transcript_text,
                        transcript_engine=excluded.transcript_engine,
                        transcript_language=excluded.transcript_language,
                        transcript_created_at=excluded.transcript_created_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        work_key,
                        fields["title"],
                        fields["video_text"],
                        fields["transcript_text"],
                        fields["transcript_engine"],
                        fields["transcript_language"],
                        fields["transcript_created_at"],
                        now,
                    ),
                )
            try:
                os.chmod(self.owner_database_path, 0o600)
            except OSError:
                pass
        except sqlite3.Error as error:
            raise BloggerLibraryUnavailable("博主原文暂时无法保存。") from error

    @staticmethod
    def _comment_snapshot(value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        complete = value.get("complete")
        return {
            "captured_at": _timestamp(value.get("captured_at")),
            "complete": complete if isinstance(complete, bool) else False,
            "expected_total": _integer(value.get("expected_total")),
            "captured_count": _integer(value.get("captured_count")),
            "top_level_count": _integer(value.get("top_level_count")),
            "reply_groups": _integer(value.get("reply_groups")),
            "missing_replies": _integer(value.get("missing_replies")),
        }


BLOGGER_LIBRARY = BloggerLibrary()


__all__ = ["BLOGGER_LIBRARY", "BloggerLibrary", "BloggerLibraryUnavailable"]
