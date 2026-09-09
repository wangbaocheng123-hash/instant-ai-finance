"""Durable, sequential automatic processing of new Model Mr arrivals.

New arrivals are on by policy.  A bounded one-time recovery window repairs
videos missed under the former default-off policy, while a persisted cursor
repairs later callback/restart gaps.  There is no unbounded history scan, news
database access, Codex session or automatic retry of ambiguous paid calls.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import doubao_asr, model_mr_keywords
from .model_mr import MODEL_MR, ModelMrClient
from .model_mr_metadata import clean_keyword_info, keyword_revision

PROVIDER_LOCK = threading.Lock()
DAILY_CALL_LIMIT = 20  # Up to ten videos if both ASR and keywords are missing.
MAX_VIDEO_SECONDS = 1200
AUTO_POLICY_VERSION = 1
INITIAL_RECOVERY_SECONDS = 48 * 60 * 60
MESSAGES = {
    "queued": "等待串行处理", "running": "处理中", "done": "处理完成",
    "configuration": "缺少豆包配置；尚未发起本阶段付费调用",
    "review": "处理未完成，请核对调用记录；没有自动重试",
    "quota": "今日20次调用额度已用完，等待次日",
    "conflict": "原文或关键词已变化，结果未覆盖；请重新查看",
}


class ModelMrProcessor:
    def __init__(
        self,
        client: ModelMrClient = MODEL_MR,
        arrival_source: Callable[[int, int], list[dict[str, Any]]] | None = None,
    ):
        self.client = client
        self.path = client.snapshot_path.parent / "processing.sqlite3"
        self.arrival_source = arrival_source
        self._worker_active = False
        self._worker_last_seen = 0

    def set_arrival_source(self, source: Callable[[int, int], list[dict[str, Any]]]) -> None:
        self.arrival_source = source

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
                    failures INTEGER NOT NULL DEFAULT 0, enabled_since INTEGER NOT NULL DEFAULT 0,
                    last_reconciled INTEGER NOT NULL DEFAULT 0, policy_version INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL,
                    work_id INTEGER NOT NULL, kind TEXT NOT NULL, automatic INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', phase TEXT NOT NULL DEFAULT 'asr',
                    revision TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
                    phase TEXT NOT NULL, day TEXT NOT NULL);
            """)
            setting_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(settings)")
            }
            policy_missing = "policy_version" not in setting_columns
            for column in ("enabled_since", "last_reconciled", "policy_version"):
                if column not in setting_columns:
                    conn.execute(
                        f"ALTER TABLE settings ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            now = int(time.time())
            setting = conn.execute("SELECT id,policy_version FROM settings WHERE id=1").fetchone()
            if setting is None:
                conn.execute(
                    "INSERT INTO settings(id,enabled,failures,enabled_since,last_reconciled,policy_version) "
                    "VALUES(1,1,0,?,0,?)",
                    (now - INITIAL_RECOVERY_SECONDS, AUTO_POLICY_VERSION),
                )
            elif policy_missing or int(setting["policy_version"] or 0) < AUTO_POLICY_VERSION:
                # This user-approved migration is intentionally bounded: it
                # recovers the current missed arrival without creating a batch
                # over the historical Model Mr library.
                recovery_boundary = now - INITIAL_RECOVERY_SECONDS
                conn.execute(
                    "UPDATE settings SET enabled=1,failures=0,enabled_since=?,last_reconciled=0,policy_version=? "
                    "WHERE id=1",
                    (recovery_boundary, AUTO_POLICY_VERSION),
                )
                # A legacy queued/quota row could otherwise run merely because
                # this migration re-enables the global switch. Keep anything
                # older than the same bounded recovery window for owner review.
                conn.execute(
                    "UPDATE jobs SET state='review',updated=? WHERE automatic=1 "
                    "AND state IN ('queued','quota') AND updated<?",
                    (now, recovery_boundary),
                )
            with conn:
                yield conn
        finally:
            conn.close()

    def status(self) -> dict[str, Any]:
        # A read endpoint must not create a database or change queue state.
        if not self.path.exists():
            enabled, failures, enabled_since, last_reconciled, items = True, 0, 0, 0, []
        else:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(settings)")}
                fields = ["enabled", "failures"]
                fields.extend(name for name in ("enabled_since", "last_reconciled", "policy_version") if name in columns)
                setting = conn.execute(f"SELECT {','.join(fields)} FROM settings WHERE id=1").fetchone()
                legacy = "policy_version" not in setting.keys() or int(setting["policy_version"] or 0) < AUTO_POLICY_VERSION
                enabled = True if legacy else bool(setting["enabled"])
                failures = 0 if legacy else int(setting["failures"])
                enabled_since = int(setting["enabled_since"]) if "enabled_since" in setting.keys() else 0
                last_reconciled = int(setting["last_reconciled"]) if "last_reconciled" in setting.keys() else 0
                rows = conn.execute("SELECT id,work_id,state,phase,updated FROM jobs ORDER BY id DESC LIMIT 30").fetchall()
                items = [{**dict(row), "message": MESSAGES[row["state"]]} for row in rows]
        return {"enabled": enabled, "failures": failures, "enabled_since": enabled_since,
                "last_reconciled": last_reconciled, "worker_running": self._worker_active,
                "worker_last_seen": self._worker_last_seen,
                "initial_recovery_hours": INITIAL_RECOVERY_SECONDS // 3600,
                "daily_call_limit": DAILY_CALL_LIMIT,
                "max_video_minutes": MAX_VIDEO_SECONDS // 60,
                "speech_configured": doubao_asr.is_configured(),
                "keywords_configured": model_mr_keywords.is_configured(), "items": items}

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("自动处理开关必须为布尔值。")
        with self.db() as conn:
            current = bool(conn.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0])
            if enabled and not current:
                conn.execute(
                    "UPDATE settings SET enabled=1,failures=0,enabled_since=?,last_reconciled=0 WHERE id=1",
                    (int(time.time()),),
                )
            else:
                conn.execute("UPDATE settings SET enabled=?,failures=0 WHERE id=1", (int(enabled),))
        return self.status()

    def enqueue_arrival(self, work_id: int, media_hash: str) -> None:
        # A real arrival initializes/migrates policy state; a status GET does not.
        with self.db() as conn:
            if not bool(conn.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0]):
                return
        self._enqueue(work_id, f"arrival:{work_id}:{media_hash}", "arrival", True, "")

    def reconcile_new_arrivals(self) -> int:
        """Idempotently repair missed callbacks inside the automatic boundary."""

        if self.arrival_source is None:
            return 0
        with self.db() as conn:
            setting = conn.execute(
                "SELECT enabled,enabled_since,last_reconciled FROM settings WHERE id=1"
            ).fetchone()
            if setting is None or not bool(setting["enabled"]) or int(setting["enabled_since"]) <= 0:
                return 0
            enabled_since = int(setting["enabled_since"])
            scan_since = max(enabled_since, int(setting["last_reconciled"]) - 1)
        arrivals = self.arrival_source(scan_since, 500)
        queued = 0
        watermark = int(time.time()) if not arrivals else scan_since
        for arrival in arrivals:
            completed_at = max(0, int(arrival.get("completed_at") or 0))
            if not bool(arrival.get("ready")):
                watermark = max(scan_since, completed_at - 1)
                break
            work_id = int(arrival.get("work_id") or 0)
            media_hash = str(arrival.get("media_hash") or "")
            self._enqueue(work_id, f"arrival:{work_id}:{media_hash}", "arrival", True, "")
            queued += 1
            watermark = max(watermark, completed_at)
        with self.db() as conn:
            conn.execute("UPDATE settings SET last_reconciled=? WHERE id=1", (watermark,))
        return queued

    def resume_configured_jobs(self) -> int:
        """Resume only known pre-call stops after both providers are configured."""

        if not (doubao_asr.is_configured() and model_mr_keywords.is_configured()):
            return 0
        with self.db() as conn:
            enabled = bool(conn.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0])
            if not enabled:
                return 0
            cursor = conn.execute(
                "UPDATE jobs SET state='queued',updated=? WHERE automatic=1 AND state='configuration' "
                "AND updated>=(SELECT enabled_since FROM settings WHERE id=1)",
                (int(time.time()),),
            )
            return max(0, int(cursor.rowcount))

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
        return self._enqueue(
            work_id,
            f"keywords:{work_id}:{model_mr_keywords.source_hash(text)}:{revision}",
            "keywords",
            False,
            revision,
            resume_configuration=model_mr_keywords.is_configured(),
        )

    def _enqueue(self, work_id: int, dedupe: str, kind: str, automatic: bool, revision: str,
                 *, resume_configuration: bool = False) -> dict[str, Any]:
        self.client.processing_detail(work_id)
        with self.db() as conn:
            conn.execute("INSERT OR IGNORE INTO jobs(dedupe,work_id,kind,automatic,revision,updated) VALUES(?,?,?,?,?,?)",
                         (dedupe, work_id, kind, int(automatic), revision, int(time.time())))
            row = conn.execute("SELECT id,state FROM jobs WHERE dedupe=?", (dedupe,)).fetchone()
            if resume_configuration and row["state"] == "configuration":
                # Repeating the paid keyword POST is an explicit owner retry. A
                # recovered configuration may therefore resume this known-safe
                # pre-call state. Ambiguous review jobs still require the
                # separate audit/retry action and are never resumed here.
                conn.execute("UPDATE jobs SET state='queued',updated=? WHERE id=?",
                             (int(time.time()), row["id"]))
                return {"ok": True, "job_id": row["id"], "state": "queued", "message": MESSAGES["queued"]}
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
            self._worker_active = True
            try:
                with self.db() as conn:
                    # Never assume an interrupted remote paid call did not happen.
                    conn.execute("UPDATE jobs SET state='review' WHERE state='running'")
                while not stop.is_set():
                    self._worker_last_seen = int(time.time())
                    try:
                        self.reconcile_new_arrivals()
                        self.resume_configured_jobs()
                        self.process_one()
                    except Exception:
                        pass  # Storage failure is fail-closed, not a service-wide crash.
                    stop.wait(3)
            finally:
                self._worker_active = False


MODEL_MR_PROCESSOR = ModelMrProcessor()
