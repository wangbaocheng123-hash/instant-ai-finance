from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mx_agent.doubao import DoubaoFoundationService
from mx_agent.keyword_taxonomy import KEYWORD_CATEGORIES
from mx_agent.keywords import KeywordExtractionService
from mx_agent.storage import Storage


def settings(**overrides):
    values = {
        "doubao_api_enabled": True,
        "doubao_asr_api_key": "asr-key",
        "doubao_asr_app_id": None,
        "doubao_asr_access_key": None,
        "doubao_asr_resource_id": "volc.seedasr.auc",
        "doubao_ark_api_key": "ark-key",
        "doubao_ark_base_url": "https://ark.example/api/v3",
        "doubao_text_model": "doubao-text-endpoint",
        "doubao_vision_model": "doubao-vision-endpoint",
        "openai_api_key": None,
        "openai_api_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DoubaoFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.storage = Storage(self.root / "test.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_video(self, suffix: str = ".mp4") -> tuple[int, Path]:
        source = self.root / f"202607291600{suffix}"
        source.write_bytes(b"source-media")
        video_id, _ = self.storage.upsert_video(
            {
                "source": "test",
                "source_video_id": f"doubao-{suffix}",
                "author": "模型先生",
                "title": "测试作品",
                "description": "",
            }
        )
        mime_type = "image/png" if suffix == ".png" else "video/mp4"
        self.storage.save_asset(
            {
                "video_id": video_id,
                "asset_type": "image" if suffix == ".png" else "video",
                "storage_mode": "source_file",
                "original_name": source.name,
                "local_path": str(source),
                "mime_type": mime_type,
                "size_bytes": source.stat().st_size,
                "sha256": "test",
                "source": "test",
                "status": "stored",
                "raw_json": {"source_path": str(source)},
            }
        )
        return video_id, source

    def test_status_reports_three_independent_capabilities(self) -> None:
        service = DoubaoFoundationService(settings(), self.storage)

        result = service.status()

        self.assertTrue(result["configured"])
        self.assertTrue(result["enabled"])
        self.assertTrue(result["capabilities"]["speech"]["enabled"])
        self.assertTrue(result["capabilities"]["text"]["enabled"])
        self.assertTrue(result["capabilities"]["vision"]["enabled"])

    def test_asr_result_is_returned_as_an_unsaved_candidate(self) -> None:
        video_id, source = self.create_video()
        service = DoubaoFoundationService(settings(), self.storage)

        def fake_extract(_source, output):
            output.write_bytes(b"fake-wav")

        requests = []

        def fake_post(url, headers, payload, timeout):
            requests.append((url, dict(headers), payload, timeout))
            if url.endswith("/submit"):
                return ({"X-Api-Status-Code": "20000000"}, {})
            return (
                {"X-Api-Status-Code": "20000000"},
                {
                    "audio_info": {"duration": 1000},
                    "result": {
                        "text": "紫金矿业的市盈率需要结合资源价格判断。",
                        "utterances": [
                            {
                                "start_time": 0,
                                "end_time": 1000,
                                "text": "紫金矿业的市盈率需要结合资源价格判断。",
                            }
                        ],
                    },
                },
            )

        runtime = self.root / "runtime"
        with (
            patch("mx_agent.doubao.RUNTIME_DIR", runtime),
            patch.object(
                DoubaoFoundationService,
                "_extract_audio",
                side_effect=fake_extract,
            ),
            patch.object(
                DoubaoFoundationService,
                "_asr_post_json",
                side_effect=fake_post,
            ),
        ):
            result = service.transcribe_video_text(video_id)

        self.assertEqual(result["engine"], "doubao-recording-asr-2.0")
        self.assertFalse(result["saved"])
        self.assertTrue(result["save_required"])
        self.assertIsNone(self.storage.get_note(video_id, "video_text"))
        self.assertTrue(result["task_id"])
        self.assertTrue(source.exists())
        self.assertFalse(
            (runtime / "doubao-asr-2" / f"video_{video_id}").exists()
        )
        self.assertEqual(requests[0][1]["X-Api-Key"], "asr-key")
        self.assertEqual(
            requests[0][1]["X-Api-Resource-Id"],
            "volc.seedasr.auc",
        )
        self.assertEqual(
            requests[0][1]["X-Api-Request-Id"],
            result["task_id"],
        )
        self.assertEqual(requests[0][2]["audio"]["format"], "wav")
        self.assertEqual(requests[0][2]["audio"]["codec"], "raw")
        self.assertEqual(requests[0][2]["audio"]["rate"], 16000)
        self.assertEqual(requests[0][2]["audio"]["bits"], 16)
        self.assertEqual(requests[0][2]["audio"]["channel"], 1)
        self.assertTrue(requests[0][2]["audio"]["data"])
        request_options = requests[0][2]["request"]
        self.assertTrue(request_options["enable_itn"])
        self.assertTrue(request_options["enable_punc"])
        self.assertFalse(request_options["enable_ddc"])
        self.assertTrue(request_options["show_utterances"])
        context = json.loads(request_options["corpus"]["context"])
        self.assertEqual(context["context_type"], "dialog_ctx")
        self.assertIn(
            "金融",
            request_options["corpus"]["context"],
        )

    def test_cover_title_uses_doubao_vision_and_respects_title_storage(self) -> None:
        video_id, _ = self.create_video(".png")

        def transport(_url, _headers, _payload, _timeout):
            return (
                {},
                {
                    "id": "vision-response",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "title": "科技股分化刚刚开始",
                                        "confidence": 0.97,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ],
                },
            )

        service = DoubaoFoundationService(
            settings(),
            self.storage,
            transport=transport,
        )
        with patch("mx_agent.doubao.RUNTIME_DIR", self.root / "runtime"):
            result = service.recognize_cover_title(video_id, force=True)

        self.assertTrue(result["recognized"])
        self.assertEqual(result["ocr_title"], "科技股分化刚刚开始")
        self.assertEqual(
            self.storage.get_video_title(video_id)["active_title"],
            "科技股分化刚刚开始",
        )

    def test_text_extraction_uses_ark_responses_api(self) -> None:
        requests = []

        def transport(url, headers, payload, timeout):
            requests.append((url, dict(headers), payload, timeout))
            return (
                {},
                {
                    "id": "ark-response-1",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            **{category: [] for category in KEYWORD_CATEGORIES},
                                            "企业、个股与产业链": ["紫金矿业"],
                                            "基本面与估值": ["估值", "资源价格"],
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                        }
                    ],
                },
            )

        service = DoubaoFoundationService(
            settings(doubao_text_model="doubao-seed-2-1-turbo-260628"),
            self.storage,
            transport=transport,
        )
        result = service.extract_keywords(
            {
                "title_reference": "紫金矿业估值",
                "video_original": "紫金矿业当前估值需要结合资源价格判断。",
            }
        )

        self.assertEqual(
            requests[0][0],
            "https://ark.example/api/v3/responses",
        )
        self.assertEqual(
            requests[0][2]["model"],
            "doubao-seed-2-1-turbo-260628",
        )
        self.assertEqual(
            requests[0][2]["thinking"],
            {"type": "disabled"},
        )
        self.assertEqual(
            requests[0][2]["input"][1]["content"][0]["type"],
            "input_text",
        )
        self.assertEqual(
            result["payload"]["企业、个股与产业链"],
            ["紫金矿业"],
        )
        prompt = requests[0][2]["input"][0]["content"][0]["text"]
        self.assertNotIn("核心内容摘要", prompt)
        self.assertIn("不得概括观点", prompt)

    def test_keyword_service_prefers_enabled_doubao_text_model(self) -> None:
        video_id, _ = self.create_video()
        self.storage.save_content_original(
            video_id,
            "紫金矿业是有色资源和AI上游的重要公司。",
        )

        class FakeDoubao:
            @staticmethod
            def text_enabled():
                return True

            @staticmethod
            def extract_keywords(_material):
                return {
                    "model": "doubao:test-model",
                    "response_id": "response-1",
                    "payload": {
                        **{category: [] for category in KEYWORD_CATEGORIES},
                        "行业与板块": ["有色资源"],
                        "企业、个股与产业链": ["紫金矿业", "AI上游"],
                    },
                }

        service = KeywordExtractionService(
            settings(),
            self.storage,
            doubao_service=FakeDoubao(),
        )
        result = service.preview(video_id)

        self.assertEqual(result["model"], "doubao:test-model")
        self.assertEqual(
            result["keywords"],
            ["有色资源", "紫金矿业", "AI上游"],
        )
        self.assertNotIn("summary", result)


if __name__ == "__main__":
    unittest.main()
