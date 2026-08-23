from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TOPIC_KEYWORDS = {
    "AI产业链": (
        "人工智能", "ai", "大模型", "算力", "gpu", "芯片", "半导体",
        "数据中心", "机器人", "云计算", "llm", "machine learning",
    ),
    "紫金矿业": (
        "紫金矿业", "紫金矿", "zijin", "601899", "2899.hk", "多宝山",
        "卡莫阿", "巨龙铜矿", "博尔铜矿",
    ),
    "黄金": ("黄金", "金矿", "金价", "gold", "bullion", "贵金属"),
    "铜/有色": (
        "铜", "铜矿", "精矿", "冶炼", "有色金属", "copper", "锌", "铅",
        "锂", "镍", "钴", "铝", "库存", "矿产",
    ),
    "宏观政策": (
        "央行", "美联储", "利率", "通胀", "政策", "监管", "关税", "制裁",
        "federal reserve", "interest rate", "inflation", "monetary", "eia",
    ),
}

ENTITY_KEYWORDS = {
    "紫金矿业": ("紫金矿业", "zijin", "601899", "2899.hk"),
    "黄金": ("黄金", "gold", "bullion"),
    "铜": ("铜", "copper"),
    "人工智能": ("人工智能", "ai", "大模型", "gpu", "算力"),
    "美联储": ("美联储", "federal reserve", "fomc"),
}

EVENT_KEYWORDS = (
    ("事故/中断", ("事故", "停产", "中断", "灾害", "罢工", "shutdown", "disruption")),
    ("监管/政策", ("政策", "监管", "处罚", "制裁", "关税", "法案", "regulation", "sanction", "tariff")),
    ("业绩/财报", ("财报", "业绩", "年报", "季报", "利润", "营收", "earnings", "revenue", "guidance")),
    ("并购/投资", ("收购", "出售", "并购", "投资", "合同", "acquisition", "merger", "contract")),
    ("产量/库存", ("产量", "产能", "库存", "品位", "储量", "production", "inventory", "reserve")),
    ("价格/宏观", ("价格", "利率", "通胀", "降息", "加息", "price", "rate", "inflation")),
)

TRACKING_QUERY_PREFIXES = ("utm_", "spm", "from", "source", "ref")


@dataclass(frozen=True)
class Analysis:
    topics: list[str]
    entities: list[str]
    event_type: str
    importance_score: int


def clean_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalized_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def canonical_key(url: str, title: str) -> str:
    basis = normalized_url(url) if url else clean_text(title).lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def analyze(title: str, summary: str, trust_level: int, topic_hints: list[str] | None = None) -> Analysis:
    haystack = f"{title} {summary}".casefold()
    topics: list[str] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword.casefold() in haystack for keyword in keywords):
            topics.append(topic)

    for hint in topic_hints or []:
        mapped = "铜/有色" if hint in {"铜", "有色金属"} else hint
        if mapped in TOPIC_KEYWORDS and mapped not in topics:
            topics.append(mapped)

    entities = [
        name
        for name, keywords in ENTITY_KEYWORDS.items()
        if any(keyword.casefold() in haystack for keyword in keywords)
    ]

    event_type = "一般动态"
    event_weight = 0
    for candidate, keywords in EVENT_KEYWORDS:
        if any(keyword.casefold() in haystack for keyword in keywords):
            event_type = candidate
            event_weight = 18 if candidate in {"事故/中断", "监管/政策", "业绩/财报"} else 12
            break

    score = trust_level * 10 + min(len(topics) * 9, 27) + event_weight
    if "紫金矿业" in topics:
        score += 12
    return Analysis(topics, entities, event_type, min(score, 100))
