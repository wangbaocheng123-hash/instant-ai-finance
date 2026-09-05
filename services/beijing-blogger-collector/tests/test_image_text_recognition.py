from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mx_agent.storage import Storage
from mx_agent.transcriber import VideoTranscriber


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp-image-ocr",
            output_text=json.dumps(
                {"text": "这是图片中的第一段文字。\n这是第二段文字。"},
                ensure_ascii=False,
            ),
        )


class ImageTextRecognitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.image_path = root / "sample.png"
        self.image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image")
        self.storage = Storage(root / "test.sqlite3")
        self.video_id, _ = self.storage.upsert_video(
            {
                "source": "manual",
                "source_video_id": "image-record-1",
                "author": "模型先生",
                "title": "图片作品",
                "description": "",
                "discovered_at": "2026-07-23T08:00:00+00:00",
            }
        )
        self.storage.save_asset(
            {
                "video_id": self.video_id,
                "asset_type": "image",
                "mime_type": "image/png",
                "local_path": str(self.image_path),
                "remote_url": "",
                "sha256": "test-image",
                "raw_json": {},
            },
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_local_ocr_returns_preview_without_saving(self):
        service = VideoTranscriber(self.storage)
        payload = {
            "text": "图片原文",
            "lines": [{"text": "图片原文"}],
        }
        with patch("mx_agent.transcriber.ocr_image_payload", return_value=payload):
            result = service.recognize_image_text(self.video_id)

        self.assertEqual(result["text"], "图片原文")
        self.assertFalse(result["saved"])
        self.assertTrue(result["save_required"])
        detail = self.storage.get_video_detail(self.video_id)
        self.assertIsNone(detail["notes"]["video_text"])
        self.assertEqual(detail["transcripts"], [])

    def test_ai_ocr_is_manual_preview_and_uses_image_input(self):
        responses = FakeResponses()
        client = SimpleNamespace(responses=responses)
        settings = SimpleNamespace(openai_api_key="test-key")
        service = VideoTranscriber(
            self.storage,
            settings,
            client_factory=lambda _key: client,
        )

        result = service.recognize_image_text_ai(self.video_id)

        self.assertEqual(result["engine"], "openai-vision")
        self.assertFalse(result["saved"])
        self.assertEqual(len(responses.calls), 1)
        request = responses.calls[0]
        self.assertEqual(request["model"], "gpt-5.6-luna")
        self.assertFalse(request["store"])
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        image_part = request["input"][0]["content"][1]
        self.assertEqual(image_part["type"], "input_image")
        self.assertTrue(image_part["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(image_part["detail"], "high")
        detail = self.storage.get_video_detail(self.video_id)
        self.assertIsNone(detail["notes"]["video_text"])
        self.assertEqual(detail["transcripts"], [])

    def test_ai_ocr_requires_api_key(self):
        service = VideoTranscriber(
            self.storage,
            SimpleNamespace(openai_api_key=None),
        )
        with self.assertRaisesRegex(RuntimeError, "尚未配置"):
            service.recognize_image_text_ai(self.video_id)


if __name__ == "__main__":
    unittest.main()
