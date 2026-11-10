from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from instant_ai.model_mr import ModelMrClient, ModelMrUnavailable


class ModelMrWorkNumberTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / 'public-snapshot.json'
        self.client = ModelMrClient('http://127.0.0.1:9', self.snapshot, self.root / 'media')
        # IDs deliberately do not match the chronological ordinal.
        self.works = [{'id': str(10_000 - index * 13), 'title': f'合成作品 {40-index}',
                       'published_at': (datetime(2026, 11, 1) - timedelta(days=index)).isoformat(),
                       'work_type': 'image' if index % 3 == 0 else 'video'} for index in range(40)]
        self.write()
        offline = patch.object(ModelMrClient, '_json', side_effect=ModelMrUnavailable('offline'))
        offline.start()
        self.addCleanup(offline.stop)

    def write(self):
        self.snapshot.write_text(json.dumps({
            'version': 3, 'works': self.works,
            'thoughts': [{'id': 1, 'name': '合成主题'}],
            'thought_links': {str(self.works[5]['id']): [1], str(self.works[20]['id']): [1]},
        }), encoding='utf-8')

    def test_global_numbers_cover_first_last_pages_and_learning_window(self):
        before = self.snapshot.read_bytes()
        with patch.object(ModelMrClient, 'work_detail', side_effect=AssertionError('index only')):
            first = self.client.works(limit=24)
            last = self.client.works(limit=24, offset=24)
            around = self.client.works(limit=7, around_id=int(self.works[20]['id']))
        self.assertEqual([item['sequence_number'] for item in first['items'] + last['items']], list(range(40, 0, -1)))
        self.assertEqual(first['items'][0]['id'], 10_000)
        self.assertEqual(first['items'][0]['sequence_number'], 40)
        self.assertEqual(last['items'][-1]['sequence_number'], 1)
        self.assertEqual(around['items'][3]['sequence_number'], 20)
        self.assertTrue(any(item['work_type'] == 'image' for item in first['items']))
        self.assertEqual(self.snapshot.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.snapshot])

    def test_newest_arrival_and_content_revision_keep_existing_numbers_and_marks(self):
        baseline = {item['id']: item['sequence_number'] for item in self.client.works(limit=100)['items']}
        learned_id = int(self.works[20]['id'])
        self.client.save_learned(learned_id, True, 0)
        self.works.insert(0, {'id': 101_000, 'title': '合成新增', 'published_at': '2026-11-02T08:00:00+08:00'})
        self.works[6]['title'] = '标题修订'
        self.works[6]['comment_count'] = 999
        self.write()
        page = self.client.works(limit=100)
        self.assertEqual(page['items'][0]['sequence_number'], 41)
        self.assertEqual({item['id']: item['sequence_number'] for item in page['items'][1:]}, baseline)
        self.assertEqual(self.client.learning()['last_work_id'], learned_id)

    def test_filtered_thought_pages_and_detail_use_library_ordinal(self):
        filtered = self.client.thought_works(1, query='作品 35')
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['items'][0]['sequence_number'], 35)
        following = self.client.thought_works(1, limit=1, offset=1)
        self.assertEqual(following['items'][0]['sequence_number'], 20)
        details = self.root / 'details'
        details.mkdir()
        work = self.works[5]
        (details / f"{work['id']}.json").write_text(json.dumps({'work': work}), encoding='utf-8')
        detail = self.client.work_detail(int(work['id']))
        self.assertEqual(detail['work']['sequence_number'], 35)

    def test_live_prefix_uses_real_total_and_does_not_guess_an_unknown_total(self):
        rows = [{'id': index, 'title': '合成实时作品'} for index in range(24, 0, -1)]
        with patch.object(ModelMrClient, '_json', return_value={'items': rows, 'total': 100}):
            page = self.client.works(limit=24)
        self.assertEqual([item['sequence_number'] for item in page['items']], list(range(100, 76, -1)))
        with patch.object(ModelMrClient, '_json', return_value={'items': rows}):
            page = self.client.works(limit=24)
        self.assertTrue(all(item['sequence_number'] is None for item in page['items']))
        with patch.object(ModelMrClient, '_json', return_value={'items': rows[:3]}):
            page = self.client.works(limit=24)
        self.assertEqual([item['sequence_number'] for item in page['items']], [3, 2, 1])
