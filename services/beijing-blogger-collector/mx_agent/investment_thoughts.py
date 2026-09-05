from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .investment_taxonomy import TAXONOMY, classify_text, taxonomy_counts


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone() is not None


def _ensure_hierarchy_columns(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(investment_thought_categories)")}
    if "parent_id" not in columns:
        conn.execute("ALTER TABLE investment_thought_categories ADD COLUMN parent_id INTEGER")
    if "level" not in columns:
        conn.execute(
            "ALTER TABLE investment_thought_categories ADD COLUMN level INTEGER NOT NULL DEFAULT 1"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_investment_thought_categories_parent ON investment_thought_categories(parent_id, position, id)"
    )


def _install_taxonomy(conn: sqlite3.Connection) -> None:
    marker = conn.execute(
        "SELECT value FROM investment_thought_meta WHERE key = 'hierarchical_taxonomy_v2'"
    ).fetchone()
    if marker is not None:
        return

    now = _now_iso()
    conn.execute("DELETE FROM investment_thought_video_links")
    if _table_exists(conn, "investment_thought_sources"):
        conn.execute("DELETE FROM investment_thought_sources")
    if _table_exists(conn, "investment_thoughts"):
        conn.execute("DELETE FROM investment_thoughts")
    conn.execute("DELETE FROM investment_thought_categories")

    category_ids: dict[str, int] = {}
    for parent_position, (parent_slug, parent_name, parent_description, children) in enumerate(
        TAXONOMY, start=1
    ):
        cursor = conn.execute(
            """
            INSERT INTO investment_thought_categories(
                slug, name, description, position, created_at, updated_at, parent_id, level
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 1)
            """,
            (parent_slug, parent_name, parent_description, parent_position, now, now),
        )
        parent_id = int(cursor.lastrowid)
        for child_position, (slug, name, description, _) in enumerate(children, start=1):
            child_cursor = conn.execute(
                """
                INSERT INTO investment_thought_categories(
                    slug, name, description, position, created_at, updated_at, parent_id, level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 2)
                """,
                (slug, name, description, child_position, now, now, parent_id),
            )
            category_ids[slug] = int(child_cursor.lastrowid)

    video_rows = conn.execute(
        """
        SELECT v.id,
               COALESCE(NULLIF(vt.active_title, ''), NULLIF(v.title, ''), '') AS title,
               COALESCE(co.original_text, '') AS original_text,
               COALESCE((
                   SELECT t.text FROM transcripts t
                   WHERE t.video_id = v.id AND TRIM(COALESCE(t.text, '')) <> ''
                   ORDER BY LENGTH(t.text) DESC, t.id DESC LIMIT 1
               ), '') AS transcript_text,
               COALESCE((
                   SELECT GROUP_CONCAT(ck.keyword, ' ')
                   FROM content_keywords ck WHERE ck.content_id = v.id
               ), '') AS keywords
        FROM videos v
        LEFT JOIN video_titles vt ON vt.video_id = v.id
        LEFT JOIN content_originals co ON co.content_id = v.id
        WHERE v.author = ?
        ORDER BY v.id
        """,
        ("模型先生",),
    ).fetchall()
    category_positions: dict[int, int] = {}
    analyzed = 0
    linked_videos: set[int] = set()
    links = 0
    for row in video_rows:
        source_text = str(row["original_text"] or "").strip() or str(
            row["transcript_text"] or ""
        ).strip()
        if not source_text:
            continue
        analyzed += 1
        blob = "\n".join((str(row["title"] or ""), source_text, str(row["keywords"] or "")))
        for slug in classify_text(blob):
            category_id = category_ids[slug]
            position = category_positions.get(category_id, 0) + 1
            category_positions[category_id] = position
            conn.execute(
                """
                INSERT OR IGNORE INTO investment_thought_video_links(
                    category_id, video_id, position, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (category_id, int(row["id"]), position, now),
            )
            linked_videos.add(int(row["id"]))
            links += 1

    parent_count, child_count = taxonomy_counts()
    value = json.dumps(
        {
            "installed_at": now,
            "parent_categories": parent_count,
            "child_categories": child_count,
            "analyzed_videos": analyzed,
            "linked_videos": len(linked_videos),
            "links": links,
        },
        ensure_ascii=False,
    )
    conn.execute(
        "INSERT INTO investment_thought_meta(key, value) VALUES ('hierarchical_taxonomy_v2', ?)",
        (value,),
    )


def ensure_investment_thought_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investment_thought_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investment_thought_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investment_thought_video_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES investment_thought_categories(id) ON DELETE CASCADE,
            video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(category_id, video_id)
        );
        CREATE INDEX IF NOT EXISTS idx_investment_thought_categories_position
            ON investment_thought_categories(position, id);
        CREATE INDEX IF NOT EXISTS idx_investment_thought_video_links_category
            ON investment_thought_video_links(category_id, position, id);
        CREATE INDEX IF NOT EXISTS idx_investment_thought_video_links_video
            ON investment_thought_video_links(video_id, category_id);
        """
    )
    _ensure_hierarchy_columns(conn)
    _install_taxonomy(conn)


class InvestmentThoughtService:
    def __init__(self, storage: Any):
        self.storage = storage

    @staticmethod
    def _category_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "position": int(row["position"]),
            "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
            "parent_name": row["parent_name"] if "parent_name" in row.keys() else None,
            "level": int(row["level"]),
            "video_count": int(row["video_count"]) if "video_count" in row.keys() else 0,
            "child_count": int(row["child_count"]) if "child_count" in row.keys() else 0,
        }

    @staticmethod
    def _category_exists(conn: sqlite3.Connection, category_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM investment_thought_categories WHERE id = ?", (category_id,)
        ).fetchone()
        if row is None:
            raise ValueError("请选择有效的投资思路分类。")
        return row

    @staticmethod
    def _video_exists(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None:
            raise ValueError("作品不存在。")
        return row

    @staticmethod
    def _slug(conn: sqlite3.Connection, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "category"
        candidate, suffix = base, 2
        while conn.execute(
            "SELECT 1 FROM investment_thought_categories WHERE slug = ?", (candidate,)
        ).fetchone():
            candidate, suffix = f"{base}-{suffix}", suffix + 1
        return candidate

    @staticmethod
    def _next_category_position(conn: sqlite3.Connection, parent_id: int | None) -> int:
        if parent_id is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS position FROM investment_thought_categories WHERE parent_id IS NULL"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS position FROM investment_thought_categories WHERE parent_id = ?",
                (parent_id,),
            ).fetchone()
        return int(row["position"])

    @staticmethod
    def _next_link_position(conn: sqlite3.Connection, category_id: int) -> int:
        return int(
            conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS position FROM investment_thought_video_links WHERE category_id = ?",
                (category_id,),
            ).fetchone()["position"]
        )

    @staticmethod
    def _category_rows(conn: sqlite3.Connection, account: str = "") -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT c.*, p.name AS parent_name,
                   CASE WHEN c.level = 1 THEN (
                       SELECT COUNT(DISTINCT l.video_id)
                       FROM investment_thought_categories child
                       JOIN investment_thought_video_links l ON l.category_id = child.id
                       JOIN videos linked_video ON linked_video.id = l.video_id
                       WHERE child.parent_id = c.id
                         AND (? = '' OR linked_video.author = ?)
                   ) ELSE (
                       SELECT COUNT(*)
                       FROM investment_thought_video_links l
                       JOIN videos linked_video ON linked_video.id = l.video_id
                       WHERE l.category_id = c.id
                         AND (? = '' OR linked_video.author = ?)
                   ) END AS video_count,
                   (SELECT COUNT(*) FROM investment_thought_categories child WHERE child.parent_id = c.id) AS child_count
            FROM investment_thought_categories c
            LEFT JOIN investment_thought_categories p ON p.id = c.parent_id
            ORDER BY COALESCE(p.position, c.position), c.level, c.position, c.id
            """,
            (account, account, account, account),
        ).fetchall()

    @staticmethod
    def _video_index_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        video_id = int(row["video_id"])
        categories = conn.execute(
            """
            SELECT c.id, c.slug, c.name, c.parent_id, p.name AS parent_name, c.level
            FROM investment_thought_video_links l
            JOIN investment_thought_categories c ON c.id = l.category_id
            LEFT JOIN investment_thought_categories p ON p.id = c.parent_id
            WHERE l.video_id = ? ORDER BY p.position, c.position, c.id
            """,
            (video_id,),
        ).fetchall()
        keywords = (
            conn.execute(
                "SELECT keyword FROM content_keywords WHERE content_id = ? ORDER BY ordinal, id LIMIT 20",
                (video_id,),
            ).fetchall()
            if _table_exists(conn, "content_keywords")
            else []
        )
        primary_asset = conn.execute(
            """
            SELECT id, asset_type, original_name, mime_type, size_bytes
            FROM video_assets
            WHERE video_id = ?
            ORDER BY
                CASE
                    WHEN mime_type LIKE 'video/%' THEN 0
                    WHEN mime_type LIKE 'image/%' THEN 1
                    ELSE 2
                END,
                created_at DESC,
                id DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        asset = dict(primary_asset) if primary_asset is not None else None
        if asset is not None:
            asset["file_url"] = f"/api/assets/{int(asset['id'])}/file"
        return {
            "id": video_id,
            "video_id": video_id,
            "record_id": f"video:{video_id}",
            "title": row["title"] or f"作品 {video_id}",
            "published_at": row["published_at"],
            "author": row["author"],
            "source_url": row["source_url"],
            "linked_at": row["linked_at"],
            "categories": [dict(item) for item in categories],
            "category_ids": [int(item["id"]) for item in categories],
            "keywords": [str(item["keyword"]) for item in keywords],
            "primary_asset": asset,
        }

    def list_library(
        self,
        *,
        category_id: int | None = None,
        query: str = "",
        account: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        query = _clean(query)
        account = _clean(account)
        with self.storage.connect() as conn:
            predicates: list[str] = []
            params: list[Any] = []
            if account:
                predicates.append("v.author = ?")
                params.append(account)
            selected = self._category_exists(conn, int(category_id)) if category_id else None
            if selected is not None:
                if int(selected["level"]) == 1:
                    predicates.append("leaf.parent_id = ?")
                else:
                    predicates.append("l.category_id = ?")
                params.append(int(selected["id"]))
            if query:
                escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                predicates.append(
                    """(
                        COALESCE(NULLIF(vt.active_title, ''), v.title, '') LIKE ? ESCAPE '\\'
                        OR COALESCE(co.original_text, '') LIKE ? ESCAPE '\\'
                        OR EXISTS (SELECT 1 FROM content_keywords ck WHERE ck.content_id = v.id AND ck.keyword LIKE ? ESCAPE '\\')
                        OR leaf.name LIKE ? ESCAPE '\\' OR parent.name LIKE ? ESCAPE '\\'
                    )"""
                )
                params.extend([pattern] * 5)
            where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
            rows = conn.execute(
                f"""
                SELECT v.id AS video_id,
                       COALESCE(NULLIF(vt.active_title, ''), NULLIF(v.title, '')) AS title,
                       COALESCE(v.published_at, v.discovered_at) AS published_at,
                       v.author, v.url AS source_url, MAX(l.created_at) AS linked_at,
                       MIN(l.position) AS link_position
                FROM investment_thought_video_links l
                JOIN investment_thought_categories leaf ON leaf.id = l.category_id
                LEFT JOIN investment_thought_categories parent ON parent.id = leaf.parent_id
                JOIN videos v ON v.id = l.video_id
                LEFT JOIN video_titles vt ON vt.video_id = v.id
                LEFT JOIN content_originals co ON co.content_id = v.id
                {where}
                GROUP BY v.id
                ORDER BY {"link_position, v.id" if category_id else "linked_at DESC, v.id DESC"}
                LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()
            links_by_video: dict[str, list[int]] = {}
            links_query = """
                SELECT l.video_id, l.category_id
                FROM investment_thought_video_links l
                JOIN videos linked_video ON linked_video.id = l.video_id
                WHERE (? = '' OR linked_video.author = ?)
                ORDER BY l.video_id, l.category_id
            """
            for link in conn.execute(links_query, (account, account)).fetchall():
                links_by_video.setdefault(str(link["video_id"]), []).append(int(link["category_id"]))
            return {
                "categories": [self._category_dict(row) for row in self._category_rows(conn, account)],
                "items": [self._video_index_dict(conn, row) for row in rows],
                "links_by_video": links_by_video,
                "count": len(rows),
                "query": query,
                "account": account,
                "category_id": category_id,
                "purpose": "两级投资思路分类索引；只保存视频关联，不复制视频内容。",
            }

    def create_category(
        self, *, name: str, description: str = "", parent_id: int | None = None
    ) -> dict[str, Any]:
        name, description = _clean(name), _clean(description)
        if not name:
            raise ValueError("分类名称不能为空。")
        if len(name) > 50:
            raise ValueError("分类名称不能超过50个字符。")
        with self.storage.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM investment_thought_categories WHERE name = ?", (name,)
            ).fetchone():
                raise ValueError("已经存在同名分类。")
            parent = self._category_exists(conn, int(parent_id)) if parent_id else None
            if parent is not None and int(parent["level"]) != 1:
                raise ValueError("二级分类只能放在一级分类下面。")
            level, parent_value = (2, int(parent["id"])) if parent is not None else (1, None)
            now = _now_iso()
            cursor = conn.execute(
                """
                INSERT INTO investment_thought_categories(
                    slug, name, description, position, created_at, updated_at, parent_id, level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._slug(conn, name), name, description,
                    self._next_category_position(conn, parent_value), now, now, parent_value, level,
                ),
            )
            row = next(row for row in self._category_rows(conn) if int(row["id"]) == cursor.lastrowid)
            return self._category_dict(row)

    def update_category(
        self,
        category_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        parent_id: int | None | object = ...,
    ) -> dict[str, Any]:
        with self.storage.connect() as conn:
            current = self._category_exists(conn, category_id)
            next_name = _clean(name) if name is not None else current["name"]
            next_description = _clean(description) if description is not None else current["description"]
            if not next_name:
                raise ValueError("分类名称不能为空。")
            if conn.execute(
                "SELECT 1 FROM investment_thought_categories WHERE name = ? AND id <> ?",
                (next_name, category_id),
            ).fetchone():
                raise ValueError("已经存在同名分类。")
            next_parent = current["parent_id"]
            if parent_id is not ...:
                if int(current["level"]) == 1 and parent_id:
                    raise ValueError("一级分类不能放到其他分类下面。")
                if int(current["level"]) == 2:
                    if not parent_id:
                        raise ValueError("二级分类必须属于一个一级分类。")
                    parent = self._category_exists(conn, int(parent_id))
                    if int(parent["level"]) != 1:
                        raise ValueError("请选择一级分类作为上级。")
                    next_parent = int(parent["id"])
            conn.execute(
                "UPDATE investment_thought_categories SET name = ?, description = ?, parent_id = ?, updated_at = ? WHERE id = ?",
                (next_name, next_description, next_parent, _now_iso(), category_id),
            )
            row = next(row for row in self._category_rows(conn) if int(row["id"]) == category_id)
            return self._category_dict(row)

    def move_category(self, category_id: int, direction: str) -> dict[str, Any]:
        if direction not in {"up", "down"}:
            raise ValueError("direction 必须是 up 或 down。")
        with self.storage.connect() as conn:
            current = self._category_exists(conn, category_id)
            if current["parent_id"] is None:
                rows = conn.execute(
                    "SELECT id, position FROM investment_thought_categories WHERE parent_id IS NULL ORDER BY position, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, position FROM investment_thought_categories WHERE parent_id = ? ORDER BY position, id",
                    (current["parent_id"],),
                ).fetchall()
            ids = [int(row["id"]) for row in rows]
            index = ids.index(category_id)
            target = index - 1 if direction == "up" else index + 1
            if target < 0 or target >= len(rows):
                return {"moved": False, "category_id": category_id}
            first, second, now = rows[index], rows[target], _now_iso()
            conn.execute(
                "UPDATE investment_thought_categories SET position = ?, updated_at = ? WHERE id = ?",
                (second["position"], now, first["id"]),
            )
            conn.execute(
                "UPDATE investment_thought_categories SET position = ?, updated_at = ? WHERE id = ?",
                (first["position"], now, second["id"]),
            )
            return {"moved": True, "category_id": category_id, "direction": direction}

    def delete_category(self, category_id: int) -> dict[str, Any]:
        with self.storage.connect() as conn:
            row = self._category_exists(conn, category_id)
            if conn.execute(
                "SELECT 1 FROM investment_thought_categories WHERE parent_id = ?", (category_id,)
            ).fetchone():
                raise ValueError("该一级分类中还有二级分类，请先移动或删除二级分类。")
            if conn.execute(
                "SELECT 1 FROM investment_thought_video_links WHERE category_id = ?", (category_id,)
            ).fetchone():
                raise ValueError("该分类中还有视频，请先移出视频后再删除分类。")
            conn.execute("DELETE FROM investment_thought_categories WHERE id = ?", (category_id,))
            return {"deleted": True, "category_id": category_id, "name": row["name"]}

    def sync_video_categories(self, *, video_id: int, category_ids: Any) -> dict[str, Any]:
        requested = []
        for value in category_ids if isinstance(category_ids, list) else []:
            try:
                category_id = int(value)
            except (TypeError, ValueError):
                continue
            if category_id > 0 and category_id not in requested:
                requested.append(category_id)
        with self.storage.connect() as conn:
            video = self._video_exists(conn, int(video_id))
            if requested:
                placeholders = ",".join("?" for _ in requested)
                valid = {
                    int(row["id"])
                    for row in conn.execute(
                        f"SELECT id FROM investment_thought_categories WHERE level = 2 AND id IN ({placeholders})",
                        requested,
                    ).fetchall()
                }
                if valid != set(requested):
                    raise ValueError("视频只能归入有效的二级分类。")
            current = {
                int(row["category_id"])
                for row in conn.execute(
                    "SELECT category_id FROM investment_thought_video_links WHERE video_id = ?",
                    (video_id,),
                ).fetchall()
            }
            wanted, removed = set(requested), sorted(current - set(requested))
            added = [category_id for category_id in requested if category_id not in current]
            if removed:
                placeholders = ",".join("?" for _ in removed)
                conn.execute(
                    f"DELETE FROM investment_thought_video_links WHERE video_id = ? AND category_id IN ({placeholders})",
                    (video_id, *removed),
                )
            for category_id in added:
                conn.execute(
                    "INSERT INTO investment_thought_video_links(category_id, video_id, position, created_at) VALUES (?, ?, ?, ?)",
                    (category_id, video_id, self._next_link_position(conn, category_id), _now_iso()),
                )
            rows = conn.execute(
                """
                SELECT c.id, c.slug, c.name, c.parent_id, p.name AS parent_name
                FROM investment_thought_video_links l
                JOIN investment_thought_categories c ON c.id = l.category_id
                LEFT JOIN investment_thought_categories p ON p.id = c.parent_id
                WHERE l.video_id = ? ORDER BY p.position, c.position
                """,
                (video_id,),
            ).fetchall()
            return {
                "video_id": int(video_id), "title": video["title"],
                "category_ids": [int(row["id"]) for row in rows],
                "categories": [dict(row) for row in rows],
                "added_category_ids": added, "removed_category_ids": removed,
                "copied_content": False,
            }

    def unlink_video(self, *, category_id: int, video_id: int) -> dict[str, Any]:
        with self.storage.connect() as conn:
            category = self._category_exists(conn, category_id)
            if int(category["level"]) != 2:
                raise ValueError("请从具体的二级分类中移出视频。")
            cursor = conn.execute(
                "DELETE FROM investment_thought_video_links WHERE category_id = ? AND video_id = ?",
                (category_id, video_id),
            )
            return {"removed": cursor.rowcount > 0, "category_id": category_id, "video_id": video_id}


def read_investment_thoughts(
    database_path: Path, *, category: str = "", query: str = "", limit: int = 50
) -> dict[str, Any]:
    database_path = Path(database_path).resolve()
    conn = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "investment_thought_video_links"):
            return {"categories": [], "items": [], "count": 0}
        predicates, params = [], []
        category_row = None
        if _clean(category):
            category_row = conn.execute(
                "SELECT * FROM investment_thought_categories WHERE name = ? OR slug = ?",
                (_clean(category), _clean(category)),
            ).fetchone()
            if category_row is None:
                return {"categories": [], "items": [], "count": 0}
            if int(category_row["level"]) == 1:
                predicates.append("leaf.parent_id = ?")
            else:
                predicates.append("l.category_id = ?")
            params.append(int(category_row["id"]))
        if _clean(query):
            pattern = f"%{_clean(query)}%"
            predicates.append(
                "(COALESCE(NULLIF(vt.active_title,''),v.title,'') LIKE ? OR COALESCE(co.original_text,'') LIKE ? OR leaf.name LIKE ? OR parent.name LIKE ?)"
            )
            params.extend([pattern] * 4)
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        rows = conn.execute(
            f"""
            SELECT v.id AS video_id, MAX(l.created_at) AS linked_at,
                   COALESCE(NULLIF(vt.active_title,''),NULLIF(v.title,'')) AS title,
                   COALESCE(v.published_at,v.discovered_at) AS published_at,
                   v.author, v.url AS source_url
            FROM investment_thought_video_links l
            JOIN investment_thought_categories leaf ON leaf.id = l.category_id
            LEFT JOIN investment_thought_categories parent ON parent.id = leaf.parent_id
            JOIN videos v ON v.id = l.video_id
            LEFT JOIN video_titles vt ON vt.video_id = v.id
            LEFT JOIN content_originals co ON co.content_id = v.id
            {where}
            GROUP BY v.id
            ORDER BY MIN(parent.position), MIN(leaf.position), MIN(l.position), v.id
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), 500))),
        ).fetchall()
        links_by_video: dict[int, list[dict[str, Any]]] = {}
        video_ids = [int(row["video_id"]) for row in rows]
        if video_ids:
            placeholders = ",".join("?" for _ in video_ids)
            link_rows = conn.execute(
                f"""
                SELECT l.video_id, leaf.id, leaf.slug, leaf.name, leaf.parent_id,
                       parent.slug AS parent_slug, parent.name AS parent_name
                FROM investment_thought_video_links l
                JOIN investment_thought_categories leaf ON leaf.id = l.category_id
                LEFT JOIN investment_thought_categories parent ON parent.id = leaf.parent_id
                WHERE l.video_id IN ({placeholders})
                ORDER BY parent.position, leaf.position, leaf.id
                """,
                video_ids,
            ).fetchall()
            for link in link_rows:
                item = dict(link)
                item["path"] = f"{item['parent_name']} › {item['name']}"
                links_by_video.setdefault(int(link["video_id"]), []).append(item)

        items: list[dict[str, Any]] = []
        for row in rows:
            video_id = int(row["video_id"])
            video_categories = links_by_video.get(video_id, [])
            primary = None
            if category_row is not None:
                if int(category_row["level"]) == 1:
                    primary = next(
                        (
                            item
                            for item in video_categories
                            if item["parent_id"] == int(category_row["id"])
                        ),
                        None,
                    )
                else:
                    primary = next(
                        (
                            item
                            for item in video_categories
                            if item["id"] == int(category_row["id"])
                        ),
                        None,
                    )
            primary = primary or (video_categories[0] if video_categories else {})
            items.append(
                {
                    "record_id": f"video:{video_id}",
                    "video_id": video_id,
                    "parent_category": primary.get("parent_name"),
                    "category": primary.get("name"),
                    "category_slug": primary.get("slug"),
                    "categories": video_categories,
                    "classification_paths": [item["path"] for item in video_categories],
                    "title": row["title"],
                    "published_at": row["published_at"],
                    "author": row["author"],
                    "source_url": row["source_url"],
                    "linked_at": row["linked_at"],
                }
            )
        categories = conn.execute(
            "SELECT id, slug, name, description, position, parent_id, level FROM investment_thought_categories ORDER BY level, position, id"
        ).fetchall()
        return {
            "categories": [dict(row) for row in categories],
            "items": items,
            "count": len(items), "copied_content": False,
            "purpose": "两级投资思路视频索引；内容从 video:<id> 唯一原记录读取。",
        }
    finally:
        conn.close()
