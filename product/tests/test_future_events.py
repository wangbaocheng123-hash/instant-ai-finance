from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from instant_ai.database import connect, initialize
from instant_ai.future_events import parse_bls_calendar, parse_earnings_announcements, parse_tsmc_calendar, supply_future_events

NOW=datetime(2026,11,6,1,tzinfo=UTC)
TSMC=b'''Upcoming Events <var class="atc_event"><var class="atc_title">TSMC Monthly Sales - October 2026</var><var class="atc_date_start">2026-11-10 13:30:00</var><var class="atc_timezone">Asia/Taipei</var></var>'''
RSS=b'''<rss version="2.0"><channel><title>Official fixture</title><item><title>NVIDIA Announces Financial Results Date</title><link>https://nvidianews.nvidia.com/news/synthetic</link><pubDate>Wed, 28 Oct 2026 12:00:00 GMT</pubDate><description>NVIDIA will host a conference call on November 17, 2026 at 2 p.m. PT to discuss its third-quarter fiscal year 2027 financial results.</description></item></channel></rss>'''
BLS=b'''<p>All times are Eastern Time.</p><table><td>6 Employment Situation October 2026 08:30 AM</td><td class="other-month">4 Employment Situation November 2026 08:30 AM</td></table>'''
SOURCES=[{'key':'tsmc-test','url':'https://investor.tsmc.com/english/financial-calendar','parser':'tsmc'}, {'key':'nvidia-test','url':'https://nvidianews.nvidia.com/cats/press_release.xml','parser':'earnings','issuer':'nvidia','name':'NVIDIA'}, {'key':'bls-test','url':'https://www.bls.gov/schedule/2026/11_sched.htm','parser':'bls','year':2026,'month':11}]


class FutureEventTests(unittest.TestCase):
    def test_official_calendar_times_are_not_rss_publication_dates(self):
        tsmc=parse_tsmc_calendar(TSMC,now=NOW)[0]
        self.assertEqual((tsmc['date'],tsmc['time']),('2026-11-10','13:30'))
        nvidia=parse_earnings_announcements(RSS,issuer='nvidia',name='NVIDIA',now=NOW)[0]
        self.assertEqual((nvidia['date'],nvidia['time']),('2026-11-18','06:00'))
        self.assertEqual(nvidia['key'],'nvidia:earnings:FY2027Q3:call')
        self.assertTrue(nvidia['source']['publishedAt'].startswith('2026-10-28'))
        self.assertEqual(parse_earnings_announcements(RSS.replace(b'2 p.m. PT',b'2 p.m.'),issuer='nvidia',name='NVIDIA',now=NOW),[])
        bls=parse_bls_calendar(BLS,year=2026,month=11,url=SOURCES[2]['url'],now=NOW)
        self.assertEqual(len(bls),1,'Next-month cells must never be assigned this month')
        self.assertEqual((bls[0]['date'],bls[0]['time']),('2026-11-06','21:30'))
        summer=parse_bls_calendar(BLS.replace(b'>6 ',b'>5 '),year=2026,month=6,url=SOURCES[2]['url'],now=NOW)[0]
        self.assertEqual(summer['time'],'20:30','IANA DST must apply, not a fixed UTC offset')
        two=BLS.replace(b'Employment Situation October 2026 08:30 AM',b'Consumer Price Index October 2026 08:30 AM Job Openings and Labor Turnover Survey September 2026 10:00 AM')
        times={e['key'].split(':')[1]:e['time'] for e in parse_bls_calendar(two,year=2026,month=11,url=SOURCES[2]['url'],now=NOW)}
        self.assertEqual(times,{'cpi':'21:30','jolts':'23:00'},'Each release keeps its own time even in the same day cell')

    def test_durable_delivery_retry_reschedule_restore_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.db';initialize(path)
            bodies={s['url']:b for s,b in zip(SOURCES,[TSMC,RSS,BLS])}
            received={};calls=[];fail=False
            def assignment(_):
                return {'calendarIngestion':{'contractVersion':1,'tokens':dict(home='h',zijin='z',status='s'),'acceptedVersions':{scope:{key:v for (sc,key),v in received.items() if sc==scope} for scope in ['home','zijin']}}}
            def sender(url,payload):
                calls.append((url,payload))
                if url.endswith('/status'):return {'ok':True}
                if fail:raise OSError('synthetic temporary outage')
                event=payload['event'];received[payload['scope'],event['key']]=event['version']
                return {'ok':True,'version':event['version'],'receivedAt':NOW.isoformat()}
            def run(now=NOW,**kwargs):
                with patch('instant_ai.future_events._sources',return_value=SOURCES):
                    return supply_future_events(path=path,now=now,fetcher=lambda url:{'status':200,'body':bodies[url]},assignment_fetcher=assignment,sender=sender,**kwargs)
            first=run()
            self.assertEqual(first['delivered'],3)
            self.assertEqual({p['scope'] for u,p in calls if not u.endswith('/status')},{'home','zijin'})
            self.assertNotIn('positionSlots',json.dumps(calls))
            self.assertEqual(run()['delivered'],0,'Routine refresh must not redeliver unchanged events')
            bodies[SOURCES[0]['url']]=TSMC.replace(b'2026-11-10',b'2026-11-11')
            fail=True
            failed=run(NOW+timedelta(hours=7))
            self.assertEqual(failed['failed'],1)
            fail=False
            self.assertEqual(run(NOW+timedelta(hours=7,minutes=2))['delivered'],1)
            with connect(path) as db:
                row=db.execute("SELECT * FROM future_calendar_events WHERE event_key LIKE 'tsmc:%'").fetchone()
                self.assertEqual(row['version'],2)
                self.assertEqual(json.loads(row['payload_json'])['date'],'2026-11-11')
            received.clear()
            self.assertEqual(run(NOW+timedelta(hours=8))['delivered'],3,'Receiver restore must replay already acknowledged versions')
            # SQLite's normal online backup includes future events with no dependency on news TTL.
            backup=Path(directory)/'restore.db'
            with connect(path) as db, sqlite3.connect(backup) as restored:db.backup(restored)
            initialize(backup)
            with connect(backup) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM future_calendar_events').fetchone()[0],3)
            self.assertEqual(run(NOW+timedelta(days=25),force=True)['state'],'connected')
            with connect(path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM future_calendar_events').fetchone()[0],0)

    def test_source_outage_keeps_nodes_and_old_receiver_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.db';initialize(path)
            waiting=supply_future_events(path=path,now=NOW,assignment_fetcher=lambda _: {},fetcher=lambda _:self.fail('Old receiver must not fetch sources'))
            self.assertEqual(waiting['state'],'waiting_receiver')
            with patch('instant_ai.future_events._sources',return_value=[SOURCES[0]]):
                args=dict(path=path,now=NOW,assignment_fetcher=lambda _:{'calendarIngestion':{'contractVersion':1,'tokens':dict(home='h',zijin='z',status='s')}},sender=lambda u,p:{'ok':True,'version':p.get('event',{}).get('version')})
                supply_future_events(**args,fetcher=lambda _:{'status':200,'body':TSMC})
                result=supply_future_events(**args,force=True,fetcher=lambda _:{'status':503,'body':b''})
            self.assertEqual(result['state'],'partial')
            with connect(path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM future_calendar_events').fetchone()[0],1)


if __name__=='__main__':unittest.main()
