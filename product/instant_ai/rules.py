from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TOPIC_KEYWORDS = {
    "全球财经": (
        "global markets", "world economy", "financial markets", "stock market",
        "全球市场", "全球经济", "财经", "金融市场",
    ),
    "华尔街": (
        "wall street", "nasdaq", "s&p 500", "dow jones", "nyse", "美股",
        "纳斯达克", "标普", "道琼斯", "高盛", "摩根士丹利", "摩根大通",
        "goldman sachs", "morgan stanley", "jpmorgan",
    ),
    "中国财经": (
        "a股", "港股", "上证", "深证", "北交所", "沪深", "人民币",
        "中国经济", "中国央行", "人民银行", "上交所", "深交所", "港交所",
        "china economy", "china stocks", "pboc", "hong kong stocks",
    ),
    "亚洲市场": (
        "亚洲市场", "亚太市场", "日经", "日本央行", "韩国央行", "新加坡金管局",
        "nikkei", "topix", "bank of japan", "boj", "kospi", "sgx", "sensex",
        "asian markets", "asia-pacific markets",
    ),
    "AI产业链": (
        "人工智能", "ai", "大模型", "算力", "gpu", "芯片", "半导体",
        "数据中心", "机器人", "云计算", "llm", "machine learning", "nvidia",
        "英伟达", "google", "谷歌", "apple", "苹果", "microsoft", "微软",
        "amazon", "meta", "amd", "tsmc", "台积电", "asml", "hbm", "光模块",
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
        "federal reserve", "interest rate", "inflation", "monetary", "eia", "ecb",
        "bank of japan", "treasury yield", "国债收益率", "cpi", "gdp", "pmi",
    ),
    "战争/地缘": (
        "战争", "冲突", "袭击", "停火", "地缘政治", "制裁", "封锁", "红海",
        "霍尔木兹", "苏伊士", "台海", "南海", "war", "conflict", "attack",
        "ceasefire", "geopolitical", "sanction", "shipping disruption", "red sea",
    ),
    "投行观点": (
        "投行", "机构观点", "市场展望", "研究报告", "高盛", "摩根士丹利",
        "摩根大通", "瑞银", "花旗", "贝莱德", "market outlook", "research note",
        "goldman sachs", "morgan stanley", "jpmorgan", "blackrock", "ubs", "citi",
    ),
    "创业融资": (
        "创业", "初创", "融资", "风投", "创投", "独角兽", "venture capital",
        "startup", "funding round", "seed round", "series a", "series b",
    ),
    "财经知识": (
        "投资者教育", "市场结构", "投资知识", "financial education",
        "investor education", "market structure", "how markets work",
    ),
}

ENTITY_KEYWORDS = {
    "紫金矿业": ("紫金矿业", "zijin", "601899", "2899.hk"),
    "黄金": ("黄金", "gold", "bullion"),
    "铜": ("铜", "copper"),
    "人工智能": ("人工智能", "ai", "大模型", "gpu", "算力"),
    "美联储": ("美联储", "federal reserve", "fomc"),
    "英伟达": ("英伟达", "nvidia", "nvda"),
    "谷歌": ("谷歌", "google", "alphabet", "googl"),
    "苹果": ("苹果公司", "apple", "aapl"),
    "微软": ("微软", "microsoft", "msft"),
    "台积电": ("台积电", "tsmc", "tsm"),
    "高盛": ("高盛", "goldman sachs"),
    "摩根士丹利": ("摩根士丹利", "morgan stanley"),
    "摩根大通": ("摩根大通", "jpmorgan", "jp morgan"),
}

EVENT_KEYWORDS = (
    ("战争/地缘冲击", ("战争", "冲突", "袭击", "停火", "封锁", "war", "conflict", "attack", "ceasefire")),
    ("事故/中断", ("事故", "停产", "中断", "灾害", "罢工", "shutdown", "disruption")),
    ("监管/政策", ("政策", "监管", "处罚", "制裁", "关税", "法案", "regulation", "sanction", "tariff")),
    ("业绩/财报", ("财报", "业绩", "年报", "季报", "利润", "营收", "earnings", "revenue", "guidance")),
    ("并购/投资", ("收购", "出售", "并购", "投资", "合同", "acquisition", "merger", "contract")),
    ("产量/库存", ("产量", "产能", "库存", "品位", "储量", "production", "inventory", "reserve")),
    ("价格/宏观", ("价格", "利率", "通胀", "降息", "加息", "price", "rate", "inflation")),
    ("融资/创业", ("融资", "风投", "创投", "初创", "funding", "venture capital", "startup")),
    ("机构观点", ("市场展望", "研究报告", "机构观点", "outlook", "research note", "forecast")),
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


def _matches(haystack: str, keyword: str) -> bool:
    token = keyword.casefold()
    if re.fullmatch(r"[a-z0-9.+&/-]+", token):
        return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack) is not None
    return token in haystack


def analyze(title: str, summary: str, trust_level: int, topic_hints: list[str] | None = None) -> Analysis:
    haystack = f"{title} {summary}".casefold()
    topics: list[str] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(_matches(haystack, keyword) for keyword in keywords):
            topics.append(topic)

    for hint in topic_hints or []:
        mapped = "铜/有色" if hint in {"铜", "有色金属"} else hint
        if mapped in TOPIC_KEYWORDS and mapped not in topics:
            topics.append(mapped)

    entities = [
        name
        for name, keywords in ENTITY_KEYWORDS.items()
        if any(_matches(haystack, keyword) for keyword in keywords)
    ]

    event_type = "一般动态"
    event_weight = 0
    for candidate, keywords in EVENT_KEYWORDS:
        if any(_matches(haystack, keyword) for keyword in keywords):
            event_type = candidate
            event_weight = 18 if candidate in {"战争/地缘冲击", "事故/中断", "监管/政策", "业绩/财报"} else 12
            break

    score = trust_level * 10 + min(len(topics) * 9, 36) + event_weight
    if "紫金矿业" in topics:
        score += 12
    return Analysis(topics, entities, event_type, min(score, 100))
