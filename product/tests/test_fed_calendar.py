from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from collections import Counter
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from instant_ai import server
from instant_ai.auth import OwnerAuth, configure_owner
from instant_ai.calendar_notices import calendar_snapshot, queue_calendar_notices
from instant_ai.database import DEFAULT_SOURCES, connect, initialize, seed_sources, transaction
from instant_ai.fed_calendar import parse_fed_month, parse_sf_event, sf_event_url, sf_upcoming_links
from instant_ai.future_events import _sources, supply_future_events
from instant_ai.service import dispatch_web_push_notifications, reclassify_items, _source_from_row, _upsert_entry
from instant_ai.collectors import Entry
from instant_ai.retention import run_retention_cleanup
from instant_ai.web_push import PushResult


NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
MONTH_URL = 'https://www.federalreserve.gov/newsevents/2026-November.htm'
INDEX_URL = 'https://www.frbsf.org/news-and-media/events/'
EVENT_URL = INDEX_URL + '2026/11/synthetic-productivity-conference/'
SOURCE_CONFIG = [
    {'key': 'fed-2026-11', 'name': '美联储本月日程', 'url': MONTH_URL, 'parser': 'fed-month', 'year': 2026, 'month': 11, 'poll_seconds': 900},
    {'key': 'sf-fed-events', 'name': '旧金山联储会议', 'url': INDEX_URL, 'parser': 'sf-index', 'poll_seconds': 1800},
]


def board_row(day='16', clock='12:00 p.m.', speaker='Speech - Chairman Kevin Warsh', subject='Opening Remarks', link=EVENT_URL):
    return f'''<div class="panel-body"><div class="row"><div class="col-xs-2"><p>{clock}</p></div>
      <div class="col-xs-7"><p>{speaker}</p><p><a href="{link}">Watch Live</a></p>
      <p class="calendar__title"><em>{subject}</em></p><p>At the synthetic productivity and jobs conference</p></div>
      <div class="col-xs-3"><p>{day}</p></div></div></div>'''


def month_page(rows=None, label='November 2026'):
    return f'<div id="article"><h4>{label}</h4><h4>Speeches</h4>{rows if rows is not None else board_row()}</div>'.encode()


def host_page(day='16', month='Nov', clock='9:00 a.m. PT', title='Synthetic Productivity Conference'):
    time = f'<p>Time</p><p>{clock}</p>' if clock else ''
    return f'''<html><title>{title}</title><h1>{title}</h1><p>Date</p><p>Monday, {month} {day}, 2026</p>
      {time}<p>Location</p><p>Federal Reserve Bank of San Francisco</p>
      <h2>Agenda</h2><p>Opening remarks by Chairman of the Board of Governors of the Federal Reserve System.</p></html>'''.encode()


INDEX = f'<h1>Events</h1><a href="{EVENT_URL}">Synthetic upcoming conference</a>'.encode()


class FakePush:
    available = True

    def __init__(self):
        self.sent = []

    def send(self, subscription, notification):
        self.sent.append(notification)
        return PushResult(True, 201, False, '')


class FedParsingTests(unittest.TestCase):
    def test_winter_summer_cross_midnight_and_year_rollover(self):
        winter = parse_fed_month(month_page(), year=2026, month=11, url=MONTH_URL)[0]
        self.assertEqual(winter.scheduled_at.astimezone(UTC).isoformat(), '2026-11-16T17:00:00+00:00')
        summer = parse_fed_month(month_page(board_row(day='15'), 'June 2026'), year=2026, month=6, url=MONTH_URL)[0]
        self.assertEqual(summer.scheduled_at.astimezone(UTC).hour, 16)
        sources = _sources(datetime(2026, 12, 31, 23, tzinfo=UTC))
        urls = [source['url'] for source in sources if source['parser'] == 'fed-month']
        self.assertIn('https://www.federalreserve.gov/newsevents/2027-January.htm', urls)
        host = parse_sf_event(host_page(), url=EVENT_URL)
        self.assertEqual(host.scheduled_at, winter.scheduled_at)
        self.assertEqual(parse_sf_event(host_page(month='Jun', day='15'), url=EVENT_URL).scheduled_at.astimezone(UTC).hour, 16)

    def test_distinct_speeches_and_unknown_future_chair_are_not_name_gated(self):
        rows = board_row(link='https://livestream.example.test/one') + board_row(
            clock='2:00 p.m.', speaker='Discussion - Chair Future Person', subject='Economic Outlook', link='https://unreviewed.example.test/two')
        parsed = parse_fed_month(month_page(rows), year=2026, month=11, url=MONTH_URL)
        self.assertEqual(len(parsed), 2)
        self.assertNotEqual(parsed[0].key, parsed[1].key)
        self.assertEqual(parsed[1].importance, 5)
        self.assertIn('Future Person', parsed[1].title)
        self.assertFalse(parsed[1].detail_url)
        def fomc(month, label, rows):
            page = month_page(rows, label).replace(b'<h4>Speeches</h4>', b'<h4>FOMC Meetings</h4>')
            return parse_fed_month(page, year=2026, month=month, url=MONTH_URL)
        row = board_row(speaker='FOMC Press Conference', subject='', link='')
        june = fomc(6, 'June 2026', row)
        july = fomc(7, 'July 2026', row)
        self.assertNotEqual(june[0].key, july[0].key, 'Adjacent FOMC months must not overwrite the nearer meeting')
        same_month = fomc(6, 'June 2026', row + board_row(day='25', speaker='FOMC Press Conference', subject='', link=''))
        self.assertEqual(len({event.key for event in same_month}), 2)

    def test_host_identity_survives_reschedule_and_url_tracking(self):
        before = parse_fed_month(month_page(), year=2026, month=11, url=MONTH_URL)[0]
        after = parse_fed_month(month_page(board_row(day='17', clock='1:00 p.m.')), year=2026, month=11, url=MONTH_URL)[0]
        host = parse_sf_event(host_page(), url=EVENT_URL + '?utm_source=test#agenda')
        self.assertEqual(before.key, after.key)
        self.assertEqual(after.key, host.key)
        self.assertEqual(host.url, EVENT_URL)
        for bad in ['http://www.frbsf.org/news-and-media/events/2026/11/a/', EVENT_URL.replace('www.frbsf.org', 'www.frbsf.org.evil.test'), 'https://user:pass@www.frbsf.org/news-and-media/events/2026/11/a/', INDEX_URL]:
            self.assertEqual(sf_event_url(bad), '')

    def test_date_only_stays_unconfirmed_and_bad_clock_does_not_hide_other_rows(self):
        pending = parse_sf_event(host_page(clock=''), url=EVENT_URL)
        self.assertIsNone(pending.scheduled_at)
        self.assertEqual(pending.local_date, '2026-11-16')
        board = parse_fed_month(month_page(board_row(clock='')), year=2026, month=11, url=MONTH_URL)
        self.assertIsNone(board[0].scheduled_at)
        partial = parse_fed_month(month_page(board_row() + board_row(clock='unknown clock')), year=2026, month=11, url=MONTH_URL)
        self.assertEqual(len(partial), 1)
        self.assertTrue(partial.warnings)
        empty = parse_fed_month(month_page(''), year=2026, month=11, url=MONTH_URL)
        self.assertTrue(empty.warnings, 'A zero-event page must not silently claim complete coverage')
        with self.assertRaises(ValueError):
            parse_fed_month(b'<h1>A redesigned or challenge page</h1>', year=2026, month=11, url=MONTH_URL)
        with self.assertRaises(ValueError):
            parse_sf_event(host_page(clock='9:00 a.m.'), url=EVENT_URL)
        with self.assertRaises(ValueError):
            parse_sf_event(host_page(month='Jun', clock='9:00 a.m. PST'), url=EVENT_URL)

    def test_ambiguous_or_nonexistent_dst_times_are_not_guessed(self):
        fall = parse_fed_month(month_page(board_row(day='1', clock='1:30 a.m.')), year=2026, month=11, url=MONTH_URL)
        spring = parse_fed_month(month_page(board_row(day='8', clock='2:30 a.m.'), 'March 2026'), year=2026, month=3, url=MONTH_URL)
        self.assertEqual(fall, [])
        self.assertEqual(spring, [])
        self.assertTrue(fall.warnings and spring.warnings)

    def test_calendar_and_actual_speech_sources_remain_separate(self):
        sources = {source['key']: source for source in DEFAULT_SOURCES}
        self.assertEqual(sources['federal-reserve-calendar']['kind'], 'official_calendar')
        self.assertEqual(sources['federal-reserve-speeches']['url'], 'https://www.federalreserve.gov/feeds/speeches.xml')
        self.assertEqual(sources['federal-reserve-testimony']['url'], 'https://www.federalreserve.gov/feeds/testimony.xml')
        self.assertEqual(sources['federal-reserve-press']['url'], 'https://www.federalreserve.gov/feeds/press_all.xml')

    def test_upcoming_detection_reads_event_date_instead_of_url_month(self):
        early_url = EVENT_URL.replace('/2026/11/', '/2026/08/')
        links = sf_upcoming_links(f'<h1>Events</h1><a href="{early_url}">A future meeting announced early</a>'.encode(), now=NOW)
        self.assertEqual(links, [early_url])
        event = parse_sf_event(host_page(), url=early_url, now=NOW)
        self.assertEqual(event.scheduled_at.astimezone(UTC).date().isoformat(), '2026-11-16')

    def test_month_boundary_keeps_last_us_day_after_beijing_crosses_month(self):
        sources = _sources(datetime(2026, 11, 30, 17, tzinfo=UTC))
        keys = {source['key'] for source in sources if source['parser'] == 'fed-month'}
        self.assertEqual(keys, {'fed-2026-11', 'fed-2026-12'})
        year_end = _sources(datetime(2026, 12, 31, 23, tzinfo=UTC))
        self.assertEqual({source['key'] for source in year_end if source['parser'] == 'fed-month'}, {'fed-2026-12', 'fed-2027-01'})

    def test_past_index_entries_are_not_mistaken_for_time_parsing_outages(self):
        event = parse_sf_event(host_page(day='5', month='Jun', clock='9:00 a.m. JST'), url=EVENT_URL, now=NOW)
        self.assertIsNone(event.scheduled_at)
        self.assertEqual(event.local_date, '2026-06-05')


class FedDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'calendar.db'
        initialize(self.path)
        seed_sources(self.path)
        self.bodies = {MONTH_URL: month_page(), INDEX_URL: INDEX, EVENT_URL: host_page()}
        self.fetches = Counter()
        self.receipts = []
        self.sender = FakePush()
        with transaction(self.path) as db:
            db.execute("""INSERT INTO web_push_subscriptions(id,endpoint,p256dh,auth_secret,created_at,updated_at)
                VALUES('synthetic-device','https://web.push.apple.com/synthetic','key','auth',?,?)""", (NOW.isoformat(), NOW.isoformat()))

    def run_discovery(self, now=NOW, ready=True, force=True, sources=SOURCE_CONFIG, failure=False):
        def fetch(url):
            self.fetches[url] += 1
            if failure:
                raise OSError('synthetic source unavailable')
            return {'status': 200, 'body': self.bodies[url]}

        def sender(url, payload):
            if url.endswith('/status'):
                self.assertEqual(set(payload), {'contractVersion','sourceSystem','deliveryToken','checkedAt','sources','discovered','queued','delivered','failed','errors'})
            self.receipts.append(payload)
            return {'ok': True, 'version': payload.get('event', {}).get('version'), 'receivedAt': now.isoformat()}

        assignment = {'calendarIngestion': {'contractVersion': 1, 'tokens': {'home': 'synthetic-h', 'zijin': 'synthetic-z', 'status': 'synthetic-s'}}} if ready else {}
        with patch('instant_ai.future_events._sources', return_value=sources):
            return supply_future_events(path=self.path, now=now, fetcher=fetch, sender=sender, assignment_fetcher=lambda _: assignment, force=force)

    def snapshot(self, now=NOW):
        with patch('instant_ai.future_events._sources', return_value=SOURCE_CONFIG):
            return calendar_snapshot(path=self.path, now=now)

    def dispatch(self, now=NOW):
        with patch('instant_ai.service.utc_now', return_value=now.isoformat()):
            return dispatch_web_push_notifications(self.path, sender=self.sender)

    def test_receiver_outage_does_not_block_discovery_or_local_mobile_outbox(self):
        report = self.run_discovery(ready=False)
        self.assertEqual(report['state'], 'waiting_receiver')
        self.assertEqual(report['queued'], 2)
        self.assertEqual(report['localNotices']['announced'], 1)
        self.assertEqual(self.receipts, [])
        self.assertEqual(self.fetches[EVENT_URL], 1, 'Board and host index share one detail request')
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot['events']), 1)
        self.assertEqual((snapshot['events'][0]['date'], snapshot['events'][0]['time']), ('2026-11-17', '01:00'))
        self.assertEqual(snapshot['events'][0]['source']['url'], MONTH_URL)
        self.assertIn(EVENT_URL, snapshot['events'][0]['source']['evidenceExcerpt'])
        self.assertEqual(self.dispatch()['delivered'], 1)
        self.assertIn('01:00', self.sender.sent[0]['body'])
        with connect(self.path) as db:
            self.assertIsNone(db.execute('SELECT published_at FROM items').fetchone()[0])
        again = self.run_discovery(now=NOW + timedelta(minutes=5), ready=False)
        self.assertEqual(again['localNotices']['announced'], 0)
        self.assertEqual(self.dispatch(now=NOW + timedelta(minutes=5))['attempted'], 0)
        restored = self.run_discovery(now=NOW + timedelta(minutes=10))
        self.assertEqual(restored['delivered'], 2)
        self.assertEqual(restored['localNotices']['announced'], 0)

    def test_reschedules_cancel_old_pending_push_and_repeated_changes_are_not_lost(self):
        self.run_discovery()
        key = self.snapshot()['events'][0]['key']
        for index, clock in enumerate(['10:00 a.m. PT', '9:00 a.m. PT', '10:00 a.m. PT'], 1):
            self.bodies[EVENT_URL] = host_page(clock=clock)
            now = NOW + timedelta(minutes=index * 5)
            changed = self.run_discovery(now=now)
            self.assertEqual(changed['localNotices']['changed'], 1)
            self.assertEqual(self.snapshot(now)['events'][0]['key'], key)
            with connect(self.path) as db:
                pending = db.execute("SELECT COUNT(*) FROM notification_outbox WHERE channel='web_push' AND status='pending'").fetchone()[0]
                self.assertEqual(pending, 1)
        self.assertEqual(self.dispatch(now=now)['delivered'], 1)
        self.assertIn('02:00', self.sender.sent[-1]['body'])
        self.assertEqual(self.sender.sent[-1]['tag'], f'instant-ai-calendar-{key}')

    def test_one_hour_reminder_requires_fresh_source_and_is_deduplicated(self):
        self.run_discovery()
        self.dispatch()
        near = NOW + timedelta(hours=4, minutes=10)
        failed = self.run_discovery(now=near, failure=True)
        self.assertEqual(failed['localNotices']['reminded'], 0)
        self.assertTrue(all(source['state'] == 'error' for source in self.snapshot(near)['sources']))
        recovered = self.run_discovery(now=near + timedelta(minutes=5))
        self.assertEqual(recovered['localNotices']['reminded'], 1)
        self.assertEqual(self.dispatch(now=near + timedelta(minutes=5))['delivered'], 1)
        repeat = self.run_discovery(now=near + timedelta(minutes=10))
        self.assertEqual(repeat['localNotices']['reminded'], 0)
        self.assertEqual(len(self.sender.sent), 2)

    def test_first_discovery_within_one_hour_does_not_add_redundant_reminder(self):
        near = NOW + timedelta(hours=4, minutes=10)
        self.assertEqual(self.run_discovery(now=near)['localNotices']['announced'], 1)
        self.assertEqual(self.run_discovery(now=near + timedelta(minutes=15))['localNotices']['reminded'], 0)

    def test_news_cleanup_keeps_calendar_and_receipts_prevent_reannouncement(self):
        self.bodies[MONTH_URL] = month_page(board_row(day='21'))
        self.bodies[EVENT_URL] = host_page(day='21')
        self.run_discovery()
        with transaction(self.path) as db:
            db.execute('DELETE FROM items')
            self.assertIsNone(db.execute('SELECT item_id FROM future_calendar_notices').fetchone()[0])
        report = self.run_discovery(now=NOW + timedelta(days=4))
        self.assertEqual(sum(report['localNotices'].values()), 0)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0], 0)
        self.assertEqual(len(self.snapshot(NOW + timedelta(days=4))['events']), 1)

    def test_real_retention_never_turns_agenda_time_into_news_publication_time(self):
        self.bodies[MONTH_URL] = month_page(board_row(day='28'))
        self.bodies[EVENT_URL] = host_page(day='28')
        self.run_discovery()
        root = Path(self.directory.name)
        def clean(now):
            return run_retention_cleanup(path=self.path, now=now, raw_root=root/'raw', cache_root=root/'cache', evidence_root=root/'evidence', backups_root=root/'backups')
        clean(NOW)
        with connect(self.path) as db:
            self.assertIsNone(db.execute('SELECT published_at FROM items').fetchone()[0], 'Agenda text is not a publication timestamp')
        clean(NOW + timedelta(days=8))
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM future_calendar_events').fetchone()[0], 2)
        result = self.run_discovery(now=NOW + timedelta(days=8))
        self.assertEqual(sum(result['localNotices'].values()), 0)

    def test_expired_notice_is_not_pushed_after_event_start(self):
        self.run_discovery()
        self.assertEqual(self.dispatch(now=NOW + timedelta(hours=6))['attempted'], 0)
        self.assertEqual(self.sender.sent, [])

    def test_initial_historical_speeches_do_not_become_new_phone_alerts(self):
        with transaction(self.path) as db:
            db.execute("UPDATE sources SET created_at=? WHERE key='federal-reserve-speeches'", (NOW.isoformat(),))
            source = _source_from_row(db.execute("SELECT * FROM sources WHERE key='federal-reserve-speeches'").fetchone())
            historical = Entry('old-speech', 'Federal Reserve economic outlook', 'https://www.federalreserve.gov/newsevents/speech/synthetic-old.htm', 'Federal Reserve monetary policy and inflation', (NOW - timedelta(days=3)).isoformat())
            for now in [NOW, NOW + timedelta(days=1)]:
                _upsert_entry(db, source, historical, 'synthetic-hash', '', 'application/rss+xml', 200, observed_at=now.isoformat())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0], 1)
            # A release after first connection must still notify if a later
            # outage delays collection by more than 24 hours.
            newer = Entry('new-speech', 'Federal Reserve economic outlook', 'https://www.federalreserve.gov/newsevents/speech/synthetic-new.htm', 'Federal Reserve monetary policy and inflation', (NOW + timedelta(days=5)).isoformat())
            _upsert_entry(db, source, newer, 'synthetic-hash', '', 'application/rss+xml', 200, observed_at=(NOW + timedelta(days=7)).isoformat())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notification_outbox WHERE channel='web_push'").fetchone()[0], 1)

    def test_date_only_is_visible_then_announced_as_time_confirmed(self):
        self.bodies[EVENT_URL] = host_page(clock='')
        initial = self.run_discovery(sources=[SOURCE_CONFIG[1]])
        self.assertEqual(initial['localNotices']['date_pending'], 1)
        self.assertEqual(initial['queued'], 0, 'Unknown time is not delivered as a confirmed receiver event')
        self.assertEqual(self.snapshot()['events'][0]['time'], '')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT scheduled_at FROM future_calendar_notices').fetchone()[0], '')
        self.bodies[EVENT_URL] = host_page()
        confirmed = self.run_discovery(now=NOW + timedelta(minutes=15), sources=[SOURCE_CONFIG[1]])
        self.assertEqual(confirmed['localNotices']['changed'], 1)
        self.assertEqual(confirmed['delivered'], 2)
        self.assertEqual(self.snapshot()['events'][0]['time'], '01:00')

    def test_owner_can_disable_calendar_without_disabling_news_sources(self):
        with transaction(self.path) as db:
            db.execute("UPDATE sources SET enabled=0 WHERE key='federal-reserve-calendar'")
        report = self.run_discovery()
        self.assertEqual(self.fetches, {})
        self.assertEqual(sum(report['localNotices'].values()), 0)
        self.assertFalse(self.snapshot()['enabled'])

    def test_disabling_calendar_cancels_pending_phone_notices(self):
        self.run_discovery()
        with transaction(self.path) as db:
            db.execute("UPDATE sources SET enabled=0 WHERE key='federal-reserve-calendar'")
        self.run_discovery(now=NOW + timedelta(minutes=5))
        self.assertEqual(self.dispatch(now=NOW + timedelta(minutes=5))['attempted'], 0)

    def test_less_precise_host_listing_cannot_erase_board_confirmed_time(self):
        self.bodies[EVENT_URL] = host_page(clock='')
        self.run_discovery()
        self.assertEqual(self.snapshot()['events'][0]['time'], '01:00')
        later = self.run_discovery(now=NOW + timedelta(minutes=5), sources=[SOURCE_CONFIG[1]])
        self.assertEqual(sum(later['localNotices'].values()), 0)
        self.assertEqual(self.snapshot()['events'][0]['time'], '01:00')

    def test_past_host_index_cannot_erase_a_previously_confirmed_start(self):
        self.run_discovery(sources=[SOURCE_CONFIG[1]])
        self.run_discovery(now=NOW + timedelta(days=2), sources=[SOURCE_CONFIG[1]])
        with connect(self.path) as db:
            records = db.execute('SELECT payload_json FROM future_calendar_events').fetchall()
            self.assertEqual(len(records), 2)
            self.assertTrue(all(json.loads(row[0])['originalScheduledAt'] for row in records))

    def test_imminent_notice_replaces_an_undelivered_announcement(self):
        self.run_discovery()
        self.run_discovery(now=NOW + timedelta(hours=4, minutes=10))
        self.assertEqual(self.dispatch(now=NOW + timedelta(hours=4, minutes=10))['delivered'], 1)
        self.assertIn('一小时内开始', self.sender.sent[-1]['body'])

    def test_conflicting_authorities_do_not_confirm_or_notify_the_wrong_time(self):
        self.bodies[EVENT_URL] = host_page(day='18')
        report = self.run_discovery()
        self.assertTrue(any('时间不一致' in error for error in report['errors']))
        self.assertEqual(sum(report['localNotices'].values()), 0)
        self.assertEqual(self.snapshot()['events'], [])

    def test_reclassification_preserves_notice_kind_and_discovery_timestamp(self):
        self.run_discovery()
        with patch('instant_ai.service.transaction', side_effect=lambda: transaction(self.path)):
            reclassify_items()
        with connect(self.path) as db:
            row = db.execute('SELECT event_type,published_at FROM items').fetchone()
            self.assertTrue(row['event_type'].startswith('官方日程'))
            self.assertIsNone(row['published_at'])

    def test_concurrent_news_and_calendar_dispatch_do_not_send_twice(self):
        self.run_discovery()
        entered = threading.Event()
        release = threading.Event()
        sent = []
        class BlockingPush:
            available = True
            def send(self, subscription, notification):
                sent.append(notification)
                entered.set()
                release.wait(3)
                return PushResult(True, 201, False, '')
        sender = BlockingPush()
        with patch('instant_ai.service.utc_now', return_value=NOW.isoformat()):
            worker = threading.Thread(target=lambda: dispatch_web_push_notifications(self.path, sender=sender))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                other = dispatch_web_push_notifications(self.path, sender=sender)
                self.assertEqual(other['attempted'], 0)
                self.assertEqual(len(sent), 1)
            finally:
                release.set();worker.join(3)
        self.assertFalse(worker.is_alive())


class CalendarDiagnosticsTests(unittest.TestCase):
    def test_loopback_diagnostics_are_aggregate_only_and_forwarded_requests_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_path = Path(directory) / 'owner.json'
            configure_owner('synthetic-owner', 'synthetic-password-2026', auth_path)
            auth = OwnerAuth(required=True, path=auth_path)
            snapshot = {'enabled': True, 'events': [{'title': 'Do not expose event bodies here', 'time': '01:00'}], 'pendingDelivery': 2,
                        'failedDelivery': 0, 'sources': [{'key': 'fed-current', 'state': 'ok', 'lastSuccessAt': NOW.isoformat(), 'lastError': ''}]}
            with patch.object(server, 'AUTH', auth), patch.object(server, 'calendar_snapshot', return_value=snapshot), patch.object(server.InstantAIHandler, 'log_message'):
                httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.InstantAIHandler)
                worker = threading.Thread(target=httpd.serve_forever, daemon=True)
                worker.start()
                try:
                    connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port)
                    connection.request('GET', server.LOCAL_CALENDAR_DIAGNOSTICS_PATH)
                    response = connection.getresponse()
                    body = response.read().decode()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(body)['confirmed_events'], 1)
                    self.assertNotIn('Do not expose', body)
                    for forbidden in ['tokens', 'username', 'Cookie', 'portfolio', 'database_path']:
                        self.assertNotIn(forbidden, body)
                    connection.request('GET', server.LOCAL_CALENDAR_DIAGNOSTICS_PATH, headers={'X-Forwarded-For': '203.0.113.5'})
                    response = connection.getresponse();response.read()
                    self.assertEqual(response.status, 404)
                    connection.request('GET', '/api/watch-events')
                    response = connection.getresponse();response.read()
                    self.assertEqual(response.status, 401)
                    connection.close()
                finally:
                    httpd.shutdown();httpd.server_close();worker.join(2)


if __name__ == '__main__':
    unittest.main()
