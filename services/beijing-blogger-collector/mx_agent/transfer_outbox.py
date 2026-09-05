from __future__ import annotations

import json
import os
import random
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from .transfer_contract import (
    SHA256_PATTERN,
    canonical_json_bytes,
    revision_decision,
    sanitize_manifest,
    sha256_file,
)


OUTBOX_STATES = {
    "pending",
    "manifest_accepted",
    "media_uploading",
    "finalizing",
    "retry_wait",
    "delivered",
    "dead_letter",
}
TERMINAL_STATES = {"delivered", "dead_letter"}
ARTIFACT_KINDS = {"media", "comment_bundle"}
ARTIFACT_STATES = {"pending", "uploading", "uploaded", "verified"}
ARTIFACT_STATE_RANK = {
    "pending": 0,
    "uploading": 1,
    "uploaded": 2,
    "verified": 3,
}
ACTIVE_STATES = {"pending", "manifest_accepted", "media_uploading", "finalizing"}
LEGAL_TRANSITIONS = {
    "pending": {"manifest_accepted"},
    "manifest_accepted": {"media_uploading", "finalizing"},
    "media_uploading": {"finalizing"},
    "finalizing": {"delivered"},
    "delivered": set(),
    "dead_letter": set(),
}


class TransferOutboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SequenceReservation:
    source_sequence: int
    work_revision: int


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _future_iso(seconds: int, *, now: datetime | None = None) -> str:
    base = now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return (base.astimezone(UTC) + timedelta(seconds=max(0, seconds))).isoformat()


def retry_delay_seconds(
    attempt_count: int,
    *,
    base_seconds: int = 30,
    cap_seconds: int = 6 * 60 * 60,
    jitter_unit: float | None = None,
) -> int:
    """Return capped exponential backoff with full jitter.

    Production calls draw fresh system randomness. Tests may supply a fixed
    ``jitter_unit`` in [0, 1].
    """

    attempt = max(1, int(attempt_count))
    base = max(1, int(base_seconds))
    cap = max(base, int(cap_seconds))
    sampled = random.SystemRandom().random() if jitter_unit is None else jitter_unit
    jitter = min(1.0, max(0.0, float(sampled)))
    window = min(cap, base * (2 ** min(attempt - 1, 20)))
    return max(1, int(window * jitter))


class TransferOutbox:
    """Persistent Beijing-side transfer queue.

    Local artifact paths are stored only in this private SQLite file and are
    never added to the transfer manifest. Protocol v1 records whole-file
    states only: failed uploads restart at byte zero; byte-range/breakpoint
    resume is intentionally not claimed or implemented.
    """

    def __init__(
        self,
        path: Path,
        *,
        allowed_artifact_roots: Iterable[Path] | None = None,
        lease_seconds: int = 30 * 60,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(self.path.parent, 0o700)
        configured_roots = (
            tuple(allowed_artifact_roots)
            if allowed_artifact_roots is not None
            else (self.path.parent,)
        )
        self.allowed_artifact_roots = self._normalize_allowed_roots(configured_roots)
        self.lease_seconds = max(30, int(lease_seconds))
        self._init_schema()
        self._secure_database_permissions()

    @staticmethod
    def _normalize_allowed_roots(values: Iterable[Path]) -> tuple[Path, ...]:
        roots: list[Path] = []
        for value in values:
            try:
                root = Path(value).expanduser().resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise TransferOutboxError("artifact 允许目录不存在或不可访问。") from exc
            if not root.is_dir():
                raise TransferOutboxError("artifact 允许根必须是目录。")
            if root not in roots:
                roots.append(root)
        if not roots:
            raise TransferOutboxError("至少需要配置一个 artifact 允许根。")
        return tuple(roots)

    def _secure_database_permissions(self) -> None:
        if os.name != "posix":
            return
        os.chmod(self.path.parent, 0o700)
        candidates = (self.path, *self.path.parent.glob(f"{self.path.name}-*"))
        for candidate in candidates:
            try:
                if candidate.is_file():
                    os.chmod(candidate, 0o600)
            except OSError:
                continue

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        self._secure_database_permissions()
        return connection

    def _init_schema(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS collector_sequences (
                    node_id TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS work_revision_counters (
                    node_id TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    source_work_id TEXT NOT NULL,
                    last_revision INTEGER NOT NULL,
                    PRIMARY KEY(node_id, creator_id, platform, source_work_id)
                );

                CREATE TABLE IF NOT EXISTS transfer_outbox (
                    transfer_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    source_work_id TEXT NOT NULL,
                    source_sequence INTEGER NOT NULL,
                    work_revision INTEGER NOT NULL,
                    revision_sha256 TEXT NOT NULL,
                    manifest_json BLOB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    resume_status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    receipt_id TEXT NOT NULL DEFAULT '',
                    last_http_status INTEGER,
                    last_error_code TEXT NOT NULL DEFAULT '',
                    last_error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    delivered_at TEXT,
                    state_version INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT,
                    UNIQUE(node_id, source_sequence),
                    UNIQUE(node_id, creator_id, platform, source_work_id, work_revision)
                );

                CREATE INDEX IF NOT EXISTS idx_transfer_outbox_ready
                ON transfer_outbox(status, next_attempt_at, source_sequence);

                CREATE TABLE IF NOT EXISTS transfer_artifacts (
                    transfer_id TEXT NOT NULL
                        REFERENCES transfer_outbox(transfer_id) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL,
                    artifact_kind TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    uploaded_bytes INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(transfer_id, artifact_id)
                );
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(transfer_outbox)")
            }
            for name, definition in (
                ("state_version", "INTEGER NOT NULL DEFAULT 0"),
                ("lease_owner", "TEXT NOT NULL DEFAULT ''"),
                ("lease_expires_at", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE transfer_outbox ADD COLUMN {name} {definition}"
                    )
            artifact_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(transfer_artifacts)")
            }
            if "mime_type" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE transfer_artifacts ADD COLUMN "
                    "mime_type TEXT NOT NULL DEFAULT 'application/octet-stream'"
                )

    def reserve(
        self,
        *,
        node_id: str,
        creator_id: str,
        platform: str,
        source_work_id: str,
    ) -> SequenceReservation:
        """Atomically reserve globally and per-work monotonic numbers.

        Gaps are allowed after a crash; a number is never reused.
        """

        identity = tuple(
            str(value or "").strip()
            for value in (node_id, creator_id, platform, source_work_id)
        )
        if not all(identity):
            raise TransferOutboxError("预留序号的作品身份不能为空。")
        node, creator, source_platform, work = identity
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT last_sequence FROM collector_sequences WHERE node_id = ?",
                (node,),
            ).fetchone()
            source_sequence = int(row["last_sequence"] if row else 0) + 1
            connection.execute(
                """
                INSERT INTO collector_sequences(node_id, last_sequence)
                VALUES (?, ?)
                ON CONFLICT(node_id) DO UPDATE SET last_sequence=excluded.last_sequence
                """,
                (node, source_sequence),
            )
            row = connection.execute(
                """
                SELECT last_revision
                FROM work_revision_counters
                WHERE node_id = ? AND creator_id = ? AND platform = ?
                  AND source_work_id = ?
                """,
                (node, creator, source_platform, work),
            ).fetchone()
            work_revision = int(row["last_revision"] if row else 0) + 1
            connection.execute(
                """
                INSERT INTO work_revision_counters(
                    node_id, creator_id, platform, source_work_id, last_revision
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(node_id, creator_id, platform, source_work_id)
                DO UPDATE SET last_revision=excluded.last_revision
                """,
                (node, creator, source_platform, work, work_revision),
            )
            connection.commit()
        return SequenceReservation(source_sequence, work_revision)

    def enqueue(
        self,
        manifest: Mapping[str, Any],
        *,
        artifacts: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        supplied_transfer_id = str(manifest.get("transfer_id") or "")
        supplied_revision_hash = str(manifest.get("revision_sha256") or "")
        sanitized = sanitize_manifest(manifest)
        if supplied_transfer_id and supplied_transfer_id != sanitized["transfer_id"]:
            raise TransferOutboxError("transfer_id 与规范化清单不一致。")
        if supplied_revision_hash and supplied_revision_hash != sanitized["revision_sha256"]:
            raise TransferOutboxError("revision_sha256 与规范化清单不一致。")
        validated_artifacts = self._validate_artifacts(sanitized, artifacts)

        collector = sanitized["collector"]
        creator = sanitized["creator"]
        work = sanitized["work"]
        identity = (
            collector["node_id"],
            creator["creator_id"],
            work["platform"],
            work["source_work_id"],
        )
        now = _now_iso()
        manifest_bytes = canonical_json_bytes(sanitized)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_transfer = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (sanitized["transfer_id"],),
            ).fetchone()
            if existing_transfer:
                connection.commit()
                return self._serialize_row(existing_transfer, action="duplicate")

            head = connection.execute(
                """
                SELECT work_revision, revision_sha256, transfer_id
                FROM transfer_outbox
                WHERE node_id = ? AND creator_id = ? AND platform = ?
                  AND source_work_id = ?
                ORDER BY work_revision DESC
                LIMIT 1
                """,
                identity,
            ).fetchone()
            decision = revision_decision(
                existing_revision=int(head["work_revision"]) if head else None,
                existing_sha256=str(head["revision_sha256"]) if head else None,
                incoming_revision=int(work["revision"]),
                incoming_sha256=sanitized["revision_sha256"],
            )
            if decision == "stale":
                connection.commit()
                return {
                    "action": "stale",
                    "transfer_id": sanitized["transfer_id"],
                    "current_transfer_id": str(head["transfer_id"]) if head else "",
                    "status": "superseded",
                }
            if decision == "duplicate":
                duplicate = connection.execute(
                    "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                    (str(head["transfer_id"]),),
                ).fetchone()
                connection.commit()
                return self._serialize_row(duplicate, action="duplicate")

            try:
                connection.execute(
                    """
                    INSERT INTO transfer_outbox(
                        transfer_id, node_id, creator_id, platform, source_work_id,
                        source_sequence, work_revision, revision_sha256,
                        manifest_json, status, resume_status, next_attempt_at,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', ?, ?, ?)
                    """,
                    (
                        sanitized["transfer_id"],
                        *identity,
                        int(collector["source_sequence"]),
                        int(work["revision"]),
                        sanitized["revision_sha256"],
                        manifest_bytes,
                        now,
                        now,
                        now,
                    ),
                )
                for artifact in validated_artifacts:
                    self._insert_artifact(connection, sanitized["transfer_id"], artifact, now)
            except sqlite3.IntegrityError as exc:
                raise TransferOutboxError(
                    "source_sequence 或作品 revision 已被其他清单占用。"
                ) from exc
            connection.commit()
            inserted = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (sanitized["transfer_id"],),
            ).fetchone()
            return self._serialize_row(inserted, action="inserted")

    def _validate_artifacts(
        self,
        manifest: Mapping[str, Any],
        values: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        expected: dict[str, dict[str, Any]] = {}
        for media in manifest["media"]:
            artifact_id = str(media["media_id"])
            if artifact_id in expected:
                raise TransferOutboxError("manifest artifact_id 重复。")
            expected[artifact_id] = {
                "artifact_id": artifact_id,
                "artifact_kind": "media",
                "size_bytes": int(media["size_bytes"]),
                "sha256": str(media["sha256"]),
                "mime_type": str(media["mime_type"]),
            }
        bundle = manifest["comment_snapshot"]["bundle"]
        bundle_id = str(bundle["bundle_id"])
        if bundle_id in expected:
            raise TransferOutboxError("评论包与媒体 artifact_id 冲突。")
        expected[bundle_id] = {
            "artifact_id": bundle_id,
            "artifact_kind": "comment_bundle",
            "size_bytes": int(bundle["size_bytes"]),
            "sha256": str(bundle["sha256"]),
            "mime_type": "application/gzip",
        }

        supplied: dict[str, Mapping[str, Any]] = {}
        for value in values:
            if not isinstance(value, Mapping):
                raise TransferOutboxError("artifact 必须是对象。")
            artifact_id = str(value.get("artifact_id") or "").strip()
            if not artifact_id:
                raise TransferOutboxError("artifact_id 不能为空。")
            if artifact_id in supplied:
                raise TransferOutboxError("artifact_id 重复。")
            supplied[artifact_id] = value
        if set(supplied) != set(expected):
            raise TransferOutboxError("artifact 必须与 manifest 一一对应。")

        validated: list[dict[str, Any]] = []
        for artifact_id, descriptor in expected.items():
            value = supplied[artifact_id]
            artifact_kind = str(value.get("artifact_kind") or "").strip()
            sha256 = str(value.get("sha256") or "").strip().lower()
            try:
                raw_size = value.get("size_bytes")
                if isinstance(raw_size, bool):
                    raise ValueError
                size_bytes = int(raw_size)
            except (TypeError, ValueError) as exc:
                raise TransferOutboxError("artifact.size_bytes 无效。") from exc
            if artifact_kind != descriptor["artifact_kind"]:
                raise TransferOutboxError("artifact_kind 与 manifest 不一致。")
            if size_bytes != descriptor["size_bytes"]:
                raise TransferOutboxError("artifact 长度与 manifest 不一致。")
            if sha256 != descriptor["sha256"]:
                raise TransferOutboxError("artifact SHA-256 与 manifest 不一致。")
            supplied_mime = str(value.get("mime_type") or "").strip()
            if supplied_mime and supplied_mime != descriptor["mime_type"]:
                raise TransferOutboxError("artifact MIME 与 manifest 不一致。")
            local_path = self._checked_artifact_path(
                value.get("local_path"),
                size_bytes=size_bytes,
                sha256=sha256,
            )
            validated.append({**descriptor, "local_path": str(local_path)})
        return validated

    @staticmethod
    def _has_symlink_component(path: Path) -> bool:
        for candidate in (path, *path.parents):
            try:
                if stat.S_ISLNK(os.lstat(candidate).st_mode):
                    return True
            except OSError:
                continue
        return False

    def _checked_artifact_path(
        self,
        value: Any,
        *,
        size_bytes: int,
        sha256: str,
    ) -> Path:
        raw = str(value or "").strip()
        candidate = Path(raw).expanduser()
        if not raw or not candidate.is_absolute():
            raise TransferOutboxError("artifact 路径必须是允许根内的绝对路径。")
        candidate = candidate.absolute()
        if self._has_symlink_component(candidate):
            raise TransferOutboxError("artifact 路径不能包含符号链接。")
        try:
            file_stat = os.lstat(candidate)
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise TransferOutboxError("artifact 文件不存在或不可访问。") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise TransferOutboxError("artifact 必须是普通文件。")
        if not any(resolved.is_relative_to(root) for root in self.allowed_artifact_roots):
            raise TransferOutboxError("artifact 文件不在允许根内。")
        if file_stat.st_size != int(size_bytes):
            raise TransferOutboxError("artifact 实际长度与 manifest 不一致。")
        try:
            actual_sha256 = sha256_file(resolved)
        except OSError as exc:
            raise TransferOutboxError("artifact 文件不可读取。") from exc
        if actual_sha256 != str(sha256):
            raise TransferOutboxError("artifact 实际 SHA-256 与 manifest 不一致。")
        return resolved

    def checked_artifact_path(self, artifact: Mapping[str, Any]) -> Path:
        """Revalidate a queued file immediately before a whole-file upload."""

        return self._checked_artifact_path(
            artifact.get("local_path"),
            size_bytes=int(artifact["size_bytes"]),
            sha256=str(artifact["sha256"]),
        )

    @staticmethod
    def _insert_artifact(
        connection: sqlite3.Connection,
        transfer_id: str,
        value: Mapping[str, Any],
        now: str,
    ) -> None:
        artifact_id = str(value.get("artifact_id") or "").strip()
        artifact_kind = str(value.get("artifact_kind") or "").strip()
        local_path = str(value.get("local_path") or "").strip()
        sha256 = str(value.get("sha256") or "").strip().lower()
        mime_type = str(value.get("mime_type") or "").strip()
        try:
            size_bytes = int(value.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise TransferOutboxError("artifact.size_bytes 无效。") from exc
        if not artifact_id or not local_path or not mime_type:
            raise TransferOutboxError("artifact_id 和 local_path 不能为空。")
        if artifact_kind not in ARTIFACT_KINDS:
            raise TransferOutboxError("artifact_kind 无效。")
        if not SHA256_PATTERN.fullmatch(sha256) or size_bytes <= 0:
            raise TransferOutboxError("artifact 的长度或 SHA-256 无效。")
        connection.execute(
            """
            INSERT INTO transfer_artifacts(
                transfer_id, artifact_id, artifact_kind, local_path,
                size_bytes, sha256, mime_type, status, uploaded_bytes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)
            """,
            (
                transfer_id,
                artifact_id,
                artifact_kind,
                local_path,
                size_bytes,
                sha256,
                mime_type,
                now,
            ),
        )

    def get(self, transfer_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (str(transfer_id),),
            ).fetchone()
            return self._serialize_row(row) if row else None

    def artifacts_for(self, transfer_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM transfer_artifacts
                WHERE transfer_id = ?
                ORDER BY artifact_kind, artifact_id
                """,
                (str(transfer_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        """Return aggregate queue state without exposing manifests or paths."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM transfer_outbox GROUP BY status"
            ).fetchall()
        counts = {state: 0 for state in sorted(OUTBOX_STATES)}
        for row in rows:
            counts[str(row["status"])] = int(row["total"] or 0)
        return counts

    def list_recent(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """List safe operational fields for the local management centre."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT transfer_id, creator_id, platform, source_work_id,
                       source_sequence, work_revision, status, attempt_count,
                       next_attempt_at, last_http_status, last_error_code,
                       created_at, updated_at, delivered_at
                FROM transfer_outbox
                ORDER BY source_sequence DESC
                LIMIT ?
                """,
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def expedite_retries(self, *, now: datetime | None = None) -> int:
        """Make retry-wait items immediately eligible without changing their state."""

        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        current = current_time.astimezone(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE transfer_outbox
                SET next_attempt_at = ?, updated_at = ?, state_version = state_version + 1
                WHERE status = 'retry_wait'
                  AND (lease_owner = '' OR lease_expires_at IS NULL OR lease_expires_at <= ?)
                """,
                (current, current, current),
            )
            return max(0, int(cursor.rowcount or 0))

    def list_ready(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        current = current_time.astimezone(UTC).isoformat()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM transfer_outbox
                WHERE (
                    status IN (
                        'pending', 'manifest_accepted', 'media_uploading', 'finalizing'
                    )
                    OR (status = 'retry_wait' AND next_attempt_at <= ?)
                )
                  AND (
                    lease_owner = '' OR lease_expires_at IS NULL
                    OR lease_expires_at <= ?
                  )
                ORDER BY source_sequence
                LIMIT ?
                """,
                (current, current, max(1, min(1000, int(limit)))),
            ).fetchall()
            return [self._serialize_row(row) for row in rows]

    def claim_ready(
        self,
        lease_owner: str,
        *,
        now: datetime | None = None,
        limit: int = 1,
        lease_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically lease ready rows, reclaiming only expired leases."""

        owner = str(lease_owner or "").strip()
        if not owner or len(owner) > 160:
            raise TransferOutboxError("lease_owner 无效。")
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        current = current_time.astimezone(UTC).isoformat()
        duration = self.lease_seconds if lease_seconds is None else max(30, int(lease_seconds))
        expires = _future_iso(duration, now=current_time)
        claimed_ids: list[str] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT transfer_id, state_version
                FROM transfer_outbox
                WHERE (
                    status IN (
                        'pending', 'manifest_accepted', 'media_uploading', 'finalizing'
                    )
                    OR (status = 'retry_wait' AND next_attempt_at <= ?)
                )
                  AND (
                    lease_owner = '' OR lease_expires_at IS NULL
                    OR lease_expires_at <= ?
                  )
                ORDER BY source_sequence
                LIMIT ?
                """,
                (current, current, max(1, min(1000, int(limit)))),
            ).fetchall()
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE transfer_outbox
                    SET lease_owner = ?, lease_expires_at = ?, updated_at = ?,
                        state_version = state_version + 1
                    WHERE transfer_id = ? AND state_version = ?
                      AND (
                        lease_owner = '' OR lease_expires_at IS NULL
                        OR lease_expires_at <= ?
                      )
                    """,
                    (
                        owner,
                        expires,
                        current,
                        str(row["transfer_id"]),
                        int(row["state_version"]),
                        current,
                    ),
                ).rowcount
                if updated == 1:
                    claimed_ids.append(str(row["transfer_id"]))
            connection.commit()
            return [
                self._serialize_row(
                    connection.execute(
                        "SELECT * FROM transfer_outbox WHERE transfer_id = ?", (transfer_id,)
                    ).fetchone()
                )
                for transfer_id in claimed_ids
            ]

    def renew_lease(
        self,
        transfer_id: str,
        lease_owner: str,
        *,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        owner = str(lease_owner or "").strip()
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        current = current_time.astimezone(UTC).isoformat()
        duration = self.lease_seconds if lease_seconds is None else max(30, int(lease_seconds))
        with closing(self._connect()) as connection, connection:
            updated = connection.execute(
                """
                UPDATE transfer_outbox
                SET lease_expires_at = ?, updated_at = ?, state_version = state_version + 1
                WHERE transfer_id = ? AND lease_owner = ?
                  AND status NOT IN ('delivered', 'dead_letter')
                """,
                (
                    _future_iso(duration, now=current_time),
                    current,
                    str(transfer_id),
                    owner,
                ),
            ).rowcount
        if updated != 1:
            raise TransferOutboxError("lease 已失效或不属于当前 worker。")
        result = self.get(transfer_id)
        assert result is not None
        return result

    def release_lease(self, transfer_id: str, lease_owner: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE transfer_outbox
                SET lease_owner = '', lease_expires_at = NULL,
                    state_version = state_version + 1
                WHERE transfer_id = ? AND lease_owner = ?
                """,
                (str(transfer_id), str(lease_owner)),
            )

    @staticmethod
    def _require_lease(row: sqlite3.Row, lease_owner: str | None) -> None:
        current_owner = str(row["lease_owner"] or "")
        if lease_owner is None:
            if current_owner:
                raise TransferOutboxError("outbox 正由其他 worker 处理。")
            return
        if current_owner != str(lease_owner):
            raise TransferOutboxError("lease 已失效或不属于当前 worker。")

    def transition(
        self,
        transfer_id: str,
        status: str,
        *,
        receipt_id: str | None = None,
        http_status: int | None = None,
        expected_status: str | None = None,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        if status not in OUTBOX_STATES or status == "retry_wait":
            raise TransferOutboxError("目标 outbox 状态无效。")
        now = _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (str(transfer_id),),
            ).fetchone()
            if not row:
                raise TransferOutboxError("transfer_id 不存在。")
            current = str(row["status"])
            self._require_lease(row, lease_owner)
            if expected_status is not None and current != expected_status:
                raise TransferOutboxError("outbox 状态 CAS 失败。")
            if current == status:
                connection.commit()
                return self._serialize_row(row)
            if current == "retry_wait":
                expected = str(row["resume_status"] or "pending")
                if status != expected:
                    raise TransferOutboxError("重试必须回到失败前状态。")
            elif status not in LEGAL_TRANSITIONS.get(current, set()):
                raise TransferOutboxError("outbox 状态迁移不合法。")
            if status in {"finalizing", "delivered"}:
                unverified = connection.execute(
                    """
                    SELECT COUNT(*) FROM transfer_artifacts
                    WHERE transfer_id = ? AND status != 'verified'
                    """,
                    (str(transfer_id),),
                ).fetchone()[0]
                if int(unverified):
                    raise TransferOutboxError("artifact 尚未全部核验。")
            delivered_at = now if status == "delivered" else row["delivered_at"]
            clear_lease = status in TERMINAL_STATES
            updated = connection.execute(
                """
                UPDATE transfer_outbox
                SET status = ?, resume_status = ?, receipt_id = ?,
                    last_http_status = ?, last_error_code = '',
                    last_error_message = '', updated_at = ?, delivered_at = ?,
                    lease_owner = ?, lease_expires_at = ?,
                    state_version = state_version + 1
                WHERE transfer_id = ? AND status = ? AND state_version = ?
                """,
                (
                    status,
                    status,
                    str(receipt_id if receipt_id is not None else row["receipt_id"]),
                    http_status if http_status is not None else row["last_http_status"],
                    now,
                    delivered_at,
                    "" if clear_lease else str(row["lease_owner"] or ""),
                    None if clear_lease else row["lease_expires_at"],
                    str(transfer_id),
                    current,
                    int(row["state_version"]),
                ),
            ).rowcount
            if updated != 1:
                raise TransferOutboxError("outbox 状态 CAS 失败。")
            connection.commit()
        result = self.get(transfer_id)
        assert result is not None
        return result

    def mark_retry(
        self,
        transfer_id: str,
        *,
        error_code: str,
        error_message: str = "",
        http_status: int | None = None,
        now: datetime | None = None,
        delay_seconds: int | None = None,
        jitter_unit: float | None = None,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (str(transfer_id),),
            ).fetchone()
            if not row:
                raise TransferOutboxError("transfer_id 不存在。")
            self._require_lease(row, lease_owner)
            if str(row["status"]) in TERMINAL_STATES:
                raise TransferOutboxError("终态传输不能进入自动重试。")
            attempts = int(row["attempt_count"]) + 1
            delay = (
                retry_delay_seconds(attempts, jitter_unit=jitter_unit)
                if delay_seconds is None
                else max(1, int(delay_seconds))
            )
            resume_status = (
                str(row["resume_status"])
                if str(row["status"]) == "retry_wait"
                else str(row["status"])
            )
            updated_at = current_time.astimezone(UTC).isoformat()
            updated = connection.execute(
                """
                UPDATE transfer_outbox
                SET status = 'retry_wait', resume_status = ?,
                    attempt_count = ?, next_attempt_at = ?,
                    last_http_status = ?, last_error_code = ?,
                    last_error_message = ?, updated_at = ?,
                    lease_owner = '', lease_expires_at = NULL,
                    state_version = state_version + 1
                WHERE transfer_id = ? AND status = ? AND state_version = ?
                """,
                (
                    resume_status,
                    attempts,
                    _future_iso(delay, now=current_time),
                    http_status,
                    str(error_code or "")[:128],
                    str(error_message or "")[:1000],
                    updated_at,
                    str(transfer_id),
                    str(row["status"]),
                    int(row["state_version"]),
                ),
            ).rowcount
            if updated != 1:
                raise TransferOutboxError("outbox 状态 CAS 失败。")
            connection.commit()
        result = self.get(transfer_id)
        assert result is not None
        return result

    def resume_due(
        self,
        transfer_id: str,
        *,
        now: datetime | None = None,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)
        current = current_time.astimezone(UTC).isoformat()
        row = self.get(transfer_id)
        if not row:
            raise TransferOutboxError("transfer_id 不存在。")
        if row["status"] != "retry_wait":
            return row
        if str(row["next_attempt_at"]) > current:
            raise TransferOutboxError("重试时间尚未到达。")
        return self.transition(
            transfer_id,
            str(row["resume_status"]),
            expected_status="retry_wait",
            lease_owner=lease_owner,
        )

    def mark_dead_letter(
        self,
        transfer_id: str,
        *,
        error_code: str,
        error_message: str = "",
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (str(transfer_id),),
            ).fetchone()
            if not row or str(row["status"]) in TERMINAL_STATES:
                raise TransferOutboxError("传输不存在或已进入终态。")
            self._require_lease(row, lease_owner)
            updated = connection.execute(
                """
                UPDATE transfer_outbox
                SET status = 'dead_letter', last_error_code = ?,
                    last_error_message = ?, updated_at = ?,
                    lease_owner = '', lease_expires_at = NULL,
                    state_version = state_version + 1
                WHERE transfer_id = ? AND status = ? AND state_version = ?
                """,
                (
                    str(error_code or "")[:128],
                    str(error_message or "")[:1000],
                    now,
                    str(transfer_id),
                    str(row["status"]),
                    int(row["state_version"]),
                ),
            ).rowcount
            if updated != 1:
                raise TransferOutboxError("outbox 状态 CAS 失败。")
            connection.commit()
        result = self.get(transfer_id)
        assert result is not None
        return result

    def mark_stale_delivered(
        self,
        transfer_id: str,
        *,
        receipt_id: str,
        http_status: int,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        """A strict stale manifest receipt needs no artifact upload."""

        now = _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (str(transfer_id),),
            ).fetchone()
            if not row or str(row["status"]) != "pending":
                raise TransferOutboxError("stale 回执只能完成 pending 传输。")
            self._require_lease(row, lease_owner)
            updated = connection.execute(
                """
                UPDATE transfer_outbox
                SET status = 'delivered', resume_status = 'delivered',
                    receipt_id = ?, last_http_status = ?, delivered_at = ?,
                    updated_at = ?, lease_owner = '', lease_expires_at = NULL,
                    state_version = state_version + 1
                WHERE transfer_id = ? AND status = 'pending' AND state_version = ?
                """,
                (
                    str(receipt_id),
                    int(http_status),
                    now,
                    now,
                    str(transfer_id),
                    int(row["state_version"]),
                ),
            ).rowcount
            if updated != 1:
                raise TransferOutboxError("outbox 状态 CAS 失败。")
            connection.commit()
        result = self.get(transfer_id)
        assert result is not None
        return result

    def update_artifact_progress(
        self,
        transfer_id: str,
        artifact_id: str,
        *,
        status: str,
        uploaded_bytes: int,
        lease_owner: str | None = None,
    ) -> dict[str, Any]:
        if status not in ARTIFACT_STATES:
            raise TransferOutboxError("artifact 状态无效。")
        amount = int(uploaded_bytes)
        if amount < 0:
            raise TransferOutboxError("uploaded_bytes 不能为负数。")
        now = _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            transfer = connection.execute(
                "SELECT * FROM transfer_outbox WHERE transfer_id = ?",
                (str(transfer_id),),
            ).fetchone()
            if not transfer or str(transfer["status"]) in TERMINAL_STATES:
                raise TransferOutboxError("artifact 所属传输不存在或已进入终态。")
            self._require_lease(transfer, lease_owner)
            row = connection.execute(
                """
                SELECT size_bytes, status, uploaded_bytes FROM transfer_artifacts
                WHERE transfer_id = ? AND artifact_id = ?
                """,
                (str(transfer_id), str(artifact_id)),
            ).fetchone()
            if not row:
                raise TransferOutboxError("artifact 不存在。")
            if amount > int(row["size_bytes"]):
                raise TransferOutboxError("uploaded_bytes 超过 artifact 长度。")
            current_status = str(row["status"])
            current_amount = int(row["uploaded_bytes"])
            if ARTIFACT_STATE_RANK[status] < ARTIFACT_STATE_RANK[current_status]:
                raise TransferOutboxError("artifact 状态不能回退。")
            if amount < current_amount:
                raise TransferOutboxError("artifact 上传进度不能回退。")
            if status == "pending" and amount != 0:
                raise TransferOutboxError("pending artifact 的进度必须为零。")
            if status in {"uploaded", "verified"} and amount != int(row["size_bytes"]):
                raise TransferOutboxError("完成态 artifact 必须记录完整文件长度。")
            updated = connection.execute(
                """
                UPDATE transfer_artifacts
                SET status = ?, uploaded_bytes = ?, updated_at = ?
                WHERE transfer_id = ? AND artifact_id = ?
                  AND status = ? AND uploaded_bytes = ?
                """,
                (
                    status,
                    amount,
                    now,
                    str(transfer_id),
                    str(artifact_id),
                    current_status,
                    current_amount,
                ),
            ).rowcount
            if updated != 1:
                raise TransferOutboxError("artifact 状态 CAS 失败。")
            connection.commit()
        return next(
            item
            for item in self.artifacts_for(transfer_id)
            if item["artifact_id"] == artifact_id
        )

    @staticmethod
    def _serialize_row(
        row: sqlite3.Row | None,
        *,
        action: str | None = None,
    ) -> dict[str, Any]:
        if row is None:
            raise TransferOutboxError("outbox 记录不存在。")
        item = dict(row)
        raw_manifest = item.pop("manifest_json")
        if isinstance(raw_manifest, bytes):
            raw_manifest = raw_manifest.decode("utf-8")
        item["manifest"] = json.loads(str(raw_manifest))
        if action:
            item["action"] = action
        return item
