from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mx_agent.downloader_engine import douyin_core as core


class VideoDownloadRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.result = core.ParseResult(
            share_url='https://www.douyin.com/video/7000000000000000001',
            final_page_url='https://www.douyin.com/video/7000000000000000001',
            video_id='7000000000000000001', title='测试视频', video_url='https://media.invalid/video?token=private',
            content_length=4096, referer='https://www.douyin.com/', user_agent='fixture',
            cookies={'sessionid': 'must-not-persist'}, candidate_count=1, rejected_count=0,
        )
        response = SimpleNamespace(status_code=200, headers={'Content-Length':'4096'},
                                   iter_content=lambda **_kwargs: iter([b'v'*4096]))
        context = self.stack.enter_context(patch.object(core.requests, 'get'))
        context.return_value.__enter__.return_value = response
        self.request = context
        self.stack.enter_context(patch.object(core, 'inspect_mp4', return_value={'has_video': True, 'has_audio':True}))
        self.stack.enter_context(patch.object(core.shutil, 'which', return_value='/fake/ffmpeg'))
        self.stack.enter_context(patch.object(core, '_video_duration', return_value=1200))
        self.run = self.stack.enter_context(patch.object(core.subprocess, 'run'))

    def timeout(self, command, **kwargs):
        Path(command[-1]).write_bytes(b'incomplete')
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    def success(self, command, **kwargs):
        Path(command[-1]).write_bytes(b'valid output fixture'*128)
        return SimpleNamespace(returncode=0, stderr='')

    def receipt(self):
        return next(self.root.glob('*.source-cache.json'))

    def test_timeout_keeps_verified_sources_and_retry_uses_them_without_network(self):
        stages=[]
        self.run.side_effect=self.timeout
        with self.assertRaises(core.VideoConversionTimeout) as error:
            core.download_video(self.result,self.root,stage_callback=stages.append)
        self.assertGreater(error.exception.budget_seconds,1200)
        self.assertLessEqual(error.exception.budget_seconds,3600)
        self.assertEqual(stages,['download','transcode'])
        cache=self.receipt()
        self.assertEqual(len(list(self.root.glob('*.mp4.part'))),1)
        self.assertEqual(list(self.root.glob('*.mux.part.mp4')),[])
        encoded=cache.read_text()
        for private in ['must-not-persist','sessionid','private','media.invalid']:
            self.assertNotIn(private,encoded)
        self.result.video_url='https://media.invalid/refreshed?token=changed'
        self.run.side_effect=self.success
        stages.clear()
        output=core.download_video(self.result,self.root,stage_callback=stages.append)
        self.assertTrue(output.is_file())
        self.assertEqual(self.request.call_count,1)
        self.assertEqual(stages,['cached_source','transcode'])
        self.assertFalse(cache.exists())
        self.assertEqual(list(self.root.glob('*.mp4.part')),[])

    def test_modified_source_is_downloaded_again_before_conversion(self):
        self.run.side_effect=self.timeout
        with self.assertRaises(core.VideoConversionTimeout):core.download_video(self.result,self.root)
        part=next(self.root.glob('*.mp4.part'));part.write_bytes(b'wrong'*1024)
        self.run.side_effect=self.success
        core.download_video(self.result,self.root)
        self.assertEqual(self.request.call_count,2)

    def test_separate_audio_is_cached_and_reused_with_the_video(self):
        self.result.audio_url='https://media.invalid/audio?token=private-audio'
        def inspect(path):
            return {'has_video':True,'has_audio':str(path).endswith('.mux.part.mp4')}
        with patch.object(core,'inspect_mp4',side_effect=inspect):
            self.run.side_effect=self.timeout
            with self.assertRaises(core.VideoConversionTimeout):core.download_video(self.result,self.root)
            self.assertEqual(self.request.call_count,2)
            self.result.audio_url=''
            self.run.side_effect=self.success
            core.download_video(self.result,self.root)
        self.assertEqual(self.request.call_count,2)
        self.assertEqual(list(self.root.glob('*.audio.part.m4a')),[])

    def test_cache_cannot_be_reused_for_a_different_work_identity(self):
        self.run.side_effect=self.timeout
        with self.assertRaises(core.VideoConversionTimeout):core.download_video(self.result,self.root)
        receipt=self.receipt();data=json.loads(receipt.read_text());data['work_id']='7000000000000000002';receipt.write_text(json.dumps(data))
        self.run.side_effect=self.success
        core.download_video(self.result,self.root)
        self.assertEqual(self.request.call_count,2)

    def test_failed_conversion_keeps_sources_but_never_installs_bad_output(self):
        def fail(command,**kwargs):
            Path(command[-1]).write_bytes(b'bad')
            return SimpleNamespace(returncode=1,stderr='decoder failure')
        self.run.side_effect=fail
        with self.assertRaises(core.VideoConversionError):core.download_video(self.result,self.root)
        self.assertTrue(self.receipt().is_file())
        self.assertEqual(list(self.root.glob('*.mp4')),[])

    def test_network_failure_does_not_create_a_verified_source_cache(self):
        self.request.side_effect=OSError('network unavailable')
        with self.assertRaises(OSError):core.download_video(self.result,self.root)
        self.assertEqual(list(self.root.iterdir()),[])
        self.run.assert_not_called()

    def test_expired_cache_is_removed_without_deleting_final_videos(self):
        self.run.side_effect=self.timeout
        with self.assertRaises(core.VideoConversionTimeout):core.download_video(self.result,self.root)
        receipt=self.receipt();old=time.time()-core.SOURCE_CACHE_TTL-1;os.utime(receipt,(old,old))
        final=self.root/'unrelated.mp4';final.write_bytes(b'existing finished video')
        core._prune_source_cache(self.root)
        self.assertFalse(receipt.exists())
        self.assertEqual(list(self.root.glob('*.mp4.part')),[])
        self.assertTrue(final.is_file())

    def test_symlink_source_is_not_followed_or_overwritten(self):
        outside=self.root/'private.txt';outside.write_text('keep')
        part=self.root/f'测试视频_{self.result.video_id}.mp4.part';part.symlink_to(outside)
        with self.assertRaises(core.ParseError):core.download_video(self.result,self.root)
        self.assertEqual(outside.read_text(),'keep')
        self.request.assert_not_called()

    def test_duration_budget_is_bounded_and_unknown_duration_is_explicit(self):
        self.assertGreaterEqual(core.conversion_budget(1),600)
        self.assertGreater(core.conversion_budget(1200),1200)
        self.assertLessEqual(core.conversion_budget(86400),3600)
        for unknown in [None,math.nan,math.inf,0,-1]:
            self.assertEqual(core.conversion_budget(unknown),1800)
