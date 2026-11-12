"""Loopback-only synthetic library for the audio queue browser integration.

Requires AUDIO_QUEUE_TEST_ENGINE to identify a verified Git-outside eSpeak
runtime. This fixture never reads production data or owner credentials.
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

        client = make_library(root, 2)
        queues = ModelMrAudioQueues()
        engine = Path(os.environ['AUDIO_QUEUE_TEST_ENGINE']).resolve(strict=True)
        with patch('instant_ai.server.MODEL_MR', client), patch('instant_ai.server.AUTH', OwnerAuth(required=False, path=root/'auth.json')), patch(
            'instant_ai.server.MODEL_MR_AUDIO_QUEUES', queues,
        ), patch('instant_ai.model_mr_speech.ensure_speech_engine', return_value=engine):
            server = ThreadingHTTPServer(('127.0.0.1', 0), InstantAIHandler)
            print(json.dumps({'origin': f'http://127.0.0.1:{server.server_port}'}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
