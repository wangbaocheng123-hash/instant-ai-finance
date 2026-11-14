from __future__ import annotations

import hashlib
import http.client
import io
import json
import math
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import wave
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from instant_ai.auth import OwnerAuth, configure_owner
from instant_ai.model_mr import ModelMrClient, ModelMrUnavailable
from instant_ai.model_mr_audio import ModelMrAudioUnavailable
from instant_ai.model_mr_audio_queue import ModelMrAudioQueues
from instant_ai.model_mr_speech import (
    ModelMrSpeechUnavailable, announcement_text, chinese_number, ensure_speech_engine,
)
from instant_ai.server import InstantAIHandler


def make_library(root: Path, duration: float = 1) -> ModelMrClient:
    media = root / 'media'
    media.mkdir()
    works = []
    for work_id, day in [(10, 11), (30, 12), (400, 13)]:
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        f'sine=frequency={440 + day * 10}:duration={duration}', '-c:a', 'aac', '-b:a', '64k',
                        '-y', str(media / f'{work_id}.mp4')], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        works.append({'id': work_id, 'title': f'合成作品 {work_id}', 'published_at': f'2026-11-{day}T08:00:00+08:00',
                      'media_available': True, 'media_file': f'{work_id}.mp4'})
    works.insert(1, {'id': 200, 'title': '合成图文', 'published_at': '2026-11-11T12:00:00+08:00', 'work_type': 'image'})
    snapshot = root / 'public-snapshot.json'
    snapshot.write_text(json.dumps({'version': 3, 'works': list(reversed(works)), 'thoughts': []}))
    return ModelMrClient('http://127.0.0.1:9', snapshot, media)


def synthetic_speech(text: str, output: Path, root: Path) -> None:
    with wave.open(str(output), 'wb') as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(8000)
        sound.writeframes(b''.join(struct.pack('<h', round(math.sin(index / 8000 * 440 * math.tau) * 2000)) for index in range(1600)))


class SpokenAnnouncementTests(unittest.TestCase):
    def test_chinese_dates_sequence_and_beijing_timezone_are_explicit(self):
        self.assertEqual(announcement_text('2026-11-12T18:00:00Z', 101), '二零二六年十一月十三日，第一百零一条。')
        self.assertEqual(announcement_text('2025-04-21T08:02:00', 1), '二零二五年四月二十一日，第一条。')
        self.assertEqual(announcement_text('', 11), '发布日期未知，第十一条。')
        self.assertEqual([chinese_number(n) for n in [0, 10, 11, 1001, 10001, 500000]], ['零', '十', '十一', '一千零一', '一万零一', '五十万'])
        for invalid in [-1, True, 500001]:
            with self.assertRaises(ValueError):
                chinese_number(invalid)

    def test_pinned_runtime_install_is_private_checked_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('espeakng_loader/libespeak-ng.so', b'synthetic library, never loaded')
                archive.writestr('espeakng_loader/espeak-ng-data/voice', b'synthetic voice')
            content = stream.getvalue()
            with patch('instant_ai.model_mr_speech.ENGINE_SHA256', hashlib.sha256(content).hexdigest()), patch(
                'instant_ai.model_mr_speech.urlopen', return_value=io.BytesIO(content),
            ) as download:
                first = ensure_speech_engine(root)
                second = ensure_speech_engine(root)
                self.assertEqual(first, second)
                self.assertEqual(download.call_count, 1)
                self.assertEqual((first / 'libespeak-ng.so').stat().st_mode & 0o777, 0o600)
            with patch('instant_ai.model_mr_speech.ENGINE_SHA256', '0' * 64), patch(
                'instant_ai.model_mr_speech.urlopen', return_value=io.BytesIO(content),
            ), self.assertRaises(ModelMrSpeechUnavailable):
                ensure_speech_engine(root)
            self.assertEqual(list((root / 'audio-tools').glob('.speech-*')), [])

    def test_runtime_rejects_symlink_directory_and_zip_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elsewhere = root / 'elsewhere'
            elsewhere.mkdir()
            (root / 'audio-tools').symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaises(ModelMrSpeechUnavailable):
                ensure_speech_engine(root)
            (root / 'audio-tools').unlink()
            content = io.BytesIO()
            with zipfile.ZipFile(content, 'w') as archive:
                archive.writestr('../escaped', b'invalid')
            data = content.getvalue()
            with patch('instant_ai.model_mr_speech.ENGINE_SHA256', hashlib.sha256(data).hexdigest()), patch(
                'instant_ai.model_mr_speech.urlopen', return_value=io.BytesIO(data),
            ), self.assertRaises(ModelMrSpeechUnavailable):
                ensure_speech_engine(root)
            self.assertFalse((root / 'escaped').exists())


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg unavailable')
class AudioQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = make_library(self.root)
        self.queues = ModelMrAudioQueues()
        self.token = 'a' * 32
        speech = patch('instant_ai.model_mr_audio_queue.synthesize_announcement', side_effect=synthetic_speech)
        self.speech = speech.start()
        self.addCleanup(speech.stop)

    def test_queue_is_ascending_skips_images_and_uses_global_numbers(self):
        before = self.client.snapshot_path.read_bytes()
        with patch.object(ModelMrClient, 'work_detail', side_effect=AssertionError('metadata only')):
            queue = self.client.audio_queue(10)
        self.assertEqual([work['id'] for work in queue], [10, 30, 400])
        self.assertEqual([work['sequence_number'] for work in queue], [1, 3, 4])
        self.assertEqual(self.client.snapshot_path.read_bytes(), before)

    def test_event_playlist_appends_only_and_finishes_without_client_script(self):
        for codec, token in [('aac', self.token), ('mp3', 'b' * 32)]:
            with self.subTest(codec=codec):
                first = self.queues.playlist(self.client, token, 10, 0, 0, codec)
                self.assertIn('#EXT-X-PLAYLIST-TYPE:EVENT', first)
                self.assertNotIn('#EXT-X-ENDLIST', first)
                previous = first
                while not self.queues._sessions[token].done:
                    session = self.queues._sessions[token]
                    self.assertEqual(self.queues.segment(token, len(session.segments) - 1).read_bytes()[0], 0x47)
                    updated = self.queues.playlist(self.client, token, 10, 0, 0, codec)
                    self.assertTrue(updated.startswith(previous))
                    previous = updated
                self.assertTrue(previous.endswith('#EXT-X-ENDLIST\n'))
                tracks = self.queues._sessions[token].tracks
                self.assertEqual([track['id'] for track in tracks], [10, 30, 400])
                self.assertEqual(self.queues.state(token, tracks[1]['start'] + .05)['current']['sequence_number'], 3)
                self.assertNotIn(str(self.root), json.dumps(self.queues.state(token, 0)))
        self.assertTrue(all('年' in call.args[0] and '条' in call.args[0] for call in self.speech.call_args_list))

    def test_timer_is_in_media_and_current_audio_offset_is_preserved(self):
        source = self.root / 'media' / '10.mp4'
        longer = self.root / 'long.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-stream_loop', '-1', '-i', str(source), '-t', '1000',
                        '-c:a', 'copy', '-y', str(longer)], check=True, timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        longer.replace(source)
        playlist = self.queues.playlist(self.client, self.token, 10, 15, 12.5, 'aac')
        session = self.queues._sessions[self.token]
        self.assertTrue(session.done)
        self.assertIn('#EXT-X-ENDLIST', playlist)
        self.assertAlmostEqual(session.elapsed, 900, delta=.1)
        self.assertEqual(session.tracks[0]['body_offset'], 12.5)
        self.assertEqual(len(session.tracks), 1)

    def test_voice_failure_does_not_silently_drop_announcement_or_advance(self):
        with patch('instant_ai.model_mr_audio_queue.synthesize_announcement', side_effect=ModelMrSpeechUnavailable('synthetic failure')):
            with self.assertRaises(ModelMrSpeechUnavailable):
                self.queues.playlist(self.client, self.token, 10, 0, 0, 'aac')
        self.assertEqual(self.queues._sessions[self.token].cursor, 0)
        self.assertEqual(self.queues._sessions[self.token].segments, [])
        self.assertEqual(list((self.root / 'audio-cache' / 'sequences').glob('.sequence-*')), [])

    def test_http_auth_head_range_private_metadata_and_parameter_validation(self):
        configure_owner('synthetic-owner', 'synthetic-password-not-a-secret', self.root / 'auth.json')
        auth = OwnerAuth(required=True, path=self.root / 'auth.json')
        server = ThreadingHTTPServer(('127.0.0.1', 0), InstantAIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        base = f'/media/model-mr/audio-queues/{self.token}'
        with patch('instant_ai.server.AUTH', auth), patch('instant_ai.server.MODEL_MR', self.client), patch(
            'instant_ai.server.MODEL_MR_AUDIO_QUEUES', self.queues,
        ):
            thread.start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                for method, path in [('GET', base + '/index.m3u8?start=10'), ('HEAD', base + '/index.m3u8?start=10'),
                                     ('GET', base + '/segments/0.ts'), ('GET', f'/api/model-mr/audio-queues/{self.token}/state')]:
                    connection.request(method, path)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 401)
                self.assertEqual(self.queues._sessions, {})
                cookie = auth.session_cookie(auth.create_session('synthetic-owner')[0], secure=False).split(';')[0]
                for method in ['GET', 'HEAD']:
                    connection.request(method, base + '/index.m3u8?start=10', headers={'Cookie': cookie})
                    response = connection.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader('Cache-Control'), 'private, no-store')
                    self.assertEqual(response.getheader('Vary'), 'Cookie')
                    self.assertEqual(bool(body), method == 'GET')
                    self.assertNotIn(str(self.root), body.decode())
                connection.request('GET', base + '/segments/0.ts', headers={'Cookie': cookie, 'Range': 'bytes=0-187'})
                response = connection.getresponse()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.getheader('Content-Type'), 'video/mp2t')
                self.assertEqual(len(response.read()), 188)
                connection.request('GET', f'/api/model-mr/audio-queues/{self.token}/state?position=0', headers={'Cookie': cookie})
                response = connection.getresponse()
                value = json.loads(response.read())
                self.assertEqual(value['current']['sequence_number'], 1)
                self.assertNotIn(str(self.root), json.dumps(value))
                for suffix in ['start=10&minutes=-1', 'start=10&offset=NaN', 'start=10&codec=bad', 'start=10&start=10']:
                    connection.request('GET', base + '/index.m3u8?' + suffix, headers={'Cookie': cookie})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_selected_work_is_never_replaced_by_a_later_work(self):
        with self.assertRaises(ModelMrUnavailable):
            self.client.audio_queue(200)
        (self.root / 'media' / '10.mp4').write_bytes(b'synthetic damaged audio')
        with self.assertRaises(ModelMrAudioUnavailable):
            self.queues.playlist(self.client, self.token, 10, 0, 0, 'aac', first_intro=False)
        self.assertEqual(self.queues._sessions[self.token].cursor, 0)
        self.speech.assert_not_called()

    def test_continuation_starts_with_current_original_and_announces_only_next(self):
        self.queues.playlist(self.client, self.token, 10, 0, .25, 'aac', first_intro=False)
        session = self.queues._sessions[self.token]
        self.assertEqual(session.tracks[0]['id'], 10)
        self.assertEqual(session.tracks[0]['intro_seconds'], 0)
        self.assertEqual(session.tracks[0]['body_offset'], .25)
        self.speech.assert_not_called()
        self.queues.segment(self.token, len(session.segments) - 1)
        self.queues.playlist(self.client, self.token, 10, 0, .25, 'aac', first_intro=False)
        self.assertEqual(session.tracks[1]['id'], 30)
        self.assertGreater(session.tracks[1]['intro_seconds'], 0)
        self.assertEqual(self.speech.call_count, 1)
        self.assertIn('第' + chinese_number(3) + '条', self.speech.call_args.args[0])

    def test_single_work_repeats_until_remaining_budget_without_reannouncing(self):
        args = (self.client, self.token, 30, 1, .25, 'mp3')
        settings = dict(continuous=False, first_intro=False, seconds=4.5)
        self.queues.playlist(*args, **settings)
        session = self.queues._sessions[self.token]
        while not session.done:
            self.queues.segment(self.token, len(session.segments) - 1)
            playlist = self.queues.playlist(*args, **settings)
        self.assertIn('#EXT-X-ENDLIST', playlist)
        self.assertAlmostEqual(session.elapsed, 4.5, delta=.15)
        self.assertTrue(all(track['id'] == 30 for track in session.tracks))
        self.assertTrue(all(track['intro_seconds'] == 0 for track in session.tracks))
        self.assertEqual(self.queues.state(self.token, 1)['remaining_works'], 0)
        self.speech.assert_not_called()
        with self.assertRaises(ValueError):
            self.queues.playlist(*args, continuous=False, first_intro=False, seconds=61)

    def test_remaining_timer_budget_is_shared_across_successive_works(self):
        args = (self.client, self.token, 10, 1, 0, 'aac')
        settings = dict(first_intro=False, seconds=2.2)
        self.queues.playlist(*args, **settings)
        session = self.queues._sessions[self.token]
        while not session.done:
            self.queues.segment(self.token, len(session.segments)-1)
            playlist = self.queues.playlist(*args, **settings)
        self.assertEqual([track['id'] for track in session.tracks], [10,30])
        self.assertAlmostEqual(session.elapsed, 2.2, delta=.1)
        self.assertIn('#EXT-X-ENDLIST', playlist)
        self.assertEqual(self.speech.call_count,1)
