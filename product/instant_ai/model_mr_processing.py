"""Durable, sequential, owner-controlled processing of new Model Mr arrivals.

No history scan, no news database, no Codex session and no automatic paid retry.
The process lock is held by the worker, not the request handling thread.
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
from .model_mr import MODEL_MR, ModelMrClient
from .model_mr_metadata import clean_keyword_info, keyword_revision

PROVIDER_LOCK = threading.Lock()
DAILY_CALL_LIMIT = 20  # Up to ten videos if both ASR and keywords are missing.
MAX_VIDEO_SECONDS = 1200
MESSAGES = {
    "queued": "等待串行处理", "running": "处理中", "done": "处理完成",
    "configuration": "缺少豆包配置；尚未发起本阶段付费调用",
    "review": "处理未完成，请核对调用记录；没有自动重试",
    "quota": "今日20次调用额度已用完，等待次日",
    "conflict": "原文或关键词已变化，结果未覆盖；请重新查看",
}


class ModelMrProcessor:
    def __init__(self, client: ModelMrClient = MODEL_MR):
        self.client = client
        self.path = client.snapshot_path.parent / "processing.sqlite3"

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                    failures INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO settings(id,enabled) VALUES(1,0);
                CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL,
                    work_id INTEGER NOT NULL, kind TEXT NOT NULL, automatic INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', phase TEXT NOT NULL DEFAULT 'asr',
                    revision TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
                    phase TEXT NOT NULL, day TEXT NOT NULL);
            """)
            with conn:
                yield conn
        finally:
            conn.close()

    def status(self) -> dict[str, Any]:
        # A read endpoint must not create a database or change queue state.
        if not self.path.exists():
            enabled, failures, items = False, 0, []
        else:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                setting = conn.execute("SELECT enabled,failures FROM settings WHERE id=1").fetchone()
                enabled, failures = bool(setting[0]), setting[1]
                rows = conn.execute("SELECT id,work_id,state,phase,updated FROM jobs ORDER BY id DESC LIMIT 30").fetchall()
                items = [{**dict(row), "message": MESSAGES[row["state"]]} for row in rows]
        return {"enabled": enabled, "failures": failures, "daily_call_limit": DAILY_CALL_LIMIT,
                "max_video_minutes": MAX_VIDEO_SECONDS // 60,
                "speech_configured": doubao_asr.is_configured(),
                "keywords_configured": model_mr_keywords.is_configured(), "items": items}

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("自动处理开关必须为布尔值。")
        with self.db() as conn:
            conn.execute("UPDATE settings SET enabled=?,failures=0 WHERE id=1", (int(enabled),))
        return self.status()

    def enqueue_arrival(self, work_id: int, media_hash: str) -> None:
        # Enabling later does not retroactively process old arrivals.
        if not self.status()["enabled"]:
            return
        self._enqueue(work_id, f"arrival:{work_id}:{media_hash}", "arrival", True, "")

    def request_keywords(self, work_id: int, revision: str) -> dict[str, Any]:
        detail = self.client.processing_detail(work_id)
        text = str(detail.get("video_text", {}).get("text") or "")
        if not text.strip():
            raise ValueError("请先保存视频原文，再提炼关键词。")
        work = detail["work"]
        if revision != keyword_revision(work.get("keyword_info"), work.get("keywords")):
            raise ValueError("关键词已变化，请刷新详情后重试。")
        info = clean_keyword_info(work.get("keyword_info"), work.get("keywords"))
        if (info.get("source_hash") == model_mr_keywords.source_hash(text)
                and info["schema_version"] == model_mr_keywords.SCHEMA_VERSION):
            return {"ok": True, "state": "done", "message": "原文未变化，沿用已保存关键词，没有调用 API。"}
        return self._enqueue(work_id, f"keywords:{work_id}:{model_mr_keywords.source_hash(text)}:{revision}",
                             "keywords", False, revision)

    def _enqueue(self, work_id: int, dedupe: str, kind: str, automatic: bool, revision: str) -> dict[str, Any]:
        self.client.processing_detail(work_id)
        with self.db() as conn:
            conn.execute("INSERT OR IGNORE INTO jobs(dedupe,work_id,kind,automatic,revision,updated) VALUES(?,?,?,?,?,?)",
                         (dedupe, work_id, kind, int(automatic), revision, int(time.time())))
            row = conn.execute("SELECT id,state FROM jobs WHERE dedupe=?", (dedupe,)).fetchone()
            return {"ok": True, "job_id": row["id"], "state": row["state"], "message": MESSAGES[row["state"]]}

    def retry(self, job_id: int) -> dict[str, Any]:
        # Only an explicit owner POST may retry an ambiguous/failed paid request.
        with self.db() as conn:
            row = conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["state"] not in {"review", "configuration"}:
                raise ValueError("此任务不可重试；已完成结果不会重复调用。")
            conn.execute("UPDATE jobs SET state='queued',updated=? WHERE id=?", (int(time.time()), job_id))
        return {"ok": True, "message": "已按主人确认重新排队；已缓存的识别结果不会重复调用。"}

    def _update(self, job_id: int, state: str, phase: str | None = None, result: dict | None = None):
        with self.db() as conn:
            conn.execute("UPDATE jobs SET state=?,updated=? WHERE id=?", (state, int(time.time()), job_id))
            if phase is not None:
                conn.execute("UPDATE jobs SET phase=? WHERE id=?", (phase, job_id))
            if result is not None:
                conn.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), job_id))

    @staticmethod
    def _day():
        return datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    def _reserve_call(self, job_id: int, phase: str) -> bool:
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            automatic = conn.execute("SELECT automatic FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            enabled = conn.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0]
            if automatic and not enabled:
                conn.execute("UPDATE jobs SET state='queued' WHERE id=?", (job_id,))
                return False
            used = conn.execute("SELECT count(*) FROM calls WHERE day=?", (self._day(),)).fetchone()[0]
            if used >= DAILY_CALL_LIMIT:
                conn.execute("UPDATE jobs SET state='quota' WHERE id=?", (job_id,))
                return False
            conn.execute("INSERT INTO calls(job_id,phase,day) VALUES(?,?,?)", (job_id, phase, self._day()))
            return True

    def process_one(self) -> bool:
        if not PROVIDER_LOCK.acquire(blocking=False):
            return False
        try:
            with self.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                enabled, failures = conn.execute("SELECT enabled,failures FROM settings WHERE id=1").fetchone()
                if conn.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                    return False
                row = conn.execute("SELECT * FROM jobs WHERE (state='queued' OR (state='quota' AND NOT EXISTS "
                                   "(SELECT 1 FROM calls WHERE day=? GROUP BY day HAVING count(*)>=?))) "
                                   "AND (automatic=0 OR (?=1 AND ?<3)) ORDER BY id LIMIT 1",
                                   (self._day(), DAILY_CALL_LIMIT, enabled, failures)).fetchone()
                if row is None:
                    return False
                job = dict(row)
                conn.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (int(time.time()), job["id"]))
            self._run(job)
            return True
        finally:
            PROVIDER_LOCK.release()

    def _run(self, job: dict):
        job_id, work_id = job["id"], job["work_id"]
        try:
            cached = json.loads(job["result"])
            detail = self.client.processing_detail(work_id)
            text = str(detail.get("video_text", {}).get("text") or "").strip()
            if not text and job["kind"] == "arrival":
                text = str(cached.get("asr_text") or "").strip()
                if not text:
                    text = next((str(item.get("text") or "").strip() for item in detail.get("transcripts", [])
                                 if "doubao" in str(item.get("source") or "").lower() and item.get("text")), "")
                if not text:
                    if not doubao_asr.is_configured():
                        self._update(job_id, "configuration", "asr")
                        return
                    root = self.client.media_root.resolve()
                    media = (root / str(detail["work"].get("media_file") or "")).resolve()
                    if root not in media.parents or not media.is_file():
                        raise ValueError("missing media")
                    self._update(job_id, "running", "asr")
                    if not self._reserve_call(job_id, "asr"):
                        return
                    result = doubao_asr.transcribe_video(media, work_id, max_duration_seconds=MAX_VIDEO_SECONDS)
                    text = str(result.get("text") or "").strip()
                    if not text:
                        raise ValueError("empty text")
                    # Save paid output before the canonical file/index update.
                    cached["asr_text"] = text
                    self._update(job_id, "running", result=cached)
                text = self.client.save_auto_video_text(work_id, text)
            detail = self.client.processing_detail(work_id)
            text = str(detail.get("video_text", {}).get("text") or "")
            work = detail["work"]
            info = clean_keyword_info(work.get("keyword_info"), work.get("keywords"))
            has_keywords = bool(info["keywords"] or info["confirmed_at"] or info["schema_version"])
            if job["kind"] == "arrival" and has_keywords:
                self._finish(job_id)
                return
            revision = keyword_revision(info)
            saved_result = cached.get("keywords")
            if (job["kind"] == "keywords" and isinstance(saved_result, dict) and not info["edited_by_owner"]
                    and saved_result.get("source_hash") == model_mr_keywords.source_hash(text)
                    and all(info.get(key) == saved_result.get(key) for key in
                            ("categories", "keywords", "model", "schema_version", "source_hash"))):
                self._finish(job_id)  # Detail was saved before a partial index failure.
                return
            if job["kind"] == "keywords" and revision != job["revision"]:
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
                cached["keyword_revision"] = revision
                self._update(job_id, "running", result=cached)
            try:
                self.client.save_keywords(work_id, result["categories"], result["keywords"],
                                          cached["keyword_revision"], ai_info=result)
            except ValueError:
                self._update(job_id, "conflict", "keywords")
                return
            self._finish(job_id)
        except Exception:
            # An upstream body can contain credentials; retain only a fixed status.
            self._update(job_id, "review")
            with self.db() as conn:
                conn.execute("UPDATE settings SET failures=failures+1 WHERE id=1")

    def _finish(self, job_id: int):
        with self.db() as conn:
            work_id = conn.execute("SELECT work_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        self.client.repair_processing_index(work_id)
        self._update(job_id, "done")
        with self.db() as conn:
            conn.execute("UPDATE settings SET failures=0 WHERE id=1")

    def run(self, stop: threading.Event | None = None):
        # Cloud-only singleton lock. No server reconfiguration or new scheduler.
        import fcntl
        stop = stop or threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with self.db() as conn:
                # Never assume an interrupted remote paid call did not happen.
                conn.execute("UPDATE jobs SET state='review' WHERE state='running'")
            while not stop.is_set():
                try:
                    self.process_one()
                except Exception:
                    pass  # Storage failure is fail-closed, not a service-wide crash.
                stop.wait(3)


MODEL_MR_PROCESSOR = ModelMrProcessor()
