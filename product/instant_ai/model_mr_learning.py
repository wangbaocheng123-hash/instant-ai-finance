"""Single-owner learning marks, isolated from imported work content and news."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


MAX_SAFE_INTEGER = 9_007_199_254_740_991


class ModelMrLearningConflict(ValueError):
    pass


class ModelMrLearningStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def validate(work_id: object, learned: object, expected_revision: object) -> None:
        if type(work_id) is not int or not 0 < work_id <= MAX_SAFE_INTEGER:
            raise ValueError("作品编号无效。")
        if type(learned) is not bool:
            raise ValueError("已学标记必须为布尔值。")
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_SAFE_INTEGER:
            raise ValueError("学习标记版本无效，请刷新后重试。")

    @staticmethod
    def _state(connection: sqlite3.Connection) -> dict[str, Any]:
        revision = connection.execute("SELECT revision FROM learning_state WHERE id = 1").fetchone()[0]
        rows = connection.execute("SELECT work_id FROM learned_works ORDER BY marked_revision DESC").fetchall()
        ids = [row[0] for row in rows]
        return {"revision": revision, "learned_work_ids": ids, "last_work_id": ids[0] if ids else None}

    def read(self) -> dict[str, Any]:
        # Merely browsing never creates a database or resets missing/corrupt data.
        if not self.path.exists():
            return {"revision": 0, "learned_work_ids": [], "last_work_id": None}
        with closing(sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)) as connection:
            with connection:
                connection.execute("BEGIN")
                return self._state(connection)

    def set_learned(self, work_id: int, learned: bool, expected_revision: int) -> dict[str, Any]:
        self.validate(work_id, learned, expected_revision)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            with connection:
                # SQLite serializes separate requests/processes. The revision
                # prevents a late phone/tab request from silently undoing a mark.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS learning_state (id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL CHECK (revision >= 0))")
                connection.execute("INSERT OR IGNORE INTO learning_state (id, revision) VALUES (1, 0)")
                connection.execute("CREATE TABLE IF NOT EXISTS learned_works (work_id INTEGER PRIMARY KEY CHECK (work_id > 0), marked_revision INTEGER NOT NULL UNIQUE CHECK (marked_revision > 0))")
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                state = self._state(connection)
                if state["revision"] != expected_revision:
                    raise ModelMrLearningConflict("学习标记已在其他页面更新，请刷新标记后重试。")
                present = connection.execute("SELECT 1 FROM learned_works WHERE work_id = ?", (work_id,)).fetchone() is not None
                if present == learned:
                    return state
                revision = state["revision"] + 1
                if revision > MAX_SAFE_INTEGER:
                    raise ValueError("学习标记版本超出支持范围。")
                if learned:
                    connection.execute("INSERT INTO learned_works (work_id, marked_revision) VALUES (?, ?)", (work_id, revision))
                else:
                    connection.execute("DELETE FROM learned_works WHERE work_id = ?", (work_id,))
                connection.execute("UPDATE learning_state SET revision = ? WHERE id = 1", (revision,))
                return self._state(connection)
