"""Instant AI owns future-event discovery; Compass receives public metadata only.

Reuse the official HTTP/DNS guard and feed parser. No model/search subscription,
credentials, portfolio data or news-lifecycle foreign keys are involved here.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Callable
from threading import Lock
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .collectors import parse_feed
from .database import connect, transaction
from .official_monitor import _fetch_official_channel, validate_official_url
from .fed_calendar import MONTH_NAMES, SF_EVENTS_URL, parse_fed_month, parse_sf_event, sf_upcoming_links

SHANGHAI = ZoneInfo('Asia/Shanghai')
MONTHS = {name.lower(): i for i, name in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'], 1)}
NUMBERS = {'first':1,'second':2,'third':3,'fourth':4}
DEFAULT_TARGET = 'http://127.0.0.1:32180/api/v1/integrations/instant-ai/calendar'
TSMC_URL = 'https://investor.tsmc.com/english/financial-calendar'
NVIDIA_URL = 'https://nvidianews.nvidia.com/cats/press_release.xml'
MICRON_URL = 'https://investors.micron.com/rss/pressrelease.aspx'


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).replace(microsecond=0).isoformat()


def _event(key: str, title: str, local: datetime, source_name: str, url: str,
           evidence: str, now: datetime, *, category: str = 'report', published: str = '') -> dict[str, Any]:
    beijing = local.astimezone(SHANGHAI)
    return {'key':key,'version':1,'title':title,'date':beijing.date().isoformat(),'time':beijing.strftime('%H:%M'),
            'status':'confirmed','category':category,'importance':4,'originalTimeZone':str(local.tzinfo),
            'originalScheduledAt':local.isoformat(),'note':'即时AI从官方日历或公告核实；已换算为北京时间。',
            'source':{'name':source_name,'url':validate_official_url(url),'publishedAt':published or '',
                      'verifiedAt':_iso(now),'evidenceExcerpt':evidence[:2000]}}


class _CalendarVars(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str,list[str]]] = []
        self.current: dict[str,str] = {}
        self.events: list[dict[str,str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        if tag == 'var':
            kind = dict(attrs).get('class') or ''
            if kind == 'atc_event': self.current = {}
            self.stack.append((kind, []))

    def handle_data(self, data: str) -> None:
        if self.stack: self.stack[-1][1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != 'var' or not self.stack: return
        kind, parts = self.stack.pop()
        if kind == 'atc_event':
            if self.current: self.events.append(self.current)
            self.current = {}
        else: self.current[kind] = ' '.join(''.join(parts).split())


def parse_tsmc_calendar(body: bytes, *, now: datetime) -> list[dict[str,Any]]:
    parser = _CalendarVars(); parser.feed(body.decode('utf-8', errors='replace')); result=[]
    for row in parser.events:
        title = row.get('atc_title','')
        monthly = re.search(r'TSMC Monthly Sales\s*-\s*([A-Za-z]+)\s+(\d{4})',title)
        quarterly = re.search(r'TSMC\s+([1-4])Q[\s\']?(\d{2,4})\s+Results',title)
        if not monthly and not quarterly: continue
        try:
            zone = ZoneInfo(row['atc_timezone']); local = datetime.fromisoformat(row['atc_date_start']).replace(tzinfo=zone)
        except (KeyError,ValueError): continue
        if monthly:
            month=MONTHS.get(monthly[1].lower())
            if not month: continue
            period=f'{monthly[2]}-{month:02d}'; key=f'tsmc:monthly-sales:{period}'
            label=f'台积电{monthly[2]}年{month}月营收公布'; category='company-data'
        else:
            year=int(quarterly[2]); year=year+2000 if year<100 else year
            key=f'tsmc:earnings:{year}Q{quarterly[1]}'; label=f'台积电{year}年第三季度业绩说明会' if quarterly[1]=='3' else f'台积电{year}年Q{quarterly[1]}业绩说明会'; category='report'
        result.append(_event(key,label,local,'TSMC Financial Calendar',TSMC_URL,
                            f"{title}; {row['atc_date_start']}; {row['atc_timezone']}",now,category=category))
    return result


def parse_earnings_announcements(body: bytes, *, issuer: str, name: str, now: datetime) -> list[dict[str,Any]]:
    result=[]
    for item in parse_feed(body,40):
        text=html.unescape(item.summary)
        if not re.search(r'\bwill (?:host|hold|report|release)\b',text,re.I): continue
        date_match=re.search(r'\b('+'|'.join(MONTHS)+r')\s+(\d{1,2})(?:,?\s+(20\d{2}))?',text,re.I)
        clock=re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*(PT|ET|PST|PDT|EST|EDT|MT|MST|MDT)\b',text,re.I)
        fiscal=re.search(r'(first|second|third|fourth)[ -]quarter (?:of )?(?:fiscal (?:year )?)?(20\d{2})',text,re.I)
        if not date_match or not clock or not fiscal or not item.published_at: continue
        published=datetime.fromisoformat(item.published_at)
        month=MONTHS[date_match[1].lower()]; year=int(date_match[3]) if date_match[3] else published.year+(1 if month<published.month-6 else 0)
        hour=int(clock[1]); minute=int(clock[2] or 0)
        if not 1<=hour<=12 or minute>59: continue
        hour=hour%12+(12 if clock[3].lower()=='p' else 0)
        zone_name={'PT':'America/Los_Angeles','PST':'America/Los_Angeles','PDT':'America/Los_Angeles','ET':'America/New_York','EST':'America/New_York','EDT':'America/New_York','MT':'America/Denver','MST':'America/Denver','MDT':'America/Denver'}[clock[4].upper()]
        try: local=datetime(year,month,int(date_match[2]),hour,minute,tzinfo=ZoneInfo(zone_name))
        except ValueError: continue
        if not published-timedelta(days=1)<=local<=published+timedelta(days=180): continue
        q=NUMBERS[fiscal[1].lower()]; fy=int(fiscal[2]); key=f'{issuer}:earnings:FY{fy}Q{q}:call'
        result.append(_event(key,f'{name}FY{fy}第三季度业绩电话会' if q==3 else f'{name}FY{fy}第{q}季度业绩电话会',local,
                            f'{name}官方业绩公告',item.url,text,now,published=item.published_at))
    return result


class _BlsCalendar(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True);self.cells=[];self.cell=None;self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag=='td': self.cell=None if 'other-month' in (dict(attrs).get('class') or '') else []
        if tag in {'script','style'}: self.skip+=1
    def handle_data(self,data):
        if self.cell is not None and not self.skip:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in {'script','style'} and self.skip:self.skip-=1
        if tag=='td' and self.cell is not None:self.cells.append(' '.join(' '.join(self.cell).split()));self.cell=None


def parse_bls_calendar(body: bytes, *, year: int, month: int, url: str, now: datetime) -> list[dict[str,Any]]:
    if b'Eastern Time' not in body:raise ValueError('BLS来源未明确声明日历时区，暂不确认北京时间')
    parser=_BlsCalendar();parser.feed(body.decode('utf-8',errors='replace'));result=[]
    families=[('Consumer Price Index','cpi','美国CPI'),('Producer Price Index','ppi','美国PPI'),('Employment Situation','employment','美国非农就业报告'),('Job Openings and Labor Turnover Survey','jolts','美国JOLTS职位空缺'),('Productivity and Costs','productivity','美国生产率与劳动力成本')]
    for cell in parser.cells:
        day_match=re.match(r'(\d{1,2})\b',cell)
        if not day_match:continue
        for english,key,label in families:
            release=re.search(re.escape(english)+r'\s+(.*?)\s+(\d{2}:\d{2}\s*[AP]M)',cell,re.I)
            if not release:continue
            clock=re.fullmatch(r'(\d{2}):(\d{2})\s*(AM|PM)',release[2],re.I)
            hour=int(clock[1])%12+(12 if clock[3].upper()=='PM' else 0)
            try:local=datetime(year,month,int(day_match[1]),hour,int(clock[2]),tzinfo=ZoneInfo('America/New_York'))
            except ValueError:continue
            period_text=release[1].strip()
            # A stable reporting-period key allows an official date change.
            period_key=re.sub(r'[^a-z0-9]+','-',period_text.lower()).strip('-') or f'{year}-{month:02d}'
            result.append(_event(f'bls:{key}:{period_key}',f'{label}（{period_text}）',local,'BLS官方发布日历',url,cell,now,category='market'))
    return result


def _sources(now: datetime) -> list[dict[str,Any]]:
    sources=[{'key':'tsmc-calendar','url':TSMC_URL,'parser':'tsmc'},
             {'key':'nvidia-announcements','url':NVIDIA_URL,'parser':'earnings','issuer':'nvidia','name':'英伟达'},
             {'key':'micron-announcements','url':MICRON_URL,'parser':'earnings','issuer':'micron','name':'美光'}]
    year,month=now.astimezone(SHANGHAI).year,now.astimezone(SHANGHAI).month
    for offset in range(3):
        y=year+(month-1+offset)//12;m=(month-1+offset)%12+1
        sources.append({'key':f'bls-{y}-{m:02d}','url':f'https://www.bls.gov/schedule/{y}/{m:02d}_sched.htm','parser':'bls','year':y,'month':m})
    # Beijing may already be in the next month while the Board still has an
    # important afternoon speech on its last local day.
    fed_now = now.astimezone(ZoneInfo('America/New_York'))
    year, month = fed_now.year, fed_now.month
    for offset in range(2):
        y=year+(month-1+offset)//12;m=(month-1+offset)%12+1
        sources.append({'key':f'fed-{y}-{m:02d}', 'name':f'美联储{y}年{m}月官方日程',
                        'url':f'https://www.federalreserve.gov/newsevents/{y}-{MONTH_NAMES[m-1]}.htm',
                        'parser':'fed-month','year':y,'month':m,'poll_seconds':900 if offset==0 else 3600})
    sources.append({'key':'sf-fed-events','name':'旧金山联储近期会议','url':SF_EVENTS_URL,'parser':'sf-index','poll_seconds':1800})
    return sources


def _store(scope: str, event: dict[str,Any], *, path: object, now: datetime) -> bool:
    with transaction(path) as db:
        previous=db.execute('SELECT * FROM future_calendar_events WHERE scope=? AND event_key=?',(scope,event['key'])).fetchone()
        if previous is None and event['key'].startswith('fed:') and event['originalScheduledAt'] and datetime.fromisoformat(event['originalScheduledAt']) <= now:
            return False  # New calendar sources announce upcoming events; actual past speeches use the speech feed.
        if (previous is None and event['key'].startswith('fed:') and not event['originalScheduledAt']
                and event['date'] < now.astimezone(ZoneInfo(event['originalTimeZone'])).date().isoformat()):
            return False
        if previous is not None and event['originalScheduledAt']:
            older = json.loads(previous['payload_json'])
            if (older.get('originalScheduledAt') and datetime.fromisoformat(older['originalScheduledAt']) == datetime.fromisoformat(event['originalScheduledAt'])
                    and urlparse(older['source']['url']).hostname == 'www.federalreserve.gov'
                    and urlparse(event['source']['url']).hostname == 'www.frbsf.org'):
                event = {**event, 'source': older['source'], 'note': older['note']}
        if previous is not None and not event['originalScheduledAt']:
            older = json.loads(previous['payload_json'])
            if older.get('originalScheduledAt') and event['date'] < now.astimezone(ZoneInfo(event['originalTimeZone'])).date().isoformat():
                return False  # A historical index entry is not a cancellation of its known time.
            if older.get('originalScheduledAt') and older['source']['url'] != event['source']['url']:
                known_day = datetime.fromisoformat(older['originalScheduledAt']).astimezone(ZoneInfo(event['originalTimeZone'])).date().isoformat()
                if known_day == event['date']:
                    return False  # A less precise host listing cannot erase a Board-confirmed time.
        normalized={**event,'version':0,'source':{**event['source'],'verifiedAt':''}}
        checksum=hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        changed=previous is None or previous['facts_hash']!=checksum
        version=1 if previous is None else previous['version']+int(changed);event={**event,'version':version}
        db.execute('''INSERT INTO future_calendar_events(scope,event_key,version,facts_hash,payload_json,checked_at) VALUES(?,?,?,?,?,?)
          ON CONFLICT(scope,event_key) DO UPDATE SET version=excluded.version,facts_hash=excluded.facts_hash,payload_json=excluded.payload_json,checked_at=excluded.checked_at,
          attempts=CASE WHEN future_calendar_events.version<>excluded.version THEN 0 ELSE attempts END,
          next_attempt_at=CASE WHEN future_calendar_events.version<>excluded.version THEN '' ELSE next_attempt_at END''',
          (scope,event['key'],version,checksum,json.dumps(event,ensure_ascii=False),_iso(now)))
    return changed


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _post_calendar(url: str,payload: dict[str,Any]) -> dict[str,Any]:
    parsed=urlparse(url)
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'/api/v1/integrations/instant-ai/calendar','/api/v1/integrations/instant-ai/calendar/status'}:
        raise ValueError('日历只能投递到本机罗盘固定接收接口')
    request=Request(url,data=json.dumps(payload,ensure_ascii=False).encode(),method='POST',headers={'Content-Type':'application/json; charset=utf-8','Accept':'application/json'})
    with build_opener(_NoRedirect()).open(request,timeout=8) as response:raw=response.read(65537)
    if len(raw)>65536:raise ValueError('日历接收回执超过限制')
    result=json.loads(raw)
    if not isinstance(result,dict) or result.get('ok') is not True:raise ValueError('罗盘未确认接收日历')
    return result


class _FetchCache:
    """Share host detail fetches across concurrent calendar and index adapters."""
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.lock = Lock()
        self.pending: dict[str, Future] = {}

    def __call__(self, url):
        with self.lock:
            owner = url not in self.pending
            if owner:
                self.pending[url] = Future()
            future = self.pending[url]
        if owner:
            try:
                result = self.fetcher(url)
                if int(result.get('status', 0)) != 200:
                    raise ValueError(f'官方日历未返回200（{result.get("status", 0)}）')
                if len(result['body']) > 1024 * 1024:
                    raise ValueError('官方日历响应超过限制')
                future.set_result(result)
            except Exception as error:
                future.set_exception(error)
        return future.result()


def _read_source(source: dict[str, Any], *, fetcher: Callable, now: datetime):
    events = []
    warnings = []
    try:
        body = fetcher(source['url'])['body']
        kind = source['parser']
        if kind == 'tsmc':
            if b'Upcoming Events' not in body:
                raise ValueError('TSMC官方日历结构待核对')
            parsed = parse_tsmc_calendar(body, now=now)
            if not parsed:
                raise ValueError('TSMC官方日历未解析到事件，保留旧节点并等待复核')
            events = [('home', event) for event in parsed]
        elif kind == 'earnings':
            events = [('home', event) for event in parse_earnings_announcements(body, issuer=source['issuer'], name=source['name'], now=now)]
        elif kind == 'bls':
            parsed = parse_bls_calendar(body, year=source['year'], month=source['month'], url=source['url'], now=now)
            if not parsed:
                raise ValueError('BLS日历未解析到核心发布时间，保留旧节点并等待复核')
            events = [('zijin', event) for event in parsed]
        elif kind in {'fed-month', 'sf-index'}:
            if kind == 'fed-month':
                parsed = parse_fed_month(body, year=source['year'], month=source['month'], url=source['url'])
                warnings.extend(getattr(parsed, 'warnings', []))
            else:
                parsed = []
                def read_host(url):
                    try:
                        return parse_sf_event(fetcher(url)['body'], url=url, now=now), ''
                    except Exception as error:
                        return None, '主办方议程待核验：' + type(error).__name__ + ': ' + str(error)[:120]
                with ThreadPoolExecutor(max_workers=3) as hosts:
                    for candidate, error in hosts.map(read_host, sf_upcoming_links(body, now=now)):
                        if candidate is not None:
                            parsed.append(candidate)
                        if error:
                            warnings.append(error)
            for candidate in parsed:
                board_reference = None
                if candidate.detail_url:
                    try:
                        detail = parse_sf_event(fetcher(candidate.detail_url)['body'], url=candidate.detail_url)
                        if detail.scheduled_at is not None and candidate.scheduled_at is not None and abs(detail.scheduled_at - candidate.scheduled_at) > timedelta(hours=12):
                            warnings.append(f'conflict[{candidate.key}] 美联储与主办方日程时间不一致，暂停确认该会议')
                            continue
                        if detail.scheduled_at is not None:
                            if candidate.scheduled_at == detail.scheduled_at:
                                board_reference = candidate
                            candidate = detail
                    except Exception as error:
                        warnings.append('主办方详情暂未核验，保留美联储日程：' + type(error).__name__)
                if candidate.scheduled_at is None:
                    event = {'key': candidate.key, 'version': 1, 'title': candidate.title, 'date': candidate.local_date,
                             'time': '', 'status': 'tentative', 'category': 'market', 'originalTimeZone': candidate.local_timezone,
                             'originalScheduledAt': '', 'note': '官方仅公布当地举办日期；具体时点及北京时间待确认。',
                             'source': {'name': candidate.source_name, 'url': candidate.url, 'publishedAt': '',
                                        'verifiedAt': _iso(now), 'evidenceExcerpt': candidate.evidence}}
                else:
                    event = _event(candidate.key, candidate.title, candidate.scheduled_at, candidate.source_name,
                                   candidate.url, candidate.evidence, now, category='market')
                event['importance'] = candidate.importance
                event['note'] += ' 这是会前日程，不代表讲话原文已经发布。'
                if board_reference is not None:
                    # The existing receiver supports Board evidence. Keep that
                    # genuine corroborating source, rather than inventing a URL
                    # or expanding another application's allowlist.
                    event['source']['name'] = '美联储官方日程（主办方复核）'
                    event['source']['url'] = board_reference.url
                    event['source']['evidenceExcerpt'] = (board_reference.evidence + '; 主办方复核：' + candidate.evidence + '; ' + candidate.url)[:2000]
                    event['note'] += ' 主办方议程：' + candidate.url
                scopes = ['home', 'zijin'] if candidate.importance >= 4 else ['zijin']
                events.extend((scope, event) for scope in scopes)
        else:
            raise ValueError('未配置官方日历适配器')
    except Exception as error:
        warnings.append(type(error).__name__ + ': ' + str(error)[:160])
    return source, events, '; '.join(warnings)[:500]


def supply_future_events(*, path: object = None, now: datetime | None = None, fetcher: Callable = _fetch_official_channel,
                         assignment_fetcher: Callable | None = None, sender: Callable = _post_calendar,
                         force: bool = False, target_url: str = DEFAULT_TARGET) -> dict[str, Any]:
    from .watch_events import _fetch_payload, DEFAULT_COMPASS_EVENTS_URL
    from .calendar_notices import queue_calendar_notices
    import os

    now = now or datetime.now(UTC)
    at = _iso(now)
    report = {'checkedAt': at, 'sources': 0, 'discovered': 0, 'queued': 0, 'delivered': 0, 'failed': 0, 'errors': []}
    assignment = {}
    try:
        payload = (assignment_fetcher or _fetch_payload)(os.environ.get('INSTANT_AI_COMPASS_EVENTS_URL', DEFAULT_COMPASS_EVENTS_URL))
        assignment = payload.get('calendarIngestion', {})
    except Exception as error:
        report['errors'].append('接收端暂未连通：' + type(error).__name__)
    receiver_ready = assignment.get('contractVersion') == 1 and all(scope in assignment.get('tokens', {}) for scope in ['home', 'zijin', 'status'])
    # Discovery and local notifications continue during a receiver outage.
    if receiver_ready and isinstance(assignment.get('acceptedVersions'), dict):
        with transaction(path) as db:
            for row in db.execute('SELECT scope,event_key,delivered_version FROM future_calendar_events').fetchall():
                accepted = assignment['acceptedVersions'].get(row['scope'], {}).get(row['event_key'], 0)
                if isinstance(accepted, int) and accepted < row['delivered_version']:
                    db.execute("UPDATE future_calendar_events SET delivered_version=?,next_attempt_at='' WHERE scope=? AND event_key=?", (accepted, row['scope'], row['event_key']))
    due = []
    configured = _sources(now)
    with connect(path) as db:
        fed = db.execute("SELECT enabled FROM sources WHERE key='federal-reserve-calendar'").fetchone()
        if fed is not None and not fed[0]:
            configured = [source for source in configured if source['parser'] not in {'fed-month', 'sf-index'}]
        for source in configured:
            old = db.execute('SELECT * FROM future_calendar_sources WHERE source_key=?', (source['key'],)).fetchone()
            seconds = source.get('poll_seconds', 21600)
            if old and old['last_error']:
                seconds = min(seconds, 300 if source['parser'] in {'fed-month', 'sf-index'} else 1800)
            if force or not old or now - datetime.fromisoformat(old['last_attempt_at']) >= timedelta(seconds=seconds):
                due.append(source)
    cached_fetch = _FetchCache(fetcher)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda source: _read_source(source, fetcher=cached_fetch, now=now), due))
    conflicts = {key for _source, _events, error in results for key in re.findall(r'conflict\[(fed:[a-z0-9:-]+)\]', error)}
    observations = {}
    for source, events, error in results:
        report['sources'] += 1
        with transaction(path) as db:
            db.execute('''INSERT INTO future_calendar_sources VALUES(?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,
                    last_success_at=CASE WHEN excluded.last_error='' THEN excluded.last_success_at ELSE last_success_at END,last_error=excluded.last_error''',
                       (source['key'], at, '' if error else at, error))
        for scope, event in events:
            if event['key'] in conflicts:
                continue
            identity = (scope, event['key'])
            old = observations.get(identity)
            if old and old['originalScheduledAt'] and not event['originalScheduledAt']:
                continue
            if (old and old['originalScheduledAt'] == event['originalScheduledAt']
                    and urlparse(old['source']['url']).hostname == 'www.federalreserve.gov'):
                continue
            observations[identity] = event
    for (scope, _key), event in observations.items():
        scheduled_date = datetime.fromisoformat(event['date']).date()
        if now.date() - timedelta(days=7) <= scheduled_date <= now.date() + timedelta(days=100):
            report['discovered'] += int(_store(scope, event, path=path, now=now))
    report['localNotices'] = queue_calendar_notices(path=path, now=now)
    with connect(path) as db:
        states = {row['source_key']: dict(row) for row in db.execute('SELECT * FROM future_calendar_sources')}
        for source in configured:
            if states.get(source['key'], {}).get('last_error'):
                report['errors'].append(source['key'] + ': ' + states[source['key']]['last_error'])
        rows = db.execute("SELECT * FROM future_calendar_events WHERE version>delivered_version AND json_extract(payload_json,'$.originalScheduledAt')<>'' AND (next_attempt_at='' OR next_attempt_at<=?) ORDER BY checked_at LIMIT 80", (at,)).fetchall() if receiver_ready else []
    fed_states = [states.get(source['key'], {}) for source in configured if source['parser'] in {'fed-month', 'sf-index'}]
    if fed_states:
        latest_attempt = max((state.get('last_attempt_at', '') for state in fed_states), default='')
        successes = [state.get('last_success_at', '') for state in fed_states]
        success = min(successes) if all(successes) else ''
        errors = '; '.join(state['last_error'] for state in fed_states if state.get('last_error'))[:500]
        with transaction(path) as db:
            db.execute("""UPDATE sources SET last_attempt_at=?,last_success_at=CASE WHEN ?<>'' THEN ? ELSE last_success_at END,
                last_error=?,last_item_count=(SELECT COUNT(DISTINCT event_key) FROM future_calendar_events WHERE event_key LIKE 'fed:%'),
                updated_at=? WHERE key='federal-reserve-calendar'""", (latest_attempt, success, success, errors or None, at))
    for row in rows:
        try:
            event = json.loads(row['payload_json'])
            receipt = sender(target_url, {'contractVersion': 1, 'sourceSystem': 'instant-ai', 'scope': row['scope'], 'deliveryToken': assignment['tokens'][row['scope']], 'event': event})
            if receipt.get('version') != row['version']:
                raise ValueError('罗盘回执版本不匹配')
            with transaction(path) as db:
                db.execute("UPDATE future_calendar_events SET delivered_version=?,attempts=0,next_attempt_at='',last_error='',received_at=? WHERE scope=? AND event_key=? AND version=?", (row['version'], receipt.get('receivedAt') or at, row['scope'], row['event_key'], row['version']))
            report['delivered'] += 1
        except Exception as error:
            attempts = row['attempts'] + 1
            next_at = _iso(now + timedelta(seconds=min(1800, 30 * 2 ** min(attempts, 6))))
            with transaction(path) as db:
                db.execute('UPDATE future_calendar_events SET attempts=?,next_attempt_at=?,last_error=? WHERE scope=? AND event_key=? AND version=?', (attempts, next_at, type(error).__name__ + ': ' + str(error)[:180], row['scope'], row['event_key'], row['version']))
    with transaction(path) as db:
        cutoff = (now.astimezone(SHANGHAI).date() - timedelta(days=7)).isoformat()
        db.execute("DELETE FROM future_calendar_events WHERE json_extract(payload_json,'$.date')<>'' AND json_extract(payload_json,'$.date')<?", (cutoff,))
        report['queued'] = db.execute("SELECT COUNT(*) FROM future_calendar_events WHERE version>delivered_version AND json_extract(payload_json,'$.originalScheduledAt')<>''").fetchone()[0]
        report['failed'] = db.execute("SELECT COUNT(*) FROM future_calendar_events WHERE version>delivered_version AND json_extract(payload_json,'$.originalScheduledAt')<>'' AND last_error<>''").fetchone()[0]
    if receiver_ready:
        try:
            public_report = {key: report[key] for key in ('checkedAt', 'sources', 'discovered', 'queued', 'delivered', 'failed', 'errors')}
            sender(target_url + '/status', {'contractVersion': 1, 'sourceSystem': 'instant-ai', 'deliveryToken': assignment['tokens']['status'], **public_report})
        except Exception as error:
            report['errors'].append('日历状态回执失败：' + type(error).__name__)
    state = 'waiting_receiver' if not receiver_ready else 'partial' if report['errors'] or report['failed'] else 'connected'
    return {**report, 'state': state}
