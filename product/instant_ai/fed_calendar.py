"""Read official Fed agendas without treating an event date as a publication date.

The Board monthly calendar uses US Eastern time (publisher profile); host pages
must state their own zone. Only the Board and reviewed SF Fed event pages are
followed. Livestream, ticketing and other third-party links are never fetched.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .official_monitor import validate_official_url


FED_CALENDAR_URL = "https://www.federalreserve.gov/newsevents/calendar.htm"
SF_EVENTS_URL = "https://www.frbsf.org/news-and-media/events/"
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self) -> str:
        return " ".join(" ".join(child.text() if isinstance(child, Node) else child for child in self.children).split())

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()


class Document(HTMLParser):
    def __init__(self, body: bytes):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]
        self.skip = 0
        self.feed(body.decode("utf-8", errors="replace"))

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "template"}:
            self.skip += 1
        if self.skip:
            return
        node = Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            if len(self.stack) > 180:
                raise ValueError("官方日历HTML层级异常")
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in {"script", "style", "svg", "template"}:
                self.skip -= 1
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not self.skip:
            self.stack[-1].children.append(data)


@dataclass(frozen=True)
class CalendarEvent:
    key: str
    title: str
    scheduled_at: datetime | None
    source_name: str
    url: str
    evidence: str
    importance: int = 4
    detail_url: str = ""
    local_date: str = ""
    local_timezone: str = "America/New_York"


class CalendarEvents(list):
    def __init__(self, events, warnings=()):
        super().__init__(events)
        self.warnings = list(warnings)


def _digest(text: str) -> str:
    return hashlib.sha256(" ".join(text.casefold().split()).encode()).hexdigest()[:24]


def sf_event_url(url: str) -> str:
    """Canonical identity and fetch target; no query/fragment or arbitrary host."""
    parsed = urlsplit(url)
    if parsed.hostname not in {"www.frbsf.org", "frbsf.org"} or not re.fullmatch(
        r"/news-and-media/events/(?:conferences/)?20\d{2}/\d{2}/[a-z0-9-]+/?", parsed.path
    ):
        return ""
    try:
        validate_official_url(url)
    except ValueError:
        return ""
    return urlunsplit(("https", "www.frbsf.org", parsed.path.rstrip("/") + "/", "", ""))


def _clock(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*", text, re.I)
    if not match or not 1 <= int(match[1]) <= 12 or int(match[2] or 0) > 59:
        return None
    return int(match[1]) % 12 + (12 if match[3].lower() == "p" else 0), int(match[2] or 0)


def _unambiguous_local(local: datetime) -> bool:
    wall = local.replace(tzinfo=None)
    return (local.astimezone(UTC).astimezone(local.tzinfo).replace(tzinfo=None) == wall
            and local.replace(fold=0).utcoffset() == local.replace(fold=1).utcoffset())


def _speaker_label(text: str) -> str:
    names = {"Kevin Warsh": "沃什", "Jerome H. Powell": "鲍威尔", "Jerome Powell": "鲍威尔"}
    text = re.sub(r"^(?:Speech|Discussion|Testimony)\s*[-–—]\s*", "", text, flags=re.I)
    for english, chinese in names.items():
        text = text.replace(english, chinese)
    for english, chinese in [("Vice Chair for Supervision", "监管副主席"), ("Vice Chair", "副主席"), ("Chairman", "主席"), ("Chair", "主席"), ("Governor", "理事")]:
        text = re.sub(r"\b" + english + r"\b", chinese, text)
    return text


def parse_fed_month(body: bytes, *, year: int, month: int, url: str) -> list[CalendarEvent]:
    document = Document(body)
    article = next((node for node in document.root.walk() if node.attrs.get("id") == "article"), None)
    expected = f"{MONTH_NAMES[month - 1]} {year}"
    if article is None or not any(node.tag == "h4" and node.text() == expected for node in article.walk()):
        raise ValueError("美联储月历月份或页面结构不符，保留已有事件并重试")
    section = ""
    recognized = 0
    invalid = 0
    result: list[CalendarEvent] = []
    occurrences: dict[str, int] = {}
    for node in article.walk():
        if node.tag == "h4":
            section = node.text()
            recognized += int(section in {"Speeches", "Testimony", "FOMC Meetings", "Beige Book"})
        if not node.has_class("panel-body") or section not in {"Speeches", "Testimony", "FOMC Meetings", "Beige Book"}:
            continue
        columns = {size: next((child for child in node.walk() if child.has_class(f"col-xs-{size}")), None) for size in (2, 7, 3)}
        if any(value is None for value in columns.values()):
            invalid += 1
            continue
        time = _clock(columns[2].text())
        dates = columns[3].text()
        # Date-only entries are not assigned a fabricated midnight/time zone.
        if (time is None and columns[2].text().casefold() not in {"", "tbd", "to be announced"}) or not re.fullmatch(r"\d{1,2}(?:\s*[-–,]\s*\d{1,2})*", dates):
            invalid += 1
            continue
        content = columns[7]
        paragraphs = [child.text() for child in content.children if isinstance(child, Node) and child.tag == "p" and child.text() and child.text() != "Watch Live"]
        if not paragraphs:
            invalid += 1
            continue
        days = [int(value) for value in re.findall(r"\d+", dates)]
        if "-" in dates or "–" in dates:
            days = days[-1:]  # FOMC decision/release is the final listed meeting day.
        speaker = paragraphs[0]
        subject = next((child.text() for child in content.walk() if child.has_class("calendar__title")), "")
        venue = " ".join(paragraphs[1:]).replace("Watch Live", "").strip()
        detail = next((target for child in content.walk() if child.tag == "a" and (target := sf_event_url(urljoin(url, child.attrs.get("href", ""))))), "")
        identity = _digest("|".join((speaker, subject, venue)))
        for day in days:
            try:
                local_day = datetime(year, month, day).date()
                local = datetime(year, month, day, *time, tzinfo=ET) if time else None
                if local is not None and not _unambiguous_local(local):
                    invalid += 1
                    continue
            except ValueError:
                invalid += 1
                continue
            if detail:
                key = f"fed:host-event:{_digest(detail)}"
            elif section == "Beige Book":
                key = f"fed:beige-book:{year}-{month:02d}"
            elif section == "FOMC Meetings":
                period = _digest(" ".join(paragraphs))
                occurrence = f"fomc:{period}"
                occurrences[occurrence] = occurrences.get(occurrence, 0) + 1
                key = f"fed:fomc:{year}-{month:02d}:{period}:{occurrences[occurrence]}"
            else:
                occurrences[identity] = occurrences.get(identity, 0) + 1
                key = f"fed:speech:{year}-{month:02d}:{identity}:{occurrences[identity]}"
            if section == "Beige Book":
                title = "美联储褐皮书公布"
            elif section == "FOMC Meetings":
                title = "美联储FOMC会议纪要公布" if "Minutes" in speaker else "美联储FOMC新闻发布会" if "Press Conference" in speaker else "美联储FOMC会议及政策发布"
            else:
                label = _speaker_label(speaker)
                topic = subject or venue or "公开讲话"
                title = f"美联储{label}：{topic}"
            relevant = re.search(r"econom|monetary|inflation|employment|productivity|artificial intelligence|financial stability|super intelligence|jobs|outlook", " ".join(paragraphs), re.I)
            ceremonial = subject.casefold() in {'welcoming remarks', 'greetings', 'congratulations'} and not relevant
            importance = 5 if re.match(r"(?:Speech|Discussion|Testimony)\s*[-–—]\s*Chair(?:man)?\b", speaker, re.I) or section == "FOMC Meetings" else 3 if ceremonial else 4
            result.append(CalendarEvent(key, title, local, "美联储官方日程", url,
                                        f"{expected}; {day}; {columns[2].text()} US Eastern; {speaker}; {venue}", importance, detail,
                                        local_date=local_day.isoformat() if local is None else ""))
    if not recognized:
        raise ValueError("美联储月历栏目结构变化，保留已有事件并重试")
    warnings = [f"美联储月历有{invalid}条记录的日期/时间或结构待核验；其余已确认记录继续采集"] if invalid else []
    if not result and not invalid:
        warnings.append("美联储月历尚未公布可读取日程，或页面结构待核验；不能确认已完整覆盖")
    return CalendarEvents(result, warnings)


def parse_sf_event(body: bytes, *, url: str, now: datetime | None = None) -> CalendarEvent:
    url = sf_event_url(url)
    if not url:
        raise ValueError("不是已核验的旧金山联储会议详情地址")
    document = Document(body)
    headings = [node.text() for node in document.root.walk() if node.tag == "h1"]
    if len(headings) != 1:
        raise ValueError("旧金山联储会议标题结构待核验")
    # Use the labelled event metadata, not article timestamps or later agenda times.
    text = document.root.text()
    start = text.find(headings[0])
    text = text[start:]
    date = re.search(r"\bDate\s+(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2})\b", text, re.I)
    if not date:
        raise ValueError("旧金山联储会议未给出可核验的举办日期")
    month = next((index for index, name in enumerate(MONTH_NAMES, 1) if date[1].lower() in {name.lower(), name[:3].lower()}), 0)
    if not month:
        raise ValueError("旧金山联储会议日期无效")
    day = datetime(int(date[3]), month, int(date[2])).date()
    english = headings[0]
    title = "美联储生产率与就业专题会议" if english.casefold() == "productivity and jobs task force conference" else f"美联储专题会议：{english}"
    importance = 5 if re.search(r"\b(?:Chairman|Chair)\s+of\s+(?:the\s+)?(?:Board|Federal Reserve)\b", text, re.I) else 4
    if now is not None and day < now.astimezone(PT).date():
        return CalendarEvent(f"fed:host-event:{_digest(url)}", title, None, "旧金山联储官方会议议程", url,
                             f"{english}; official local date: {day}; past index entry", importance, local_date=day.isoformat(), local_timezone="America/Los_Angeles")
    time = re.match(r"\s+Time\s+(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)\s+(PT|PST|PDT)\b", text[date.end():], re.I)
    if time is None:
        if re.match(r"\s+Time\b", text[date.end():], re.I):
            raise ValueError("旧金山联储已列出时间但时区或格式待核验")
        return CalendarEvent(f"fed:host-event:{_digest(url)}", title, None, "旧金山联储官方会议议程", url,
                             f"{english}; official local date: {day}; exact time not announced", importance, local_date=day.isoformat(), local_timezone="America/Los_Angeles")
    clock = _clock(time[1])
    if clock is None:
        raise ValueError("旧金山联储会议时间无效")
    local = datetime(day.year, day.month, day.day, *clock, tzinfo=PT)
    if not _unambiguous_local(local):
        if time[2].upper() in {"PST", "PDT"}:
            candidates = [local.replace(fold=fold) for fold in (0, 1)]
            matching = [candidate for candidate in candidates if candidate.tzname() == time[2].upper()
                        and candidate.astimezone(UTC).astimezone(PT).replace(tzinfo=None) == candidate.replace(tzinfo=None)]
            if not matching:
                raise ValueError("旧金山联储给出的时间位于夏令时缺失时段")
            local = matching[0]
        else:
            raise ValueError("旧金山联储给出的时间位于夏令时缺失或重复时段")
    if time[2].upper() in {"PST", "PDT"} and time[2].upper() != local.tzname():
        raise ValueError("旧金山联储显式时区与当日夏令时冲突")
    evidence = f"{english}; {day}; {time[1]} {time[2]}; Federal Reserve Bank of San Francisco"
    return CalendarEvent(f"fed:host-event:{_digest(url)}", title, local, "旧金山联储官方会议议程", url, evidence, importance)


def sf_upcoming_links(body: bytes, *, now: datetime) -> list[str]:
    document = Document(body)
    if not any(node.tag == "h1" and "Events" in node.text() for node in document.root.walk()):
        raise ValueError("旧金山联储会议索引结构待核验")
    urls: set[str] = set()
    for node in document.root.walk():
        if node.tag != "a":
            continue
        url = sf_event_url(urljoin(SF_EVENTS_URL, node.attrs.get("href", "")))
        if not url:
            continue
        # A URL's month may be the original publication month. The labelled
        # date in the detail page decides whether an event is upcoming.
        urls.add(url)
    if len(urls) > 12:
        raise ValueError("旧金山联储近期会议超过单轮上限，需核对索引分页")
    return sorted(urls)
