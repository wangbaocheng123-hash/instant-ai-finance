from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RESOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,95}")
ASSET_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,64}")
MAX_GALLERY_ASSETS = 40
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _image_type(path: Path) -> tuple[str, str]:
    with path.open("rb") as source:
        header = source.read(16)
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValueError("unsupported_image_type")


class SizeLibraryStore:
    """Community-owned metadata and image storage for the size library."""

    def __init__(self, database_path: Path, knowledge_root: Path, seed_path: Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.knowledge_root = Path(knowledge_root).expanduser().resolve()
        self.seed_path = Path(seed_path).resolve()
        self._write_lock = threading.Lock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        (self.knowledge_root / "originals" / "images").mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._seed_external_resources()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_schema_versions (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY,
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    primary_topic TEXT NOT NULL DEFAULT 'unclassified',
                    summary TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    review_status TEXT NOT NULL CHECK(review_status IN ('draft', 'published', 'withdrawn')),
                    version INTEGER NOT NULL DEFAULT 1,
                    display_view TEXT NOT NULL DEFAULT '',
                    display_sequence INTEGER,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS knowledge_items_view_sequence
                ON knowledge_items(display_view, display_sequence)
                WHERE display_sequence IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
                    source_type TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    authorization_scope TEXT NOT NULL,
                    verified_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_measurements (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
                    part_name TEXT NOT NULL,
                    value_min REAL,
                    value_max REAL,
                    unit TEXT NOT NULL,
                    conditions TEXT NOT NULL DEFAULT '',
                    source_id TEXT REFERENCES knowledge_sources(id),
                    verification_status TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_assets (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
                    asset_kind TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    storage_key TEXT NOT NULL UNIQUE,
                    content_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    public_level TEXT NOT NULL CHECK(public_level IN ('private', 'public')),
                    version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(item_id, position)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_relations (
                    id TEXT PRIMARY KEY,
                    source_item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
                    target_item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
                    relation_type TEXT NOT NULL,
                    UNIQUE(source_item_id, target_item_id, relation_type)
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_schema_versions (version, applied_at)
                VALUES (1, ?)
                """,
                (datetime.now(UTC).isoformat(),),
            )

    def _seed_external_resources(self) -> None:
        try:
            payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        resources = payload.get("resources") if isinstance(payload, dict) else None
        if not isinstance(resources, list):
            return
        with self._write_lock, closing(self._connect()) as connection, connection:
            for item in resources:
                if not isinstance(item, dict) or item.get("type", "external") != "external":
                    continue
                try:
                    resource_id = str(item["id"]).strip()
                    sequence = int(item["sequence"])
                    title = str(item["title"]).strip()
                    source = str(item["source"]).strip()
                    summary = str(item["summary"]).strip()
                    source_url = str(item["url"]).strip()
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    not RESOURCE_ID_PATTERN.fullmatch(resource_id)
                    or sequence < 1
                    or not all((title, source, summary, source_url))
                ):
                    continue
                connection.execute(
                    """
                    INSERT INTO knowledge_items (
                        id, item_type, title, aliases_json, primary_topic,
                        summary, body, review_status, version, display_view,
                        display_sequence, updated_at
                    ) VALUES (?, 'article', ?, '[]', 'furniture_object', ?, '',
                              'published', 1, 'sizes', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        item_type = 'article',
                        title = excluded.title,
                        summary = excluded.summary,
                        review_status = 'published',
                        display_view = 'sizes',
                        display_sequence = excluded.display_sequence,
                        updated_at = excluded.updated_at
                    """,
                    (resource_id, title, summary, sequence, datetime.now(UTC).isoformat()),
                )
                connection.execute(
                    """
                    INSERT INTO knowledge_sources (
                        id, item_id, source_type, source_url, author,
                        authorization_scope, verified_at
                    ) VALUES (?, ?, ?, ?, '', 'link_only', '')
                    ON CONFLICT(id) DO UPDATE SET
                        source_type = excluded.source_type,
                        source_url = excluded.source_url,
                        authorization_scope = 'link_only'
                    """,
                    (f"source-{resource_id}", resource_id, source, source_url),
                )

    def list_resources(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            resources = connection.execute(
                """
                SELECT i.id, i.display_sequence, i.title, i.summary, i.item_type, i.body,
                       s.source_type, s.source_url
                FROM knowledge_items AS i
                JOIN knowledge_sources AS s ON s.id = (
                    SELECT first_source.id
                    FROM knowledge_sources AS first_source
                    WHERE first_source.item_id = i.id
                    ORDER BY first_source.id ASC
                    LIMIT 1
                )
                WHERE i.review_status = 'published' AND i.display_view = 'sizes'
                ORDER BY i.display_sequence ASC, i.id ASC
                """
            ).fetchall()
            assets = connection.execute(
                """
                SELECT a.id, a.item_id, a.position
                FROM knowledge_assets AS a
                JOIN knowledge_items AS i ON i.id = a.item_id
                WHERE a.public_level = 'public' AND i.review_status = 'published'
                ORDER BY a.item_id ASC, a.position ASC
                """
            ).fetchall()
        assets_by_resource: dict[str, list[dict[str, Any]]] = {}
        for asset in assets:
            assets_by_resource.setdefault(str(asset["item_id"]), []).append(
                {
                    "id": str(asset["id"]),
                    "position": int(asset["position"]),
                    "url": f"/api/size-assets/{asset['id']}",
                }
            )
        result = []
        for row in resources:
            resource = {
                "id": str(row["id"]),
                "sequence": int(row["display_sequence"]),
                "title": str(row["title"]),
                "source": str(row["source_type"]),
                "summary": str(row["summary"]),
            }
            item_assets = assets_by_resource.get(resource["id"], [])
            if row["item_type"] == "local_article":
                from .articles import public_article, trusted_article_url

                blocks = public_article(str(row["body"]), item_assets)
                if blocks is None or not trusted_article_url(str(row["source_url"])):
                    continue
                resource["type"] = "article"
                resource["source_url"] = str(row["source_url"])
                resource["blocks"] = blocks
                resource["image_count"] = len(item_assets)
                resource["video_count"] = sum(block["type"] == "video" for block in blocks)
            elif not item_assets:
                resource["type"] = "external"
                resource["url"] = str(row["source_url"])
            else:
                resource["type"] = "gallery"
                resource["assets"] = item_assets
            result.append(resource)
        return result

    def get_asset(self, asset_id: str) -> tuple[Path, str] | None:
        if not ASSET_ID_PATTERN.fullmatch(asset_id):
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT a.storage_key, a.content_type
                FROM knowledge_assets AS a
                JOIN knowledge_items AS i ON i.id = a.item_id
                WHERE a.id = ? AND a.public_level = 'public'
                      AND i.review_status = 'published'
                """,
                (asset_id,),
            ).fetchone()
        if row is None:
            return None
        path = (self.knowledge_root / str(row["storage_key"])).resolve()
        if self.knowledge_root not in path.parents or not path.is_file():
            return None
        return path, str(row["content_type"])

    def import_gallery(
        self,
        metadata: Mapping[str, Any],
        image_paths: Iterable[Path],
    ) -> dict[str, Any]:
        resource_id = str(metadata.get("id") or "").strip()
        title = str(metadata.get("title") or "").strip()
        source = str(metadata.get("source") or "").strip()
        summary = str(metadata.get("summary") or "").strip()
        source_url = str(metadata.get("source_url") or "").strip()
        try:
            sequence = int(metadata.get("sequence"))
        except (TypeError, ValueError):
            raise ValueError("invalid_gallery_metadata") from None
        paths = [Path(path).expanduser().resolve() for path in image_paths]
        if (
            not RESOURCE_ID_PATTERN.fullmatch(resource_id)
            or sequence < 1
            or not all((title, source, summary, source_url))
            or not 1 <= len(paths) <= MAX_GALLERY_ASSETS
        ):
            raise ValueError("invalid_gallery_metadata")

        staged: list[tuple[str, int, str, str, int, str]] = []
        staged_directories: list[Path] = []
        try:
            for position, source_path in enumerate(paths, start=1):
                if not source_path.is_file():
                    raise ValueError("missing_gallery_image")
                byte_size = source_path.stat().st_size
                if not 1 <= byte_size <= MAX_IMAGE_BYTES:
                    raise ValueError("invalid_gallery_image_size")
                content_type, extension = _image_type(source_path)
                asset_id = secrets.token_urlsafe(18)
                target_directory = self.knowledge_root / "originals" / "images" / asset_id
                target_directory.mkdir(parents=True, exist_ok=False)
                staged_directories.append(target_directory)
                destination = target_directory / f"original{extension}"
                digest = hashlib.sha256()
                with source_path.open("rb") as source_file, destination.open("xb") as target_file:
                    while chunk := source_file.read(1024 * 1024):
                        digest.update(chunk)
                        target_file.write(chunk)
                relative_name = destination.relative_to(self.knowledge_root).as_posix()
                staged.append(
                    (asset_id, position, relative_name, content_type, byte_size, digest.hexdigest())
                )
        except Exception:
            for directory in reversed(staged_directories):
                for staged_path in directory.iterdir():
                    staged_path.unlink(missing_ok=True)
                directory.rmdir()
            raise

        try:
            with self._write_lock, closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO knowledge_items (
                        id, item_type, title, aliases_json, primary_topic,
                        summary, body, review_status, version, display_view,
                        display_sequence, updated_at
                    ) VALUES (?, 'image_post', ?, '[]', 'unclassified', ?, '',
                              'published', 1, 'sizes', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        item_type = 'image_post',
                        title = excluded.title,
                        summary = excluded.summary,
                        review_status = 'published',
                        version = knowledge_items.version + 1,
                        display_view = 'sizes',
                        display_sequence = excluded.display_sequence,
                        updated_at = excluded.updated_at
                    """,
                    (resource_id, title, summary, sequence, datetime.now(UTC).isoformat()),
                )
                connection.execute(
                    """
                    INSERT INTO knowledge_sources (
                        id, item_id, source_type, source_url, author,
                        authorization_scope, verified_at
                    ) VALUES (?, ?, ?, ?, '', 'owner_authorized_copy', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_type = excluded.source_type,
                        source_url = excluded.source_url,
                        authorization_scope = 'owner_authorized_copy',
                        verified_at = excluded.verified_at
                    """,
                    (
                        f"source-{resource_id}",
                        resource_id,
                        source,
                        source_url,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                previous = connection.execute(
                    "SELECT storage_key FROM knowledge_assets WHERE item_id = ?",
                    (resource_id,),
                ).fetchall()
                connection.execute(
                    "DELETE FROM knowledge_assets WHERE item_id = ?", (resource_id,)
                )
                connection.executemany(
                    """
                    INSERT INTO knowledge_assets (
                        id, item_id, asset_kind, position, storage_key,
                        content_type, byte_size, sha256, public_level, version
                    ) VALUES (?, ?, 'image', ?, ?, ?, ?, ?, 'public', 1)
                    """,
                    [
                        (asset_id, resource_id, position, storage_name, content_type, byte_size, sha256)
                        for asset_id, position, storage_name, content_type, byte_size, sha256 in staged
                    ],
                )
        except Exception:
            for directory in reversed(staged_directories):
                for staged_path in directory.iterdir():
                    staged_path.unlink(missing_ok=True)
                directory.rmdir()
            raise
        for row in previous:
            previous_path = (self.knowledge_root / str(row["storage_key"])).resolve()
            if self.knowledge_root in previous_path.parents:
                try:
                    previous_path.unlink(missing_ok=True)
                    previous_path.parent.rmdir()
                except OSError:
                    # A stale file is safer than removing a newly committed asset.
                    pass
        return next(item for item in self.list_resources() if item["id"] == resource_id)
