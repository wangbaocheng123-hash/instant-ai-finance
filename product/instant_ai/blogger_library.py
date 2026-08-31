from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

from .blogger_ingest import DEFAULT_BLOGGER_AGENT_ROOT, STORE_SCHEMA_VERSION, opaque_work_key


MODULE_NAME = "blogger-library"
MODULE_MODE = "owner-read-only"
MAX_CURRENT_ROWS = 10_000

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
            "message": "博主资料库已按主人只读模式连接。",
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
        if not isinstance(work_key, str) or len(work_key) != 64:
            return None
        if any(character not in "0123456789abcdef" for character in work_key):
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
        return detail

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
        return {
            "work_key": _text(value.get("work_key")),
            "creator_id": _text(value.get("creator_id")),
            "source_work_id": _text(value.get("source_work_id")),
            "platform": _text(value.get("work_platform")),
            "work_type": _text(work.get("work_type")),
            "title": _text(work.get("title")),
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
            "processing_status": _text(value.get("processing_status")),
        }

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
