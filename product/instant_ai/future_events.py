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
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .collectors import parse_feed
from .database import connect, transaction
from .official_monitor import _fetch_official_channel, validate_official_url

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
    return sources


def _store(scope: str, event: dict[str,Any], *, path: object, now: datetime) -> bool:
    normalized={**event,'version':0,'source':{**event['source'],'verifiedAt':''}}
    checksum=hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with transaction(path) as db:
        previous=db.execute('SELECT * FROM future_calendar_events WHERE scope=? AND event_key=?',(scope,event['key'])).fetchone()
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


def supply_future_events(*,path: object=None,now: datetime|None=None,fetcher: Callable=_fetch_official_channel,
                         assignment_fetcher: Callable|None=None,sender: Callable=_post_calendar,force: bool=False,target_url: str=DEFAULT_TARGET) -> dict[str,Any]:
    from .watch_events import _fetch_payload,DEFAULT_COMPASS_EVENTS_URL
    import os
    now=now or datetime.now(UTC);at=_iso(now);report={'checkedAt':at,'sources':0,'discovered':0,'queued':0,'delivered':0,'failed':0,'errors':[]}
    try:payload=(assignment_fetcher or _fetch_payload)(os.environ.get('INSTANT_AI_COMPASS_EVENTS_URL',DEFAULT_COMPASS_EVENTS_URL))
    except Exception as e:return {**report,'state':'waiting_receiver','errors':[type(e).__name__+': '+str(e)[:180]]}
    assignment=payload.get('calendarIngestion',{})
    if assignment.get('contractVersion')!=1 or not all(s in assignment.get('tokens',{}) for s in ['home','zijin','status']):return {**report,'state':'waiting_receiver'}
    # Receiver restore/loss must be repairable even after this outbox got a receipt.
    # Only lower acknowledgements, never invent or regress a source version.
    if isinstance(assignment.get('acceptedVersions'),dict):
        with transaction(path) as db:
            for row in db.execute('SELECT scope,event_key,version,delivered_version FROM future_calendar_events').fetchall():
                accepted=assignment['acceptedVersions'].get(row['scope'],{}).get(row['event_key'],0)
                if isinstance(accepted,int) and accepted<row['delivered_version']:
                    db.execute("UPDATE future_calendar_events SET delivered_version=?,next_attempt_at='' WHERE scope=? AND event_key=?",(accepted,row['scope'],row['event_key']))
    due=[]
    with connect(path) as db:
        for source in _sources(now):
            old=db.execute('SELECT * FROM future_calendar_sources WHERE source_key=?',(source['key'],)).fetchone()
            interval=timedelta(minutes=30) if old and old['last_error'] else timedelta(hours=6)
            if force or not old or now-datetime.fromisoformat(old['last_attempt_at'])>=interval:due.append(source)
    def read(source):
        try:
            response=fetcher(source['url'])
            if int(response.get('status',0))!=200:raise ValueError('官方日历未返回200')
            if source['parser']=='tsmc':
                if b'Upcoming Events' not in response['body']:raise ValueError('TSMC官方日历结构待核对')
                events=parse_tsmc_calendar(response['body'],now=now);scope='home'
                if not events:raise ValueError('TSMC官方日历未解析到事件，保留旧节点并等待复核')
            elif source['parser']=='earnings':events=parse_earnings_announcements(response['body'],issuer=source['issuer'],name=source['name'],now=now);scope='home'
            else:
                events=parse_bls_calendar(response['body'],year=source['year'],month=source['month'],url=source['url'],now=now);scope='zijin'
                if not events:raise ValueError('BLS日历未解析到核心发布时间，保留旧节点并等待复核')
            return source,scope,events,''
        except Exception as e:return source,'',[],type(e).__name__+': '+str(e)[:160]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for source,scope,events,error in pool.map(read,due):
            report['sources']+=1
            with transaction(path) as db:
                db.execute('''INSERT INTO future_calendar_sources VALUES(?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,
                    last_success_at=CASE WHEN excluded.last_error='' THEN excluded.last_success_at ELSE last_success_at END,last_error=excluded.last_error''',
                    (source['key'],at,'' if error else at,error))
            for event in events:
                scheduled=datetime.fromisoformat(event['originalScheduledAt'])
                if now-timedelta(days=7)<=scheduled<=now+timedelta(days=100):
                    report['discovered']+=int(_store(scope,event,path=path,now=now))
    with connect(path) as db:
        rows=db.execute("SELECT * FROM future_calendar_events WHERE version>delivered_version AND (next_attempt_at='' OR next_attempt_at<=?) ORDER BY checked_at LIMIT 80",(at,)).fetchall()
        report['errors']=[row[0] for row in db.execute("SELECT source_key||': '||last_error FROM future_calendar_sources WHERE last_error<>'' LIMIT 8")]
    for row in rows:
        report['queued']+=1
        try:
            event=json.loads(row['payload_json'])
            receipt=sender(target_url,{'contractVersion':1,'sourceSystem':'instant-ai','scope':row['scope'],'deliveryToken':assignment['tokens'][row['scope']],'event':event})
            if receipt.get('version')!=row['version']:raise ValueError('罗盘回执版本不匹配')
            with transaction(path) as db:db.execute("UPDATE future_calendar_events SET delivered_version=?,attempts=0,next_attempt_at='',last_error='',received_at=? WHERE scope=? AND event_key=? AND version=?",(row['version'],receipt.get('receivedAt') or at,row['scope'],row['event_key'],row['version']))
            report['delivered']+=1
        except Exception as e:
            report['failed']+=1;attempts=row['attempts']+1;next_at=_iso(now+timedelta(seconds=min(1800,30*2**min(attempts,6))))
            with transaction(path) as db:db.execute('UPDATE future_calendar_events SET attempts=?,next_attempt_at=?,last_error=? WHERE scope=? AND event_key=? AND version=?',(attempts,next_at,type(e).__name__+': '+str(e)[:180],row['scope'],row['event_key'],row['version']))
    with connect(path) as db:
        report['queued']=db.execute('SELECT COUNT(*) FROM future_calendar_events WHERE version>delivered_version').fetchone()[0]
        report['failed']=db.execute("SELECT COUNT(*) FROM future_calendar_events WHERE version>delivered_version AND last_error<>''").fetchone()[0]
    try:sender(target_url+'/status',{'contractVersion':1,'sourceSystem':'instant-ai','deliveryToken':assignment['tokens']['status'],**report})
    except Exception as e:report['errors'].append('日历状态回执失败：'+type(e).__name__)
    with transaction(path) as db:
        cutoff=(now.astimezone(SHANGHAI).date()-timedelta(days=7)).isoformat()
        db.execute("DELETE FROM future_calendar_events WHERE json_extract(payload_json,'$.date')<>'' AND json_extract(payload_json,'$.date')<?",(cutoff,))
    return {**report,'state':'partial' if report['errors'] or report['failed'] else 'connected'}
