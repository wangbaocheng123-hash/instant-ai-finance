from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mx_agent.mainlines import (
    get_confirmed_mainline,
    InvestmentMainlineService,
    build_qualitative_chart,
    read_confirmed_mainlines,
    search_confirmed_mainlines,
)
from mx_agent.storage import Storage


class FakeResponses:
    def __init__(self, video_id: int) -> None:
        self.video_id = video_id
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp-mainline-1",
            output_text=json.dumps(
                {
                    "relevant": True,
                    "update_required": True,
                    "confidence": 0.94,
                    "reason": "新视频明确把科技行情判断从主跌调整为筑底观察。",
                    "status": "当前判断：主跌浪基本完成，进入筑底观察",
                    "as_of": "2026.07.24",
                    "summary": "高位科技股风险已大幅释放，重点观察科创指数筑底是否成立。",
                    "change_summary": "撤销旧版机械阴跌推演，改为有条件的筑底观察。",
                    "nodes": [
                        {
                            "date": "07月24日",
                            "title": "主跌浪基本完成",
                            "text": "风险已大幅释放，但筑底仍需观察。",
                            "state": "current",
                            "evidence": "高位的科技股，风险已经大幅释放，主跌浪已经基本完成。",
                            "source_video_ids": [self.video_id],
                        },
                        {
                            "date": "后续",
                            "title": "观察科创指数筑底",
                            "text": "把筑底作为观察条件，不写成确定结果。",
                            "state": "watch",
                            "evidence": "科创指数进入筑底。",
                            "source_video_ids": [self.video_id],
                        },
                    ],
                    "sources": [
                        {
                            "video_id": self.video_id,
                            "evidence": "主跌浪已经基本完成。",
                            "impact": "修正旧版行情阶段。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )


class MainlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp_dir.name) / "test.sqlite3")
        self.video_id, _ = self.storage.upsert_video(
            {
                "source": "manual",
                "source_video_id": "mainline-video-1",
                "author": "模型先生",
                "title": "长鑫科技上市影响几何?",
                "description": "",
                "published_at": "2026-07-24T15:24:00+08:00",
                "discovered_at": "2026-07-24T07:30:00+00:00",
            }
        )
        self.storage.save_note(
            self.video_id,
            "video_text",
            "高位的科技股，风险已经大幅释放，主跌浪已经基本完成。科创指数进入筑底。",
        )
        self.responses = FakeResponses(self.video_id)
        client = SimpleNamespace(responses=self.responses)
        settings = SimpleNamespace(
            openai_api_key="test-key",
            source_account_name="模型先生",
        )
        self.service = InvestmentMainlineService(
            settings,
            self.storage,
            client_factory=lambda _key: client,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_draft_requires_confirmation_and_preserves_version_history(self):
        initial = self.service.list_mainlines()
        self.assertEqual(initial["items"][0]["version"]["version_number"], 1)
        self.assertEqual(initial["pending_count"], 0)
        self.assertEqual(initial["update_mode"], "manual")

        result = self.service.analyze_video(self.video_id)

        self.assertFalse(result["cached"])
        self.assertEqual(result["draft"]["status"], "pending")
        self.assertEqual(len(self.responses.calls), 1)
        request = self.responses.calls[0]
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        self.assertEqual(request["text"]["format"]["type"], "json_schema")
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertFalse(request["store"])

        before_confirm = self.service.list_mainlines()
        self.assertEqual(before_confirm["pending_count"], 1)
        self.assertEqual(before_confirm["items"][0]["version"]["version_number"], 1)
        self.assertIn("收尾阶段", before_confirm["items"][0]["status"])

        confirmed = self.service.confirm_draft(result["draft"]["id"])

        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["version_number"], 2)
        after_confirm = self.service.list_mainlines()
        self.assertEqual(after_confirm["pending_count"], 0)
        self.assertIn("筑底观察", after_confirm["items"][0]["status"])
        with self.storage.connect() as conn:
            sources = conn.execute(
                "SELECT video_id, evidence FROM investment_mainline_sources"
            ).fetchall()
        self.assertEqual(len(sources), 1)
        self.assertEqual(int(sources[0]["video_id"]), self.video_id)

    def test_automatic_mainline_analysis_is_disabled(self):
        self.assertFalse(self.service.analyze_latest_async())
        self.assertFalse(self.service.analyze_video_async(self.video_id))
        self.assertEqual(len(self.responses.calls), 0)
        self.assertEqual(self.service.list_mainlines()["pending_count"], 0)

    def test_unchanged_video_is_not_analyzed_twice_after_confirmation(self):
        first = self.service.analyze_video(self.video_id)
        self.service.confirm_draft(first["draft"]["id"])

        second = self.service.analyze_video(self.video_id)

        self.assertTrue(second["cached"])
        self.assertEqual(second["draft"]["id"], first["draft"]["id"])
        self.assertEqual(len(self.responses.calls), 1)

    def test_external_ai_content_is_only_a_clue_and_duplicate_submission_is_cached(self):
        first = self.service.analyze_external_content(
            content="本地AI认为科技股已经进入筑底观察期，应调整K线推演。",
            source_label="模型先生本地AI",
            context="请结合最新视频判断科技行情阶段。",
        )

        self.assertFalse(first["cached"])
        self.assertEqual(first["draft"]["status"], "pending")
        self.assertEqual(
            first["draft"]["proposal"]["input_context"]["source_label"],
            "模型先生本地AI",
        )
        self.assertIn(
            "科技股已经进入筑底观察期",
            first["draft"]["proposal"]["input_context"]["content_excerpt"],
        )
        self.assertEqual(first["draft"]["source_video_id"], self.video_id)
        request_material = json.loads(self.responses.calls[0]["input"])
        self.assertEqual(
            request_material["external_suggestion"]["instruction"],
            "把这段内容仅作为待核验线索，不得当作模型先生原话。",
        )
        self.assertEqual(
            request_material["recent_model_mr_videos"][0]["video_id"],
            self.video_id,
        )
        self.assertEqual(
            self.service.list_mainlines()["items"][0]["version"]["version_number"],
            1,
        )

        second = self.service.analyze_external_content(
            content="本地AI认为科技股已经进入筑底观察期，应调整K线推演。",
            source_label="模型先生本地AI",
            context="请结合最新视频判断科技行情阶段。",
        )

        self.assertTrue(second["cached"])
        self.assertEqual(second["draft"]["id"], first["draft"]["id"])
        self.assertEqual(len(self.responses.calls), 1)

    def test_chart_is_derived_from_confirmed_nodes_and_does_not_invent_wave_labels(self):
        result = self.service.analyze_video(self.video_id)
        self.service.confirm_draft(result["draft"]["id"])

        line = self.service.list_mainlines()["items"][0]

        self.assertEqual(line["chart"]["segments"][0]["direction"], "down")
        self.assertEqual(line["chart"]["segments"][1]["direction"], "sideways")
        self.assertEqual(line["chart"]["segments"][0]["wave_label"], "")
        self.assertEqual(line["chart"]["segments"][0]["source_video_ids"], [self.video_id])

    def test_chart_keeps_abc_wave_only_when_present_in_evidence(self):
        chart = build_qualitative_chart(
            [
                {
                    "date": "下周",
                    "title": "反弹观察",
                    "text": "这里只讨论反弹。",
                    "state": "forecast",
                    "evidence": "模型先生原话明确称为B浪反弹。",
                    "source_video_ids": [7],
                }
            ]
        )

        self.assertEqual(chart["segments"][0]["direction"], "rebound")
        self.assertEqual(chart["segments"][0]["wave_label"], "B浪")

    def test_read_confirmed_mainlines_returns_only_current_read_only_view(self):
        result = self.service.analyze_video(self.video_id)
        self.service.confirm_draft(result["draft"]["id"])

        mainlines = read_confirmed_mainlines(self.storage.path)

        self.assertTrue(mainlines["read_only"])
        self.assertEqual(mainlines["scope"], "confirmed_mainlines_only")
        self.assertEqual(mainlines["update_mode"], "manual")
        self.assertEqual(mainlines["count"], 1)
        self.assertNotIn("pending_drafts", mainlines["items"][0])
        self.assertEqual(
            mainlines["items"][0]["version"]["version_number"],
            2,
        )
        self.assertEqual(
            mainlines["items"][0]["version"]["sources"][0]["video_id"],
            self.video_id,
        )

    def test_old_knowledge_tools_can_search_and_read_confirmed_mainline(self):
        result = self.service.analyze_video(self.video_id)
        self.service.confirm_draft(result["draft"]["id"])

        hits = search_confirmed_mainlines(
            self.storage.path,
            "请读取当前科技投资主线和K线推演",
        )
        record = get_confirmed_mainline(
            self.storage.path,
            hits[0]["record_id"],
        )

        self.assertEqual(hits[0]["record_id"], "mainline:technology")
        self.assertEqual(hits[0]["matched_in"], ["investment_mainline"])
        self.assertEqual(record["record_type"], "confirmed_investment_mainline")
        self.assertEqual(record["title"], "科技投资路线")
        self.assertTrue(record["read_only"])
        self.assertEqual(
            len(record["content_sections"]["timeline_nodes"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
