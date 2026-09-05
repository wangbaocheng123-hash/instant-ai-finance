from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .settings import Settings, openai_api_is_enabled, require_openai_api_enabled
from .storage import Storage, from_json, now_iso, to_json


DEFAULT_MAINLINE_MODEL = "gpt-5.6-terra"
TECHNOLOGY_LINE_KEY = "technology"
NODE_STATES = {"verified", "current", "watch", "forecast", "weak", "target"}
CHART_DIRECTIONS = {"down", "sideways", "retest", "up", "rebound"}


def build_qualitative_chart(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a non-price, evidence-bound market-path view from confirmed nodes."""
    segments: list[dict[str, Any]] = []
    for index, node in enumerate(nodes[:8]):
        title = str(node.get("title") or "")
        combined = " ".join(
            str(node.get(key) or "")
            for key in ("title", "text", "evidence")
        )
        if "二次探底" in title or "再次探底" in title:
            direction = "retest"
        elif any(word in title for word in ("反弹", "反抽")):
            direction = "rebound"
        elif any(word in title for word in ("主升", "上涨", "回升", "修复")):
            direction = "up"
        elif any(word in title for word in ("主跌", "下跌", "回撤", "阴跌", "杀跌")):
            direction = "down"
        elif any(word in title for word in ("震荡", "筑底", "横盘", "消化", "等待")):
            direction = "sideways"
        elif "二次探底" in combined or "再次探底" in combined:
            direction = "retest"
        elif any(word in combined for word in ("反弹", "反抽")):
            direction = "rebound"
        elif any(word in combined for word in ("主升", "上涨", "回升", "修复")):
            direction = "up"
        elif any(word in combined for word in ("主跌", "下跌", "回撤", "阴跌", "杀跌")):
            direction = "down"
        else:
            direction = "sideways"

        if any(word in combined for word in ("腰斩", "大幅", "主跌浪")):
            amplitude = "large"
        elif any(word in combined for word in ("有限", "小幅", "偏弱")):
            amplitude = "small"
        else:
            amplitude = "medium"

        state = str(node.get("state") or "watch")
        certainty = (
            "confirmed"
            if state == "verified"
            else "current"
            if state == "current"
            else "watch"
            if state == "watch"
            else "forecast"
        )
        wave_match = re.search(r"(?<![A-Za-z])([ABCabc])\s*浪", combined)
        wave_label = f"{wave_match.group(1).upper()}浪" if wave_match else ""
        source_ids: list[int] = []
        for source_id in node.get("source_video_ids", []):
            try:
                numeric = int(source_id)
            except (TypeError, ValueError):
                continue
            if numeric not in source_ids:
                source_ids.append(numeric)
        segments.append(
            {
                "index": index,
                "date": str(node.get("date") or "")[:60],
                "label": str(node.get("title") or "")[:100],
                "direction": direction if direction in CHART_DIRECTIONS else "sideways",
                "amplitude": amplitude,
                "certainty": certainty,
                "wave_label": wave_label,
                "evidence": str(node.get("evidence") or "")[:600],
                "source_video_ids": source_ids,
            }
        )
    return {
        "title": "模型先生科技行情推演图",
        "subtitle": "相对走势 · 不代表真实指数点位",
        "segments": segments,
        "disclaimer": "图形只还原已确认的模型先生观点；未来区间使用虚线或半透明显示。",
    }

DEFAULT_TECHNOLOGY_LINE = {
    "line_key": TECHNOLOGY_LINE_KEY,
    "number": "01",
    "title": "科技投资路线",
    "status": "当前判断：收尾阶段",
    "as_of": "更新至 2026.07.20",
    "summary": "围绕本轮科技行情的节奏推演，观察反弹窗口、调整周期与下一轮主升机会。",
    "nodes": [
        {
            "date": "07月20日",
            "title": "行情收尾",
            "text": "科技行情处于本轮收尾阶段，重点观察高位回撤与强势股承接。",
            "state": "current",
            "evidence": "",
            "source_video_ids": [],
        },
        {
            "date": "07月23—25日",
            "title": "反弹窗口（预计）",
            "text": "本周可能出现一次反弹，作为时间窗口观察，不把预测当成确定结果。",
            "state": "forecast",
            "evidence": "",
            "source_video_ids": [],
        },
        {
            "date": "07月下旬—09月",
            "title": "阴跌阶段（预计）",
            "text": "反弹后可能进入震荡阴跌阶段，需要控制追高风险并等待结构稳定。",
            "state": "weak",
            "evidence": "",
            "source_video_ids": [],
        },
        {
            "date": "11月",
            "title": "主升浪窗口（预期）",
            "text": "真正的主升浪可能在11月展开，后续结合新增观点持续验证和调整。",
            "state": "target",
            "evidence": "",
            "source_video_ids": [],
        },
    ],
}

MAINLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "update_required": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "status": {"type": "string"},
        "as_of": {"type": "string"},
        "summary": {"type": "string"},
        "change_summary": {"type": "string"},
        "nodes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "state": {
                        "type": "string",
                        "enum": sorted(NODE_STATES),
                    },
                    "evidence": {"type": "string"},
                    "source_video_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": [
                    "date",
                    "title",
                    "text",
                    "state",
                    "evidence",
                    "source_video_ids",
                ],
                "additionalProperties": False,
            },
        },
        "sources": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "video_id": {"type": "integer"},
                    "evidence": {"type": "string"},
                    "impact": {"type": "string"},
                },
                "required": ["video_id", "evidence", "impact"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "relevant",
        "update_required",
        "confidence",
        "reason",
        "status",
        "as_of",
        "summary",
        "change_summary",
        "nodes",
        "sources",
    ],
    "additionalProperties": False,
}

MAINLINE_INSTRUCTIONS = """你是“模型先生智能体”的投资主线版本编辑器。

目标：判断新增视频是否会改变“科技投资路线”，并生成可供人工确认的结构化草稿。

证据边界：
- 只允许使用输入中的模型先生视频标题、视频原文、模型先生本人回复和当前路线。
- 不得使用外部知识，不得把常识或你的推测写成模型先生观点。
- 用户解读、感悟和普通用户评论不能作为模型先生原始观点。

更新规则：
1. 先判断新视频是否真正涉及科技股、科创板、芯片、半导体、算力或相关科技投资节奏。
2. 只有新视频对行情阶段、时间窗口、风险、操作节奏或旧判断产生实质影响时，update_required 才为 true。
3. 新观点明确修正旧观点时可以更新，但必须在 change_summary 中说明修正关系；不能悄悄删除旧预测。
4. 区分已经发生、当前判断、观察条件和未来预测。等待验证的内容使用 watch 或 forecast，不能写成事实。
5. “下周、今天、本周”等相对时间必须以视频 published_at 为锚点，同时保留原话的不确定性。
6. 每个节点必须附最短原文证据及来源 video_id。不得伪造来源编号。
7. 如果不相关或无需更新，返回当前路线原样，update_required=false。
8. 节点按时间或逻辑顺序排列，最多6个，语言简洁、使用简体中文。
9. A浪、B浪、C浪等波浪标签，只有在模型先生视频原文或本人回复明确出现时才能保留；不得根据普通用户评论或通用理论自行补充。
10. 如果输入包含 external_suggestion，它只是用户认可的讨论线索，不是模型先生原话。只有 recent_model_mr_videos 中的原文或本人回复能够支持时，才能更新正式节点；不受原文支持的内容不得进入草稿。
"""


class InvestmentMainlineService:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self._client_factory = client_factory
        # A cloned agent starts without any creator-specific investment thesis.
        # New mainlines must come from the new creator's confirmed material.
        # Keep the migrated Model Mr timeline only for that legacy account.
        if str(getattr(settings, "source_account_name", "") or "").strip() == "模型先生":
            self._seed_default()

    def list_mainlines(self) -> dict[str, Any]:
        with self.storage.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM investment_mainlines ORDER BY number, id"
            ).fetchall()
            items = []
            for row in rows:
                line = self._line_dict(row)
                drafts = conn.execute(
                    """
                    SELECT d.*, v.title AS source_title, v.published_at AS source_published_at,
                           COALESCE(NULLIF(t.active_title, ''), v.title) AS active_source_title
                    FROM investment_mainline_drafts d
                    JOIN videos v ON v.id = d.source_video_id
                    LEFT JOIN video_titles t ON t.video_id = v.id
                    WHERE d.mainline_id = ? AND d.status = 'pending'
                    ORDER BY d.created_at DESC
                    """,
                    (line["id"],),
                ).fetchall()
                line["pending_drafts"] = [self._draft_dict(item) for item in drafts]
                version = conn.execute(
                    """
                    SELECT version_number, change_summary, model, created_at
                    FROM investment_mainline_versions
                    WHERE mainline_id = ?
                    ORDER BY version_number DESC
                    LIMIT 1
                    """,
                    (line["id"],),
                ).fetchone()
                line["version"] = dict(version) if version else None
                items.append(line)
        return {
            "items": items,
            "pending_count": sum(len(item["pending_drafts"]) for item in items),
            "model": DEFAULT_MAINLINE_MODEL,
            "openai_enabled": openai_api_is_enabled(self.settings),
            "update_mode": "manual",
        }

    def analyze_latest(self) -> dict[str, Any]:
        video_id = self._latest_video_with_text()
        if not video_id:
            raise ValueError("还没有可用于更新投资主线的视频文字。")
        return self.analyze_video(video_id)

    def analyze_latest_async(self) -> bool:
        """Compatibility guard: investment mainlines are intentionally manual-only."""
        return False

    def analyze_video_async(self, video_id: int) -> bool:
        """Compatibility guard: video updates never spend AI tokens automatically."""
        _ = video_id
        return False

    def analyze_video(self, video_id: int) -> dict[str, Any]:
        require_openai_api_enabled(self.settings, "投资主线分析")
        line = self._get_line(TECHNOLOGY_LINE_KEY)
        material = self._material(line, video_id)
        # Deduplicate by the actual source video content. Confirming a draft changes
        # the current mainline, but must not bill for analyzing the same unchanged
        # video again on the next server restart.
        source_hash = self._source_hash(material["new_video"])
        cached = self._draft_by_hash(source_hash)
        if cached:
            return {"draft": cached, "cached": True}

        try:
            proposal, response_id = self._generate_proposal(material)
            status = (
                "pending"
                if proposal["relevant"] and proposal["update_required"]
                else "no_change"
            )
            draft_id = self._save_draft(
                line_id=int(line["id"]),
                video_id=video_id,
                source_hash=source_hash,
                proposal=proposal,
                status=status,
                response_id=response_id,
            )
            return {"draft": self.get_draft(draft_id), "cached": False}
        except Exception as exc:
            self._save_error_draft(
                line_id=int(line["id"]),
                video_id=video_id,
                source_hash=source_hash,
                error=str(exc),
            )
            raise

    def analyze_external_content(
        self,
        *,
        content: str,
        source_label: str = "外部AI内容",
        context: str = "",
    ) -> dict[str, Any]:
        require_openai_api_enabled(self.settings, "投资主线分析")
        text = str(content or "").strip()
        if not text:
            raise ValueError("请先输入需要核验的AI内容。")
        if len(text) > 16000:
            raise ValueError("输入内容过长，请保留与科技投资路线相关的部分。")
        label = str(source_label or "外部AI内容").strip()[:80] or "外部AI内容"
        line = self._get_line(TECHNOLOGY_LINE_KEY)
        video_id = self._latest_video_with_text()
        if not video_id:
            raise ValueError("知识库中还没有可用于核验的模型先生视频原文。")
        material = self._material(line, video_id)
        material["external_suggestion"] = {
            "source_label": label,
            "context": str(context or "").strip()[:6000],
            "content": text,
            "instruction": "把这段内容仅作为待核验线索，不得当作模型先生原话。",
        }
        source_hash = self._source_hash(
            {
                "kind": "external_mainline_suggestion",
                "source_label": label,
                "content": text,
                "context": str(context or "").strip(),
                "line_updated_at": line["updated_at"],
            }
        )
        cached = self._draft_by_hash(source_hash)
        if cached:
            return {"draft": cached, "cached": True}

        try:
            proposal, response_id = self._generate_proposal(material)
            proposal["input_context"] = {
                "source_label": label,
                "content_excerpt": text[:1000],
                "context_excerpt": str(context or "").strip()[:600],
            }
            status = (
                "pending"
                if proposal["relevant"] and proposal["update_required"]
                else "no_change"
            )
            source_ids = [
                int(item["video_id"])
                for item in proposal.get("sources", [])
                if item.get("video_id")
            ]
            primary_video_id = source_ids[0] if source_ids else video_id
            draft_id = self._save_draft(
                line_id=int(line["id"]),
                video_id=primary_video_id,
                source_hash=source_hash,
                proposal=proposal,
                status=status,
                response_id=response_id,
            )
            return {"draft": self.get_draft(draft_id), "cached": False}
        except Exception as exc:
            self._save_error_draft(
                line_id=int(line["id"]),
                video_id=video_id,
                source_hash=source_hash,
                error=str(exc),
            )
            raise

    def _generate_proposal(
        self,
        material: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        response = self._client().responses.create(
            model=DEFAULT_MAINLINE_MODEL,
            reasoning={"effort": "medium"},
            instructions=MAINLINE_INSTRUCTIONS,
            input=json.dumps(material, ensure_ascii=False),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "investment_mainline_update",
                    "description": "基于模型先生原文生成的投资主线更新草稿",
                    "schema": MAINLINE_SCHEMA,
                    "strict": True,
                },
                "verbosity": "low",
            },
            max_output_tokens=3600,
            store=False,
        )
        raw = str(getattr(response, "output_text", "") or "").strip()
        if not raw:
            raise RuntimeError("AI没有返回投资主线分析结果。")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AI返回的投资主线格式无法解析，请重试。") from exc
        return self._clean_proposal(parsed, material), getattr(response, "id", None)

    def confirm_draft(self, draft_id: int) -> dict[str, Any]:
        with self.storage.connect() as conn:
            draft = conn.execute(
                "SELECT * FROM investment_mainline_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            if not draft:
                raise ValueError("投资主线草稿不存在。")
            if draft["status"] != "pending":
                raise ValueError("该草稿已经处理，不能重复确认。")
            proposal = from_json(draft["proposal_json"], {})
            if not proposal.get("update_required"):
                raise ValueError("该草稿没有需要发布的路线变化。")

            line_id = int(draft["mainline_id"])
            version_number = int(
                conn.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
                    FROM investment_mainline_versions
                    WHERE mainline_id = ?
                    """,
                    (line_id,),
                ).fetchone()[0]
            )
            updated_at = now_iso()
            conn.execute(
                """
                UPDATE investment_mainlines
                SET status = ?, as_of = ?, summary = ?, nodes_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    proposal["status"],
                    proposal["as_of"],
                    proposal["summary"],
                    to_json(proposal["nodes"]),
                    updated_at,
                    line_id,
                ),
            )
            cur = conn.execute(
                """
                INSERT INTO investment_mainline_versions (
                    mainline_id, version_number, status, as_of, summary, nodes_json,
                    change_summary, model, source_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    line_id,
                    version_number,
                    proposal["status"],
                    proposal["as_of"],
                    proposal["summary"],
                    to_json(proposal["nodes"]),
                    proposal["change_summary"],
                    draft["model"],
                    draft["source_hash"],
                    updated_at,
                ),
            )
            version_id = int(cur.lastrowid)
            for source in proposal.get("sources", []):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO investment_mainline_sources (
                        version_id, video_id, evidence, impact
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        int(source["video_id"]),
                        source["evidence"],
                        source["impact"],
                    ),
                )
            conn.execute(
                """
                UPDATE investment_mainline_drafts
                SET status = 'confirmed', reviewed_at = ?
                WHERE id = ?
                """,
                (updated_at, draft_id),
            )
            conn.execute(
                """
                UPDATE investment_mainline_drafts
                SET status = 'superseded', reviewed_at = ?
                WHERE mainline_id = ? AND status = 'pending' AND id <> ?
                """,
                (updated_at, line_id, draft_id),
            )
        return {
            "confirmed": True,
            "draft_id": draft_id,
            "version_id": version_id,
            "version_number": version_number,
            "mainlines": self.list_mainlines(),
        }

    def reject_draft(self, draft_id: int) -> dict[str, Any]:
        with self.storage.connect() as conn:
            row = conn.execute(
                "SELECT status FROM investment_mainline_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            if not row:
                raise ValueError("投资主线草稿不存在。")
            if row["status"] != "pending":
                raise ValueError("该草稿已经处理。")
            conn.execute(
                """
                UPDATE investment_mainline_drafts
                SET status = 'rejected', reviewed_at = ?
                WHERE id = ?
                """,
                (now_iso(), draft_id),
            )
        return {"rejected": True, "draft_id": draft_id, "mainlines": self.list_mainlines()}

    def get_draft(self, draft_id: int) -> dict[str, Any]:
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT d.*, v.title AS source_title, v.published_at AS source_published_at,
                       COALESCE(NULLIF(t.active_title, ''), v.title) AS active_source_title
                FROM investment_mainline_drafts d
                JOIN videos v ON v.id = d.source_video_id
                LEFT JOIN video_titles t ON t.video_id = v.id
                WHERE d.id = ?
                """,
                (draft_id,),
            ).fetchone()
        if not row:
            raise ValueError("投资主线草稿不存在。")
        return self._draft_dict(row)

    def _seed_default(self) -> None:
        with self.storage.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM investment_mainlines WHERE line_key = ?",
                (TECHNOLOGY_LINE_KEY,),
            ).fetchone()
            if existing:
                return
            created_at = now_iso()
            cur = conn.execute(
                """
                INSERT INTO investment_mainlines (
                    line_key, number, title, status, as_of, summary,
                    nodes_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_TECHNOLOGY_LINE["line_key"],
                    DEFAULT_TECHNOLOGY_LINE["number"],
                    DEFAULT_TECHNOLOGY_LINE["title"],
                    DEFAULT_TECHNOLOGY_LINE["status"],
                    DEFAULT_TECHNOLOGY_LINE["as_of"],
                    DEFAULT_TECHNOLOGY_LINE["summary"],
                    to_json(DEFAULT_TECHNOLOGY_LINE["nodes"]),
                    created_at,
                    created_at,
                ),
            )
            line_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO investment_mainline_versions (
                    mainline_id, version_number, status, as_of, summary,
                    nodes_json, change_summary, model, source_hash, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    line_id,
                    DEFAULT_TECHNOLOGY_LINE["status"],
                    DEFAULT_TECHNOLOGY_LINE["as_of"],
                    DEFAULT_TECHNOLOGY_LINE["summary"],
                    to_json(DEFAULT_TECHNOLOGY_LINE["nodes"]),
                    "由原页面静态时间线迁移为可追溯的数据库版本。",
                    "manual-seed",
                    "seed-technology-v1",
                    created_at,
                ),
            )

    def _latest_video_with_text(self) -> int | None:
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT v.id
                FROM videos v
                LEFT JOIN content_originals co ON co.content_id = v.id
                LEFT JOIN video_notes n ON n.video_id = v.id AND n.note_type = 'video_text'
                WHERE v.author = ?
                  AND LENGTH(TRIM(COALESCE(co.original_text, n.text, ''))) > 0
                ORDER BY COALESCE(v.published_at, v.discovered_at) DESC, v.id DESC
                LIMIT 1
                """,
                (self.settings.source_account_name,),
            ).fetchone()
        return int(row["id"]) if row else None

    def _get_line(self, line_key: str) -> dict[str, Any]:
        with self.storage.connect() as conn:
            row = conn.execute(
                "SELECT * FROM investment_mainlines WHERE line_key = ?",
                (line_key,),
            ).fetchone()
        if not row:
            raise ValueError("投资主线不存在。")
        return self._line_dict(row)

    def _material(self, line: dict[str, Any], video_id: int) -> dict[str, Any]:
        new_video = self._video_material(video_id)
        if not new_video["video_text"]:
            raise ValueError("这条视频还没有视频文字，不能更新投资主线。")
        with self.storage.connect() as conn:
            rows = conn.execute(
                """
                SELECT v.id
                FROM videos v
                LEFT JOIN content_originals co ON co.content_id = v.id
                LEFT JOIN video_notes n ON n.video_id = v.id AND n.note_type = 'video_text'
                WHERE v.author = ?
                  AND LENGTH(TRIM(COALESCE(co.original_text, n.text, ''))) > 0
                ORDER BY COALESCE(v.published_at, v.discovered_at) DESC, v.id DESC
                LIMIT 6
                """,
                (self.settings.source_account_name,),
            ).fetchall()
        recent = [self._video_material(int(row["id"])) for row in rows]
        return {
            "current_line": {
                "line_key": line["line_key"],
                "title": line["title"],
                "status": line["status"],
                "as_of": line["as_of"],
                "summary": line["summary"],
                "nodes": line["nodes"],
                "chart": line["chart"],
                "updated_at": line["updated_at"],
            },
            "new_video": new_video,
            "recent_model_mr_videos": recent,
        }

    def _video_material(self, video_id: int) -> dict[str, Any]:
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT v.id, v.title, v.published_at, v.discovered_at,
                       COALESCE(NULLIF(t.active_title, ''), v.title) AS active_title,
                       COALESCE(co.original_text, n.text) AS video_text,
                       k.text AS keyword_note
                FROM videos v
                LEFT JOIN video_titles t ON t.video_id = v.id
                LEFT JOIN content_originals co ON co.content_id = v.id
                LEFT JOIN video_notes n ON n.video_id = v.id AND n.note_type = 'video_text'
                LEFT JOIN video_notes k ON k.video_id = v.id AND k.note_type = 'ai_keywords'
                WHERE v.id = ?
                """,
                (video_id,),
            ).fetchone()
            comments = conn.execute(
                """
                SELECT c.text
                FROM comments c
                JOIN videos v ON v.id = c.video_id
                WHERE c.video_id = ? AND c.author = v.author
                ORDER BY c.captured_at DESC
                LIMIT 5
                """,
                (video_id,),
            ).fetchall()
        if not row:
            raise ValueError("video not found")
        keyword_data = from_json(row["keyword_note"], {})
        keywords = keyword_data.get("keywords", []) if isinstance(keyword_data, dict) else []
        return {
            "video_id": int(row["id"]),
            "title": str(row["active_title"] or row["title"] or "")[:160],
            "published_at": row["published_at"] or row["discovered_at"],
            "video_text": str(row["video_text"] or "")[:5000],
            "keywords": [str(item)[:60] for item in keywords[:15]],
            "model_mr_replies": [str(item["text"])[:1200] for item in comments],
        }

    @staticmethod
    def _source_hash(material: dict[str, Any]) -> str:
        raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _draft_by_hash(self, source_hash: str) -> dict[str, Any] | None:
        with self.storage.connect() as conn:
            row = conn.execute(
                "SELECT id FROM investment_mainline_drafts WHERE source_hash = ?",
                (source_hash,),
            ).fetchone()
        return self.get_draft(int(row["id"])) if row else None

    def _save_draft(
        self,
        *,
        line_id: int,
        video_id: int,
        source_hash: str,
        proposal: dict[str, Any],
        status: str,
        response_id: str | None,
    ) -> int:
        with self.storage.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO investment_mainline_drafts (
                    mainline_id, source_video_id, source_hash, relevance,
                    update_required, reason, proposal_json, model, response_id,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    line_id,
                    video_id,
                    source_hash,
                    float(proposal["confidence"]),
                    int(bool(proposal["update_required"])),
                    proposal["reason"],
                    to_json(proposal),
                    DEFAULT_MAINLINE_MODEL,
                    response_id,
                    status,
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def _save_error_draft(
        self,
        *,
        line_id: int,
        video_id: int,
        source_hash: str,
        error: str,
    ) -> None:
        with self.storage.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO investment_mainline_drafts (
                    mainline_id, source_video_id, source_hash, relevance,
                    update_required, reason, proposal_json, model, status,
                    error, created_at
                ) VALUES (?, ?, ?, 0, 0, '', '{}', ?, 'error', ?, ?)
                """,
                (
                    line_id,
                    video_id,
                    source_hash,
                    DEFAULT_MAINLINE_MODEL,
                    error[:1000],
                    now_iso(),
                ),
            )

    def _clean_proposal(
        self,
        value: dict[str, Any],
        material: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_ids = {
            int(item["video_id"])
            for item in material["recent_model_mr_videos"]
        }
        allowed_ids.add(int(material["new_video"]["video_id"]))
        current = material["current_line"]
        nodes = []
        for raw in value.get("nodes", [])[:6]:
            if not isinstance(raw, dict):
                continue
            source_ids = []
            for source_id in raw.get("source_video_ids", []):
                try:
                    numeric = int(source_id)
                except (TypeError, ValueError):
                    continue
                if numeric in allowed_ids and numeric not in source_ids:
                    source_ids.append(numeric)
            state = str(raw.get("state") or "watch")
            nodes.append(
                {
                    "date": str(raw.get("date") or "")[:60],
                    "title": str(raw.get("title") or "")[:100],
                    "text": str(raw.get("text") or "")[:500],
                    "state": state if state in NODE_STATES else "watch",
                    "evidence": str(raw.get("evidence") or "")[:600],
                    "source_video_ids": source_ids,
                }
            )
        if not nodes:
            nodes = current["nodes"]

        sources = []
        seen_sources: set[int] = set()
        for raw in value.get("sources", [])[:8]:
            if not isinstance(raw, dict):
                continue
            try:
                video_id = int(raw.get("video_id"))
            except (TypeError, ValueError):
                continue
            if video_id not in allowed_ids or video_id in seen_sources:
                continue
            seen_sources.add(video_id)
            sources.append(
                {
                    "video_id": video_id,
                    "evidence": str(raw.get("evidence") or "")[:800],
                    "impact": str(raw.get("impact") or "")[:500],
                }
            )

        relevant = bool(value.get("relevant"))
        update_required = bool(value.get("update_required")) and relevant
        as_of = str(value.get("as_of") or current["as_of"])[:80]
        if update_required and not as_of.startswith("更新至"):
            as_of = f"更新至 {as_of}"
        return {
            "relevant": relevant,
            "update_required": update_required,
            "confidence": max(0.0, min(float(value.get("confidence") or 0), 1.0)),
            "reason": str(value.get("reason") or "")[:1000],
            "status": str(value.get("status") or current["status"])[:120],
            "as_of": as_of,
            "summary": str(value.get("summary") or current["summary"])[:500],
            "change_summary": str(value.get("change_summary") or "")[:1200],
            "nodes": nodes,
            "sources": sources,
        }

    @staticmethod
    def _line_dict(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["nodes"] = from_json(item.pop("nodes_json"), [])
        item["chart"] = build_qualitative_chart(item["nodes"])
        return item

    @staticmethod
    def _draft_dict(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["proposal"] = from_json(item.pop("proposal_json"), {})
        proposal = item["proposal"]
        if isinstance(proposal, dict):
            proposal["chart"] = build_qualitative_chart(proposal.get("nodes", []))
        if "active_source_title" in item:
            item["source_title"] = item.pop("active_source_title") or item.get("source_title")
        return item

    def _client(self) -> Any:
        if self._client_factory:
            return self._client_factory(str(self.settings.openai_api_key))
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("缺少 openai 依赖，请运行项目已有的依赖安装流程。") from exc
        return OpenAI(api_key=self.settings.openai_api_key)


def read_confirmed_mainlines(database_path: Path) -> dict[str, Any]:
    """Read only the currently confirmed investment mainline versions."""
    resolved_path = database_path.resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{resolved_path}")

    conn = sqlite3.connect(
        f"{resolved_path.as_uri()}?mode=ro",
        uri=True,
        timeout=2.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        rows = conn.execute(
            "SELECT * FROM investment_mainlines ORDER BY number, id"
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            line = InvestmentMainlineService._line_dict(row)
            version = conn.execute(
                """
                SELECT id, version_number, change_summary, model, created_at
                FROM investment_mainline_versions
                WHERE mainline_id = ?
                ORDER BY version_number DESC
                LIMIT 1
                """,
                (line["id"],),
            ).fetchone()
            if version:
                version_data = dict(version)
                version_id = int(version_data.pop("id"))
                sources = conn.execute(
                    """
                    SELECT
                        s.video_id,
                        COALESCE(NULLIF(t.active_title, ''), v.title) AS title,
                        v.published_at,
                        s.evidence,
                        s.impact
                    FROM investment_mainline_sources s
                    JOIN videos v ON v.id = s.video_id
                    LEFT JOIN video_titles t ON t.video_id = v.id
                    WHERE s.version_id = ?
                    ORDER BY s.video_id
                    """,
                    (version_id,),
                ).fetchall()
                version_data["sources"] = [dict(source) for source in sources]
                line["version"] = version_data
            else:
                line["version"] = None
            items.append(line)
        return {
            "items": items,
            "count": len(items),
            "scope": "confirmed_mainlines_only",
            "update_mode": "manual",
            "read_only": True,
            "evidence_note": (
                "投资主线与K线图是基于已确认资料形成的阶段性推演；"
                "预测和观察节点不是事实保证，应结合节点状态、原文证据和来源视频理解。"
            ),
        }
    finally:
        conn.close()


def search_confirmed_mainlines(
    database_path: Path,
    question: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return compact mainline hits for backward-compatible knowledge search."""
    query = re.sub(r"\s+", "", str(question or "")).lower()
    if not query:
        return []

    triggers = (
        "投资主线",
        "科技路线",
        "行情路线",
        "k线",
        "推演",
        "预测演练",
        "阶段节点",
        "二次探底",
        "筑底",
        "反弹",
        "a浪",
        "b浪",
        "c浪",
    )
    data = read_confirmed_mainlines(database_path)
    hits: list[dict[str, Any]] = []
    for line in data["items"]:
        searchable = re.sub(
            r"\s+",
            "",
            json.dumps(
                {
                    "title": line.get("title"),
                    "status": line.get("status"),
                    "summary": line.get("summary"),
                    "nodes": line.get("nodes"),
                },
                ensure_ascii=False,
            ),
        ).lower()
        matched_triggers = [trigger for trigger in triggers if trigger in query]
        title = str(line.get("title") or "")
        title_terms = [term for term in ("科技", "算力", "半导体", "芯片") if term in query]
        if not matched_triggers and not any(term in searchable for term in title_terms):
            continue

        score = 20.0 + (4.0 * len(matched_triggers)) + (3.0 * len(title_terms))
        nodes = list(line.get("nodes") or [])
        source_video_ids = sorted(
            {
                int(video_id)
                for node in nodes
                for video_id in (node.get("source_video_ids") or [])
                if str(video_id).isdigit()
            }
        )
        hits.append(
            {
                "record_id": f"mainline:{line['line_key']}",
                "title": title,
                "title_kind": "confirmed_investment_mainline",
                "summary": str(line.get("summary") or ""),
                "source": {
                    "type": "confirmed_investment_mainline",
                    "author": "模型先生智能体",
                    "source_video_ids": source_video_ids,
                    "url": "",
                },
                "published_at": line.get("updated_at"),
                "matched_in": ["investment_mainline"],
                "matches": [
                    {
                        "chunk_id": f"mainline:{line['line_key']}",
                        "content_type": "investment_mainline",
                        "content_label": "已确认投资主线与K线推演",
                        "quote": str(line.get("status") or ""),
                        "context": str(line.get("summary") or ""),
                        "evidence_priority": 2,
                        "relevance_score": round(score, 3),
                        "source_reference": f"investment_mainlines:{line['id']}",
                    }
                ],
                "relevance_score": round(score, 3),
            }
        )
    return sorted(
        hits,
        key=lambda item: (
            float(item["relevance_score"]),
            str(item["published_at"] or ""),
        ),
        reverse=True,
    )[: max(1, min(int(limit), 30))]


def get_confirmed_mainline(database_path: Path, record_id: str) -> dict[str, Any]:
    """Read one confirmed mainline by the mainline:<line_key> record id."""
    reference = str(record_id or "").strip()
    if not reference.startswith("mainline:"):
        raise ValueError("投资主线记录编号必须是 mainline:<line_key>。")
    line_key = reference.split(":", 1)[1].strip()
    if not line_key:
        raise ValueError("投资主线记录编号缺少 line_key。")

    data = read_confirmed_mainlines(database_path)
    line = next(
        (item for item in data["items"] if item.get("line_key") == line_key),
        None,
    )
    if not line:
        raise ValueError(f"没有找到投资主线记录：{reference}")
    return {
        "record_id": reference,
        "record_type": "confirmed_investment_mainline",
        "title": line.get("title"),
        "date": line.get("updated_at"),
        "source": {
            "type": "local_confirmed_mainline",
            "line_key": line_key,
        },
        "content_sections": {
            "current_status": line.get("status"),
            "summary": line.get("summary"),
            "timeline_nodes": line.get("nodes"),
            "qualitative_chart": line.get("chart"),
            "version": line.get("version"),
        },
        "read_only": True,
        "attribution_rules": [
            "该记录是基于已确认资料形成的阶段性投资主线，不等同于单条模型先生原话。",
            "节点中的 evidence 和 source_video_ids 用于回到原始视频核验。",
            "watch 和 forecast 节点属于观察或预测，不得表述为已经发生的事实。",
        ],
    }
