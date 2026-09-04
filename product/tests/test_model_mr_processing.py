from __future__ import annotations

import copy
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from instant_ai.auth import OwnerAuth
from instant_ai.model_mr import ModelMrClient
from instant_ai.model_mr_keywords import KEYWORD_CATEGORIES, SCHEMA_VERSION, extract_keywords, normalize_categories, source_hash
from instant_ai.model_mr_metadata import keyword_revision
from instant_ai.model_mr_processing import ModelMrProcessor, PROVIDER_LOCK
from instant_ai.server import InstantAIHandler


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = ModelMrClient('http://127.0.0.1:9', self.root / 'snapshot.json', self.root / 'media')
        self.work = {'id': 1, 'title': '标题不是关键词来源', 'media_file': '1.mp4', 'keywords': []}
        self.snapshot = {'version': 2, 'works': [self.work], 'thoughts': []}
        self.client._write_json(self.client.snapshot_path, self.snapshot)
        self.client.media_root.mkdir()
        (self.client.media_root / '1.mp4').write_bytes(b'synthetic-not-real-video')
        self.detail = {'work': self.work, 'video_text': {'text': '', 'official': False},
                       'transcripts': [], 'comments': [{'text': '不要发送这条评论'}], 'interpretation': {'text': '保留感悟'}}
        self.write_detail()
        self.processor = ModelMrProcessor(self.client)
        self.categories = {name: [] for name in KEYWORD_CATEGORIES}
        self.categories['行业与板块'] = ['科技股']
        self.asr = patch('instant_ai.model_mr_processing.doubao_asr.transcribe_video', return_value={'text': '科技股原文'}).start()
        self.kw = patch('instant_ai.model_mr_processing.model_mr_keywords.extract_keywords', side_effect=self.keyword_result).start()
        patch('instant_ai.model_mr_processing.doubao_asr.is_configured', return_value=True).start()
        patch('instant_ai.model_mr_processing.model_mr_keywords.is_configured', return_value=True).start()
        patch.object(ModelMrClient, '_json', side_effect=AssertionError('never contact desktop sidecar')).start()
        self.addCleanup(patch.stopall)

    def keyword_result(self, text):
        return {'categories': copy.deepcopy(self.categories), 'keywords': ['科技股'], 'model': 'doubao:test',
                'schema_version': SCHEMA_VERSION, 'source_hash': source_hash(text)}

    def write_detail(self):
        self.client._write_json(self.client._detail_path(1), self.detail)

    def enqueue(self):
        self.processor.set_enabled(True)
        self.processor.enqueue_arrival(1, 'a' * 64)

    def state(self):
        return self.processor.status()['items'][0]['state']

    def test_default_off_and_read_status_has_no_side_effect(self):
        self.assertFalse(self.processor.status()['enabled'])
        self.assertFalse(self.processor.path.exists())
        self.processor.enqueue_arrival(1, 'a' * 64)
        self.assertFalse(self.processor.path.exists())
        self.processor.set_enabled(True)
        self.assertFalse(self.processor.process_one())  # No historical library scan.
        self.asr.assert_not_called()

    def test_arrival_pipeline_persists_original_keywords_and_deduplicates(self):
        self.enqueue()
        self.processor.enqueue_arrival(1, 'a' * 64)
        self.assertTrue(self.processor.process_one())
        self.assertEqual(self.state(), 'done')
        saved = self.client.processing_detail(1)
        self.assertEqual(saved['video_text']['text'], '科技股原文')
        self.assertEqual(saved['video_text']['source'], 'doubao-auto-unreviewed')
        self.assertEqual(saved['work']['keyword_info']['source_hash'], source_hash('科技股原文'))
        self.assertFalse(saved['work']['keyword_info']['edited_by_owner'])
        self.assertEqual(saved['comments'], self.detail['comments'])
        self.assertEqual(saved['interpretation'], self.detail['interpretation'])
        self.kw.assert_called_once_with('科技股原文')
        self.asr.assert_called_once()
        self.processor.enqueue_arrival(1, 'a' * 64)
        self.assertFalse(self.processor.process_one())

    def test_existing_original_and_keywords_are_preserved(self):
        self.detail['video_text']['text'] = '主人原文'
        self.detail['work']['keywords'] = ['主人整理']
        self.write_detail()
        self.enqueue()
        self.processor.process_one()
        self.asr.assert_not_called()
        self.kw.assert_not_called()
        self.assertEqual(self.client.processing_detail(1)['video_text']['text'], '主人原文')

    def test_partial_keyword_index_save_is_repaired_without_rebilling(self):
        self.enqueue()
        original_write = self.client._write_json
        def write(path, value):
            if path == self.client.snapshot_path and value['works'][0].get('keyword_info'):
                raise OSError('simulated index failure')
            original_write(path, value)
        with patch.object(ModelMrClient, '_write_json', side_effect=write):
            self.processor.process_one()
        self.assertEqual(self.state(), 'review')
        self.processor.retry(self.processor.status()['items'][0]['id'])
        self.processor.process_one()
        self.assertEqual(self.state(), 'done')
        self.asr.assert_called_once()
        self.kw.assert_called_once()
        self.assertEqual(self.client._require_snapshot()['works'][0]['keywords'], ['科技股'])

    def test_manual_keyword_partial_save_can_finish_from_cached_result(self):
        self.detail['video_text']['text'] = '科技股原文'
        self.write_detail()
        self.processor.request_keywords(1, keyword_revision(None, []))
        original_write = self.client._write_json
        def write(path, value):
            if path == self.client.snapshot_path:
                raise OSError('simulated index failure')
            original_write(path, value)
        with patch.object(ModelMrClient, '_write_json', side_effect=write):
            self.processor.process_one()
        self.assertEqual(self.state(), 'review')
        self.processor.retry(self.processor.status()['items'][0]['id'])
        self.processor.process_one()
        self.assertEqual(self.state(), 'done')
        self.kw.assert_called_once()

    def test_cached_doubao_transcript_is_saved_without_paid_asr(self):
        self.detail['transcripts'] = [{'source': 'doubao', 'text': '科技股已有转写'}]
        self.write_detail()
        self.enqueue()
        self.processor.process_one()
        self.asr.assert_not_called()
        self.kw.assert_called_once_with('科技股已有转写')

    def test_missing_keyword_config_does_not_repeat_successful_asr_on_retry(self):
        self.enqueue()
        with patch('instant_ai.model_mr_processing.model_mr_keywords.is_configured', return_value=False):
            self.processor.process_one()
        self.assertEqual(self.state(), 'configuration')
        self.assertFalse(self.processor.process_one())
        self.processor.retry(self.processor.status()['items'][0]['id'])
        self.processor.process_one()
        self.assertEqual(self.state(), 'done')
        self.asr.assert_called_once()

    def test_failed_paid_request_does_not_retry_automatically_or_leak_body(self):
        self.enqueue()
        self.asr.side_effect = RuntimeError('upstream-secret-body-never-show')
        self.processor.process_one()
        self.assertEqual(self.state(), 'review')
        self.assertNotIn('upstream-secret', json.dumps(self.processor.status()))
        for _ in range(3):
            self.assertFalse(self.processor.process_one())
        self.asr.assert_called_once()

    def test_asr_result_survives_save_failure_and_retry_without_rebilling(self):
        self.enqueue()
        with patch.object(ModelMrClient, 'save_auto_video_text', side_effect=OSError('disk full')):
            self.processor.process_one()
        self.assertEqual(self.state(), 'review')
        self.processor.retry(self.processor.status()['items'][0]['id'])
        self.processor.process_one()
        self.asr.assert_called_once()
        self.assertEqual(self.state(), 'done')

    def test_owner_text_written_during_asr_is_not_overwritten(self):
        def asr(*args, **kwargs):
            self.detail['video_text']['text'] = '主人同时保存'
            self.write_detail()
            return {'text': '自动候选'}
        self.asr.side_effect = asr
        self.enqueue()
        self.processor.process_one()
        self.assertEqual(self.client.processing_detail(1)['video_text']['text'], '主人同时保存')
        self.kw.assert_called_once_with('主人同时保存')

    def test_text_changed_during_extraction_rejects_stale_result(self):
        self.detail['video_text']['text'] = '旧原文'
        self.write_detail()
        def keywords(text):
            self.detail['video_text']['text'] = '主人新原文'
            self.write_detail()
            return self.keyword_result(text)
        self.kw.side_effect = keywords
        self.enqueue()
        self.processor.process_one()
        self.assertEqual(self.state(), 'conflict')
        self.assertEqual(self.client.processing_detail(1)['work']['keywords'], [])

    def test_manual_keywords_are_revision_guarded_and_cached(self):
        self.detail['video_text']['text'] = '科技股原文'
        self.write_detail()
        revision = keyword_revision(None, [])
        with self.assertRaises(ValueError):
            self.processor.request_keywords(1, 'wrong')
        self.processor.request_keywords(1, revision)
        self.processor.request_keywords(1, revision)
        self.processor.process_one()
        work = self.client.processing_detail(1)['work']
        cached = self.processor.request_keywords(1, keyword_revision(work['keyword_info']))
        self.assertIn('没有调用 API', cached['message'])
        self.kw.assert_called_once()

    def test_quota_and_pause_are_checked_before_next_paid_stage(self):
        self.enqueue()
        with self.processor.db() as conn:
            conn.executemany('INSERT INTO calls(job_id,phase,day) VALUES(1,?,?)', [('asr', self.processor._day())] * 20)
        self.processor.process_one()
        self.assertEqual(self.state(), 'quota')
        self.asr.assert_not_called()
        self.assertFalse(self.processor.process_one())
        with self.processor.db() as conn:
            conn.execute('DELETE FROM calls')
        self.asr.side_effect = lambda *args, **kw: (self.processor.set_enabled(False) and {'text': '科技股原文'})
        self.processor.process_one()
        self.kw.assert_not_called()
        self.assertEqual(self.state(), 'queued')

    def test_serial_lock_blocks_parallel_worker_and_manual_asr(self):
        self.enqueue()
        with PROVIDER_LOCK:
            self.assertFalse(self.processor.process_one())
            with self.assertRaisesRegex(RuntimeError, '未重复提交'):
                self.client.transcribe(1, 'doubao')
        self.asr.assert_not_called()

    def test_three_failures_pause_and_completed_jobs_cannot_retry(self):
        self.enqueue()
        self.asr.side_effect = RuntimeError('failed')
        for _ in range(3):
            self.processor.process_one()
            self.processor.retry(self.processor.status()['items'][0]['id'])
        self.assertEqual(self.processor.status()['failures'], 3)
        self.assertFalse(self.processor.process_one())
        self.processor.set_enabled(True)
        self.asr.side_effect = None
        self.processor.process_one()
        with self.assertRaises(ValueError):
            self.processor.retry(self.processor.status()['items'][0]['id'])

    def test_mcp_auto_original_is_readable_but_not_human_verified(self):
        from instant_ai.model_mr_mcp import ModelMrMcpLibrary
        self.enqueue()
        self.processor.process_one()
        original = ModelMrMcpLibrary._video_original(self.client.processing_detail(1))
        self.assertEqual(original['text'], '科技股原文')
        self.assertFalse(original['verified'])

    def test_http_owner_csrf_and_explicit_billing_consent(self):
        auth = OwnerAuth(required=False, path=self.root / 'unused.json')
        server = ThreadingHTTPServer(('127.0.0.1', 0), InstantAIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch('instant_ai.server.MODEL_MR_PROCESSOR', self.processor), patch('instant_ai.server.AUTH', auth):
            thread.start()
            try:
                def request(method, path, body=None, headers=None):
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
                    response = conn.getresponse()
                    result = response.status, json.loads(response.read())
                    conn.close()
                    return result
                route = '/api/model-mr/processing'
                self.assertEqual(request('GET', route)[0], 200)
                self.assertFalse(self.processor.path.exists())
                self.assertEqual(request('POST', route)[0], 403)
                self.assertEqual(request('POST', route, {'enabled': True}, {'X-Instant-AI': '1'})[0], 400)
                status, value = request('POST', route, {'enabled': True, 'confirm_billing': True}, {'X-Instant-AI': '1'})
                self.assertEqual(status, 200)
                self.assertTrue(value['enabled'])
                with patch('instant_ai.server.AUTH', OwnerAuth(required=True, path=self.root / 'missing-auth.json')):
                    self.assertIn(request('GET', route)[0], (401, 503))
                    self.assertIn(request('POST', route, {'enabled': False, 'confirm_billing': True},
                                          {'X-Instant-AI': '1'})[0], (401, 503))
                self.assertTrue(self.processor.status()['enabled'])
            finally:
                server.shutdown(); server.server_close(); thread.join(2)

    @unittest.skipUnless(os.name == 'posix', 'cloud worker uses POSIX singleton lock')
    def test_restart_never_retries_an_interrupted_paid_call(self):
        self.enqueue()
        with self.processor.db() as conn:
            conn.execute("UPDATE jobs SET state='running'")
        stop = threading.Event()
        stop.set()
        self.processor.run(stop)
        self.assertEqual(self.state(), 'review')
        self.asr.assert_not_called()

    def test_owner_keywords_written_during_ai_call_are_not_replaced(self):
        self.detail['video_text']['text'] = '科技股原文'
        self.write_detail()
        def keywords(text):
            self.client.save_keywords(1, {'行业与板块': ['主人新词']}, [], keyword_revision(None, []))
            return self.keyword_result(text)
        self.kw.side_effect = keywords
        self.enqueue()
        self.processor.process_one()
        self.assertEqual(self.state(), 'conflict')
        self.assertEqual(self.client.processing_detail(1)['work']['keywords'], ['主人新词'])

    def test_manual_keywords_without_original_and_media_escape_rejected(self):
        with self.assertRaises(ValueError):
            self.processor.request_keywords(1, keyword_revision(None, []))
        self.detail['work']['media_file'] = '../outside.mp4'
        (self.root / 'outside.mp4').write_bytes(b'synthetic')
        self.write_detail()
        self.enqueue()
        self.processor.process_one()
        self.assertEqual(self.state(), 'review')
        self.asr.assert_not_called()

    def test_long_original_not_truncated_or_sent(self):
        self.detail['video_text']['text'] = '长' * 60001
        self.write_detail()
        self.processor.request_keywords(1, keyword_revision(None, []))
        self.processor.process_one()
        self.assertEqual(self.state(), 'review')
        self.kw.assert_not_called()


class KeywordAdapterTests(unittest.TestCase):
    def test_long_audio_is_rejected_before_submit(self):
        from instant_ai import doubao_asr
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / 'test.mp4'
            video.write_bytes(b'synthetic')
            with patch.object(doubao_asr, 'is_configured', return_value=True), \
                 patch.object(doubao_asr, '_find_ffmpeg', return_value='ffmpeg'), \
                 patch.object(doubao_asr, '_extract_audio'), \
                 patch.object(doubao_asr, '_post_json') as submit, \
                 patch.object(doubao_asr.wave, 'open') as wave:
                wave.return_value.__enter__.return_value.getnframes.return_value = 16000 * 1201
                wave.return_value.__enter__.return_value.getframerate.return_value = 16000
                with self.assertRaisesRegex(RuntimeError, '未提交付费'):
                    doubao_asr.transcribe_video(video, 1, max_duration_seconds=1200)
                submit.assert_not_called()

    def test_same_local_limits_and_deduplication(self):
        data = {name: [f'词{index}-{n}' for n in range(10)] for index, name in enumerate(KEYWORD_CATEGORIES)}
        result = normalize_categories(data)
        self.assertLessEqual(sum(map(len, result.values())), 40)
        self.assertTrue(all(len(words) <= 8 for words in result.values()))
        with self.assertRaises(RuntimeError):
            normalize_categories({'unknown': ['词']})

    def test_ark_responses_contract_only_sends_original_and_does_not_retry(self):
        categories = {name: [] for name in KEYWORD_CATEGORIES}
        categories[KEYWORD_CATEGORIES[0]] = ['科技股']
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return json.dumps({'output': [{'content': [{'text': json.dumps(categories)}]}]}).encode()
        with patch.dict(os.environ, {'INSTANT_AI_DOUBAO_ARK_API_KEY': 'synthetic-test-key'}):
            with patch('instant_ai.model_mr_keywords.build_opener') as opener:
                opener.return_value.open.return_value = Response()
                result = extract_keywords('科技股原文')
                self.assertEqual(result['keywords'], ['科技股'])
                req = opener.return_value.open.call_args.args[0]
                self.assertTrue(req.full_url.endswith('/api/v3/responses'))
                user = json.loads(req.data)['input'][1]['content'][0]['text']
                self.assertEqual(json.loads(user), {'video_original': '科技股原文'})
                opener.return_value.open.side_effect = RuntimeError('synthetic-secret-upstream')
                with self.assertRaisesRegex(RuntimeError, '未自动重试'):
                    extract_keywords('科技股原文')
                self.assertEqual(opener.return_value.open.call_count, 2)


if __name__ == '__main__':
    unittest.main()
