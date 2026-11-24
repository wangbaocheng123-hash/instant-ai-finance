"""Lock policy news as evidence candidates, never as an inferred rate decision."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .database import connect, transaction
from .rules import clean_text, normalized_url


BANKS = {
    "fed": ("美联储", r"美联储|聯儲|联储|\bfed(?:eral reserve)?\b|\bfomc\b", ("federalreserve.gov", "frbsf.org")),
    "ecb": ("欧洲央行", r"欧洲央行|歐洲央行|\becb\b|european central bank", ("ecb.europa.eu",)),
    "boj": ("日本央行", r"日本央行|\bboj\b|bank of japan", ("boj.or.jp",)),
    "boe": ("英国央行", r"英国央行|英國央行|\bboe\b|bank of england", ("bankofengland.co.uk",)),
    "pboc": ("中国人民银行", r"中国央行|人民银行|人民銀行|\bpboc\b|people.s bank of china", ("pbc.gov.cn",)),
}
MEDIA_DOMAINS = ("cnbc.com", "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "apnews.com", "cls.cn")
POLICY_TERMS = r"加息|降息|利率|货币政策|貨幣政策|紧缩|寬鬆|宽松|\brates?\b|\bhikes?\b|\bcuts?\b|monetary policy|tighten|easing|FOMC (?:statement|minutes)|minutes of the federal open market committee"
# Explicit policy mechanics are often reported without the word "rate".
POLICY_TERMS += r"|federal funds|basis[- ]points?|量化宽松|量化寬鬆|量化紧缩|量化緊縮|缩表|縮表|扩表|擴表|\bQE\b|\bQT\b|quantitative (?:easing|tightening)|balance[ -]sheet (?:runoff|reduction|policy)|asset purchases"
PRIORITY_SOURCE_KEYS = ('federal-reserve-monetary-policy', 'federal-reserve-press',
                        'federal-reserve-speeches', 'federal-reserve-testimony', 'cnbc-top-news', 'cnbc-finance')
SPEECH_TERMS = r"economic outlook|monetary policy|经济展望|經濟展望|通胀|inflation"
FORECAST_TERMS = r"预计|預計|预测|預測|预测调查|调查|\bforecast|\bpoll\b|economists? expect|strategists?|goldman|高盛|jpmorgan|摩根|美银|美国银行|bank of america|deutsche bank|德意志"
INSTITUTION_TERMS = r"economists?|strategists?|经济学家|机构|goldman|高盛|jpmorgan|摩根|美银|美国银行|bank of america|deutsche bank|德意志"
MARKET_TERMS = r"fedwatch|\bodds\b|probabilit|traders? (?:expect|price|bet)|市场押注|市場押注|概率|機率|期货定价|期貨定價"
SPEAKERS = (
    ("Christopher Waller", r"\bwaller\b|沃勒"), ("Kevin Warsh", r"\bwarsh\b|沃什|沃许|沃許"),
    ("Jerome Powell", r"\bpowell\b|鲍威尔|鮑威爾"), ("John Williams", r"\bwilliams\b|威廉姆斯"),
    ("Philip Jefferson", r"\bjefferson\b|杰斐逊|傑斐遜"), ("Michelle Bowman", r"\bbowman\b|鲍曼|鮑曼"),
    ("Lisa Cook", r"\bcook\b|库克|庫克"), ("Mary Daly", r"\bdaly\b|戴利"),
    ("Lorie Logan", r"\blogan\b|洛根"), ("Beth Hammack", r"\bhammack\b|哈马克|哈馬克"),
    ("Christine Lagarde", r"\blagarde\b|拉加德"), ("Andrew Bailey", r"\bbailey\b|贝利|貝利"),
    ("Kazuo Ueda", r"\bueda\b|植田"),
)
KIND_LABELS = {"decision": "利率决议线索", "minutes": "会议纪要线索", "official_comment": "官员表态线索", "forecast": "机构预测", "market_expectation": "市场预期", "policy_report": "利率政策报道"}
DISPATCH_LOCK = threading.Lock()


def _at(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def policy_url(value: str) -> str:
    parsed = urlsplit(value)
    allowed = MEDIA_DOMAINS + tuple(domain for bank in BANKS.values() for domain in bank[2])
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(_host_matches(parsed.hostname or "", domain) for domain in allowed)
            or re.search(r"(?:^|[?&])(?:token|secret|password|api_?key|auth)=", parsed.query, re.I)):
        raise ValueError("利率政策线索须有已核验发布方的公开HTTPS原链接")
    return normalized_url(value)


def classify_policy(title: str, summary: str, url: str, *, source_key: str = "") -> dict[str, Any] | None:
    """Only classify supplied text; absent amounts/conditions/originality stay absent."""
    try:
        link = policy_url(url)
    except ValueError:
        return None
    title, summary = clean_text(title), clean_text(summary)
    text = title + " " + summary
    host = urlsplit(link).hostname or ""
    bank = next((key for key, (_, _, domains) in BANKS.items() if any(_host_matches(host, domain) for domain in domains)), None)
    bank = bank or next((key for key, (_, pattern, _) in BANKS.items() if re.search(pattern, title, re.I)), None)
    bank = bank or next((key for key, (_, pattern, _) in BANKS.items() if re.search(pattern, summary, re.I)), None)
    if not bank and any(re.search(r"\b" + re.escape(name) + r"\b", title, re.I) for name, _ in SPEAKERS[:10]):
        bank = "fed"
    if not bank:
        return None
    if re.search(r"\bcuts?\s+(?:staff|jobs|workforce|costs|spending)\b", title, re.I) and not re.search(r"interest rates?|policy rates?|federal funds|monetary|加息|降息|利率|QE|QT", text, re.I):
        return None
    official = any(_host_matches(host, domain) for domain in BANKS[bank][2])
    if not re.search(POLICY_TERMS, text, re.I) and not (official and re.search(SPEECH_TERMS, text, re.I)):
        return None
    speaker = next((name for name, pattern in SPEAKERS if re.search(pattern, title, re.I)), "")
    if re.search(r"minutes|会议纪要|會議紀要", title, re.I):
        kind = "minutes"
    elif re.search(FORECAST_TERMS, title, re.I) and (not speaker or re.search(INSTITUTION_TERMS, title, re.I)):
        kind = "forecast"
    elif speaker or (official and ("speech" in link or "testimony" in link)):
        kind = "official_comment"
    elif re.search(MARKET_TERMS, title, re.I):
        kind = "market_expectation"
    elif re.search(r"(?:issues|releases?) (?:an? )?(?:FOMC|monetary policy) statement|利率决议|利率決議|(?:fed|ecb|boe|boj|央行|人民银行|bank of japan|bank of england).{0,45}(?:raises|cuts|holds|维持|上调|下调)|interest rate decision", title, re.I):
        kind = "decision"
    else:
        kind = "policy_report"
    return {"bank": bank, "bankName": BANKS[bank][0], "kind": kind, "kindLabel": KIND_LABELS[kind],
            "speaker": speaker, "sourceRole": "official-original" if official else "publisher-report",
            "originality": "official-original" if official else "original-channel-verification-required"}


def lock_policy_entry(connection, source, entry, *, item_id: int, observed_at: str) -> bool:
    if source.kind == "official_calendar":
        return False
    policy = classify_policy(entry.title, entry.summary, entry.url, source_key=source.key)
    if not policy:
        return False
    now = _at(observed_at)
    if now is None:
        return False
    connection.execute("INSERT OR IGNORE INTO schema_meta(key,value) VALUES('monetary_policy_started_at',?)", (_iso(now),))
    started = _at(connection.execute("SELECT value FROM schema_meta WHERE key='monetary_policy_started_at'").fetchone()[0]) or now
    published = _at(entry.published_at)
    # Keep uncertain-date clues visible, but don't turn an undated archive into a new report.
    fresh = bool(published and max(started - timedelta(hours=24), now - timedelta(days=7)) <= published <= now + timedelta(minutes=5))
    title, excerpt = clean_text(entry.title)[:500], clean_text(entry.summary)[:1800]
    link = policy_url(entry.url)
    day = published.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() if published else "undated"
    normalized_title = re.sub(r"[\W_]+", "", title.casefold())
    story_key = hashlib.sha256(f"{policy['bank']}|{day}|{normalized_title}".encode()).hexdigest()
    existing = connection.execute("SELECT * FROM monetary_policy_signals WHERE story_key=? OR source_url=? ORDER BY first_seen_at LIMIT 1", (story_key, link)).fetchone()
    signal_id = existing["signal_key"] if existing else "rate-policy:" + hashlib.sha256(link.encode()).hexdigest()[:40]
    evidence = {"title": title, "url": link, "publisher": entry.publisher or source.name,
                "publishedAt": _iso(published) if published else "", "excerpt": excerpt,
                "sourceRole": policy["sourceRole"], "originality": policy["originality"]}
    prior = json.loads(existing["payload_json"]) if existing else {}
    sources = {record["url"]: record for record in prior.get("sources", [])}
    sources[link] = evidence
    # Prefer a verified official original, then the earliest *observed* publication.
    records = sorted(sources.values(), key=lambda r: (r["sourceRole"] != "official-original", r["publishedAt"] or "9999"))[:5]
    lead = records[0]
    policy["sourceRole"], policy["originality"] = lead["sourceRole"], lead["originality"]
    # A feed changing only its timestamp must not create another interpretation.
    evidence_hash = hashlib.sha256(json.dumps([lead["title"], lead["url"], lead["excerpt"], policy], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    payload = {"key": signal_id, "title": title, **policy, "firstObservedSource": lead,
               "sources": records, "evidenceHash": evidence_hash,
               "publishedAt": lead["publishedAt"], "detectedAt": prior.get("detectedAt", _iso(now)),
               "publicationTimeStatus": "feed-reported-time",
               "factStatus": "candidate-requires-verification", "evidenceScope": "feed-title-and-permitted-summary"}
    changed = existing is None or evidence_hash != existing["evidence_hash"] or (existing["status"] == "date_review" and fresh)
    connection.execute("""INSERT INTO monetary_policy_signals(signal_key,story_key,source_url,item_id,evidence_hash,payload_json,
            first_seen_at,updated_at,expires_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(signal_key) DO UPDATE SET item_id=excluded.item_id,evidence_hash=excluded.evidence_hash,
            payload_json=excluded.payload_json,updated_at=excluded.updated_at,
            status=CASE WHEN monetary_policy_signals.evidence_hash<>excluded.evidence_hash
                OR (monetary_policy_signals.status='date_review' AND excluded.status='pending')
                THEN excluded.status ELSE monetary_policy_signals.status END,
            attempts=CASE WHEN monetary_policy_signals.evidence_hash<>excluded.evidence_hash THEN 0 ELSE monetary_policy_signals.attempts END,
            next_attempt_at=CASE WHEN monetary_policy_signals.evidence_hash<>excluded.evidence_hash THEN '' ELSE monetary_policy_signals.next_attempt_at END""",
        (signal_id, story_key, link, item_id, evidence_hash, json.dumps(payload, ensure_ascii=False),
         _iso(now), _iso(now), _iso((published or now) + timedelta(days=7)), "pending" if fresh else "date_review"))
    item = connection.execute("SELECT topics_json,entities_json FROM items WHERE id=?", (item_id,)).fetchone()
    topics, entities = json.loads(item[0]), json.loads(item[1])
    if "宏观政策" not in topics:
        topics.append("宏观政策")
    if policy["bankName"] not in entities:
        entities.append(policy["bankName"])
    connection.execute("UPDATE items SET importance_score=MAX(importance_score,90),topics_json=?,entities_json=?,event_type='央行利率政策线索' WHERE id=?",
                       (json.dumps(topics, ensure_ascii=False), json.dumps(entities, ensure_ascii=False), item_id))
    return changed and fresh


def _feedback(receipt: dict[str, Any]) -> str:
    return {"received": "罗盘已收到，等待既有GPT任务核验", "analyzing": "罗盘正在核验", "analyzed": "罗盘已完成解读",
            "skipped": "罗盘已核验，本次未生成新报告", "failed": "罗盘核验待重试"}.get(receipt.get("status"), "罗盘已收到")


def dispatch_policy_signals(*, path: object = None, now: datetime | None = None,
                            assignment_fetcher: Callable | None = None, sender: Callable | None = None,
                            target_url: str | None = None) -> dict[str, Any]:
    if not DISPATCH_LOCK.acquire(blocking=False):
        return {"state": "running", "delivered": 0}
    try:
        return _dispatch(path=path, now=now or datetime.now(UTC), assignment_fetcher=assignment_fetcher, sender=sender, target_url=target_url)
    finally:
        DISPATCH_LOCK.release()


def _dispatch(*, path, now, assignment_fetcher, sender, target_url) -> dict[str, Any]:
    import os
    from .watch_events import _fetch_payload, DEFAULT_COMPASS_EVENTS_URL
    from .event_signal_delivery import _post_json, DEFAULT_COMPASS_SIGNAL_URL, validate_compass_signal_url

    at = _iso(now)
    with transaction(path) as db:
        db.execute("DELETE FROM monetary_policy_signals WHERE expires_at<=?", (at,))
        count = db.execute("SELECT COUNT(*) FROM monetary_policy_signals WHERE status<>'date_review'").fetchone()[0]
    result = {"state": "idle", "delivered": 0, "failed": 0}
    if not count:
        return result
    url = validate_compass_signal_url(target_url or os.environ.get("INSTANT_AI_COMPASS_SIGNAL_URL", DEFAULT_COMPASS_SIGNAL_URL))
    try:
        response = (assignment_fetcher or _fetch_payload)(os.environ.get("INSTANT_AI_COMPASS_EVENTS_URL", DEFAULT_COMPASS_EVENTS_URL))
        assignment = response.get("monetaryPolicyIngestion", {})
        if assignment.get("contractVersion") != 1 or not assignment.get("deliveryToken"):
            return {**result, "state": "waiting_receiver"}
    except Exception:
        return {**result, "state": "waiting_receiver"}
    receipts = assignment.get("receipts", {})
    with transaction(path) as db:
        rows = db.execute("SELECT * FROM monetary_policy_signals WHERE status<>'date_review'").fetchall()
        for row in rows:
            receipt = receipts.get(row["signal_key"], {})
            if receipt.get("evidenceHash") == row["evidence_hash"]:
                db.execute("UPDATE monetary_policy_signals SET status='delivered',feedback=?,compass_signal_id=?,last_error='' WHERE signal_key=?",
                           (_feedback(receipt), str(receipt.get("id") or "")[:120], row["signal_key"]))
            elif row["status"] == "delivered" and assignment.get("inventoryComplete") is True:
                db.execute("UPDATE monetary_policy_signals SET status='pending',next_attempt_at='' WHERE signal_key=?", (row["signal_key"],))
        pending = db.execute("SELECT * FROM monetary_policy_signals WHERE status IN ('pending','failed') AND (next_attempt_at='' OR next_attempt_at<=?) ORDER BY first_seen_at LIMIT 10", (at,)).fetchall()
    for row in pending:
        payload = json.loads(row["payload_json"])
        while len(json.dumps(payload, ensure_ascii=False).encode()) > 27 * 1024 and len(payload["sources"]) > 1:
            payload["sources"].pop()
        try:
            receipt = (sender or _post_json)(url, {"contractVersion": 1, "sourceSystem": "instant-ai",
                "signalKind": "monetary-policy-candidate", "deliveryToken": assignment["deliveryToken"], "candidate": payload})
            accepted = receipt.get("signal", {})
            if accepted.get("evidenceHash") != row["evidence_hash"] or not accepted.get("id"):
                raise ValueError("罗盘未返回匹配的利率信号回执")
            with transaction(path) as db:
                db.execute("UPDATE monetary_policy_signals SET status='delivered',compass_signal_id=?,feedback=?,last_error='',attempts=0 WHERE signal_key=? AND evidence_hash=?",
                           (str(accepted["id"])[:120], _feedback(accepted), row["signal_key"], row["evidence_hash"]))
            result["delivered"] += 1
        except Exception as error:
            attempts = row["attempts"] + 1
            retry = _iso(now + timedelta(seconds=min(1800, 30 * 2 ** min(attempts, 6))))
            with transaction(path) as db:
                db.execute("UPDATE monetary_policy_signals SET status='failed',attempts=?,next_attempt_at=?,last_error=? WHERE signal_key=? AND evidence_hash=?",
                           (attempts, retry, type(error).__name__ + ": 投递未确认，自动重试", row["signal_key"], row["evidence_hash"]))
            result["failed"] += 1
    return {**result, "state": "retrying" if result["failed"] else "connected"}


def policy_snapshot(*, path: object = None, now: datetime | None = None) -> dict[str, Any]:
    at = _iso(now or datetime.now(UTC))
    with connect(path) as db:
        rows = db.execute("SELECT * FROM monetary_policy_signals WHERE expires_at>? ORDER BY first_seen_at DESC LIMIT 30", (at,)).fetchall()
        marks = ','.join('?' for _ in PRIORITY_SOURCE_KEYS)
        source_rows = db.execute(f"SELECT key,name,enabled,last_success_at,next_fetch_at,last_error FROM sources WHERE key IN ({marks})", PRIORITY_SOURCE_KEYS).fetchall()
    signals = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        signals.append({**payload, "itemId": row["item_id"], "status": row["status"],
                        "pipelineStatus": row["feedback"] if row["status"] == "delivered" else
                        "日期或时效待核验，尚未自动输送" if row["status"] == "date_review" else
                        "投递未确认，自动重试" if row["status"] == "failed" else "已锁定，等待输送罗盘"})
    sources = []
    current = _at(at)
    for row in source_rows:
        success = _at(row['last_success_at'])
        state = ('disabled' if not row['enabled'] else 'error' if row['last_error'] else
                 'pending' if not success else 'stale' if (current-success).total_seconds() > 900 or success > current+timedelta(minutes=5) else 'ok')
        sources.append({'key': row['key'], 'name': row['name'], 'state': state,
                        'lastSuccessAt': row['last_success_at'] or '', 'nextCheckAt': row['next_fetch_at'] or ''})
    return {"signals": signals, "monitoring": {"defaultIntervalSeconds": 300, "sources": sources},
            "coverage": "优先检查已接入的美联储政策、讲话/证词及财经媒体；默认每5分钟一轮，来源异常另列。首发渠道须核验，不保证覆盖全部采访。"}
