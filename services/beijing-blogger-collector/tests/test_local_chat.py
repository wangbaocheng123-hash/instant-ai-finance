from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from mx_agent.chat import LocalChatService


class FakeKnowledgeReader:
    def __init__(self) -> None:
        self.searches: list[tuple[str, int]] = []

    def search(self, question: str, limit: int = 10):
        self.searches.append((question, limit))
        return {"count": 1, "items": [{"record_id": "video:7", "title": "科技反弹"}]}

    def get(self, record_id: str):
        return {"record_id": record_id, "video_text": "测试原文"}


class FakeMemoryStore:
    def search(self, **kwargs):
        return {"count": 0, "items": []}

    def get(self, reference: str):
        return {"reference": reference}


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(
                id="resp-tool",
                output_text="",
                output=[
                    {
                        "type": "function_call",
                        "name": "search_model_knowledge",
                        "call_id": "call-1",
                        "arguments": json.dumps({"question": "科技反弹", "limit": 5}),
                    }
                ],
            )
        return SimpleNamespace(
            id="resp-final",
            output_text="根据 video:7，模型先生讨论过科技反弹。",
            output=[],
        )


class LocalChatServiceTests(unittest.TestCase):
    def settings(self, key: str | None = "test-key"):
        return SimpleNamespace(openai_api_key=key, database_path=None)

    def test_one_key_can_select_different_allowed_models(self):
        responses = FakeResponses()
        client = SimpleNamespace(responses=responses)
        reader = FakeKnowledgeReader()
        service = LocalChatService(
            self.settings(),
            knowledge_reader=reader,
            memory_store=FakeMemoryStore(),
            client_factory=lambda _key: client,
        )

        result = service.chat(
            messages=[{"role": "user", "content": "模型先生怎么看科技反弹？"}],
            model="gpt-5.6-terra",
        )

        self.assertEqual(result.model, "gpt-5.6-terra")
        self.assertEqual(result.tools_used, ["search_model_knowledge"])
        self.assertIn("video:7", result.answer)
        self.assertEqual(reader.searches, [("科技反弹", 5)])
        self.assertEqual(responses.calls[0]["model"], "gpt-5.6-terra")
        self.assertFalse(responses.calls[0]["store"])

    def test_latest_question_keeps_original_time_intent_when_model_rewrites_query(self):
        responses = FakeResponses()
        client = SimpleNamespace(responses=responses)
        reader = FakeKnowledgeReader()
        service = LocalChatService(
            self.settings(),
            knowledge_reader=reader,
            memory_store=FakeMemoryStore(),
            client_factory=lambda _key: client,
        )
        question = "模型先生今天最新的视频和观点是什么？"

        result = service.chat(
            messages=[{"role": "user", "content": question}],
            model="gpt-5.6-terra",
        )

        self.assertEqual(reader.searches, [(question, 5)])
        self.assertEqual(result.tools_used, ["search_model_knowledge"])
        self.assertIn(question, responses.calls[0]["instructions"])

    def test_missing_api_key_returns_clear_error(self):
        service = LocalChatService(
            self.settings(None),
            knowledge_reader=FakeKnowledgeReader(),
            memory_store=FakeMemoryStore(),
        )
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            service.chat(messages=[{"role": "user", "content": "测试"}])

    def test_api_master_switch_blocks_chat_even_when_key_is_configured(self):
        settings = self.settings()
        settings.openai_api_enabled = False
        service = LocalChatService(
            settings,
            knowledge_reader=FakeKnowledgeReader(),
            memory_store=FakeMemoryStore(),
        )
        self.assertFalse(service.config()["enabled"])
        self.assertTrue(service.config()["configured"])
        with self.assertRaisesRegex(RuntimeError, "总开关当前已关闭"):
            service.chat(messages=[{"role": "user", "content": "测试"}])

    def test_rejects_unlisted_model(self):
        service = LocalChatService(
            self.settings(),
            knowledge_reader=FakeKnowledgeReader(),
            memory_store=FakeMemoryStore(),
        )
        with self.assertRaisesRegex(ValueError, "不支持的模型"):
            service.chat(
                messages=[{"role": "user", "content": "测试"}],
                model="unknown-model",
            )


if __name__ == "__main__":
    unittest.main()
