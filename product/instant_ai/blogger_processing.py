"""Durable, sequential, owner-controlled processing for ordinary bloggers.

The queue mirrors the Model Mr safety contract: it is off by default, only
handles arrivals completed after it is enabled, never starts a historical
batch and never automatically retries an ambiguous paid request.  A persisted
activation boundary lets the worker reconcile completion callbacks that were
missed during a restart without widening that paid-work boundary.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from . import doubao_asr, model_mr_keywords
from .blogger_library import BLOGGER_LIBRARY, BloggerLibrary
from .model_mr_metadata import clean_keyword_info, keyword_revision
from .model_mr_processing import PROVIDER_LOCK


DAILY_CALL_LIMIT = 20
MAX_VIDEO_SECONDS = 1200
MESSAGES = {
    "queued": "等待串行处理",
    "running": "处理中",
    "done": "处理完成",
    "configuration": "缺少豆包配置；尚未发起本阶段付费调用",
    "review": "处理未完成，请核对调用记录；没有自动重试",
    "quota": "今日20次调用额度已用完，等待次日",
    "conflict": "原文或关键词已变化，结果未覆盖；请重新查看",
}


class BloggerProcessor:
    def __init__(self, library: BloggerLibrary = BLOGGER_LIBRARY):
        self.library = library
        self.path = library.root / "database" / "blogger_processing.sqlite3"
        self._worker_active = False
        self._worker_last_seen = 0

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings(
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                    failures INTEGER NOT NULL DEFAULT 0,
                    enabled_since INTEGER NOT NULL DEFAULT 0,
                    last_reconciled INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO settings(id,enabled) VALUES(1,0);
                CREATE TABLE IF NOT EXISTS jobs(
                    id INTEGER PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL,
                    work_key TEXT NOT NULL, kind TEXT NOT NULL,
                    automatic INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    phase TEXT NOT NULL DEFAULT 'asr', revision TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls(
                    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
                    phase TEXT NOT NULL, day TEXT NOT NULL
                );
                """
            )
            setting_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(settings)")
            }
            added_enabled_since = "enabled_since" not in setting_columns
            if added_enabled_since:
                connection.execute(
                    "ALTER TABLE settings ADD COLUMN enabled_since INTEGER NOT NULL DEFAULT 0"
                )
            if "last_reconciled" not in setting_columns:
                connection.execute(
                    "ALTER TABLE settings ADD COLUMN last_reconciled INTEGER NOT NULL DEFAULT 0"
                )
            if added_enabled_since:
                # Legacy queues did not persist their activation time.  Starting
                # the new boundary at migration is the only safe choice: future
                # callbacks gain durable recovery without silently billing an
                # unknown amount of older content.
                connection.execute(
                    "UPDATE settings SET enabled_since=? WHERE enabled=1 AND enabled_since=0",
                    (int(time.time()),),
                )
            with connection:
                yield connection
        finally:
            connection.close()

    def status(self) -> dict[str, Any]:
        # Merely opening the page must not create queue state.
        if not self.path.exists():
            enabled, failures, enabled_since, last_reconciled, items = False, 0, 0, 0, []
            persisted_counts: dict[str, int] = {}
        else:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                setting_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(settings)")
                }
                setting_fields = ["enabled", "failures"]
                setting_fields.extend(
                    name for name in ("enabled_since", "last_reconciled") if name in setting_columns
                )
                setting = connection.execute(
                    f"SELECT {','.join(setting_fields)} FROM settings WHERE id=1"
                ).fetchone()
                enabled, failures = bool(setting["enabled"]), int(setting["failures"])
                enabled_since = int(setting["enabled_since"]) if "enabled_since" in setting.keys() else 0
                last_reconciled = int(setting["last_reconciled"]) if "last_reconciled" in setting.keys() else 0
                rows = connection.execute(
                    "SELECT id,work_key,kind,automatic,state,phase,updated "
                    "FROM jobs ORDER BY id DESC LIMIT 30"
                ).fetchall()
                items = [dict(row) for row in rows]
                persisted_counts = {
                    str(row["state"]): int(row["count"])
                    for row in connection.execute(
                        "SELECT state,count(*) AS count FROM jobs GROUP BY state"
                    )
                }
        labels = self.library.processing_labels([str(item["work_key"]) for item in items])
        for item in items:
            item.update(labels.get(str(item["work_key"]), {}))
            item.setdefault("title", "作品资料待刷新")
            item.setdefault("creator_name", "博主待确认")
            item["automatic"] = bool(item["automatic"])
            item["mode"] = "自动处理" if item["automatic"] else "手动补做"
            item["message"] = MESSAGES.get(str(item["state"]), "状态待确认")
            item["steps"] = self._step_status(item)
        state_counts = {
            state: persisted_counts.get(state, 0)
            for state in ("queued", "running", "done", "configuration", "review", "quota", "conflict")
        }
        return {
            "enabled": enabled,
            "failures": failures,
            "enabled_since": enabled_since,
            "last_reconciled": last_reconciled,
            "worker_running": self._worker_active,
            "worker_last_seen": self._worker_last_seen,
            "daily_call_limit": DAILY_CALL_LIMIT,
            "max_video_minutes": MAX_VIDEO_SECONDS // 60,
            "speech_configured": doubao_asr.is_configured(),
            "keywords_configured": model_mr_keywords.is_configured(),
            "summary": {**state_counts, "total": sum(persisted_counts.values())},
            "items": items,
        }

    @staticmethod
    def _step_status(job: dict[str, Any]) -> dict[str, dict[str, str]]:
        """Project a job state into the two owner-visible pipeline steps."""
        state = str(job.get("state") or "queued")
        kind = str(job.get("kind") or "arrival")
        phase = "keywords" if kind == "keywords" else str(job.get("phase") or "asr")
        current = {
            "queued": "queued",
            "running": "running",
            "configuration": "blocked",
            "review": "review",
            "quota": "waiting",
            "conflict": "review",
        }.get(state, "waiting")
        if kind == "keywords":
            return {
                "asr": {"state": "skipped", "message": "使用已有视频原文"},
                "keywords": {
                    "state": "done" if state == "done" else current,
                    "message": "已完成" if state == "done" else MESSAGES.get(state, "等待处理"),
                },
            }
        if state == "done" or phase == "complete":
            return {
                "asr": {"state": "done", "message": "已完成或复用已有原文"},
                "keywords": {"state": "done", "message": "已完成或复用已有关键词"},
            }
        if phase == "keywords":
            return {
                "asr": {"state": "done", "message": "已完成或复用已有原文"},
                "keywords": {"state": current, "message": MESSAGES.get(state, "等待处理")},
            }
        return {
            "asr": {"state": current, "message": MESSAGES.get(state, "等待处理")},
            "keywords": {"state": "waiting", "message": "等待视频原文"},
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("自动处理开关必须为布尔值。")
        with self.db() as connection:
            current = bool(connection.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0])
            if enabled and not current:
                connection.execute(
                    "UPDATE settings SET enabled=1,failures=0,enabled_since=?,last_reconciled=0 WHERE id=1",
                    (int(time.time()),),
                )
            else:
                connection.execute(
                    "UPDATE settings SET enabled=?,failures=0 WHERE id=1",
                    (int(enabled),),
                )
        return self.status()

    def enqueue_transfer(self, transfer_id: str) -> None:
        # The persisted activation boundary is enforced again by reconciliation.
        if not self.status()["enabled"]:
            return
        arrival = self.library.processing_arrival(transfer_id)
        if not arrival:
            return
        work_key = arrival["work_key"]
        self._enqueue(
            work_key,
            f"arrival:{work_key}:{arrival['media_hash']}",
            "arrival",
            True,
            "",
        )

    def reconcile_new_arrivals(self) -> int:
        """Idempotently repair missed callbacks inside the enabled time window."""
        if not self.path.exists():
            return 0
        with self.db() as connection:
            setting = connection.execute(
                "SELECT enabled,enabled_since,last_reconciled FROM settings WHERE id=1"
            ).fetchone()
            if setting is None or not bool(setting["enabled"]) or int(setting["enabled_since"]) <= 0:
                return 0
            enabled_since = int(setting["enabled_since"])
            # Keep a one-second overlap because transfer timestamps have second
            # precision. Queue dedupe makes this overlap safe and prevents a
            # completion exactly on the scan boundary from being missed.
            scan_since = max(enabled_since, int(setting["last_reconciled"]) - 1)
        arrivals = self.library.processing_arrivals_since(scan_since)
        for arrival in arrivals:
            work_key = arrival["work_key"]
            self._enqueue(
                work_key,
                f"arrival:{work_key}:{arrival['media_hash']}",
                "arrival",
                True,
                "",
            )
        with self.db() as connection:
            connection.execute(
                "UPDATE settings SET last_reconciled=? WHERE id=1",
                (int(time.time()),),
            )
        return len(arrivals)

    def request_pipeline(self, work_key: str) -> dict[str, Any]:
        """Explicitly repair ASR and keyword extraction for one selected work."""
        detail = self.library.processing_detail(work_key)
        candidate = self.library.processing_candidate(work_key)
        if candidate is None:
            raise ValueError("这条作品没有已完成传输的可识别视频。")
        text = str(detail.get("video_text", {}).get("text") or "").strip()
        info = clean_keyword_info(detail.get("keyword_info"), detail.get("keywords"))
        if text and (info["keywords"] or info["confirmed_at"] or info["schema_version"]):
            return {"ok": True, "state": "done", "message": "原文和关键词均已存在，没有调用 API"}
        return self._enqueue(
            work_key,
            f"arrival:{work_key}:{candidate['media_hash']}",
            "pipeline",
            False,
            "",
            resume_configuration=(bool(text) or doubao_asr.is_configured())
            and model_mr_keywords.is_configured(),
            promote_manual=True,
        )

    def request_keywords(self, work_key: str, revision: str) -> dict[str, Any]:
        detail = self.library.processing_detail(work_key)
        text = str(detail.get("video_text", {}).get("text") or "")
        if not text.strip():
            raise ValueError("请先保存视频原文，再提炼关键词。")
        current_revision = str(detail.get("keyword_revision") or "")
        if revision != current_revision:
            raise ValueError("关键词已变化，请刷新详情后重试。")
        info = clean_keyword_info(detail.get("keyword_info"), detail.get("keywords"))
        if (
            info.get("source_hash") == model_mr_keywords.source_hash(text)
            and info["schema_version"] == model_mr_keywords.SCHEMA_VERSION
        ):
            return {"ok": True, "state": "done", "message": "原文未变化，沿用已保存关键词，没有调用 API"}
        return self._enqueue(
            work_key,
            f"keywords:{work_key}:{model_mr_keywords.source_hash(text)}:{revision}",
            "keywords",
            False,
            revision,
            resume_configuration=model_mr_keywords.is_configured(),
        )

    def _enqueue(
        self,
        work_key: str,
        dedupe: str,
        kind: str,
        automatic: bool,
        revision: str,
        *,
        resume_configuration: bool = False,
        promote_manual: bool = False,
    ) -> dict[str, Any]:
        self.library.processing_detail(work_key)
        phase = "keywords" if kind == "keywords" else "asr"
        with self.db() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO jobs(dedupe,work_key,kind,automatic,phase,revision,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (dedupe, work_key, kind, int(automatic), phase, revision, int(time.time())),
            )
            row = connection.execute(
                "SELECT id,state,automatic FROM jobs WHERE dedupe=?", (dedupe,)
            ).fetchone()
            if promote_manual and bool(row["automatic"]):
                connection.execute(
                    "UPDATE jobs SET automatic=0,updated=? WHERE id=?",
                    (int(time.time()), row["id"]),
                )
            if resume_configuration and row["state"] == "configuration":
                # Repeating this paid keyword POST is an explicit owner action.
                # A known pre-call configuration stop is safe to resume after
                # configuration recovery; ambiguous review jobs and automatic
                # arrival jobs keep their separate confirmation boundaries.
                connection.execute(
                    "UPDATE jobs SET state='queued',updated=? WHERE id=?",
                    (int(time.time()), row["id"]),
                )
                return {
                    "ok": True,
                    "job_id": row["id"],
                    "state": "queued",
                    "message": MESSAGES["queued"],
                }
            return {"ok": True, "job_id": row["id"], "state": row["state"], "message": MESSAGES[row["state"]]}

    def retry(self, job_id: int) -> dict[str, Any]:
        with self.db() as connection:
            row = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["state"] not in {"review", "configuration"}:
                raise ValueError("此任务不可重试；已完成结果不会重复调用。")
            connection.execute(
                "UPDATE jobs SET state='queued',automatic=0,updated=? WHERE id=?",
                (int(time.time()), job_id),
            )
        return {"ok": True, "message": "已按主人确认重新排队；已缓存的结果不会重复调用。"}

    def _update(
        self,
        job_id: int,
        state: str,
        phase: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self.db() as connection:
            connection.execute("UPDATE jobs SET state=?,updated=? WHERE id=?", (state, int(time.time()), job_id))
            if phase is not None:
                connection.execute("UPDATE jobs SET phase=? WHERE id=?", (phase, job_id))
            if result is not None:
                connection.execute(
                    "UPDATE jobs SET result=? WHERE id=?",
                    (json.dumps(result, ensure_ascii=False), job_id),
                )

    @staticmethod
    def _day() -> str:
        return datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    def _reserve_call(self, job_id: int, phase: str) -> bool:
        with self.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            automatic = connection.execute("SELECT automatic FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            enabled = connection.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0]
            if automatic and not enabled:
                connection.execute("UPDATE jobs SET state='queued' WHERE id=?", (job_id,))
                return False
            used = connection.execute("SELECT count(*) FROM calls WHERE day=?", (self._day(),)).fetchone()[0]
            if used >= DAILY_CALL_LIMIT:
                connection.execute("UPDATE jobs SET state='quota' WHERE id=?", (job_id,))
                return False
            connection.execute(
                "INSERT INTO calls(job_id,phase,day) VALUES(?,?,?)",
                (job_id, phase, self._day()),
            )
            return True

    def process_one(self) -> bool:
        # Model Mr and ordinary bloggers share one provider lock and cannot bill concurrently.
        if not PROVIDER_LOCK.acquire(blocking=False):
            return False
        try:
            with self.db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                enabled, failures = connection.execute("SELECT enabled,failures FROM settings WHERE id=1").fetchone()
                if connection.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                    return False
                row = connection.execute(
                    "SELECT * FROM jobs WHERE (state='queued' OR (state='quota' AND NOT EXISTS "
                    "(SELECT 1 FROM calls WHERE day=? GROUP BY day HAVING count(*)>=?))) "
                    "AND (automatic=0 OR (?=1 AND ?<3)) ORDER BY id LIMIT 1",
                    (self._day(), DAILY_CALL_LIMIT, enabled, failures),
                ).fetchone()
                if row is None:
                    return False
                job = dict(row)
                connection.execute(
                    "UPDATE jobs SET state='running',updated=? WHERE id=?",
                    (int(time.time()), job["id"]),
                )
            self._run(job)
            return True
        finally:
            PROVIDER_LOCK.release()

    def _run(self, job: dict[str, Any]) -> None:
        job_id, work_key = int(job["id"]), str(job["work_key"])
        try:
            cached = json.loads(job["result"])
            detail = self.library.processing_detail(work_key)
            text = str(detail.get("video_text", {}).get("text") or "").strip()
            if not text and job["kind"] in {"arrival", "pipeline"}:
                text = str(cached.get("asr_text") or "").strip()
                if not text:
                    text = next(
                        (
                            str(item.get("text") or "").strip()
                            for item in detail.get("transcripts", [])
                            if "doubao" in str(item.get("source") or "").lower() and item.get("text")
                        ),
                        "",
                    )
                if not text:
                    if not doubao_asr.is_configured():
                        self._update(job_id, "configuration", "asr")
                        return
                    local = self.library.video_path(work_key)
                    if local is None:
                        raise ValueError("missing media")
                    self._update(job_id, "running", "asr")
                    if not self._reserve_call(job_id, "asr"):
                        return
                    result = doubao_asr.transcribe_video(
                        local[0],
                        int(work_key[:12], 16),
                        scope="blogger",
                        max_duration_seconds=MAX_VIDEO_SECONDS,
                    )
                    text = str(result.get("text") or "").strip()
                    if not text:
                        raise ValueError("empty text")
                    cached["asr_text"] = text
                    self._update(job_id, "running", result=cached)
                text = self.library.save_auto_video_text(work_key, text)

            detail = self.library.processing_detail(work_key)
            text = str(detail.get("video_text", {}).get("text") or "")
            info = clean_keyword_info(detail.get("keyword_info"), detail.get("keywords"))
            has_keywords = bool(info["keywords"] or info["confirmed_at"] or info["schema_version"])
            if job["kind"] in {"arrival", "pipeline"} and has_keywords:
                self._finish(job_id)
                return
            current_revision = keyword_revision(info)
            saved_result = cached.get("keywords")
            if (
                job["kind"] == "keywords"
                and isinstance(saved_result, dict)
                and not info["edited_by_owner"]
                and saved_result.get("source_hash") == model_mr_keywords.source_hash(text)
                and all(
                    info.get(key) == saved_result.get(key)
                    for key in ("categories", "keywords", "model", "schema_version", "source_hash")
                )
            ):
                self._finish(job_id)
                return
            if job["kind"] == "keywords" and current_revision != job["revision"]:
                self._update(job_id, "conflict", "keywords")
                return
            if not text.strip():
                raise ValueError("missing original")
            result = cached.get("keywords")
            if result is None:
                if not model_mr_keywords.is_configured():
                    self._update(job_id, "configuration", "keywords")
                    return
                if len(text) > 60_000:
                    raise ValueError("original too long")
                self._update(job_id, "running", "keywords")
                if not self._reserve_call(job_id, "keywords"):
                    return
                result = model_mr_keywords.extract_keywords(text)
                cached["keywords"] = result
                cached["keyword_revision"] = current_revision
                self._update(job_id, "running", result=cached)
            try:
                self.library.save_keywords(
                    work_key,
                    result["categories"],
                    result["keywords"],
                    str(cached["keyword_revision"]),
                    ai_info=result,
                )
            except ValueError:
                self._update(job_id, "conflict", "keywords")
                return
            self._finish(job_id)
        except Exception:
            # Never persist provider responses, paths, credentials or raw errors.
            self._update(job_id, "review")
            with self.db() as connection:
                connection.execute("UPDATE settings SET failures=failures+1 WHERE id=1")

    def _finish(self, job_id: int) -> None:
        self._update(job_id, "done", "complete")
        with self.db() as connection:
            connection.execute("UPDATE settings SET failures=0 WHERE id=1")

    def run(self, stop: threading.Event | None = None) -> None:
        import fcntl

        stop = stop or threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self._worker_active = True
            try:
                with self.db() as connection:
                    # An interrupted provider request may already have incurred a charge.
                    connection.execute("UPDATE jobs SET state='review' WHERE state='running'")
                while not stop.is_set():
                    self._worker_last_seen = int(time.time())
                    try:
                        self.reconcile_new_arrivals()
                        self.process_one()
                    except Exception:
                        pass
                    stop.wait(3)
            finally:
                self._worker_active = False


BLOGGER_PROCESSOR = BloggerProcessor()


__all__ = ["BLOGGER_PROCESSOR", "BloggerProcessor"]
