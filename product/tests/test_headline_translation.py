from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import unicodedata
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from instant_ai import headline_translation as adapter
from instant_ai.database import connect, initialize, transaction, utc_now
from instant_ai.translation import (TranslationProvider, active_provider, headline_provider,
                                    needs_translation, translate_items, translation_status)


class HeadlineTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "news.db"
        initialize(self.path)
        self.env = patch.dict(os.environ, {"INSTANT_AI_HEADLINE_ARK_API_KEY": "test-only-ark-key",
                                          "INSTANT_AI_DOUBAO_ARK_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def add_item(self, item_id=1, title="Gold rises as dollar weakens"):
        with transaction(self.path) as connection:
            connection.execute("INSERT INTO items(id,canonical_key,title,url,summary,first_seen_at,last_seen_at) "
                               "VALUES(?,?,?,?,?,?,?)", (item_id, str(item_id), title,
                               "https://example.test/private-url", "summary-must-not-be-sent", utc_now(), utc_now()))

    def response(self, **overrides):
        value = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "美元走弱，黄金上涨"}]}]}
        value.update(overrides)
        response = Mock()
        response.read.return_value = json.dumps(value).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_dedicated_responses_contract_sends_only_one_public_title(self):
        with patch.object(adapter, "build_opener") as opener:
            opener.return_value.open.return_value = self.response()
            value = adapter.translate_headline("Gold rises as dollar weakens")
        request = opener.return_value.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, adapter.ARK_URL)
        self.assertEqual(set(payload), {"model", "input"})
        self.assertEqual(payload["model"], "doubao-seed-translation-250915")
        self.assertEqual(payload["input"], [{"role": "user", "content": [{"type": "input_text",
                         "text": "Gold rises as dollar weakens",
                         "translation_options": {"source_language": "en", "target_language": "zh"}}]}])
        self.assertEqual(value, "美元走弱，黄金上涨")
        self.assertNotIn("test-only-ark-key", request.data.decode())
        opener.return_value.open.assert_called_once()

    def test_missing_configuration_does_not_call_a_model_or_fall_back_to_free_translation(self):
        self.add_item()
        with patch.dict(os.environ, {"INSTANT_AI_HEADLINE_ARK_API_KEY": "", "INSTANT_AI_DOUBAO_ARK_API_KEY": ""}), \
                patch.object(adapter, "build_opener") as opener:
            result = translate_items([1], path=self.path)
            status = translation_status(self.path)
        opener.assert_not_called()
        self.assertFalse(status["enabled"])
        self.assertEqual(status["provider"], adapter.MODEL)
        self.assertEqual(result["translated_count"], 0)
        self.assertEqual(status["used_characters_today"], 0)

    def test_same_title_across_items_and_reopening_reuses_the_saved_translation(self):
        self.add_item(1)
        self.add_item(2)
        provider = headline_provider()
        translated = Mock(return_value="美元走弱，黄金上涨")
        provider = TranslationProvider(provider.name, True, 50_000, translated, provider.label)
        first = translate_items([1], path=self.path, provider=provider)
        second = translate_items([2], path=self.path, provider=provider)
        with transaction(self.path) as connection:
            connection.execute("DELETE FROM items WHERE id=1")
        third = translate_items([2], path=self.path, provider=provider)
        translated.assert_called_once()
        self.assertEqual(first["translated_count"], 1)
        self.assertEqual(second["cached_count"], 1)
        self.assertEqual(third["cached_count"], 1)
        self.assertEqual(third["translations"]["2"], "美元走弱，黄金上涨")

    def test_changed_original_title_invalidates_its_translation(self):
        self.add_item()
        call = Mock(side_effect=["黄金上涨", "美元走强"])
        provider = TranslationProvider("fake", False, None, call)
        translate_items([1], path=self.path, provider=provider)
        with transaction(self.path) as connection:
            connection.execute("UPDATE items SET title='Dollar gains before data' WHERE id=1")
        result = translate_items([1], path=self.path, provider=provider)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["original_titles"]["1"], "Dollar gains before data")
        self.assertEqual(result["translations"]["1"], "美元走强")

    def test_error_or_incomplete_output_is_not_cached_and_does_not_leak_secrets(self):
        self.add_item()
        error = HTTPError(adapter.ARK_URL, 403, "test-only-ark-key", {}, io.BytesIO(b"private-body"))
        with patch.object(adapter, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            result = translate_items([1], path=self.path)
        encoded = json.dumps(result)
        self.assertNotIn("test-only-ark-key", encoded)
        self.assertNotIn("private-body", encoded)
        self.assertEqual(result["translated_count"], 0)
        self.assertEqual(result["status"]["cached_titles"], 0)
        opener.return_value.open.assert_called_once()
        for payload in [{"status": "incomplete"}, {"output": [{"type": "message", "content": None}]},
                        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "English only"}]}]}]:
            with self.subTest(payload=payload), patch.object(adapter, "build_opener") as opener:
                opener.return_value.open.return_value = self.response(**payload)
                with self.assertRaises(adapter.HeadlineTranslationUnavailable):
                    adapter.translate_headline("English market headline")

    def test_dedicated_provider_counts_full_title_and_honours_the_daily_limit(self):
        title = "Market headline " * 40
        self.add_item(title=title)
        call = Mock(return_value="市场标题")
        provider = TranslationProvider(adapter.MODEL, True, len(title), call, "豆包标题翻译")
        first = translate_items([1], path=self.path, provider=provider)
        self.assertEqual(first["status"]["used_characters_today"], len(title))
        self.add_item(2, "Different market title")
        second = translate_items([2], path=self.path, provider=provider)
        self.assertTrue(second["quota_exhausted"])
        call.assert_called_once_with(title)

    def test_summary_translation_keeps_its_previous_provider(self):
        self.assertEqual(headline_provider().name, adapter.MODEL)
        self.assertEqual(active_provider().name, "mymemory-anonymous")

    def test_headline_scripts_select_korean_including_hanja_and_decomposed_hangul(self):
        cases = [("코스피 상승, 반도체주 강세", "ko"), ("韓國 증시, AI 반도체株 강세", "ko"),
                 (unicodedata.normalize("NFD", "금값 상승"), "ko"),
                 ("Gold rises as dollar weakens", "en"), ("黄金价格上涨", None),
                 ("中国AI产业上涨", None), ("2026-11-23 12:30", None)]
        for text, language in cases:
            with self.subTest(text=text):
                self.assertEqual(adapter.source_language(text), language)
                self.assertEqual(needs_translation(text), language is not None)

    def test_korean_request_uses_ko_to_zh_and_keeps_the_full_original(self):
        for title in ["코스피 상승, 반도체주 강세", "韓國 증시, AI 반도체株 강세"]:
            with self.subTest(title=title), patch.object(adapter, "build_opener") as opener:
                opener.return_value.open.return_value = self.response()
                adapter.translate_headline(title)
                payload = json.loads(opener.return_value.open.call_args.args[0].data)
                content = payload["input"][0]["content"][0]
                self.assertEqual(content["text"], title)
                self.assertEqual(content["translation_options"], {"source_language": "ko", "target_language": "zh"})
                opener.return_value.open.assert_called_once()

    def test_korean_translation_is_cached_while_chinese_stays_unchanged(self):
        title = "삼성전자 반도체 매출 증가"
        self.add_item(1, title)
        self.add_item(2, "黄金价格上涨")
        call = Mock(return_value="三星电子半导体营收增长")
        provider = TranslationProvider(adapter.MODEL, True, 50_000, call, "豆包标题翻译")
        first = translate_items([1, 2], path=self.path, provider=provider)
        second = translate_items([1], path=self.path, provider=provider)
        call.assert_called_once_with(title)
        self.assertEqual(first["skipped_count"], 1)
        self.assertEqual(second["cached_count"], 1)
        self.assertEqual(second["original_titles"]["1"], title)
        with connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT title FROM items WHERE id=1").fetchone()[0], title)
            self.assertEqual(connection.execute("SELECT title FROM items WHERE id=2").fetchone()[0], "黄金价格上涨")

    def test_model_redirect_is_rejected_instead_of_forwarding_credentials(self):
        with self.assertRaises(adapter.HeadlineTranslationUnavailable):
            adapter._NoRedirect().redirect_request(None, None, 302, "move", {}, "https://untrusted.test")

    def test_old_client_cannot_launch_background_paid_batches(self):
        from instant_ai import server
        def handler(payload, authenticated=True):
            instance = server.InstantAIHandler.__new__(server.InstantAIHandler)
            instance.path = "/api/translate"
            body = json.dumps(payload).encode()
            instance.headers = {"X-Instant-AI": "1", "Content-Length": str(len(body))}
            instance.rfile = io.BytesIO(body)
            instance._json = Mock()
            instance._host_allowed = lambda: True
            instance._require_auth = lambda: authenticated
            for name in ["_handle_blogger_transfer", "_handle_oauth_post", "_handle_mcp", "_handle_blogger_mcp"]:
                setattr(instance, name, lambda *args: False)
            return instance
        with patch.object(server, "translate_items", return_value={}) as call:
            old = handler({"item_ids": [1, 2], "max_new": 12})
            old.do_POST()
            self.assertEqual(old._json.call_args.args[1], 400)
            call.assert_not_called()
            denied = handler({"item_ids": [1], "visible_only": True}, authenticated=False)
            denied.do_POST()
            call.assert_not_called()
            current = handler({"item_ids": [1, 2], "max_new": 12, "visible_only": True})
            current.do_POST()
            call.assert_called_once_with([1], max_new=1)


if __name__ == "__main__":
    unittest.main()
