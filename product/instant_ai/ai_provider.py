from __future__ import annotations

import json
import os
from typing import Any

from .database import connect, transaction, utc_now


CONTRACT_VERSION = "evidence-v1"


def provider_status() -> dict[str, Any]:
    """Expose capability state without ever returning a secret."""

    provider = os.environ.get("INSTANT_AI_MODEL_PROVIDER", "").strip()
    model = os.environ.get("INSTANT_AI_MODEL_NAME", "").strip()
    endpoint = os.environ.get("INSTANT_AI_MODEL_ENDPOINT", "").strip()
    has_credential = bool(os.environ.get("INSTANT_AI_MODEL_API_KEY", "").strip())
    configured = bool(provider and model and endpoint and has_credential)
    return {
        "contract_version": CONTRACT_VERSION,
        "configured": configured,
        "provider": provider or None,
        "model": model or None,
        "endpoint_configured": bool(endpoint),
        "credential_configured": has_credential,
        "runner_state": "adapter_pending" if configured else "awaiting_secure_configuration",
        "message": (
            "模型参数已检测到；联网执行器尚未启用。"
            if configured
            else "尚未配置真实模型；采集、证据、规则评分和阅读不受影响。"
        ),
    }


def _evidence_packet(item_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        item = connection.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if item is None:
            return None
        evidence = connection.execute(
            """
            SELECT e.id, e.url, e.title, e.fetched_at, e.published_at,
                   e.content_hash, s.name AS source_name, s.trust_level
            FROM item_evidence ie
            JOIN evidence e ON e.id=ie.evidence_id
            JOIN sources s ON s.id=e.source_id
            WHERE ie.item_id=? ORDER BY s.trust_level DESC, e.fetched_at DESC
            """,
            (item_id,),
        ).fetchall()
    return {
        "contract_version": CONTRACT_VERSION,
        "item": {
            "id": item["id"],
            "title": item["title"],
            "official_summary": item["summary"],
            "url": item["url"],
            "published_at": item["published_at"],
            "topics": json.loads(item["topics_json"]),
            "entities": json.loads(item["entities_json"]),
            "event_type": item["event_type"],
            "rule_score": item["importance_score"],
        },
        "evidence": [dict(row) for row in evidence],
        "instructions": {
            "required": ["summary", "why_it_matters", "citations"],
            "citation_rule": "citations must contain evidence ids from this packet",
            "prohibited": ["automatic trading", "fabricated facts", "uncited claims"],
        },
    }


def queue_analysis(item_id: int) -> dict[str, Any] | None:
    packet = _evidence_packet(item_id)
    if packet is None:
        return None
    status = provider_status()
    job_status = "waiting_for_runner" if status["configured"] else "waiting_for_provider"
    now = utc_now()
    with transaction() as connection:
        existing = connection.execute(
            """
            SELECT id, status FROM ai_jobs
            WHERE item_id=? AND status IN ('waiting_for_provider', 'waiting_for_runner', 'queued', 'running')
            ORDER BY id DESC LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": existing["status"],
                "provider": status,
                "packet": packet,
                "reused": True,
            }
        cursor = connection.execute(
            """
            INSERT INTO ai_jobs(
                item_id, status, provider, model, prompt_version,
                input_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                job_status,
                status["provider"],
                status["model"],
                CONTRACT_VERSION,
                json.dumps(packet, ensure_ascii=False),
                now,
                now,
            ),
        )
        job_id = int(cursor.lastrowid)
    return {"job_id": job_id, "status": job_status, "provider": status, "packet": packet}


def latest_job(item_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM ai_jobs WHERE item_id=? ORDER BY id DESC LIMIT 1", (item_id,)
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["input"] = json.loads(result.pop("input_json"))
    result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
    return result
