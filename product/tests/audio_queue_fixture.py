"""Loopback-only synthetic library for the audio queue browser integration.

Requires AUDIO_QUEUE_TEST_PROMPTS to identify verified Git-outside recordings.
Normal dates must not call the fallback synthesizer. No production data or
owner credentials are read.
"""
import json
import os
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='instant-ai-queue-browser-') as directory:
        root = Path(directory)
        os.environ['INSTANT_AI_LIBRARY_ROOT'] = str(root)
        from instant_ai.auth import OwnerAuth
        from instant_ai.model_mr_audio_queue import ModelMrAudioQueues
        from instant_ai.server import InstantAIHandler
        from test_model_mr_audio_queue import make_library

        client = make_library(root, float(os.environ.get('AUDIO_QUEUE_TEST_SECONDS', '8')))
        queues = ModelMrAudioQueues()
        prompts = Path(os.environ['AUDIO_QUEUE_TEST_PROMPTS']).resolve(strict=True)
        with patch('instant_ai.server.MODEL_MR', client), patch('instant_ai.server.AUTH', OwnerAuth(required=False, path=root/'auth.json')), patch(
            'instant_ai.server.MODEL_MR_AUDIO_QUEUES', queues,
        ), patch('instant_ai.model_mr_speech.ensure_speech_engine', side_effect=AssertionError('recorded dates must not call TTS')), patch(
            'instant_ai.model_mr_prompts.ensure_recorded_prompts', return_value=prompts,
        ):
            server = ThreadingHTTPServer(('127.0.0.1', 0), InstantAIHandler)
            print(json.dumps({'origin': f'http://127.0.0.1:{server.server_port}'}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
