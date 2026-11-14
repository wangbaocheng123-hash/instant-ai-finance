from __future__ import annotations

import array
import hashlib
import io
import json
import math
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from instant_ai.model_mr_prompts import RATE, VERSION, announcement_parts, ensure_recorded_prompts, render_recorded_announcement
from instant_ai.model_mr_speech import announcement_text, synthesize_announcement


class RecordedAnnouncementTests(unittest.TestCase):
    def test_date_and_work_number_use_recorded_words_in_correct_order(self):
        parts = [part for part in announcement_parts(announcement_text('2026-11-14T08:00:00+08:00', 129)) if isinstance(part, str)]
        self.assertEqual([Path(part).stem for part in parts],
                         ['2', '0', '2', '6', 'year', '11', 'month', '14', 'day', 'h-1', 'hundred', '20', '9', 'dir-result_match'])
        for number, ending in [(21, ['h-2', '10', '1']), (101, ['h-1', 'hundred', '0', '1']), (1001, ['h-1', 'thousand', '0', '1'])]:
            tokens = [Path(p).stem for p in announcement_parts(announcement_text('2026-01-01', number)) if isinstance(p, str)]
            self.assertEqual(tokens[-len(ending) - 1:-1], ending)
        self.assertIsNone(announcement_parts(announcement_text('', 129)))

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_prompt_install_checks_hash_reuses_offline_and_rejects_bad_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as sound:
                sound.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                sound.writeframes(array.array('h', [round(6000 * math.sin(i / 8000 * 440 * math.tau)) for i in range(2400)]).tobytes())
            data = buffer.getvalue()
            manifest = root / 'prompts.json'
            metadata = {'files': {'digits/8000/0.wav': hashlib.sha256(data).hexdigest()}, 'base_url': 'https://example.invalid/', 'repository': 'https://example.invalid', 'commit': 'synthetic'}
            manifest.write_text(json.dumps(metadata))
            with patch('instant_ai.model_mr_prompts._MANIFEST', manifest), patch('instant_ai.model_mr_prompts.urlopen', return_value=io.BytesIO(data)) as download:
                cached = ensure_recorded_prompts(root)
                self.assertEqual(download.call_count, 1)
                self.assertEqual((cached / 'digits/8000/0.wav').stat().st_mode & 0o777, 0o600)
                self.assertTrue((cached / 'LICENSE').is_file())
                self.assertTrue((cached / 'README.md').is_file())
                with patch('instant_ai.model_mr_prompts.urlopen', side_effect=AssertionError('must remain offline')):
                    self.assertEqual(ensure_recorded_prompts(root), cached)
            shutil.rmtree(root / 'audio-tools' / VERSION)
            metadata['files']['digits/8000/0.wav'] = '0' * 64
            manifest.write_text(json.dumps(metadata))
            with patch('instant_ai.model_mr_prompts._MANIFEST', manifest), patch('instant_ai.model_mr_prompts.urlopen', return_value=io.BytesIO(data)), self.assertRaisesRegex(ValueError, 'hash'):
                ensure_recorded_prompts(root)
            self.assertEqual(list((root / 'audio-tools').glob('.recorded-*')), [])
            metadata['files'] = {'../escaped.wav': hashlib.sha256(data).hexdigest()}
            manifest.write_text(json.dumps(metadata))
            with patch('instant_ai.model_mr_prompts._MANIFEST', manifest), self.assertRaisesRegex(ValueError, 'name'):
                ensure_recorded_prompts(root)
            (root / 'audio-tools').rmdir()
            (root / 'audio-tools').symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'directory'):
                ensure_recorded_prompts(root)

    def test_composition_has_short_silences_no_clipping_and_no_repeated_synthesis(self):
        text = announcement_text('2026-11-14', 129)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for part in set(announcement_parts(text)):
                if not isinstance(part, str):
                    continue
                target = root / part
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(array.array('h', [8000] * 1600).tobytes())
            with patch('instant_ai.model_mr_prompts.ensure_recorded_prompts', return_value=root):
                first, second = root / 'first.wav', root / 'second.wav'
                self.assertTrue(render_recorded_announcement(text, first, root))
                self.assertTrue(render_recorded_announcement(text, second, root))
                self.assertEqual(first.read_bytes(), second.read_bytes())
                with patch('instant_ai.model_mr_speech.ensure_speech_engine', side_effect=AssertionError('fixed dates must not use TTS')):
                    synthesize_announcement(text, root / 'through-public-renderer.wav', root)
                    self.assertEqual(first.read_bytes(), (root / 'through-public-renderer.wav').read_bytes())
                self.assertFalse(render_recorded_announcement('发布日期未知，第129条。', root / 'unknown.wav', root))
                self.assertFalse((root / 'unknown.wav').exists())
            with wave.open(str(first)) as sound:
                self.assertEqual((sound.getnchannels(), sound.getframerate(), sound.getsampwidth()), (1, RATE, 2))
                samples = array.array('h', sound.readframes(sound.getnframes()))
                self.assertLess(sound.getnframes() / RATE, 20)
                self.assertEqual(max(samples), 8000)
                self.assertEqual(samples[:round(.12 * RATE)], array.array('h', [0] * round(.12 * RATE)))
