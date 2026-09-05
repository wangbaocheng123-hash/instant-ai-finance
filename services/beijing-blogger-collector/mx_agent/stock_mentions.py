from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .settings import DATA_DIR
from .storage import Storage


DEFAULT_STOCK_MASTER_PATH = DATA_DIR / "stock_master_a_share.json"

_CODE_PATTERN = re.compile(r"(?<!\d)(?:SH|SZ|BJ)?(\d{6})(?!\d)", re.IGNORECASE)
_SPACE_PATTERN = re.compile(r"\s+")
_ST_PREFIX_PATTERN = re.compile(r"^(?:\*?ST|S\*ST|SST)", re.IGNORECASE)

# These short forms are common enough in investment discussion to be useful,
# but are only accepted when they point to one explicit security.
EXPLICIT_ALIASES: dict[str, str] = {
    "紫光": "紫光股份",
    "中兴": "中兴通讯",
    "浪潮": "浪潮信息",
    "寒武纪": "寒武纪",
    "中际": "中际旭创",
    "旭创": "中际旭创",
    "兆易": "兆易创新",
    "北方华创": "北方华创",
    "工业富联": "工业富联",
    "海光": "海光信息",
    "茅台": "贵州茅台",
    "比亚迪": "比亚迪",
    "宁德": "宁德时代",
    "宁王": "宁德时代",
    "东财": "东方财富",
    "平安": "中国平安",
    "招行": "招商银行",
    "中信": "中信证券",
    "东山": "东山精密",
    "天孚": "天孚通信",
    "沪电": "沪电股份",
    "生益": "生益科技",
    "紫金": "紫金矿业",
    "江铜": "江西铜业",
    "云铜": "云南铜业",
    "中铝": "中国铝业",
    "山金": "山东黄金",
}

# These names are also ordinary Chinese words or broad themes. A bare mention
# must not be counted as a definite stock without a code or a less ambiguous
# full name.
AMBIGUOUS_BARE_NAMES = {
    "机器人",
    "同花顺",
    "指南针",
    "人民网",
    "中国软件",
    "中文在线",
    "值得买",
    "我爱我家",
    "万科A",
}

ALIAS_SUFFIXES = (
    "股份有限公司",
    "集团股份",
    "股份",
    "集团",
    "科技",
    "信息",
    "通信",
    "电子",
    "证券",
    "银行",
    "矿业",
    "铜业",
    "黄金",
    "能源",
    "电力",
    "汽车",
    "药业",
    "精密",
    "国际",
)


def _normalize(value: Any) -> str:
    text = _SPACE_PATTERN.sub("", str(value or ""))
    return text.replace("Ａ", "A").replace("Ｂ", "B").strip()


def _clean_security_name(value: Any) -> str:
    return _ST_PREFIX_PATTERN.sub("", _normalize(value))


class StockMentionService:
    """Count explicit security mentions in comments without using an AI API."""

    def __init__(
        self,
        storage: Storage,
        master_path: Path | None = None,
        master_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.storage = storage
        self.master_path = master_path or DEFAULT_STOCK_MASTER_PATH
        self._master_items = master_items
        self._index_signature: tuple[int, int] | None = None
        self._by_code: dict[str, dict[str, str]] = {}
        self._alias_targets: dict[str, set[str]] = {}
        self._aliases_by_first: dict[str, list[str]] = {}
        self._ambiguous_aliases: dict[str, list[str]] = {}

    def analyze(self, video_id: int, limit: int = 20) -> dict[str, Any]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        self._ensure_index()

        comments = self.storage.list_comments(video_id, limit=5000)
        account_author = str(video.get("author") or "模型先生")
        counts: Counter[str] = Counter()
        occurrence_counts: Counter[str] = Counter()
        fan_counts: Counter[str] = Counter()
        author_counts: Counter[str] = Counter()
        examples: dict[str, list[str]] = defaultdict(list)
        comment_ids: dict[str, list[int]] = defaultdict(list)
        uncertain_counts: Counter[str] = Counter()
        uncertain_candidates: dict[str, list[str]] = {}

        for comment in comments:
            text = _normalize(comment.get("text"))
            if not text:
                continue
            raw = comment.get("raw_json") or {}
            if not isinstance(raw, dict):
                try:
                    raw = json.loads(str(raw))
                except (TypeError, json.JSONDecodeError):
                    raw = {}
            is_author = (
                raw.get("kind") == "author_reply"
                or str(comment.get("author") or "") == account_author
            )

            found, occurrences, uncertain = self._extract(text)
            for name in found:
                counts[name] += 1
                occurrence_counts[name] += occurrences[name]
                (author_counts if is_author else fan_counts)[name] += 1
                comment_id = int(comment.get("id") or 0)
                if comment_id:
                    comment_ids[name].append(comment_id)
                if len(examples[name]) < 3:
                    examples[name].append(str(comment.get("text") or "")[:180])
            for alias, candidates in uncertain.items():
                uncertain_counts[alias] += 1
                uncertain_candidates[alias] = candidates

        ranked = sorted(
            counts,
            key=lambda name: (
                -counts[name],
                -occurrence_counts[name],
                -author_counts[name],
                name,
            ),
        )[: max(1, min(int(limit or 20), 20))]
        items = []
        for rank, name in enumerate(ranked, start=1):
            security = self._security_by_name(name)
            items.append(
                {
                    "rank": rank,
                    "name": name,
                    "code": security.get("code", ""),
                    "comment_count": counts[name],
                    "mention_count": occurrence_counts[name],
                    "fan_comment_count": fan_counts[name],
                    "author_comment_count": author_counts[name],
                    "examples": examples[name],
                    "comment_ids": comment_ids[name],
                }
            )

        uncertain_items = [
            {
                "text": alias,
                "comment_count": count,
                "candidates": uncertain_candidates.get(alias, []),
            }
            for alias, count in uncertain_counts.most_common(20)
        ]
        return {
            "video_id": video_id,
            "total_comments": len(comments),
            "stock_count": len(counts),
            "items": items,
            "uncertain": uncertain_items,
            "method": "local-security-master",
            "api_used": False,
            "message": (
                "只统计能够由本地证券名称表唯一确认的股票；模糊简称不会被强行猜测。"
            ),
        }

    def _extract(
        self,
        text: str,
    ) -> tuple[set[str], Counter[str], dict[str, list[str]]]:
        found: set[str] = set()
        occurrences: Counter[str] = Counter()
        uncertain: dict[str, list[str]] = {}

        for code in _CODE_PATTERN.findall(text):
            security = self._by_code.get(code)
            if security:
                name = security["name"]
                found.add(name)
                occurrences[name] = max(occurrences[name], text.count(code))

        checked_aliases: set[str] = set()
        for char in set(text):
            for alias in self._aliases_by_first.get(char, []):
                if alias in checked_aliases or alias not in text:
                    continue
                checked_aliases.add(alias)
                targets = self._alias_targets[alias]
                if len(targets) != 1:
                    uncertain[alias] = sorted(targets)
                    continue
                name = next(iter(targets))
                if alias == name and len(alias) <= 2:
                    continue
                if alias == name and alias in AMBIGUOUS_BARE_NAMES:
                    uncertain[alias] = [name]
                    continue
                found.add(name)
                occurrences[name] = max(occurrences[name], text.count(alias))

        # Do not count a short alias twice when the same comment contains the
        # corresponding full security name.
        for name in list(found):
            occurrences[name] = max(1, occurrences[name])
        return found, occurrences, uncertain

    def _ensure_index(self) -> None:
        if self._master_items is not None:
            signature = (id(self._master_items), len(self._master_items))
            items = self._master_items
        else:
            if not self.master_path.is_file():
                raise RuntimeError(
                    f"本地证券名称表不存在：{self.master_path}。请先生成证券名称表。"
                )
            stat = self.master_path.stat()
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
            if self._index_signature == signature:
                return
            items = json.loads(self.master_path.read_text(encoding="utf-8"))
        if self._index_signature == signature:
            return

        by_code: dict[str, dict[str, str]] = {}
        name_to_security: dict[str, dict[str, str]] = {}
        alias_targets: dict[str, set[str]] = defaultdict(set)
        for item in items:
            code = str(item.get("code") or "").zfill(6)
            name = _clean_security_name(item.get("name"))
            if not re.fullmatch(r"\d{6}", code) or len(name) < 2:
                continue
            security = {"code": code, "name": name}
            by_code[code] = security
            name_to_security[name] = security
            alias_targets[name].add(name)
            for suffix in ALIAS_SUFFIXES:
                if name.endswith(suffix):
                    alias = name[: -len(suffix)]
                    if len(alias) >= 3:
                        alias_targets[alias].add(name)

        for alias, target_name in EXPLICIT_ALIASES.items():
            if target_name in name_to_security:
                alias_targets[_normalize(alias)].add(target_name)

        aliases_by_first: dict[str, list[str]] = defaultdict(list)
        for alias in alias_targets:
            if len(alias) >= 2:
                aliases_by_first[alias[0]].append(alias)
        for aliases in aliases_by_first.values():
            aliases.sort(key=lambda value: (-len(value), value))

        self._by_code = by_code
        self._alias_targets = dict(alias_targets)
        self._aliases_by_first = dict(aliases_by_first)
        self._index_signature = signature

    def _security_by_name(self, name: str) -> dict[str, str]:
        for security in self._by_code.values():
            if security["name"] == name:
                return security
        return {"name": name, "code": ""}
