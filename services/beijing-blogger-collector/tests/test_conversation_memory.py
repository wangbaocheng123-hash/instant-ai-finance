from __future__ import annotations

import gc
import shutil
import tempfile
import unittest
from pathlib import Path

from mx_agent.conversation_memory import ConversationMemoryStore


class ConversationMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mx-memory-test-"))
        self.database_path = self.temp_dir / "memory.sqlite3"
        self.store = ConversationMemoryStore(self.database_path)

    def tearDown(self) -> None:
        self.store = None
        gc.collect()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _save_sample(
        self,
        *,
        started_at: str = "2026-07-19T09:00:00+08:00",
        ended_at: str = "2026-07-19T10:00:00+08:00",
        memory_key: str = "zijin-long-term-valuation",
    ) -> dict:
        return self.store.save(
            title="紫金矿业的长线估值讨论",
            chat_started_at=started_at,
            chat_ended_at=ended_at,
            chat_timezone="Asia/Shanghai",
            chat_session_id="chat-thread-001",
            discussion_topic="有色资源价格与长期估值",
            core_conclusions=["长期逻辑与短线交易需要分开判断"],
            related_record_ids=["video:27"],
            securities=["紫金矿业"],
            industries=["有色资源"],
            keywords=["市盈率", "AI上游", "短线交易"],
            model_mr_view="长线暂时想不到卖出的理由，短线交易不会选择它。",
            user_view="重点关注资源价格上涨对未来利润的影响。",
            gpt_analysis="当前估值依赖利润假设，资源价格回落时需要重新检查。",
            unresolved_questions=["资源价格上涨能持续多久"],
            verification_items=["铜价", "金价", "未来两年盈利预测"],
            source_chat_reference="ChatGPT项目：模型先生智能体",
            memory_key=memory_key,
        )

    def test_save_keeps_three_attributions_separate_and_is_idempotent(self) -> None:
        first = self._save_sample()
        second = self._save_sample()

        self.assertTrue(first["saved"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["memory"]["id"], second["memory"]["id"])
        memory = first["memory"]
        self.assertIn("卖出的理由", memory["model_mr_view"])
        self.assertIn("资源价格上涨", memory["user_view"])
        self.assertIn("利润假设", memory["gpt_analysis"])
        self.assertEqual(first["chat_batch"]["chat_started_at_local"][:16], "2026-07-19T09:00")
        self.assertIn("核心结论", first["refined_memory"]["summary"])

    def test_search_finds_memory_from_a_natural_chinese_question(self) -> None:
        saved = self._save_sample()
        result = self.store.search("之前我们怎么分析紫金矿业的？")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["retrieval_layer"], "refined_conversation_memories")
        self.assertEqual(result["items"][0]["reference"], f"memory:{saved['memory']['id']}")
        self.assertIn("紫金矿业", result["items"][0]["securities"])
        self.assertIn("GPT分析", result["items"][0]["summary"])

    def test_update_preserves_version_history(self) -> None:
        saved = self._save_sample()
        reference = f"memory:{saved['memory']['id']}"
        updated = self.store.update(
            reference,
            user_view="继续跟踪，但不把短期价格上涨直接等同于长期确定性。",
            verification_items=["铜价", "金价", "公司产量", "盈利预测"],
            change_note="补充用户风险判断",
        )

        self.assertTrue(updated["updated"])
        self.assertEqual(updated["memory"]["version"], 2)
        complete = self.store.get(reference)
        self.assertTrue(complete["found"])
        self.assertEqual(len(complete["memory"]["version_history"]), 2)
        self.assertEqual(complete["memory"]["version_history"][0]["change_note"], "补充用户风险判断")
        self.assertEqual(complete["refined_memory"]["memory_version"], 2)
        self.assertIn("不把短期价格上涨", complete["refined_memory"]["user_view"])

    def test_overlapping_chat_interval_is_rejected(self) -> None:
        self._save_sample()
        with self.assertRaisesRegex(ValueError, "时间段与已保存批次重叠"):
            self._save_sample(
                started_at="2026-07-19T09:30:00+08:00",
                ended_at="2026-07-19T10:30:00+08:00",
                memory_key="overlapping-memory",
            )

    def test_adjacent_chat_interval_is_allowed_even_with_same_summary(self) -> None:
        first = self._save_sample()
        second = self._save_sample(
            started_at="2026-07-19T10:00:00+08:00",
            ended_at="2026-07-19T11:00:00+08:00",
            memory_key="next-chat-same-topic",
        )

        self.assertFalse(second["duplicate"])
        self.assertNotEqual(first["memory"]["id"], second["memory"]["id"])
        checkpoint = self.store.search(
            "",
            limit=1,
            source_chat_reference="ChatGPT项目：模型先生智能体",
            chat_session_id="chat-thread-001",
        )["latest_saved_batch"]
        self.assertEqual(checkpoint["chat_ended_at_local"][:16], "2026-07-19T11:00")

    def test_refined_fts_finds_old_memory_beyond_one_thousand_newer_rows(self) -> None:
        saved = self._save_sample()
        with self.store.connect() as conn:
            conn.execute(
                """
                WITH RECURSIVE sequence(number) AS (
                    SELECT 1
                    UNION ALL
                    SELECT number + 1 FROM sequence WHERE number < 1100
                )
                INSERT INTO conversation_memories (
                    memory_key, title, discussion_topic, core_conclusions_json,
                    related_record_ids_json, securities_json, industries_json, keywords_json,
                    model_mr_view, user_view, gpt_analysis, unresolved_questions_json,
                    verification_items_json, source_chat_reference, source, status, version,
                    content_hash, search_text, metadata_json, created_at, updated_at
                )
                SELECT
                    'noise-memory-' || number, '无关记忆 ' || number, '其他主题', '["无关"]',
                    '[]', '[]', '[]', '["其他"]', '', '', '', '[]', '[]',
                    'bulk-test', 'mcp_chat', 'active', 1,
                    printf('%064x', number), '无关 其他', '{}',
                    '2026-07-20T00:00:00+00:00', '2026-07-20T00:00:00+00:00'
                FROM sequence
                """
            )
            conn.execute(
                """
                INSERT INTO refined_conversation_memories (
                    memory_id, batch_id, title, discussion_topic, summary,
                    core_conclusions_json, related_record_ids_json, securities_json,
                    industries_json, keywords_json, model_mr_view, user_view, gpt_analysis,
                    unresolved_questions_json, verification_items_json, status,
                    memory_version, search_text, created_at, updated_at
                )
                SELECT
                    id, NULL, title, discussion_topic, '无关摘要',
                    core_conclusions_json, related_record_ids_json, securities_json,
                    industries_json, keywords_json, '', '', '', '[]', '[]', status,
                    version, search_text, created_at, updated_at
                FROM conversation_memories
                WHERE memory_key LIKE 'noise-memory-%'
                """
            )
            fts_count = conn.execute(
                "SELECT COUNT(*) FROM refined_conversation_memories_fts"
            ).fetchone()[0]

        result = self.store.search("紫金矿业")
        self.assertGreater(fts_count, 1000)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["reference"], f"memory:{saved['memory']['id']}")

    def test_memory_writer_does_not_create_or_modify_source_knowledge_tables(self) -> None:
        self._save_sample()
        with self.store.connect() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }

        self.assertIn("conversation_memories", tables)
        self.assertIn("conversation_memory_versions", tables)
        self.assertIn("conversation_memory_batches", tables)
        self.assertIn("refined_conversation_memories", tables)
        self.assertIn("refined_conversation_memories_fts", tables)
        self.assertNotIn("videos", tables)
        self.assertNotIn("comments", tables)


if __name__ == "__main__":
    unittest.main()
