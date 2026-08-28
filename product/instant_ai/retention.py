from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .database import connect, transaction, utc_now
from .paths import BACKUPS_ROOT, CACHE_ROOT, DATABASE_PATH, EVIDENCE_ROOT, RAW_ROOT


ORDINARY_TTL = timedelta(hours=72)
IMPORTANT_TTL = timedelta(days=5)
CRITICAL_TTL = timedelta(days=7)
RUN_TTL = timedelta(days=7)
MAX_EVIDENCE_PER_ITEM = 5
MAX_BACKUPS = 3


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=current.tzinfo or UTC).astimezone(UTC)


def item_expiry_cutoffs(now: datetime | None = None) -> dict[str, str]:
    current = _now(now)
    return {
        "ordinary": _iso(current - ORDINARY_TTL),
        "important": _iso(current - IMPORTANT_TTL),
        "critical": _iso(current - CRITICAL_TTL),
        "runs": _iso(current - RUN_TTL),
    }


def published_within_hard_limit(published_at: str | None, now: datetime | None = None) -> bool:
    if not published_at:
        return True
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
    except ValueError:
        return True
    return published.astimezone(UTC) >= _now(now) - CRITICAL_TTL


def _expired_where() -> str:
    anchor = "COALESCE(published_at, first_seen_at)"
    return (
        f"({anchor} < :critical OR "
        f"(importance_score < 85 AND {anchor} < :important) OR "
        f"(importance_score < 70 AND {anchor} < :ordinary))"
    )


def _safe_files(root: Path, values: list[str]) -> list[Path]:
    resolved_root = root.resolve()
    result: list[Path] = []
    for value in values:
        if not value:
            continue
        path = Path(value).resolve()
        if path != resolved_root and resolved_root in path.parents:
            result.append(path)
    return result


def _remove_files(paths: list[Path]) -> int:
    removed = 0
    for path in dict.fromkeys(paths):
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def prune_backups(*, root: Path = BACKUPS_ROOT, keep: int = MAX_BACKUPS) -> int:
    backups = sorted(root.glob("instant_ai-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    removed = 0
    for backup in backups[max(1, keep):]:
        for target in (backup, backup.with_suffix(".db.sha256")):
            try:
                if target.is_file():
                    target.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def retention_preview(
    *, path: Path | str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    cutoffs = item_expiry_cutoffs(now)
    with connect(path) as connection:
        expired_items = int(
            connection.execute(
                f"SELECT COUNT(*) FROM items WHERE {_expired_where()}", cutoffs
            ).fetchone()[0]
        )
        expired_links = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM item_evidence ie
                JOIN items i ON i.id=ie.item_id
                WHERE {_expired_where()}
                """,
                cutoffs,
            ).fetchone()[0]
        )
        old_runs = int(
            connection.execute(
                "SELECT COUNT(*) FROM collection_runs WHERE started_at < ?", (cutoffs["runs"],)
            ).fetchone()[0]
        )
        totals = {
            "items": int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]),
            "evidence": int(connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]),
            "runs": int(connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0]),
        }
    return {
        "mode": "preview",
        "policy": {
            "ordinary_hours": int(ORDINARY_TTL.total_seconds() // 3600),
            "important_days": IMPORTANT_TTL.days,
            "critical_days": CRITICAL_TTL.days,
            "max_evidence_per_item": MAX_EVIDENCE_PER_ITEM,
            "max_backups": MAX_BACKUPS,
        },
        "cutoffs": cutoffs,
        "totals": totals,
        "would_remove": {
            "items": expired_items,
            "item_evidence_links_at_least": expired_links,
            "runs": old_runs,
        },
    }


def run_retention_cleanup(
    *,
    path: Path | str | None = None,
    raw_root: Path = RAW_ROOT,
    cache_root: Path = CACHE_ROOT,
    evidence_root: Path = EVIDENCE_ROOT,
    backups_root: Path = BACKUPS_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    cutoffs = item_expiry_cutoffs(now)
    database_path = Path(path or DATABASE_PATH)
    thumbnail_root = cache_root / "thumbnails"
    expired_thumbnail_paths: list[str] = []

    with transaction(database_path) as connection:
        expired_thumbnail_paths = [
            str(row[0] or "")
            for row in connection.execute(
                f"""
                SELECT t.local_path
                FROM item_thumbnails t
                JOIN items i ON i.id=t.item_id
                WHERE {_expired_where()} AND t.local_path IS NOT NULL
                """,
                cutoffs,
            ).fetchall()
        ]
        expired_items = connection.execute(
            f"DELETE FROM items WHERE {_expired_where()}", cutoffs
        ).rowcount

        connection.execute(
            "CREATE TEMP TABLE retention_keep_links ("
            "item_id INTEGER NOT NULL, evidence_id TEXT NOT NULL, "
            "PRIMARY KEY(item_id, evidence_id)) WITHOUT ROWID"
        )
        connection.execute(
            """
            WITH per_source AS (
                SELECT ie.item_id, ie.evidence_id, e.source_id, e.fetched_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY ie.item_id, e.source_id
                           ORDER BY e.fetched_at DESC, e.id DESC
                       ) AS source_rank
                FROM item_evidence ie
                JOIN evidence e ON e.id=ie.evidence_id
            ), ranked AS (
                SELECT item_id, evidence_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY item_id
                           ORDER BY fetched_at DESC, evidence_id DESC
                       ) AS evidence_rank
                FROM per_source
                WHERE source_rank=1
            )
            INSERT INTO retention_keep_links(item_id, evidence_id)
            SELECT item_id, evidence_id
            FROM ranked
            WHERE evidence_rank <= ?
            """,
            (MAX_EVIDENCE_PER_ITEM,),
        )
        connection.execute(
            """
            DELETE FROM item_evidence
            WHERE NOT EXISTS (
                SELECT 1 FROM retention_keep_links keep
                WHERE keep.item_id=item_evidence.item_id
                  AND keep.evidence_id=item_evidence.evidence_id
            )
            """
        )
        orphan_evidence = connection.execute(
            "DELETE FROM evidence WHERE NOT EXISTS "
            "(SELECT 1 FROM item_evidence ie WHERE ie.evidence_id=evidence.id)"
        ).rowcount
        connection.execute(
            """
            UPDATE items
            SET source_count=MAX(1, COALESCE((
                SELECT COUNT(DISTINCT e.source_id)
                FROM item_evidence ie
                JOIN evidence e ON e.id=ie.evidence_id
                WHERE ie.item_id=items.id
            ), 0))
            """
        )
        old_runs = connection.execute(
            "DELETE FROM collection_runs WHERE started_at < ?", (cutoffs["runs"],)
        ).rowcount
        connection.execute(
            "DELETE FROM translation_usage WHERE usage_date < ?",
            ((_now(now) - CRITICAL_TTL).date().isoformat(),),
        )

        referenced_raw = {
            str(Path(row[0]).resolve())
            for row in connection.execute("SELECT DISTINCT raw_path FROM evidence").fetchall()
            if row[0]
        }
        referenced_thumbnails = {
            str(Path(row[0]).resolve())
            for row in connection.execute(
                "SELECT DISTINCT local_path FROM item_thumbnails WHERE local_path IS NOT NULL"
            ).fetchall()
            if row[0]
        }
        retained_run_ids = {
            int(row[0]) for row in connection.execute("SELECT id FROM collection_runs").fetchall()
        }

    raw_files = [
        item for item in raw_root.rglob("*")
        if item.is_file() and str(item.resolve()) not in referenced_raw
    ] if raw_root.is_dir() else []
    thumbnail_files = [
        item for item in thumbnail_root.glob("*")
        if item.is_file() and str(item.resolve()) not in referenced_thumbnails
    ] if thumbnail_root.is_dir() else []
    run_files = [
        item for item in (evidence_root / "runs").glob("run-*.json")
        if item.is_file() and _run_id(item) not in retained_run_ids
    ] if (evidence_root / "runs").is_dir() else []

    removed_files = _remove_files(_safe_files(thumbnail_root, expired_thumbnail_paths))
    removed_files += _remove_files(raw_files + thumbnail_files + run_files)
    removed_backups = prune_backups(root=backups_root)

    try:
        with connect(database_path) as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.Error:
        pass

    return {
        "mode": "applied",
        "finished_at": utc_now(),
        "removed": {
            "items": max(0, expired_items),
            "orphan_evidence": max(0, orphan_evidence),
            "runs": max(0, old_runs),
            "files": removed_files,
            "backup_files": removed_backups,
        },
        "policy": retention_preview(path=database_path, now=now)["policy"],
    }


def _run_id(path: Path) -> int:
    try:
        return int(path.stem.removeprefix("run-"))
    except ValueError:
        return -1
