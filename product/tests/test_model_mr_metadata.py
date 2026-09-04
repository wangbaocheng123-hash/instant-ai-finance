from __future__ import annotations

import copy
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from instant_ai.auth import OwnerAuth
from instant_ai.model_mr import ModelMrClient, ModelMrUnavailable
from instant_ai.model_mr_metadata import KEYWORD_CATEGORIES, METADATA_SCHEMA, clean_keyword_info, keyword_revision, prepare_metadata_merge
from instant_ai.server import InstantAIHandler


class ModelMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / 'public-snapshot.json'
        self.info = clean_keyword_info({'categories': {name: [f'词{i}-{n}' for n in range(3)] for i, name in enumerate(KEYWORD_CATEGORIES)}, 'confirmed_at': '2026-09-01'})
        self.works = [{'id': i, 'title': f'作品{i}', 'url': f'https://www.douyin.com/video/{i}', 'published_at': '2026-09-01', 'keywords': ['旧关键词'], 'media_file': f'{i}.mp4'} for i in range(1, 31)]
        self.categories = [{'id': 1, 'name': '行业', 'level': 1, 'parent_id': None}, {'id': 2, 'name': '科技', 'level': 2, 'parent_id': 1}]
        self.data = {'version': 2, 'works': self.works, 'thoughts': self.categories, 'thought_links': {str(i): [2] for i in range(1, 30)}}
        self.snapshot.write_text(json.dumps(self.data), encoding='utf-8')
        (self.root / 'details').mkdir()
        self.detail = {'version': 2, 'work': self.works[0], 'video_text': {'text': '保留原文', 'official': True}, 'comments': [{'text': '保留评论'}], 'interpretation': {'text': '保留感悟'}}
        (self.root / 'details/1.json').write_text(json.dumps(self.detail), encoding='utf-8')
        self.client = ModelMrClient('http://127.0.0.1:9', self.snapshot, self.root / 'media')
        self.offline = patch.object(ModelMrClient, '_json', side_effect=ModelMrUnavailable('offline'))
        self.offline.start()
        self.addCleanup(self.offline.stop)

    def package(self):
        return {'schema': METADATA_SCHEMA, 'works': [{**self.works[0], 'keyword_info': self.info}], 'thoughts': self.categories, 'thought_links': {'1': [2]}}

    def test_clean_retains_categories_and_more_than_twelve_keywords_without_raw_data(self):
        info = clean_keyword_info({**self.info, 'raw_json': 'private', 'source_path': 'private'})
        work = ModelMrClient._clean_snapshot_work({**self.works[0], 'keyword_info': info})
        self.assertEqual(len(info['keywords']), 30)
        self.assertEqual(work['keyword_info']['categories'], self.info['categories'])
        self.assertGreater(len(work['keywords']), 12)
        self.assertNotIn('raw_json', str(work))
        self.assertNotIn('source_path', str(work))

    def test_merge_preserves_cloud_only_works_media_text_and_owner_title(self):
        before = copy.deepcopy(self.data)
        merged, report = prepare_metadata_merge(self.data, self.package())
        self.assertEqual(report['matched'], 1)
        self.assertEqual(len(merged['works']), 30)
        self.assertEqual(merged['works'][1:], self.works[1:])
        self.assertEqual(merged['works'][0]['media_file'], '1.mp4')
        self.assertEqual(self.data, before)
        self.assertEqual(len(merged['works'][0]['keyword_info']['keywords']), 31)
        self.assertEqual(prepare_metadata_merge(merged, self.package())[1]['keyword_updates'], 0)

    def test_ambiguous_identity_not_matched_by_title_and_category_conflict_stops(self):
        package = self.package()
        package['works'][0]['url'] = 'https://www.douyin.com/video/9999'
        result, report = prepare_metadata_merge(self.data, package)
        self.assertEqual(report['unmatched_ids'], [1])
        self.assertEqual(result['works'], self.works)
        package['thoughts'] = [{**self.categories[0], 'name': '不同分类'}]
        with self.assertRaises(ValueError):
            prepare_metadata_merge(self.data, package)

    def test_manual_keywords_are_not_replaced_by_import(self):
        self.data['works'][0]['keyword_info'] = clean_keyword_info({'keywords': ['主人整理'], 'edited_by_owner': True})
        result, report = prepare_metadata_merge(self.data, self.package())
        self.assertEqual(report['preserved_owner_keywords'], 1)
        self.assertIn('主人整理', result['works'][0]['keyword_info']['keywords'])

    def test_parent_leaf_paging_search_and_unknown_category(self):
        page = self.client.thought_works(1, limit=24)
        self.assertEqual((page['count'], page['total'], page['has_more']), (24, 29, True))
        leaf = self.client.thought_works(2, limit=24, offset=24)
        self.assertEqual([w['id'] for w in leaf['items']], list(range(25, 30)))
        self.assertFalse(leaf['has_more'])
        self.assertEqual(self.client.thought_works(2, query='作品29')['count'], 1)
        self.assertNotIn('comments', str(page))
        with self.assertRaises(ValueError):
            self.client.thought_works(999)

    def test_legacy_missing_links_explains_gap(self):
        del self.data['thought_links']
        self.snapshot.write_text(json.dumps(self.data), encoding='utf-8')
        page = self.client.thought_works(2)
        self.assertFalse(page['links_available'])
        self.assertIn('关联尚未同步', page['message'])

    def test_keyword_save_revision_and_other_fields_preserved(self):
        revision = keyword_revision(None, ['旧关键词'])
        result = self.client.save_keywords(1, self.info['categories'], [], revision)
        self.assertEqual(len(result['keywords']), 30)
        self.assertTrue(result['keyword_info']['edited_by_owner'])
        current = json.loads((self.root / 'details/1.json').read_text(encoding='utf-8'))
        for key in ('video_text', 'comments', 'interpretation'):
            self.assertEqual(current[key], self.detail[key])
        before = self.snapshot.read_bytes()
        with self.assertRaises(ValueError):
            self.client.save_keywords(1, {}, ['过期草稿'], revision)
        self.assertEqual(before, self.snapshot.read_bytes())
        with self.assertRaises(ValueError):
            self.client.save_keywords(1, {'未知': []}, [], result['keyword_revision'])

    def test_http_routes_require_owner_and_csrf_then_use_bounded_page(self):
        auth = OwnerAuth(required=False, path=self.root / 'unused-auth.json')
        server = ThreadingHTTPServer(('127.0.0.1', 0), InstantAIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch('instant_ai.server.MODEL_MR', self.client), patch('instant_ai.server.AUTH', auth):
            thread.start()
            try:
                def request(method, path, body=None, headers=None):
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    connection.request(method, path, body=body, headers=headers or {})
                    response = connection.getresponse()
                    status, raw = response.status, response.read()
                    connection.close()
                    return status, json.loads(raw)
                status, page = request('GET', '/api/model-mr/thoughts/2/works?limit=2&offset=24')
                self.assertEqual(status, 200)
                self.assertEqual([item['id'] for item in page['items']], [25, 26])
                body = json.dumps({'categories': self.info['categories'], 'keywords': [], 'expected_revision': keyword_revision(None, ['旧关键词'])})
                self.assertEqual(request('POST', '/api/model-mr/works/1/keywords', body, {'Content-Type': 'application/json', 'Origin': 'https://evil.example'})[0], 403)
                self.assertEqual(request('POST', '/api/model-mr/works/1/keywords', body, {'Content-Type': 'application/json', 'X-Instant-AI': '1'})[0], 200)
                with patch('instant_ai.server.AUTH', OwnerAuth(required=True, path=self.root / 'not-configured.json')):
                    self.assertIn(request('GET', '/api/model-mr/thoughts/2/works')[0], (401, 503))
                    self.assertIn(request('POST', '/api/model-mr/works/1/keywords', body, {'Content-Type': 'application/json', 'X-Instant-AI': '1'})[0], (401, 503))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)


if __name__ == '__main__':
    unittest.main()
