"""Bounded owner-library metadata; no model calls, credentials or database access."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

KEYWORD_CATEGORIES = (
    "行业与板块", "企业、个股与产业链", "基本面与估值", "时间周期与走势状态",
    "投资战略、战术与选股方法", "宏观、政策与事件", "市场、指数与资金",
    "技术面与交易信号", "交易管理与风险控制", "投资心理、学习与适用人群",
)
METADATA_SCHEMA = "model-mr-owner-metadata/v1"


def clean_words(value: Any, limit: int = 80) -> list[str]:
    result: list[str] = []
    for word in value if isinstance(value, list) else []:
        if not isinstance(word, str):
            continue
        word = " ".join(word.split()).strip("#，,。；;：:、 ")
        if not word or len(word) > 64 or re.search(r"[\\/]|[A-Za-z]:", word):
            continue
        if word not in result:
            result.append(word)
        if len(result) >= limit:
            break
    return result


def clean_keyword_info(value: Any, legacy: Any = None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    raw = source.get("categories") if isinstance(source.get("categories"), dict) else {}
    categories = {name: clean_words(raw.get(name), 8) for name in KEYWORD_CATEGORIES}
    flattened = [word for values in categories.values() for word in values]
    words = clean_words(flattened + clean_words(source.get("keywords")) + clean_words(legacy))
    return {
        "categories": categories,
        "keywords": words,
        "model": str(source.get("model") or "")[:120],
        "schema_version": str(source.get("schema_version") or "")[:80],
        "confirmed_at": str(source.get("confirmed_at") or "")[:64],
        "stale": bool(source.get("stale")),
        "edited_by_owner": bool(source.get("edited_by_owner")),
    }


def keyword_revision(info: Any, legacy: Any = None) -> str:
    material = json.dumps(clean_keyword_info(info, legacy), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def clean_links(value: Any, work_ids: set[int], category_ids: set[int]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    if not isinstance(value, dict):
        return result
    for key, raw in value.items():
        if not str(key).isdigit() or int(key) not in work_ids or not isinstance(raw, list):
            continue
        ids = list(dict.fromkeys(int(item) for item in raw if str(item).isdigit() and int(item) in category_ids))
        result[str(int(key))] = ids[:300]
    return result


def public_work_url(value: Any) -> str:
    parsed = urlsplit(str(value or ""))
    if parsed.scheme != "https" or parsed.hostname not in {"www.douyin.com", "douyin.com", "v.douyin.com"}:
        return ""
    return urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))


def prepare_metadata_merge(snapshot: dict[str, Any], package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Plan a non-destructive merge. Never match on title alone or discard cloud-only works."""
    if package.get("schema") != METADATA_SCHEMA:
        raise ValueError("元数据包版本不支持。")
    incoming = package.get("works")
    incoming_categories = package.get("thoughts")
    if not isinstance(incoming, list) or len(incoming) > 5000 or not isinstance(incoming_categories, list):
        raise ValueError("元数据包结构无效。")
    result = copy.deepcopy(snapshot)
    works = {int(item["id"]): item for item in result.get("works", [])}
    url_ids: dict[str, list[int]] = {}
    for work_id, item in works.items():
        url = public_work_url(item.get("url"))
        if url:
            url_ids.setdefault(url, []).append(work_id)
    categories = {int(item["id"]): item for item in result.get("thoughts", [])}
    seen: set[int] = set()
    for raw in incoming_categories:
        if not isinstance(raw, dict) or not str(raw.get("id", "")).isdigit():
            raise ValueError("投资思路分类编号无效。")
        category_id = int(raw["id"])
        if category_id <= 0 or category_id in seen:
            raise ValueError("投资思路分类编号重复。")
        seen.add(category_id)
        category = {key: raw.get(key) for key in ("id", "name", "description", "level", "parent_id", "video_count")}
        category["id"] = category_id
        category["name"] = str(category["name"] or "")[:120]
        category["description"] = str(category["description"] or "")[:1000]
        category["level"] = int(category["level"] or 1)
        category["video_count"] = max(0, int(category["video_count"] or 0))
        category["parent_id"] = int(category["parent_id"]) if category["parent_id"] is not None else None
        if category["level"] not in (1, 2) or (category["level"] == 1 and category["parent_id"] is not None):
            raise ValueError("仅支持一级、二级投资思路分类。")
        old = categories.get(category_id)
        if old and (old.get("name"), old.get("parent_id")) != (category["name"], category["parent_id"]):
            raise ValueError(f"分类 {category_id} 与云端已有分类冲突，未覆盖。")
        categories[category_id] = category
    if len(categories) > 300:
        raise ValueError("分类数量超过上限。")
    for category in categories.values():
        parent = categories.get(category.get("parent_id"))
        if category.get("level") == 2 and (not parent or parent.get("level") != 1):
            raise ValueError("二级分类缺少有效上级。")
    mapped: dict[int, int] = {}
    changed: list[int] = []
    missing: list[int] = []
    preserved: list[int] = []
    seen.clear()
    for item in incoming:
        if not isinstance(item, dict):
            raise ValueError("元数据作品格式无效。")
        source_id = int(item.get("id") or 0)
        if source_id <= 0 or source_id in seen:
            raise ValueError("元数据作品编号重复或无效。")
        seen.add(source_id)
        url = public_work_url(item.get("url"))
        candidates = url_ids.get(url, []) if url else []
        same_id = works.get(source_id)
        if same_id and url and public_work_url(same_id.get("url")) == url:
            target_id = source_id
        elif len(candidates) == 1:
            target_id = candidates[0]
        elif same_id and not url and not public_work_url(same_id.get("url")) and (
            str(same_id.get("published_at") or "") == str(item.get("published_at") or "")
            and str(same_id.get("title") or "") == str(item.get("title") or "")
        ):
            target_id = source_id
        else:
            missing.append(source_id)
            continue
        if target_id in mapped.values():
            raise ValueError("多个本地作品对应同一云端作品，停止合并。")
        mapped[source_id] = target_id
        target = works[target_id]
        current = clean_keyword_info(target.get("keyword_info"), target.get("keywords"))
        proposed = clean_keyword_info(item.get("keyword_info"), item.get("keywords"))
        if current["edited_by_owner"]:
            preserved.append(target_id)
        elif proposed["keywords"] or proposed["confirmed_at"]:
            if current != proposed:
                changed.append(target_id)
            target["keyword_info"] = proposed
            target["keywords"] = proposed["keywords"]
    links = clean_links(result.get("thought_links"), set(works), set(categories))
    incoming_links = clean_links(package.get("thought_links"), set(mapped), set(categories))
    for source_id, target_id in mapped.items():
        if str(source_id) in incoming_links:
            links[str(target_id)] = incoming_links[str(source_id)]
    result["thought_links"] = links
    result["thoughts"] = list(categories.values())
    result["metadata_schema"] = METADATA_SCHEMA
    result["metadata_exported_at"] = str(package.get("exported_at") or "")[:64]
    return result, {"matched": len(mapped), "keyword_updates": len(changed), "changed_ids": changed,
                    "unmatched_ids": missing, "preserved_owner_keywords": len(preserved),
                    "linked_works": sum(bool(ids) for ids in links.values()), "categories": len(categories)}
