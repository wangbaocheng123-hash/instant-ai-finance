from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from instant_ai.auth import OwnerAuth, configure_owner
from instant_ai.model_mr_audio import (
    AUDIO_CACHE_AGE, ModelMrAudioUnavailable, _encode, _probe, _prune,
    prepare_model_mr_audio,
)
from instant_ai.server import InstantAIHandler


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg/ffprobe unavailable')
class ModelMrAudioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.video = self.root / 'source.mp4'
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'color=c=black:s=32x32:r=5:d=1',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
            '-c:v', 'mpeg4', '-c:a', 'aac', '-b:a', '64k', '-shortest', str(self.video),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)

    def test_original_is_audio_only_faststart_and_reused_without_ffmpeg(self):
        before = self.video.read_bytes()
        audio = prepare_model_mr_audio(self.video, self.root)
        data = json.loads(subprocess.check_output([
            'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(audio),
        ], timeout=10))
        self.assertEqual([item['codec_type'] for item in data['streams']], ['audio'])
        self.assertEqual(data['streams'][0]['codec_name'], 'aac')
        self.assertAlmostEqual(float(data['streams'][0]['duration']), 1, delta=.1)
        contents = audio.read_bytes()
        self.assertLess(contents.index(b'moov'), contents.index(b'mdat'))
        self.assertEqual(audio.stat().st_mode & 0o777, 0o600)
        with patch('instant_ai.model_mr_audio._run', side_effect=AssertionError('cache miss')):
            self.assertEqual(prepare_model_mr_audio(self.video, self.root), audio)
        self.assertEqual(self.video.read_bytes(), before)

    def test_finite_repeats_have_stop_point_in_media_without_script(self):
        for minutes in [15, 30, 60, 90]:
            with self.subTest(minutes=minutes):
                audio = prepare_model_mr_audio(self.video, self.root, minutes)
                self.assertAlmostEqual(_probe(audio)['duration'], minutes * 60, delta=.1)
        self.assertLess(sum(item.stat().st_size for item in (self.root / 'audio-cache').glob('*.m4a')), 128 * 1024**2)

    def test_changed_source_gets_new_cache_and_parallel_requests_share_one_encode(self):
        original_encode = _encode
        with patch('instant_ai.model_mr_audio._encode', wraps=original_encode) as encode:
            with ThreadPoolExecutor(max_workers=3) as executor:
                paths = list(executor.map(lambda _: prepare_model_mr_audio(self.video, self.root), range(3)))
            self.assertEqual(len(set(paths)), 1)
            self.assertEqual(encode.call_count, 1)
        info = self.video.stat()
        os.utime(self.video, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
        self.assertNotEqual(prepare_model_mr_audio(self.video, self.root), paths[0])

    def test_non_aac_audio_is_converted_once_and_missing_track_does_not_leave_cache(self):
        wav = self.root / 'other.mkv'
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(self.video),
            '-map', '0:a:0', '-c:a', 'pcm_s16le', str(wav),
        ], check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        converted = prepare_model_mr_audio(wav, self.root)
        self.assertEqual(_probe(converted)['codec'], 'aac')
        silent = self.root / 'silent.mp4'
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(self.video),
            '-an', '-c:v', 'copy', str(silent),
        ], check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        before = list((self.root / 'audio-cache').iterdir())
        with self.assertRaisesRegex(ModelMrAudioUnavailable, '音轨'):
            prepare_model_mr_audio(silent, self.root)
        self.assertEqual(list((self.root / 'audio-cache').iterdir()), before)

    def test_failed_encoding_cleans_temporary_and_hides_private_path(self):
        with patch('instant_ai.model_mr_audio.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['ffmpeg'], stderr=b'/private/secret')):
            with self.assertRaises(ModelMrAudioUnavailable) as error:
                _encode(self.video, self.root / 'output.m4a', copy=True)
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(list(self.root.glob('.audio-*')), [])

    def test_prune_is_bounded_and_leaves_unknown_files_links_and_originals_alone(self):
        cache = self.root / 'audio-cache'
        cache.mkdir()
        stale = cache / ('a' * 64 + '.m4a')
        stale.write_bytes(b'old')
        os.utime(stale, (time.time() - AUDIO_CACHE_AGE - 60,) * 2)
        protected = cache / ('b' * 64 + '.m4a')
        protected.write_bytes(b'keep')
        unknown = cache / 'owner-note.txt'
        unknown.write_text('keep')
        link = cache / ('c' * 64 + '.m4a')
        link.symlink_to(self.video)
        before = self.video.read_bytes()
        _prune(cache, {protected})
        self.assertFalse(stale.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(link.is_symlink())
        with patch('instant_ai.model_mr_audio.AUDIO_CACHE_BYTES', 5):
            with self.assertRaisesRegex(ModelMrAudioUnavailable, '空间'):
                _prune(cache, {protected}, reserve=2)
        self.assertEqual(self.video.read_bytes(), before)

    def test_unsupported_timer_and_symlink_cache_are_rejected(self):
        for minutes in [-1, 1, 120, True, '15']:
            with self.assertRaises(ValueError):
                prepare_model_mr_audio(self.video, self.root, minutes)
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.root / 'audio-cache').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ModelMrAudioUnavailable, '目录'):
            prepare_model_mr_audio(self.video, self.root)
        self.assertEqual(list(elsewhere.iterdir()), [])


class ModelMrAudioHTTPTests(unittest.TestCase):
    def test_owner_gate_ranges_head_validation_and_no_media_paths_in_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / 'private.m4a'
            media.write_bytes(b'0123456789')
            model = Mock(snapshot_path=root / 'public-snapshot.json')
            model.video_path.return_value = (root / 'video.mp4', 'video/mp4')
            configure_owner('synthetic-owner', 'synthetic-password-not-a-secret', root / 'auth.json')
            auth = OwnerAuth(required=True, path=root / 'auth.json')
            server = ThreadingHTTPServer(('127.0.0.1', 0), InstantAIHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch('instant_ai.server.AUTH', auth), patch('instant_ai.server.MODEL_MR', model), patch('instant_ai.server.prepare_model_mr_audio', return_value=media) as prepare:
                thread.start()
                try:
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    for method in ['GET', 'HEAD']:
                        connection.request(method, '/media/model-mr/works/1/audio?minutes=15', headers={'Range': 'bytes=0-2'})
                        response = connection.getresponse()
                        response.read()
                        self.assertEqual(response.status, 401)
                    prepare.assert_not_called()
                    model.video_path.assert_not_called()
                    cookie = auth.session_cookie(auth.create_session('synthetic-owner')[0], secure=False).split(';')[0]
                    with self.subTest(access='authenticated owner'):
                        for method in ['GET', 'HEAD']:
                            connection.request(method, '/media/model-mr/works/1/audio?minutes=15', headers={'Range': 'bytes=1-3', 'Cookie': cookie})
                            response = connection.getresponse()
                            self.assertEqual(response.status, 206)
                            self.assertEqual(response.getheader('Content-Type'), 'audio/mp4')
                            self.assertEqual(response.getheader('Content-Range'), 'bytes 1-3/10')
                            self.assertEqual(response.getheader('Cache-Control'), 'private, max-age=3600')
                            self.assertEqual(response.getheader('Vary'), 'Cookie')
                            self.assertEqual(response.read(), b'123' if method == 'GET' else b'')
                        for suffix in ['minutes=-1', 'minutes=1', 'minutes=', 'minutes=15&minutes=30']:
                            connection.request('GET', '/media/model-mr/works/1/audio?' + suffix, headers={'Cookie': cookie})
                            response = connection.getresponse()
                            self.assertEqual(response.status, 400)
                            self.assertNotIn(str(root), response.read().decode())
                        model.video_path.return_value = None
                        connection.request('GET', '/media/model-mr/works/2/audio', headers={'Cookie': cookie})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 404)
                        response.read()
                        model.video_path.return_value = (root / 'video.mp4', 'video/mp4')
                        prepare.side_effect = ModelMrAudioUnavailable('音频暂不可用。')
                        connection.request('GET', '/media/model-mr/works/1/audio', headers={'Cookie': cookie})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 503)
                        response.read()
                        connection.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
