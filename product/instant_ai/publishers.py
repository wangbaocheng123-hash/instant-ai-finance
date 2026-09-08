from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


UNKNOWN_PUBLISHER = "原站待识别"


@dataclass(frozen=True)
class PublisherIdentity:
    name: str
    url: str = ""


_DOMAIN_LABELS = (
    ("reuters.com", "路透社"),
    ("bloomberg.com", "彭博社"),
    ("ft.com", "英国《金融时报》"),
    ("wsj.com", "《华尔街日报》"),
    ("moodysratings.com", "穆迪评级"),
    ("moodys.com", "穆迪"),
    ("spglobal.com", "标普全球"),
    ("goldmansachs.com", "高盛"),
    ("morganstanley.com", "摩根士丹利"),
    ("jpmorgan.com", "摩根大通"),
    ("blackrock.com", "贝莱德"),
    ("ubs.com", "瑞银"),
    ("bankofamerica.com", "美国银行"),
    ("citigroup.com", "花旗集团"),
    ("cls.cn", "财联社"),
    ("caixin.com", "财新"),
    ("yicai.com", "第一财经"),
    ("hankyung.com", "韩国经济日报"),
    ("sedaily.com", "首尔经济日报"),
    ("stockplus.com", "Stockplus Newsroom"),
    ("kbsec.com", "KB证券研究"),
    ("federalreserve.gov", "美联储"),
    ("eia.gov", "美国能源信息署"),
    ("sec.gov", "美国证监会 SEC"),
    ("openai.com", "OpenAI"),
    ("nvidia.com", "NVIDIA"),
    ("google", "Google"),
    ("apple.com", "Apple"),
    ("microsoft.com", "Microsoft"),
    ("zjky.cn", "紫金矿业"),
)

_NAME_ALIASES = {
    "reuters": "路透社",
    "路透": "路透社",
    "bloomberg": "彭博社",
    "financial times": "英国《金融时报》",
    "the financial times": "英国《金融时报》",
    "the wall street journal": "《华尔街日报》",
    "wall street journal": "《华尔街日报》",
    "moody's": "穆迪",
    "moodys": "穆迪",
    "moody's ratings": "穆迪评级",
    "s&p global": "标普全球",
    "goldman sachs": "高盛",
    "morgan stanley": "摩根士丹利",
    "jpmorgan": "摩根大通",
    "j.p. morgan": "摩根大通",
    "blackrock": "贝莱德",
    "ubs": "瑞银",
    "bank of america": "美国银行",
    "citigroup": "花旗集团",
    "cailian press": "财联社",
    "한국경제": "韩国经济日报",
    "서울경제": "首尔经济日报",
    "증권플러스 뉴스룸": "Stockplus Newsroom",
    "kb증권 리서치": "KB证券研究",
}

_AGGREGATOR_HOSTS = {
    "news.google.com",
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "qnmlgb.tech",
    "www.qnmlgb.tech",
}

_GENERIC_PUBLISHER_NAMES = {
    "google news",
    "bing news",
    "google 新闻",
    "必应新闻",
    "中国财经",
    "全球财经",
    "华尔街",
    "亚洲市场",
}

_TITLE_SEPARATORS = (" - ", " – ", " — ", " | ")
_SPACE = re.compile(r"\s+")


def _clean_name(value: Any) -> str:
    name = _SPACE.sub(" ", str(value or "")).strip(" \t\r\n-|—–")
    if not name:
        return ""
    alias = _NAME_ALIASES.get(name.casefold())
    return alias or name[:160]


def _hostname(value: str) -> str:
    try:
        hostname = (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    while hostname.startswith(("www.", "m.", "amp.")):
        hostname = hostname.split(".", 1)[1]
    return hostname


def _is_aggregator_url(value: str) -> bool:
    hostname = _hostname(value)
    return not hostname or hostname in _AGGREGATOR_HOSTS


def publisher_from_url(value: str) -> str:
    hostname = _hostname(value)
    if not hostname or hostname in _AGGREGATOR_HOSTS:
        return ""
    for domain, label in _DOMAIN_LABELS:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return label
    return hostname


def publisher_from_title(title: str) -> str:
    """Read the publisher suffix used by Google/Bing News discovery feeds."""

    for separator in _TITLE_SEPARATORS:
        if separator not in title:
            continue
        candidate = _clean_name(title.rsplit(separator, 1)[-1])
        if (
            1 < len(candidate) <= 80
            and candidate.casefold() not in _GENERIC_PUBLISHER_NAMES
            and not any(mark in candidate for mark in ("。", "！", "？", "!", "?"))
        ):
            return candidate
    return ""


def _usable_publisher_name(value: Any) -> str:
    candidate = _clean_name(value)
    if not candidate or candidate.casefold() in _GENERIC_PUBLISHER_NAMES:
        return ""
    return candidate


def _usable_url(value: Any) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate[:2000]


def resolve_publisher_identity(
    *,
    explicit_name: Any = "",
    explicit_url: Any = "",
    article_url: str = "",
    title: str = "",
    source_name: str = "",
    source_url: str = "",
    source_config: Mapping[str, Any] | None = None,
) -> PublisherIdentity:
    """Separate the real publisher from the internal collection channel.

    Discovery feeds such as “中国财经资讯发现” are routing labels, never
    publishers.  The resolver prefers feed-declared publisher metadata, then a
    configured publisher or original website, and only uses the internal source
    name for a direct (non-discovery) feed.
    """

    config = source_config or {}
    publisher_url = _usable_url(explicit_url)
    configured_name = config.get("publisher") or config.get("expected_account")

    for value in (explicit_name, configured_name):
        name = _usable_publisher_name(value)
        if name:
            return PublisherIdentity(name, publisher_url or _publisher_site(article_url, source_url, config))

    for candidate_url in (publisher_url, article_url):
        name = publisher_from_url(candidate_url)
        if name:
            return PublisherIdentity(name, _usable_url(candidate_url))

    if bool(config.get("discovery_only")) or _is_aggregator_url(article_url):
        name = publisher_from_title(title)
        if name:
            return PublisherIdentity(name, publisher_url)

    source_site_name = publisher_from_url(source_url)
    if source_site_name:
        return PublisherIdentity(source_site_name, _usable_url(source_url))

    if not bool(config.get("discovery_only")):
        direct_name = _usable_publisher_name(source_name)
        if direct_name:
            return PublisherIdentity(direct_name, _usable_url(source_url))

    return PublisherIdentity(UNKNOWN_PUBLISHER, publisher_url)


def _publisher_site(article_url: str, source_url: str, config: Mapping[str, Any]) -> str:
    for value in (config.get("publisher_url"), article_url, config.get("public_page"), source_url):
        candidate = _usable_url(value)
        if candidate and not _is_aggregator_url(candidate):
            return candidate
    return ""
