from __future__ import annotations

import re
from typing import Any


KEYWORD_SCHEMA_VERSION = "2026-08-01-v1-ten-categories"
KEYWORD_CATEGORIES = (
    "行业与板块",
    "企业、个股与产业链",
    "基本面与估值",
    "时间周期与走势状态",
    "投资战略、战术与选股方法",
    "宏观、政策与事件",
    "市场、指数与资金",
    "技术面与交易信号",
    "交易管理与风险控制",
    "投资心理、学习与适用人群",
)
KEYWORD_MAX_PER_CATEGORY = 8
KEYWORD_MAX_TOTAL = 40

LEGACY_CATEGORY_MAP = {
    "company": "企业、个股与产业链",
    "security": "企业、个股与产业链",
    "supply_chain": "企业、个股与产业链",
    "industry": "行业与板块",
    "concept": "行业与板块",
    "valuation": "基本面与估值",
    "financial_metric": "基本面与估值",
    "time": "时间周期与走势状态",
    "strategy": "投资战略、战术与选股方法",
    "index": "市场、指数与资金",
    "risk": "交易管理与风险控制",
}


def empty_keyword_categories() -> dict[str, list[str]]:
    return {category: [] for category in KEYWORD_CATEGORIES}


def clean_keyword(value: Any) -> str:
    keyword = re.sub(r"\s+", " ", str(value or "")).strip("#，,。；;：:、 ")
    if not keyword or len(keyword) > 24:
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", keyword):
        return ""
    return keyword


def normalize_keyword(value: Any) -> str:
    keyword = clean_keyword(value).casefold()
    return re.sub(r"[\s·•・·、，,;；:：/\\_-]+", "", keyword)


def normalize_keyword_categories(payload: Any) -> dict[str, list[str]]:
    """Normalize current ten-category payloads and read legacy keyword notes."""
    result = empty_keyword_categories()
    if not isinstance(payload, dict):
        return result

    raw_categories = payload.get("categories")
    if not isinstance(raw_categories, dict):
        raw_categories = payload

    total = 0
    global_seen: set[str] = set()
    for category in KEYWORD_CATEGORIES:
        values = raw_categories.get(category, [])
        if not isinstance(values, list):
            continue
        for value in values:
            keyword = clean_keyword(value)
            normalized = normalize_keyword(keyword)
            if not keyword or not normalized or normalized in global_seen:
                continue
            result[category].append(keyword)
            global_seen.add(normalized)
            total += 1
            if len(result[category]) >= KEYWORD_MAX_PER_CATEGORY or total >= KEYWORD_MAX_TOTAL:
                break
        if total >= KEYWORD_MAX_TOTAL:
            break

    if total:
        return result

    legacy_items = payload.get("items", [])
    if not legacy_items and isinstance(payload.get("keywords"), list):
        legacy_items = [
            item for item in payload["keywords"]
            if isinstance(item, dict)
        ]
    if isinstance(legacy_items, list):
        for item in legacy_items:
            if not isinstance(item, dict):
                continue
            category = LEGACY_CATEGORY_MAP.get(
                str(item.get("category") or "concept"),
                "行业与板块",
            )
            keyword = clean_keyword(item.get("name"))
            normalized = normalize_keyword(keyword)
            if not keyword or not normalized or normalized in global_seen:
                continue
            if len(result[category]) >= KEYWORD_MAX_PER_CATEGORY or total >= KEYWORD_MAX_TOTAL:
                continue
            result[category].append(keyword)
            global_seen.add(normalized)
            total += 1

    legacy_keywords = payload.get("keywords", [])
    if not total and isinstance(legacy_keywords, list):
        category = "行业与板块"
        for value in legacy_keywords:
            keyword = clean_keyword(value)
            normalized = normalize_keyword(keyword)
            if not keyword or not normalized or normalized in global_seen:
                continue
            result[category].append(keyword)
            global_seen.add(normalized)
            total += 1
            if len(result[category]) >= KEYWORD_MAX_PER_CATEGORY or total >= KEYWORD_MAX_TOTAL:
                break
    return result


def flatten_keyword_categories(categories: Any) -> list[str]:
    normalized = normalize_keyword_categories({"categories": categories})
    return [
        keyword
        for category in KEYWORD_CATEGORIES
        for keyword in normalized[category]
    ]
