from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from instant_ai.collectors import Entry, Source
from instant_ai.database import connect, initialize, seed_sources, transaction
from instant_ai.monetary_policy import classify_policy, dispatch_policy_signals, policy_snapshot
from instant_ai.service import _source_from_row, _upsert_entry, _select_due_source_rows


NOW = datetime(2026, 11, 19, 12, tzinfo=UTC)
URL = 'https://www.cnbc.com/2026/11/19/synthetic-waller-policy-interview.html'
TITLE = "Fed's Waller discusses a half-point rate hike and a conditional January move"


class MonetaryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'news.db'
        initialize(self.path)
        seed_sources(self.path)
        with connect(self.path) as db:
            self.source = _source_from_row(db.execute("SELECT * FROM sources WHERE key='cnbc-top-news'").fetchone())

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, *, title=TITLE, url=URL, summary='', published=None, source=None, observed=NOW):
        with transaction(self.path) as db:
            _upsert_entry(db, source or self.source,
                Entry(url, title, url, summary, published if published is not None else NOW.isoformat(), publisher='CNBC'),
                'a'*64, '', 'application/xml', 200, observed_at=observed.isoformat())

    def assignment(self, receipts=None, complete=True):
        return {'monetaryPolicyIngestion': {'contractVersion': 1, 'deliveryToken': 'synthetic-short-lived-token',
                                          'receipts': receipts or {}, 'inventoryComplete': complete}}

    def test_interview_is_candidate_without_invented_conditions_or_decision(self):
        self.add()
        signal = policy_snapshot(path=self.path, now=NOW)['signals'][0]
        self.assertEqual(signal['kind'], 'official_comment')
        self.assertEqual(signal['speaker'], 'Christopher Waller')
        self.assertEqual(signal['factStatus'], 'candidate-requires-verification')
        self.assertEqual(signal['firstObservedSource']['originality'], 'original-channel-verification-required')
        self.assertEqual(signal['sources'][0]['excerpt'], '')
        self.assertNotIn('25', signal['title'] + signal['sources'][0]['excerpt'])
        self.assertEqual(signal['publishedAt'], NOW.isoformat())
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT importance_score FROM items').fetchone()[0], 90)
            item = db.execute('SELECT topics_json,entities_json,event_type FROM items').fetchone()
            self.assertIn('宏观政策', json.loads(item[0]))
            self.assertIn('美联储', json.loads(item[1]))
            self.assertEqual(item[2], '央行利率政策线索')

    def test_decision_minutes_forecast_and_market_are_distinct(self):
        cases = [
            ('Federal Reserve issues FOMC statement', 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20261119a.htm', 'decision'),
            ('Minutes of the Federal Open Market Committee', 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20261119b.htm', 'minutes'),
            ('Goldman forecasts Fed rate cuts next year', URL, 'forecast'),
            ('美联储理事沃勒预计明年需要继续加息', URL, 'official_comment'),
            ('Goldman forecasts Fed Waller will support rate cuts', URL, 'forecast'),
            ('FedWatch odds of a Fed rate hike increase', URL, 'market_expectation'),
            ('Waller, The Economic Outlook', 'https://www.federalreserve.gov/newsevents/speech/waller20261119a.htm', 'official_comment'),
            ('ECB cuts interest rates', 'https://www.ecb.europa.eu/press/synthetic.html', 'decision'),
            ('Bank of Japan raises interest rates', 'https://www.boj.or.jp/en/synthetic.htm', 'decision'),
            ('中国人民银行下调政策利率', 'https://www.pbc.gov.cn/synthetic.html', 'decision'),
        ]
        for title, url, kind in cases:
            with self.subTest(title=title):
                result = classify_policy(title, '', url)
                self.assertIsNotNone(result)
                self.assertEqual(result['kind'], kind)

    def test_policy_mechanics_and_named_speaker_are_high_priority_without_inventing_decisions(self):
        for title in ['Fed discusses quantitative tightening and balance-sheet runoff',
                      'Fed considers asset purchases and QE',
                      'Christopher Waller discusses 50 basis points in January',
                      '美联储考虑缩表及货币政策调整']:
            with self.subTest(title=title):
                result=classify_policy(title, '', URL)
                self.assertIsNotNone(result)
                self.add(title=title, url=URL+'-'+str(len(title)))
        self.assertIsNone(classify_policy('Fed cuts staff and operating costs', '', URL))
        self.assertIsNone(classify_policy('Tim Cook cuts iPhone prices', '', URL))
        with connect(self.path) as db:
            self.assertTrue(all(row[0]>=90 for row in db.execute('SELECT importance_score FROM items')))
            self.assertGreater(db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0],0)

    def test_final_policy_priority_queues_existing_phone_channel_once(self):
        with transaction(self.path) as db:
            db.execute("INSERT INTO web_push_subscriptions(id,endpoint,p256dh,auth_secret,created_at,updated_at) VALUES('synthetic-phone','https://web.push.apple.com/synthetic','synthetic-public-key','synthetic-auth',?,?)",(NOW.isoformat(),NOW.isoformat()))
        self.add(title='Christopher Waller discusses 50 basis points in January')
        self.add(title='Christopher Waller discusses 50 basis points in January')
        self.add(title='Christopher Waller discusses 50 basis points in January', url='https://www.reuters.com/world/us/synthetic-same-policy/')
        with connect(self.path) as db:
            rows=db.execute('SELECT channel,reason_json FROM notification_outbox ORDER BY channel').fetchall()
            self.assertEqual([row['channel'] for row in rows],['in_app','web_push'])
            self.assertTrue(all(json.loads(row['reason_json'])['importance_score']>=90 for row in rows))
            self.assertTrue(all(json.loads(row['reason_json'])['event_type']=='央行利率政策线索' for row in rows))
        from instant_ai.service import dispatch_web_push_notifications
        sender=Mock(available=True)
        sender.send.return_value=Mock(ok=True,status=201,expired=False,error='')
        first=dispatch_web_push_notifications(self.path,sender=sender)
        second=dispatch_web_push_notifications(self.path,sender=sender)
        self.assertEqual(first['delivered'],1)
        self.assertEqual(second['attempted'],0)
        sender.send.assert_called_once()
        message=sender.send.call_args.args[1]
        self.assertIn('美联储',message['title'])
        self.assertIn('官员表态线索',message['title'])
        self.assertIn('政策线索待核验',message['body'])

    def test_policy_source_health_reports_disabled_failed_stale_and_healthy(self):
        at=NOW.isoformat()
        with transaction(self.path) as db:
            db.execute("UPDATE sources SET last_success_at=?,last_error=NULL",(at,))
            db.execute("UPDATE sources SET enabled=0 WHERE key='federal-reserve-press'")
            db.execute("UPDATE sources SET last_error='sensitive error body' WHERE key='cnbc-top-news'")
            db.execute("UPDATE sources SET last_success_at=? WHERE key='federal-reserve-monetary-policy'",((NOW-timedelta(hours=2)).isoformat(),))
        snapshot=policy_snapshot(path=self.path,now=NOW)
        states={row['key']:row['state'] for row in snapshot['monitoring']['sources']}
        self.assertEqual(states['federal-reserve-press'],'disabled')
        self.assertEqual(states['cnbc-top-news'],'error')
        self.assertEqual(states['federal-reserve-monetary-policy'],'stale')
        self.assertEqual(states['cnbc-finance'],'ok')
        self.assertNotIn('sensitive',str(snapshot))

    def test_unrelated_and_untrusted_sources_do_not_create_signals(self):
        for title, url in [('FedEx raises shipping rates', URL), ('Mortgage rates decline', URL),
                           ('Fed raises rates', 'https://www.cnbc.com.evil.test/a'),
                           ('Fed cuts rates', 'http://www.cnbc.com/a'),
                           ('Fed cuts rates', 'https://www.cnbc.com/a?token=not-a-real-secret'),
                           ('Fed raises rates', 'https://news.google.com/rss/articles/unresolved')]:
            self.assertIsNone(classify_policy(title, '', url))

    def test_repeat_same_url_and_identical_reprint_merge_without_resend(self):
        self.add()
        calls = []
        def send(_, payload):
            calls.append(payload)
            return {'ok': True, 'signal': {'id': 'receipt-1', 'evidenceHash': payload['candidate']['evidenceHash'], 'status': 'received'}}
        result = dispatch_policy_signals(path=self.path, now=NOW, assignment_fetcher=lambda _: self.assignment(), sender=send)
        self.assertEqual(result['delivered'], 1)
        payload = calls[0]['candidate']
        self.add(url=URL + '?utm_source=another-feed')
        self.add(url='https://www.reuters.com/world/us/synthetic-reprint-2026-11-19/')
        signals = policy_snapshot(path=self.path, now=NOW)['signals']
        self.assertEqual(len(signals), 1)
        self.assertEqual(len(signals[0]['sources']), 2)
        self.assertEqual(signals[0]['status'], 'delivered')
        receipts = {payload['key']: {'id': 'receipt-1', 'evidenceHash': payload['evidenceHash'], 'status': 'analyzed'}}
        self.assertEqual(dispatch_policy_signals(path=self.path, now=NOW, assignment_fetcher=lambda _: self.assignment(receipts), sender=send)['delivered'], 0)
        self.assertEqual(len(calls), 1)
        self.assertIn('已完成', policy_snapshot(path=self.path, now=NOW)['signals'][0]['pipelineStatus'])

    def test_missing_receiver_retry_bad_receipt_and_recovery_preserve_pending(self):
        self.add()
        self.assertEqual(dispatch_policy_signals(path=self.path, now=NOW, assignment_fetcher=lambda _: {})['state'], 'waiting_receiver')
        def invalid(*_):
            return {'ok': True, 'signal': {'id': 'wrong', 'evidenceHash': 'b'*64}}
        self.assertEqual(dispatch_policy_signals(path=self.path, now=NOW, assignment_fetcher=lambda _: self.assignment(), sender=invalid)['failed'], 1)
        self.assertEqual(dispatch_policy_signals(path=self.path, now=NOW, assignment_fetcher=lambda _: self.assignment(), sender=invalid)['failed'], 0)
        def accept(_, body):
            return {'ok': True, 'signal': {'id': 'correct', 'evidenceHash': body['candidate']['evidenceHash'], 'status': 'received'}}
        self.assertEqual(dispatch_policy_signals(path=self.path, now=NOW + timedelta(minutes=2), assignment_fetcher=lambda _: self.assignment(), sender=accept)['delivered'], 1)
        # A complete receiver inventory repairs loss after receiver restore.
        self.assertEqual(dispatch_policy_signals(path=self.path, now=NOW + timedelta(minutes=3), assignment_fetcher=lambda _: self.assignment(), sender=accept)['delivered'], 1)
        self.assertEqual(dispatch_policy_signals(path=self.path, now=NOW + timedelta(minutes=4), assignment_fetcher=lambda _: self.assignment(complete=False), sender=invalid)['failed'], 0)

    def test_old_undated_future_news_not_automatically_delivered_and_ttl_prunes(self):
        self.add(title='Fed rate hike archive', url=URL + '-old', published=(NOW-timedelta(days=3)).isoformat())
        self.add(title='Fed rate cut undated', url=URL + '-undated', published='')
        self.add(title='Fed rate hike future', url=URL + '-future', published=(NOW+timedelta(days=1)).isoformat())
        self.assertTrue(all(row['status']=='date_review' for row in policy_snapshot(path=self.path, now=NOW)['signals']))
        dispatch_policy_signals(path=self.path, now=NOW+timedelta(days=9))
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM monetary_policy_signals').fetchone()[0], 0)

    def test_material_same_url_update_creates_one_new_pending_version(self):
        self.add()
        signal = policy_snapshot(path=self.path, now=NOW)['signals'][0]
        with transaction(self.path) as db:
            db.execute("UPDATE monetary_policy_signals SET status='delivered'")
        self.add(published=(NOW+timedelta(minutes=3)).isoformat(), observed=NOW+timedelta(minutes=3))
        unchanged = policy_snapshot(path=self.path, now=NOW)['signals'][0]
        self.assertEqual(unchanged['status'], 'delivered', 'Feed timestamp changes alone are not new facts')
        self.assertEqual(unchanged['evidenceHash'], signal['evidenceHash'])
        self.add(summary='If guidance is explicit, the speaker prefers two smaller moves; otherwise a larger one.', observed=NOW+timedelta(minutes=5))
        signals = policy_snapshot(path=self.path, now=NOW)['signals']
        self.assertEqual(len(signals), 1)
        self.assertNotEqual(signals[0]['evidenceHash'], signal['evidenceHash'])
        self.assertEqual(signals[0]['status'], 'pending')

        self.add(title='Fed rate cut pending date', url=URL+'-date', published='')
        self.add(title='Fed rate cut pending date', url=URL+'-date', published=NOW.isoformat())
        restored = next(s for s in policy_snapshot(path=self.path, now=NOW)['signals'] if s['title']=='Fed rate cut pending date')
        self.assertEqual(restored['status'], 'pending', 'A genuinely resolved missing date must become eligible')

    def test_pause_source_skips_new_collection_and_schema_keeps_owner_disable(self):
        with transaction(self.path) as db:
            db.execute("UPDATE sources SET enabled=0 WHERE key='federal-reserve-monetary-policy'")
        seed_sources(self.path)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT enabled FROM sources WHERE key='federal-reserve-monetary-policy'").fetchone()[0], 0)
            rows = [dict(row) for row in db.execute('SELECT * FROM sources WHERE enabled=1')]
        selected, _ = _select_due_source_rows(rows, NOW+timedelta(days=1))
        keys = [row['key'] for row in selected]
        self.assertLess(keys.index('cnbc-top-news'), keys.index('sec-press-releases'))


if __name__ == '__main__':
    unittest.main()
