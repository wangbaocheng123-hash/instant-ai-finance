from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from instant_ai.database import connect, initialize, transaction, utc_now
from instant_ai.retention import run_retention_cleanup
from instant_ai.translation import TranslationProvider, translate_items
from instant_ai.watch_translation import annotate_watch_titles, translate_watch_title


class WatchTranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'news.db'
        initialize(self.path)
        self.title = '美联储公开讲话：The Economic Outlook'
        self.call = Mock(return_value='美联储公开讲话：经济展望')
        self.provider = TranslationProvider('synthetic-headline-model', True, 50_000, self.call, '豆包标题翻译')
        at = utc_now()
        with transaction(self.path) as db:
            db.execute("INSERT INTO watch_events(event_key,scope,source_kind,source_event_id,title,event_date,last_synced_at,note) VALUES('event-1','home','timeline','source-1',?,'2026-11-24',?,'private note must never be sent')", (self.title, at))
            db.execute("INSERT INTO future_calendar_events(scope,event_key,version,facts_hash,payload_json,checked_at) VALUES('home','calendar-1',1,'hash',?,?)", (json.dumps({'title':self.title}), at))
            db.execute("INSERT INTO monetary_policy_signals(signal_key,story_key,source_url,evidence_hash,payload_json,first_seen_at,updated_at,expires_at) VALUES('policy-1','story-1','https://www.federalreserve.gov/synthetic','hash',?,?,?,?)", (json.dumps({'title':self.title}),at,at,(datetime.now(UTC)+timedelta(days=1)).isoformat()))

    def translate(self, kind='event', key='event-1', title=None, provider=None):
        return translate_watch_title(kind,key,self.title if title is None else title,path=self.path,provider=provider or self.provider)

    def test_three_known_target_types_share_cache_and_preserve_source_records(self):
        for kind,key in [('event','event-1'),('calendar','calendar-1'),('policy','policy-1')]:
            result=self.translate(kind,key)
            self.assertEqual(result['translations'][f'watch:{kind}:{key}'],'美联储公开讲话：经济展望')
        self.call.assert_called_once_with(self.title)
        with connect(self.path) as db:
            row=db.execute('SELECT title,note FROM watch_events').fetchone()
            self.assertEqual(row[0],self.title)
            self.assertEqual(row[1],'private note must never be sent')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM items').fetchone()[0],0)

    def test_cache_is_visible_on_read_and_no_models_run_for_get_or_chinese(self):
        self.translate()
        payload={'events':[{'title':self.title}], 'discovery':{'events':[{'title':self.title}]}, 'monetaryPolicy':{'signals':[{'title':self.title}]}}
        for _ in range(2):
            annotated=annotate_watch_titles(payload,path=self.path)
            self.assertEqual(annotated['events'][0]['translated_title'],'美联储公开讲话：经济展望')
        self.call.assert_called_once()
        with transaction(self.path) as db:db.execute('UPDATE watch_events SET title=?',('黄金 ETF 与 AI',))
        result=self.translate(title='黄金 ETF 与 AI')
        self.assertEqual(result['skipped_count'],1)
        self.call.assert_called_once()

    def test_client_cannot_send_arbitrary_private_text_or_changed_titles(self):
        for kind,key,title in [('event','event-1','another secret text'),('event','missing',self.title),('unknown','event-1',self.title),([], 'event-1',self.title)]:
            with self.assertRaises(ValueError):self.translate(kind,key,title)
        self.call.assert_not_called()
        with transaction(self.path) as db:db.execute('UPDATE watch_events SET title=?',('Updated Federal Reserve economic outlook',))
        with self.assertRaises(ValueError):self.translate()
        self.translate(title='Updated Federal Reserve economic outlook')
        self.call.assert_called_once_with('Updated Federal Reserve economic outlook')

    def test_watch_and_news_share_paid_results_in_both_directions(self):
        with transaction(self.path) as db:
            db.execute("INSERT INTO items(id,canonical_key,title,url,first_seen_at,last_seen_at) VALUES(1,'synthetic',?,'https://example.test',?,?)",(self.title,utc_now(),utc_now()))
        self.translate()
        news=translate_items([1],path=self.path,provider=self.provider)
        self.assertEqual(news['cached_count'],1)
        self.call.assert_called_once()
        with transaction(self.path) as db:db.execute('DELETE FROM watch_title_translations')
        watch=self.translate()
        self.assertEqual(watch['cached_count'],1)
        self.call.assert_called_once()
        with transaction(self.path) as db:
            db.execute("INSERT INTO items(id,canonical_key,title,url,first_seen_at,last_seen_at) VALUES(2,'synthetic-2','Another English headline beyond remaining quota','https://example.test/2',?,?)",(utc_now(),utc_now()))
        limited=TranslationProvider(self.provider.name,True,len(self.title)+1,self.call,'豆包标题翻译')
        self.assertTrue(translate_items([2],path=self.path,provider=limited)['quota_exhausted'])
        self.call.assert_called_once()

    def test_daily_limit_configuration_and_failure_accounting_are_shared(self):
        limited=TranslationProvider('limited',True,1,self.call,'豆包标题翻译')
        self.assertTrue(self.translate(provider=limited)['quota_exhausted'])
        missing=TranslationProvider('missing',True,50_000,self.call,'豆包标题翻译',False)
        self.assertTrue(self.translate(provider=missing)['errors'])
        self.call.assert_not_called()
        self.call.side_effect=RuntimeError('sensitive provider response must not escape')
        failed=self.translate()
        self.assertTrue(failed['errors'])
        self.assertNotIn('sensitive',str(failed))
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT character_count FROM translation_usage').fetchone()[0],len(self.title))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM watch_title_translations').fetchone()[0],0)

    def test_expired_cache_is_ignored_and_short_lifecycle_cleans_it(self):
        self.translate()
        with transaction(self.path) as db:db.execute("UPDATE watch_title_translations SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertNotIn('translated_title',annotate_watch_titles({'events':[{'title':self.title}]},path=self.path)['events'][0])
        run_retention_cleanup(path=self.path,raw_root=Path(self.tmp.name)/'raw',evidence_root=Path(self.tmp.name)/'evidence',cache_root=Path(self.tmp.name)/'cache',backups_root=Path(self.tmp.name)/'backups')
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM watch_title_translations').fetchone()[0],0)

    def test_legacy_non_chinese_cache_does_not_suppress_a_real_watch_translation(self):
        at=utc_now()
        with transaction(self.path) as db:
            db.execute("INSERT INTO items(id,canonical_key,title,url,first_seen_at,last_seen_at) VALUES(1,'legacy',?,'https://example.test',?,?)",(self.title,at,at))
            db.execute("INSERT INTO item_translations VALUES(1,'zh-CN',?,'untranslated legacy value','legacy',?,?)",(self.title,at,at))
        self.assertNotIn('translated_title',annotate_watch_titles({'events':[{'title':self.title}]},path=self.path)['events'][0])
        self.translate()
        self.call.assert_called_once_with(self.title)

    def test_schema_upgrade_is_idempotent_and_preserves_existing_events(self):
        with transaction(self.path) as db:
            db.execute('DROP TABLE watch_title_translations')
            db.execute("UPDATE schema_meta SET value='16' WHERE key='schema_version'")
        initialize(self.path);initialize(self.path)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0],'17')
            self.assertEqual(db.execute('SELECT title FROM watch_events').fetchone()[0],self.title)

    def test_http_owner_marker_and_known_reference_are_required(self):
        from instant_ai import server
        def handler(payload, authenticated=True):
            instance=server.InstantAIHandler.__new__(server.InstantAIHandler)
            instance.path='/api/watch-events/translate'
            body=json.dumps(payload).encode()
            instance.headers={'X-Instant-AI':'1','Content-Length':str(len(body))}
            instance.rfile=io.BytesIO(body);instance._json=Mock()
            instance._host_allowed=lambda:True;instance._require_auth=lambda:authenticated
            for name in ['_handle_blogger_transfer','_handle_oauth_post','_handle_mcp','_handle_blogger_mcp']:
                setattr(instance,name,lambda *args:False)
            return instance
        body={'kind':'event','key':'event-1','expected_title':self.title}
        with patch.object(server,'translate_watch_title',return_value={}) as call:
            missing=handler(body);missing.do_POST();self.assertEqual(missing._json.call_args.args[1],400)
            denied=handler({**body,'visible_only':True},False);denied.do_POST();call.assert_not_called()
            valid=handler({**body,'visible_only':True});valid.do_POST();call.assert_called_once_with('event','event-1',self.title)
