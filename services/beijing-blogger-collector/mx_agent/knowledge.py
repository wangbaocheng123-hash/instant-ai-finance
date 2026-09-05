from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .keyword_taxonomy import (
    KEYWORD_CATEGORIES,
    KEYWORD_SCHEMA_VERSION,
    normalize_keyword,
    normalize_keyword_categories,
)
from .knowledge_index import CONTENT_LABELS, build_model_comment_threads
from .settings import DATA_DIR


DATABASE_PATH = DATA_DIR / "mx_agent.sqlite3"

_QUESTION_PHRASES = (
    "模型先生",
    "请帮我",
    "请问",
    "怎么看待",
    "怎么看",
    "如何看待",
    "如何看",
    "有什么看法",
    "是什么观点",
    "相关资料",
    "告诉我",
    "谈一谈",
    "谈谈",
    "为什么",
    "是否认为",
    "你认为",
    "他认为",
    "关于",
)

_QUERY_SUFFIXES = (
    "有哪些观点",
    "有什么观点",
    "有什么区别",
    "是什么逻辑",
    "是什么",
    "怎么样",
    "可以买吗",
    "应该买吗",
    "可以卖吗",
    "应该卖吗",
    "吗",
    "呢",
)

_ALIASES = {
    "AI": ("人工智能", "算力"),
    "人工智能": ("AI",),
    "半导体": ("芯片", "集成电路"),
    "芯片": ("半导体", "集成电路"),
    "科创芯片": ("科创板", "芯片"),
    "算力": ("人工智能", "AI"),
    "中特估": ("中国特色综合估值体系",),
}

_RECENCY_MARKERS = (
    "最新发布",
    "最近发布",
    "刚刚发布",
    "刚发布",
    "新发布",
    "最新视频",
    "最新作品",
    "最新观点",
    "今天",
    "今日",
    "最新",
    "最近",
    "近期",
    "刚刚",
)

_RECENCY_QUERY_NOISE = (
    "最新发布的",
    "最近发布的",
    "刚刚发布的",
    "今天发布的",
    "今日发布的",
    "最新的视频",
    "最新的作品",
    "最新的观点",
    "今天的视频",
    "今天的观点",
    "今日的视频",
    "今日的观点",
    "最新的",
    "最近的",
    "近期的",
    "今天的",
    "今日的",
    "最新发布",
    "最近发布",
    "刚刚发布",
    "刚发布",
    "新发布",
    "最新视频",
    "最新作品",
    "最新观点",
    "说了什么",
    "讲了什么",
    "说什么",
    "讲什么",
    "有什么",
    "今天",
    "今日",
    "最新",
    "最近",
    "近期",
    "刚刚",
    "视频",
    "作品",
    "观点",
    "内容",
    "发布",
    "更新",
)


def _from_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _snippet(text: str, length: int = 280) -> str:
    compact = _compact(text)
    if len(compact) <= length:
        return compact
    return compact[:length].rstrip() + "…"


def _search_terms(question: str) -> list[str]:
    cleaned = _compact(question)
    if not cleaned:
        raise ValueError("问题或关键词不能为空。")
    if len(cleaned) > 500:
        raise ValueError("问题或关键词不能超过 500 个字符。")

    reduced = cleaned
    for phrase in _QUESTION_PHRASES:
        reduced = reduced.replace(phrase, " ")
    for phrase in _QUERY_SUFFIXES:
        reduced = reduced.replace(phrase, " ")
    reduced = re.sub(r"[，。！？、；：,.!?;:()（）\[\]【】]+", " ", reduced)

    candidates: list[str] = []
    candidates.extend(re.findall(r"[A-Za-z][A-Za-z0-9.+_-]{1,30}", reduced))
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", reduced):
        parts = [part for part in re.split(r"(?:以及|或者|还是|和|与|及|的)", segment) if len(part) >= 2]
        candidates.extend(parts or [segment])
        if 2 <= len(segment) <= 20:
            candidates.append(segment)

    if not candidates:
        candidates.append(reduced.strip() or cleaned)

    expanded: list[str] = []
    for candidate in candidates:
        expanded.append(candidate)
        for canonical, aliases in _ALIASES.items():
            if canonical.casefold() in candidate.casefold():
                expanded.extend(aliases)

    terms: list[str] = []
    seen: set[str] = set()
    for candidate in expanded:
        term = _compact(candidate)
        key = term.casefold()
        if len(term) < 2 or key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) == 12:
            break
    return terms


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_query(terms: list[str]) -> str:
    phrases = []
    for term in terms:
        if len(term) < 3:
            continue
        escaped = term.replace('"', '""')
        phrases.append(f'"{escaped}"')
    return " OR ".join(phrases)


def _requested_date(question: str) -> str | None:
    text = _compact(question)
    if "今天" in text or "今日" in text:
        china_now = datetime.now(timezone(timedelta(hours=8)))
        return china_now.date().isoformat()
    match = re.search(r"(?P<year>20\d{2})[年./-](?P<month>\d{1,2})[月./-](?P<day>\d{1,2})日?", text)
    if not match:
        match = re.search(r"(?<!\d)(?P<month>\d{1,2})月(?P<day>\d{1,2})日?", text)
    if not match:
        return None
    year = int(match.groupdict().get("year") or datetime.now().year)
    try:
        return datetime(
            year,
            int(match.group("month")),
            int(match.group("day")),
        ).date().isoformat()
    except ValueError:
        return None


def _recency_intent(question: str) -> tuple[bool, str | None, str]:
    text = _compact(question)
    requested_date = _requested_date(text)
    is_recency = requested_date is not None or any(marker in text for marker in _RECENCY_MARKERS)
    if not is_recency:
        return False, None, text

    topic = text
    topic = re.sub(
        r"(?:只|仅)(?:需(?:要)?|要)?(?:列出|返回|显示)[^，。！？；,.!?;]*",
        " ",
        topic,
    )
    topic = re.sub(
        r"(?:不要|别|无需)(?:沿用|引用|使用)[^，。！？；,.!?;]*",
        " ",
        topic,
    )
    topic = re.sub(r"(?:请)?(?:重新)?(?:查询|检索|搜索|查找)", " ", topic)
    topic = re.sub(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?", " ", topic)
    topic = re.sub(r"(?<!\d)\d{1,2}月\d{1,2}日?", " ", topic)
    for phrase in sorted(
        (*_RECENCY_QUERY_NOISE, *_QUESTION_PHRASES, *_QUERY_SUFFIXES),
        key=len,
        reverse=True,
    ):
        topic = topic.replace(phrase, " ")
    topic = re.sub(r"(?:[一二两三四五六七八九十]|\d+)个", " ", topic)
    topic = re.sub(r"[，。！？、；：,.!?;:()（）\[\]【】]+", " ", topic)
    topic = _compact(topic)
    topic = re.sub(r"^(?:关于|针对|对于|请|对|就|的)+", "", topic)
    topic = re.sub(r"(?:呢|吗|吧|的)+$", "", topic)
    return True, requested_date, _compact(topic)


def _requested_account(question: str) -> str | None:
    text = _compact(question)
    if "模型哥看世界" in text:
        return "模型哥看世界"
    if "模型先生" in text:
        return "模型先生"
    return None


def _published_on_date(value: Any, requested_date: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text.startswith(requested_date)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    china_time = parsed.astimezone(timezone(timedelta(hours=8)))
    return china_time.date().isoformat() == requested_date


def _row_score(row: sqlite3.Row, terms: list[str]) -> float:
    title = str(row["retrieval_title"] or "").casefold()
    keywords = str(row["keywords"] or "").casefold()
    content = str(row["content"] or "").casefold()
    context = str(row["context"] or "").casefold()
    score = float(row["evidence_priority"] or 0) / 50.0
    for term in terms:
        needle = term.casefold()
        if needle in title:
            score += 8.0
        if needle in keywords:
            score += 6.0
        if needle in content:
            score += 4.0
        if needle in context:
            score += 1.5
    rank = row["fts_rank"] if "fts_rank" in row.keys() else None
    if rank is not None:
        score += min(12.0, max(0.0, -float(rank) * 2.0))
    return score


class KnowledgeReader:
    """Read-only retrieval from the original database and its derived search index."""

    def __init__(self, database_path: Path = DATABASE_PATH):
        self.database_path = database_path.resolve()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self.database_path.is_file():
            raise FileNotFoundError(f"数据库不存在：{self.database_path}")
        uri = f"{self.database_path.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _has_table(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table,),
        ).fetchone() is not None

    def search(self, question: str, limit: int = 10) -> dict[str, Any]:
        recency_intent, requested_date, topic = _recency_intent(question)
        terms = _search_terms(topic if recency_intent else question) if topic else []
        safe_limit = max(1, min(int(limit), 30))
        candidate_limit = max(50, safe_limit * 12)
        requested_account = _requested_account(question)

        with self._connect() as conn:
            if self._has_table(conn, "knowledge_chunks") and self._has_table(conn, "knowledge_chunks_fts"):
                if recency_intent and not terms:
                    rows = self._search_recent_index(
                        conn,
                        candidate_limit,
                        requested_date=requested_date,
                        account=requested_account,
                    )
                else:
                    rows = self._search_index(conn, terms, candidate_limit)
            else:
                rows = []
            thought_rows = (
                []
                if recency_intent
                else self._search_investment_thoughts(conn, terms, candidate_limit)
            )
        if requested_date and terms:
            rows = [
                row
                for row in rows
                if _published_on_date(row["video_published_at"], requested_date)
            ]
        if requested_account:
            rows = [
                row
                for row in rows
                if str(row["video_author"] or "") == requested_account
            ]

        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            video_id = int(row["video_id"])
            score = _row_score(row, terms)
            match = {
                "chunk_id": f"chunk:{row['id']}",
                "content_type": row["source_type"],
                "content_label": CONTENT_LABELS.get(row["source_type"], row["source_type"]),
                "quote": _snippet(row["content"]),
                "context": _snippet(row["context"], 220),
                "evidence_priority": int(row["evidence_priority"]),
                "relevance_score": round(score, 3),
                "source_reference": f"{row['source_table']}:{row['source_id']}",
            }
            item = grouped.setdefault(
                video_id,
                {
                    "record_id": f"video:{video_id}",
                    "title": row["retrieval_title"],
                    "title_kind": row["title_kind"],
                    "summary": _snippet(row["content"]),
                    "source": {
                        "type": row["video_source"],
                        "source_video_id": row["source_video_id"],
                        "author": row["video_author"],
                        "url": row["source_url"],
                    },
                    "published_at": row["video_published_at"],
                    "matched_in": [],
                    "matches": [],
                    "relevance_score": score,
                },
            )
            item["relevance_score"] = max(float(item["relevance_score"]), score)
            if row["source_type"] not in item["matched_in"]:
                item["matched_in"].append(row["source_type"])
            item["matches"].append(match)

        for row in thought_rows:
            video_id = int(row["video_id"])
            score = 36.0
            for term in terms:
                needle = term.casefold()
                if needle in str(row["title"] or "").casefold():
                    score += 10.0
                if needle in str(row["category_name"] or "").casefold():
                    score += 12.0
                if needle in str(row["parent_category_name"] or "").casefold():
                    score += 10.0
                if needle in str(row["original_text"] or "").casefold():
                    score += 6.0
            item = grouped.setdefault(
                video_id,
                {
                    "record_id": f"video:{video_id}",
                    "title": row["title"],
                    "title_kind": "source",
                    "summary": _snippet(row["original_text"] or row["title"]),
                    "source": {
                        "type": row["video_source"],
                        "source_video_id": row["source_video_id"],
                        "author": row["video_author"],
                        "url": row["source_url"],
                    },
                    "published_at": row["video_published_at"],
                    "matched_in": [],
                    "matches": [],
                    "investment_categories": [],
                    "relevance_score": round(score, 3),
                },
            )
            item.setdefault("investment_categories", [])
            category_path = " › ".join(
                value for value in [row["parent_category_name"], row["category_name"]] if value
            )
            if category_path not in item["investment_categories"]:
                item["investment_categories"].append(category_path)
            if "investment_classification" not in item["matched_in"]:
                item["matched_in"].append("investment_classification")
            item["matches"].append(
                {
                    "chunk_id": f"investment-link:{row['link_id']}",
                    "content_type": "investment_classification",
                    "content_label": "投资思路分类视频",
                    "quote": _snippet(row["original_text"] or row["title"]),
                    "context": f"已人工归入：{category_path}",
                    "evidence_priority": 95,
                    "relevance_score": round(score, 3),
                    "source_reference": f"investment_thought_video_links:{row['link_id']}",
                }
            )
            item["relevance_score"] = max(float(item["relevance_score"]), score)

        candidates = list(grouped.values())
        if recency_intent:
            items = sorted(
                candidates,
                key=lambda item: (
                    str(item["published_at"] or ""),
                    float(item["relevance_score"]),
                ),
                reverse=True,
            )[:safe_limit]
        else:
            items = sorted(
                candidates,
                key=lambda item: (
                    float(item["relevance_score"]),
                    str(item["published_at"] or ""),
                ),
                reverse=True,
            )[:safe_limit]
        for item in items:
            item["matches"] = sorted(
                item["matches"],
                key=lambda match: (match["relevance_score"], match["evidence_priority"]),
                reverse=True,
            )[:3]
            item["summary"] = item["matches"][0]["quote"] if item["matches"] else item["summary"]
            item["relevance_score"] = round(float(item["relevance_score"]), 3)

        return {
            "question": question,
            "search_terms": terms,
            "count": len(items),
            "items": items,
            "retrieval": (
                "recent_knowledge_index"
                if recency_intent
                else "incremental_fts5_trigram"
            ),
            "query_mode": "latest" if recency_intent else "relevance",
            "requested_date": requested_date,
            "latest_available_at": items[0]["published_at"] if items else None,
            "evidence_note": (
                "投资思路分类是人工确认的视频索引，分类只建立关联、不复制内容；"
                "视频原文和账号作者本人回复属于原始观点，用户解读感悟属于二次理解，"
                "不得冒充博主原话。"
            ),
        }

    def _search_investment_thoughts(
        self,
        conn: sqlite3.Connection,
        terms: list[str],
        candidate_limit: int,
    ) -> list[sqlite3.Row]:
        if not terms or not self._has_table(conn, "investment_thought_video_links"):
            return []
        predicates = " OR ".join(
            """(
                COALESCE(NULLIF(vt.active_title, ''), v.title, '') LIKE ? ESCAPE '\\'
                OR c.name LIKE ? ESCAPE '\\'
                OR c.description LIKE ? ESCAPE '\\'
                OR p.name LIKE ? ESCAPE '\\'
                OR p.description LIKE ? ESCAPE '\\'
                OR COALESCE(co.original_text, '') LIKE ? ESCAPE '\\'
                OR EXISTS (
                    SELECT 1 FROM content_keywords ck
                    WHERE ck.content_id = v.id AND ck.keyword LIKE ? ESCAPE '\\'
                )
            )"""
            for _ in terms
        )
        patterns = [pattern for term in terms for pattern in [_like_pattern(term)] * 7]
        return conn.execute(
            f"""
            SELECT l.id AS link_id, l.video_id, l.created_at AS linked_at,
                   c.name AS category_name, c.slug AS category_slug,
                   p.name AS parent_category_name, p.slug AS parent_category_slug,
                   COALESCE(NULLIF(vt.active_title, ''), NULLIF(v.title, '')) AS title,
                   COALESCE(co.original_text, '') AS original_text,
                   v.source AS video_source, v.source_video_id,
                   v.author AS video_author, v.url AS source_url,
                   COALESCE(v.published_at, v.discovered_at) AS video_published_at
            FROM investment_thought_video_links l
            JOIN investment_thought_categories c ON c.id = l.category_id
            LEFT JOIN investment_thought_categories p ON p.id = c.parent_id
            JOIN videos v ON v.id = l.video_id
            LEFT JOIN video_titles vt ON vt.video_id = v.id
            LEFT JOIN content_originals co ON co.content_id = v.id
            WHERE {predicates}
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT ?
            """,
            (*patterns, candidate_limit),
        ).fetchall()

    def search_video_originals_by_keywords(
        self,
        keywords: list[str] | None = None,
        *,
        categories: list[str] | None = None,
        date_from: str = "",
        date_to: str = "",
        match_all: bool = False,
        author: str = "模型先生",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Find official originals through confirmed categorized keywords only."""
        query_keywords: list[tuple[str, set[str]]] = []
        seen_queries: set[str] = set()
        for value in keywords or []:
            label = _compact(value)
            normalized = normalize_keyword(label)
            if not label or not normalized or normalized in seen_queries:
                continue
            seen_queries.add(normalized)
            variants = {normalized}
            for canonical, aliases in _ALIASES.items():
                group = {normalize_keyword(canonical), *(normalize_keyword(item) for item in aliases)}
                if normalized in group:
                    variants.update(item for item in group if item)
            query_keywords.append((label, variants))
            if len(query_keywords) >= 20:
                break

        selected_categories: list[str] = []
        for value in categories or []:
            category = _compact(value)
            if category not in KEYWORD_CATEGORIES:
                raise ValueError(f"不支持的关键词分类：{category}")
            if category not in selected_categories:
                selected_categories.append(category)
        if not query_keywords and not selected_categories:
            raise ValueError("至少提供一个关键词或一个关键词分类。")
        for value, label in ((date_from, "开始日期"), (date_to, "结束日期")):
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError(f"{label}必须使用 YYYY-MM-DD 格式。")

        safe_limit = max(1, min(int(limit), 30))
        predicates = ["TRIM(co.original_text) != ''"]
        params: list[Any] = []
        if selected_categories:
            placeholders = ",".join("?" for _ in selected_categories)
            predicates.append(f"ck.category IN ({placeholders})")
            params.extend(selected_categories)
        if author:
            predicates.append("v.author = ?")
            params.append(_compact(author))

        with self._connect() as conn:
            if not self._has_table(conn, "content_keywords"):
                return {
                    "keywords": [item[0] for item in query_keywords],
                    "categories": selected_categories,
                    "count": 0,
                    "items": [],
                    "message": "分类关键词索引尚未建立。",
                }
            rows = conn.execute(
                f"""
                SELECT
                    ck.content_id, ck.category, ck.keyword, ck.normalized_keyword,
                    v.author, v.published_at, v.discovered_at, v.url,
                    co.original_text, co.text_sha256,
                    COALESCE(NULLIF(vt.active_title, ''), NULLIF(kr.retrieval_title, ''), v.title) AS title,
                    cks.categories_json, cks.schema_version, cks.source_hash,
                    CASE
                        WHEN instr(pa.original_name, '.') > 0
                            THEN substr(pa.original_name, 1, instr(pa.original_name, '.') - 1)
                        ELSE pa.original_name
                    END AS canonical_name,
                    CASE WHEN pa.asset_type = 'screenshot' THEN 'image' ELSE pa.asset_type END AS media_type
                FROM content_keywords ck
                JOIN content_keyword_sets cks ON cks.content_id = ck.content_id
                JOIN content_originals co ON co.content_id = ck.content_id
                JOIN videos v ON v.id = ck.content_id
                LEFT JOIN video_titles vt ON vt.video_id = v.id
                LEFT JOIN knowledge_records kr ON kr.video_id = v.id
                LEFT JOIN video_assets pa ON pa.id = (
                    SELECT candidate.id
                    FROM video_assets candidate
                    WHERE candidate.video_id = v.id
                    ORDER BY
                        CASE WHEN candidate.asset_type IN ('video', 'screenshot') THEN 0 ELSE 1 END,
                        candidate.id DESC
                    LIMIT 1
                )
                WHERE {' AND '.join(predicates)}
                ORDER BY COALESCE(v.published_at, v.discovered_at) DESC, ck.ordinal ASC
                """,
                params,
            ).fetchall()

        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            published_at = str(row["published_at"] or row["discovered_at"] or "")
            local_date = self._china_date(published_at)
            if date_from and local_date < date_from:
                continue
            if date_to and local_date > date_to:
                continue

            row_keyword = str(row["keyword"] or "")
            row_normalized = str(row["normalized_keyword"] or "")
            matched_terms: list[str] = []
            exact_matches = 0
            for label, variants in query_keywords:
                if row_normalized in variants:
                    matched_terms.append(label)
                    exact_matches += 1
                elif any(
                    variant in row_normalized or row_normalized in variant
                    for variant in variants
                    if variant and row_normalized
                ):
                    matched_terms.append(label)
            if query_keywords and not matched_terms:
                continue

            content_id = int(row["content_id"])
            item = grouped.setdefault(
                content_id,
                {
                    "record_id": f"video:{content_id}",
                    "title": row["title"],
                    "published_at": published_at,
                    "author": row["author"],
                    "media_type": row["media_type"],
                    "canonical_name": row["canonical_name"],
                    "source_url": row["url"],
                    "matched_query_terms": [],
                    "matched_keywords": {},
                    "keyword_categories": normalize_keyword_categories(
                        {"categories": _from_json(row["categories_json"], {})}
                    ),
                    "original_excerpt": "",
                    "original_reference": f"content_originals:{content_id}",
                    "keyword_index_current": (
                        str(row["schema_version"] or "") == KEYWORD_SCHEMA_VERSION
                        and str(row["source_hash"] or "") == str(row["text_sha256"] or "")
                    ),
                    "relevance_score": 0.0,
                    "_original_text": str(row["original_text"] or ""),
                    "_exact": 0,
                },
            )
            for term in matched_terms:
                if term not in item["matched_query_terms"]:
                    item["matched_query_terms"].append(term)
            category_matches = item["matched_keywords"].setdefault(str(row["category"]), [])
            if row_keyword not in category_matches:
                category_matches.append(row_keyword)
            item["_exact"] += exact_matches

        items: list[dict[str, Any]] = []
        required_terms = {label for label, _variants in query_keywords}
        for item in grouped.values():
            if match_all and required_terms.difference(item["matched_query_terms"]):
                continue
            matched_count = sum(len(values) for values in item["matched_keywords"].values())
            item["relevance_score"] = round(
                len(item["matched_query_terms"]) * 10
                + int(item.pop("_exact")) * 4
                + min(matched_count, 6),
                3,
            )
            first_keyword = next(
                (
                    keyword
                    for values in item["matched_keywords"].values()
                    for keyword in values
                ),
                "",
            )
            item["original_excerpt"] = self._keyword_excerpt(
                item.pop("_original_text"),
                first_keyword,
            )
            items.append(item)
        items.sort(
            key=lambda item: (
                float(item["relevance_score"]),
                str(item["published_at"] or ""),
            ),
            reverse=True,
        )
        items = items[:safe_limit]
        return {
            "keywords": [item[0] for item in query_keywords],
            "categories": selected_categories,
            "match_mode": "all" if match_all else "any",
            "date_from": date_from or None,
            "date_to": date_to or None,
            "count": len(items),
            "items": items,
            "next_step": "需要引用或比较观点时，用 get_video_original 读取命中记录的完整正式原文。",
        }

    def get_video_original(self, record_id: str) -> dict[str, Any]:
        video_id = self._parse_record_id(record_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT catalog.*, v.author, v.url,
                       cks.model AS keyword_model,
                       cks.confirmed_at AS keywords_confirmed_at
                FROM content_original_catalog catalog
                JOIN videos v ON v.id = catalog.content_id
                LEFT JOIN content_keyword_sets cks ON cks.content_id = catalog.content_id
                WHERE catalog.content_id = ?
                """,
                (video_id,),
            ).fetchone()
        if not row:
            return {
                "found": False,
                "record_id": f"video:{video_id}",
                "message": "该记录没有经过人工确认的正式视频原文。",
            }
        categories = normalize_keyword_categories(
            {"categories": _from_json(row["keyword_categories_json"], {})}
        )
        return {
            "found": True,
            "record_id": f"video:{video_id}",
            "title": row["active_title"],
            "published_at": row["published_at"],
            "author": row["author"],
            "media_type": row["media_type"],
            "canonical_name": row["canonical_name"],
            "source_url": row["url"],
            "video_original": {
                "text": row["original_text"],
                "text_sha256": row["text_sha256"],
                "source_reference": f"content_originals:{video_id}",
                "verified": True,
            },
            "keyword_index": {
                "categories": categories,
                "schema_version": row["keyword_schema_version"],
                "source_hash": row["keyword_source_hash"],
                "model": row["keyword_model"],
                "confirmed_at": row["keywords_confirmed_at"],
                "current": (
                    str(row["keyword_schema_version"] or "") == KEYWORD_SCHEMA_VERSION
                    and str(row["keyword_source_hash"] or "") == str(row["text_sha256"] or "")
                ),
            },
            "usage_note": "关键词只用于定位；解释观点时必须以这里的完整正式原文为依据。",
        }

    @staticmethod
    def _china_date(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return str(value or "")[:10]
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=8))).date().isoformat()

    @staticmethod
    def _keyword_excerpt(text: str, keyword: str, length: int = 320) -> str:
        compact = _compact(text)
        if len(compact) <= length:
            return compact
        index = compact.casefold().find(str(keyword or "").casefold())
        if index < 0:
            return compact[:length].rstrip() + "…"
        start = max(0, index - length // 3)
        end = min(len(compact), start + length)
        prefix = "…" if start else ""
        suffix = "…" if end < len(compact) else ""
        return prefix + compact[start:end].strip() + suffix

    def _search_index(
        self,
        conn: sqlite3.Connection,
        terms: list[str],
        candidate_limit: int,
    ) -> list[sqlite3.Row]:
        columns = """
            kc.*,
            kr.title_kind,
            v.source AS video_source,
            v.source_video_id,
            v.author AS video_author,
            v.published_at AS video_published_at
        """
        joins = """
            JOIN knowledge_records kr ON kr.video_id = kc.video_id
            JOIN videos v ON v.id = kc.video_id
        """
        found: dict[int, sqlite3.Row] = {}
        query = _fts_query(terms)
        if query:
            fts_rows = conn.execute(
                f"""
                SELECT {columns},
                       bm25(knowledge_chunks_fts, 8.0, 6.0, 2.2, 1.0) AS fts_rank
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks kc ON kc.id = knowledge_chunks_fts.rowid
                {joins}
                WHERE knowledge_chunks_fts MATCH ?
                ORDER BY fts_rank ASC, kc.evidence_priority DESC
                LIMIT ?
                """,
                (query, candidate_limit),
            ).fetchall()
            found.update({int(row["id"]): row for row in fts_rows})

        predicates = " OR ".join(
            "(kc.retrieval_title LIKE ? ESCAPE '\\' OR kc.keywords LIKE ? ESCAPE '\\' "
            "OR kc.content LIKE ? ESCAPE '\\' OR kc.context LIKE ? ESCAPE '\\')"
            for _ in terms
        )
        patterns: list[str] = []
        for term in terms:
            pattern = _like_pattern(term)
            patterns.extend([pattern, pattern, pattern, pattern])
        if predicates:
            like_rows = conn.execute(
                f"""
                SELECT {columns}, NULL AS fts_rank
                FROM knowledge_chunks kc
                {joins}
                WHERE {predicates}
                ORDER BY kc.evidence_priority DESC,
                         COALESCE(kc.published_at, '') DESC
                LIMIT ?
                """,
                (*patterns, candidate_limit),
            ).fetchall()
            for row in like_rows:
                found.setdefault(int(row["id"]), row)
        return list(found.values())

    def _search_recent_index(
        self,
        conn: sqlite3.Connection,
        candidate_limit: int,
        *,
        requested_date: str | None,
        account: str | None,
    ) -> list[sqlite3.Row]:
        columns = """
            kc.*,
            kr.title_kind,
            v.source AS video_source,
            v.source_video_id,
            v.author AS video_author,
            COALESCE(v.published_at, v.discovered_at) AS video_published_at
        """
        predicates: list[str] = []
        params: list[Any] = []
        if requested_date:
            predicates.append(
                "date(COALESCE(v.published_at, v.discovered_at), '+8 hours') = ?"
            )
            params.append(requested_date)
        if account:
            predicates.append("v.author = ?")
            params.append(account)
        where_clause = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        return conn.execute(
            f"""
            SELECT {columns}, NULL AS fts_rank
            FROM knowledge_chunks kc
            JOIN knowledge_records kr ON kr.video_id = kc.video_id
            JOIN videos v ON v.id = kc.video_id
            {where_clause}
            ORDER BY COALESCE(v.published_at, v.discovered_at) DESC,
                     kc.evidence_priority DESC,
                     kc.id DESC
            LIMIT ?
            """,
            (*params, candidate_limit),
        ).fetchall()

    def get(self, record_id: str) -> dict[str, Any]:
        video_id = self._parse_record_id(record_id)
        with self._connect() as conn:
            video_row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if video_row is None:
                return {
                    "found": False,
                    "record_id": f"video:{video_id}",
                    "message": "未找到该记录。",
                }

            transcript_rows = conn.execute(
                "SELECT * FROM transcripts WHERE video_id = ? ORDER BY created_at DESC, id DESC",
                (video_id,),
            ).fetchall()
            analysis_rows = conn.execute(
                "SELECT * FROM analyses WHERE video_id = ? ORDER BY created_at DESC, id DESC",
                (video_id,),
            ).fetchall()
            comment_rows = conn.execute(
                "SELECT * FROM comments WHERE video_id = ? ORDER BY captured_at ASC, id ASC",
                (video_id,),
            ).fetchall()
            note_rows = conn.execute(
                "SELECT * FROM video_notes WHERE video_id = ? ORDER BY updated_at DESC, id DESC",
                (video_id,),
            ).fetchall()
            content_original_row = None
            if self._has_table(conn, "content_originals"):
                content_original_row = conn.execute(
                    "SELECT * FROM content_originals WHERE content_id = ?",
                    (video_id,),
                ).fetchone()
            record_row = None
            if self._has_table(conn, "knowledge_records"):
                record_row = conn.execute(
                    "SELECT * FROM knowledge_records WHERE video_id = ?",
                    (video_id,),
                ).fetchone()
            title_row = None
            if self._has_table(conn, "video_titles"):
                title_row = conn.execute(
                    "SELECT * FROM video_titles WHERE video_id = ?",
                    (video_id,),
                ).fetchone()
            keyword_set_row = None
            if self._has_table(conn, "content_keyword_sets"):
                keyword_set_row = conn.execute(
                    "SELECT * FROM content_keyword_sets WHERE content_id = ?",
                    (video_id,),
                ).fetchone()
            investment_category_rows = []
            if self._has_table(conn, "investment_thought_video_links"):
                investment_category_rows = conn.execute(
                    """
                    SELECT c.id, c.slug, c.name, c.description, c.parent_id,
                           p.name AS parent_name, p.slug AS parent_slug
                    FROM investment_thought_video_links l
                    JOIN investment_thought_categories c ON c.id = l.category_id
                    LEFT JOIN investment_thought_categories p ON p.id = c.parent_id
                    WHERE l.video_id = ? ORDER BY p.position, c.position, c.id
                    """,
                    (video_id,),
                ).fetchall()

        video = dict(video_row)
        video["raw"] = _from_json(video.pop("raw_json"), {})
        notes = {row["note_type"]: dict(row) for row in note_rows}

        transcripts = []
        for row in transcript_rows:
            item = dict(row)
            item["raw"] = _from_json(item.pop("raw_json"), {})
            transcripts.append(item)

        analyses = []
        for row in analysis_rows:
            item = dict(row)
            item["insights"] = _from_json(item.pop("insights_json"), [])
            item["recommendations"] = _from_json(item.pop("recommendations_json"), [])
            item["risk_flags"] = _from_json(item.pop("risk_flags_json"), [])
            item["score"] = _from_json(item.pop("score_json"), {})
            item["raw_output"] = _from_json(item["raw_output"], item["raw_output"])
            analyses.append(item)

        video_text_note = notes.get("video_text")
        if content_original_row and str(content_original_row["original_text"] or "").strip():
            video_original = {
                "content_type": "video_original",
                "content_label": CONTENT_LABELS["video_original"],
                "text": content_original_row["original_text"],
                "text_sha256": content_original_row["text_sha256"],
                "source_reference": f"content_originals:{video_id}",
                "verified": True,
            }
        elif video_text_note and str(video_text_note.get("text") or "").strip():
            video_original = {
                "content_type": "video_original",
                "content_label": CONTENT_LABELS["video_original"],
                "text": video_text_note["text"],
                "source_reference": f"video_notes:{video_text_note['id']}",
                "verified": True,
            }
        else:
            video_original = None

        interpretation_note = notes.get("interpretation")
        interpretation = None
        if interpretation_note and str(interpretation_note.get("text") or "").strip():
            interpretation = {
                "content_type": "user_interpretation",
                "content_label": CONTENT_LABELS["user_interpretation"],
                "text": interpretation_note["text"],
                "source_reference": f"video_notes:{interpretation_note['id']}",
                "attribution_warning": "这是用户的解读感悟，不是博主原话。",
            }

        keyword_index = None
        if keyword_set_row:
            keyword_index = {
                "categories": normalize_keyword_categories(
                    {"categories": _from_json(keyword_set_row["categories_json"], {})}
                ),
                "schema_version": keyword_set_row["schema_version"],
                "source_hash": keyword_set_row["source_hash"],
                "model": keyword_set_row["model"],
                "confirmed_at": keyword_set_row["confirmed_at"],
                "current": bool(
                    content_original_row
                    and str(keyword_set_row["schema_version"] or "") == KEYWORD_SCHEMA_VERSION
                    and str(keyword_set_row["source_hash"] or "")
                    == str(content_original_row["text_sha256"] or "")
                ),
            }

        source_author = str(video.get("author") or "模型先生")
        comment_threads = build_model_comment_threads(comment_rows, model_author=source_author)
        retrieval_title = record_row["retrieval_title"] if record_row else video["title"]
        return {
            "found": True,
            "record_id": f"video:{video_id}",
            "title": retrieval_title,
            "title_kind": record_row["title_kind"] if record_row else "source",
            "title_info": {
                "ocr_title": title_row["ocr_title"],
                "manual_title": title_row["manual_title"],
                "active_title": title_row["active_title"],
                "title_source": title_row["title_source"],
                "confidence": title_row["confidence"],
                "verified": bool(title_row["verified"]),
            } if title_row else None,
            "date": video["published_at"] or video["discovered_at"],
            "source": {
                "type": video["source"],
                "source_video_id": video["source_video_id"],
                "author": video["author"],
                "url": video["url"],
            },
            "content_sections": {
                "video_original": video_original,
                "model_comment_threads": comment_threads,
                "user_interpretation": interpretation,
            },
            "keyword_index": keyword_index,
            "investment_thought_categories": [dict(row) for row in investment_category_rows],
            "attribution_rules": [
                f"视频原文和{source_author}账号的作者回复可以作为原始观点证据。",
                f"用户解读感悟只能作为二次理解，不能表述成{source_author}原话。",
                "系统分析只能作为辅助，重要结论必须回到原文或本人回复核实。",
            ],
            "video": video,
            "transcripts": transcripts,
            "analyses": analyses,
        }

    @staticmethod
    def _parse_record_id(record_id: str) -> int:
        value = str(record_id).strip()
        if value.startswith("video:"):
            value = value.split(":", 1)[1]
        if not value.isdigit() or int(value) <= 0:
            raise ValueError("记录编号必须是 search_model_knowledge 返回的 video:<数字>。")
        return int(value)
