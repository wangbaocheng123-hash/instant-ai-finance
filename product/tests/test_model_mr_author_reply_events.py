from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from instant_ai.blogger_library import MODEL_MR_TRANSFER_CREATOR_ID
from instant_ai.blogger_mcp_protocol import handle_message, tool_definitions
from instant_ai.model_mr import ModelMrClient
from instant_ai.model_mr_mcp import ModelMrMcpLibrary, author_reply_ref
from instant_ai.model_mr_mcp_events import (
    AUTHOR_REPLIES_EVENT, EVENT_NAME, MAX_REPLIES_PER_EVENT, ModelMrMcpEvents,
)
from instant_ai.model_mr_transfer import ModelMrTransferProjector


class AuthorReplyEventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.media = self.root / "incoming.mp4"
        self.media.write_bytes(b"synthetic-video-no-network")
        self.snapshot = self.root / "public-snapshot.json"
        self.client = ModelMrClient("http://127.0.0.1:9", self.snapshot, self.root / "media")
        self.library = ModelMrMcpLibrary(self.snapshot)
        self.now = 1_800_000_000.0
        self.sent = []
        self.callback = "https://events.example.test/callback/owner"
        self.secret = "whsec_" + base64.b64encode(b"s" * 32).decode()
        self.manager = self.new_manager()
        self.fan = {
            "text": "原提问：判断在什么条件下成立？", "author": "PRIVATE_FAN_NAME",
            "kind": "user_comment", "source_comment_id": "PRIVATE_ROOT_ID",
            "reply_depth": 0, "published_at": "2026-10-01T10:00:00+08:00",
        }
        self.old = self.reply("旧有判断", minute=1)
        self.new = self.reply("新增判断仍需要验证", minute=2)

    def new_manager(self):
        def sender(url, body, headers):
            payload = json.loads(body)
            if payload.get("type") == "verification":
                return 200, json.dumps({"challenge": payload["challenge"]}).encode()
            self.sent.append((payload, dict(headers)))
            return 204, b""

        return ModelMrMcpEvents(
            self.root / "events.sqlite3", self.library,
            resolver=lambda host, port: ["8.8.8.8"], sender=sender, clock=lambda: self.now,
        )

    def reply(self, text, *, minute=2, **kwargs):
        return {
            "text": text, "author": "模型先生", "kind": "author_reply",
            "published_at": f"2026-10-01T10:{minute:02}:00+08:00",
            "source_comment_id": f"PRIVATE_REPLY_ID_{minute}",
            "root_source_comment_id": "PRIVATE_ROOT_ID", "reply_depth": 1,
            **kwargs,
        }

    def subscribe(self, name=AUTHOR_REPLIES_EVENT):
        return self.manager.subscribe("owner", {
            "name": name, "arguments": {}, "_meta": {},
            "delivery": {"mode": "webhook", "url": self.callback, "secret": self.secret},
        })

    def project(self, comments, *, revision=1, observe=True, observer=None):
        return self.client.import_beijing_work(
            source_work_id="778901", source_revision=revision,
            title="合成视频判断", description="合成测试", source_url="https://www.douyin.com/video/778901",
            published_at="2026-10-01T09:00:00+08:00", comments=comments,
            media_path=self.media, media_sha256=hashlib.sha256(self.media.read_bytes()).hexdigest(),
            comments_observer=(observer or self.manager.observe_author_replies) if observe else None,
        )

    def event_rows(self):
        with self.manager.db() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY occurred_at,id")]

    def call_replies(self, **arguments):
        return handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "get_model_mr_author_replies",
                "arguments": {"record_id": "model-mr-work:1000000", **arguments},
            }},
            library=Mock(), model_mr_library=self.library, version="test", authenticated=True,
            principal="owner", events=self.manager,
        )

    def test_existing_history_is_baseline_and_only_source_marked_new_reply_is_delivered(self):
        self.project([self.fan, self.old], observe=False)
        original_subscription = self.subscribe(EVENT_NAME)
        author_subscription = self.subscribe()
        self.assertNotEqual(original_subscription["id"], author_subscription["id"])
        fake_author = self.reply("同名网友不是作者", kind="user_comment", author_liked=True)
        self.project([self.fan, self.old, fake_author, self.new], revision=2)
        events = self.event_rows()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], AUTHOR_REPLIES_EVENT)
        payload = json.loads(events[0]["data"])
        self.assertEqual(payload, {
            "record_id": "model-mr-work:1000000", "reply_refs": [author_reply_ref(self.new)], "reply_count": 1,
        })
        self.assertTrue(self.manager.dispatch_one())
        self.assertFalse(self.manager.dispatch_one())
        self.assertEqual(self.sent[0][0]["data"], payload)
        self.assertIn("webhook-signature", self.sent[0][1])
        with self.manager.db() as connection:
            rows = connection.execute("SELECT subscription_id FROM deliveries").fetchall()
            self.assertEqual([row[0] for row in rows], [author_subscription["id"]])
            persisted = "\n".join(connection.iterdump())
        for forbidden in (self.new["text"], self.fan["text"], "PRIVATE_FAN_NAME", "PRIVATE_REPLY_ID"):
            self.assertNotIn(forbidden, persisted)

    def test_likes_order_names_threads_restart_disappear_and_reappear_do_not_repeat(self):
        self.subscribe()
        self.project([self.fan, self.new])
        changed_counts = {**self.new, "like_count": 90, "author": "改名",
                          "root_source_comment_id": "ANOTHER_THREAD"}
        self.project([changed_counts, self.fan], revision=2)
        self.project([self.fan], revision=3)
        self.manager = self.new_manager()
        self.project([self.fan, changed_counts], revision=4)
        self.project([self.fan, changed_counts], revision=4)
        self.assertEqual(len(self.event_rows()), 1)
        self.assertTrue(self.manager.dispatch_one())
        self.assertFalse(self.manager.dispatch_one())

    def test_new_text_or_publication_time_is_new_but_same_content_same_time_is_consumed_once(self):
        self.project([self.new, {**self.new, "source_comment_id": "DIFFERENT_SOURCE_ID"}])
        self.assertEqual(json.loads(self.event_rows()[0]["data"])["reply_count"], 1)
        exact = self.call_replies(reply_refs=[author_reply_ref(self.new)])["result"]["structuredContent"]
        self.assertEqual(sum(len(item["author_messages"]) for item in exact["items"]), 1)
        self.project([self.reply("正文调整", minute=2)], revision=2)
        self.project([self.reply("正文调整", minute=3)], revision=3)
        self.assertEqual(len(self.event_rows()), 3)

    def test_historical_subscribe_does_not_replay_and_event_names_unsubscribe_independently(self):
        self.project([self.old])
        self.subscribe()
        self.subscribe(EVENT_NAME)
        self.assertFalse(self.manager.dispatch_one())
        self.manager.unsubscribe("owner", {
            "name": AUTHOR_REPLIES_EVENT, "arguments": {},
            "delivery": {"mode": "webhook", "url": self.callback},
        })
        self.project([self.old, self.new], revision=2)
        self.client.save_video_text(1000000, "合成视频原文")
        self.manager.record_original_ready(1000000, "合成视频原文")
        self.assertTrue(self.manager.dispatch_one())
        self.assertEqual(self.sent[0][0]["name"], EVENT_NAME)
        self.assertFalse(self.manager.dispatch_one())

    def test_stale_revision_cannot_trigger_and_missing_original_does_not_start_processing(self):
        with patch("instant_ai.model_mr_processing.ModelMrProcessor.enqueue_arrival") as enqueue:
            self.project([self.fan], revision=3)
            stale = self.project([self.new], revision=2)
            self.assertEqual(stale["status"], "stale")
            self.project([self.fan, self.new], revision=4)
            self.assertEqual(len(self.event_rows()), 1)
            detail = self.library.get_work_for_mcp("model-mr-work:1000000")
            self.assertFalse(detail["video_original"]["text"])
            enqueue.assert_not_called()

    def test_failed_saved_handoff_rolls_back_seen_then_same_revision_recovers_once(self):
        self.subscribe()
        self.project([self.fan, self.old], observe=False)
        record = self.manager._record_event

        def fail_after_insert(*args):
            record(*args)
            raise RuntimeError("synthetic transaction failure")

        with patch.object(self.manager, "_record_event", side_effect=fail_after_insert):
            with self.assertRaises(RuntimeError):
                self.project([self.fan, self.old, self.new], revision=2)
        self.assertEqual(self.event_rows(), [])
        with self.manager.db() as connection:
            refs = [row[0] for row in connection.execute("SELECT reply_ref FROM author_reply_seen")]
            self.assertEqual(refs, [author_reply_ref(self.old)])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)
        self.manager = self.new_manager()
        self.project([self.fan, self.old, self.new], revision=2)
        self.project([self.fan, self.old, self.new], revision=2)
        self.assertEqual(len(self.event_rows()), 1)
        self.assertTrue(self.manager.dispatch_one())
        self.assertFalse(self.manager.dispatch_one())

    def test_higher_revision_recovers_interrupted_saved_handoff_before_overwriting(self):
        self.project([self.old], observe=False)

        def interrupted(stage, work_id, comments):
            if stage == "saved":
                raise RuntimeError("synthetic crash")
            return self.manager.observe_author_replies(stage, work_id, comments)

        with self.assertRaises(RuntimeError):
            self.project([self.old, self.new], revision=2, observer=interrupted)
        self.project([self.old], revision=3)
        self.assertEqual(len(self.event_rows()), 1)
        result = self.library.get_author_replies_for_mcp(
            "model-mr-work:1000000", reply_refs=[author_reply_ref(self.new)]
        )
        self.assertEqual(result["items"], [])
        self.assertEqual(result["unavailable_reply_refs"], [author_reply_ref(self.new)])

    def test_completed_handoff_followed_by_retry_keeps_one_event(self):
        def interrupted(stage, work_id, comments):
            result = self.manager.observe_author_replies(stage, work_id, comments)
            if stage == "saved":
                raise RuntimeError("synthetic lost response")
            return result

        with self.assertRaises(RuntimeError):
            self.project([self.new], observer=interrupted)
        self.project([self.new])
        self.assertEqual(len(self.event_rows()), 1)

    def test_initial_baseline_failure_prevents_comment_replacement(self):
        self.project([self.old], observe=False)
        before = (self.client.details_root / "1000000.json").read_bytes()
        with self.assertRaises(RuntimeError):
            self.project([self.new], revision=2, observer=Mock(side_effect=RuntimeError("failure")))
        self.assertEqual((self.client.details_root / "1000000.json").read_bytes(), before)

    def test_large_batch_is_bounded_and_read_by_exact_refs_beyond_default_thirty(self):
        replies = [self.reply(f"合成判断 {index}") for index in range(205)]
        self.subscribe()
        self.project([self.fan, *replies])
        payloads = [json.loads(row["data"]) for row in self.event_rows()]
        self.assertEqual(sorted(payload["reply_count"] for payload in payloads), [5, 100, 100])
        self.assertTrue(all(len(p["reply_refs"]) <= MAX_REPLIES_PER_EVENT for p in payloads))
        self.assertEqual(len(set(ref for p in payloads for ref in p["reply_refs"])), 205)
        default = self.library.get_author_replies_for_mcp("model-mr-work:1000000")
        self.assertEqual(len(default["items"][0]["author_messages"]), 30)
        refs = [author_reply_ref(reply) for reply in replies[105:]]
        exact = self.call_replies(reply_refs=refs)["result"]["structuredContent"]
        self.assertEqual(exact["unavailable_reply_refs"], [])
        self.assertEqual({m["reply_ref"] for m in exact["items"][0]["author_messages"]}, set(refs))
        self.assertEqual(exact["items"][0]["question"]["text"], self.fan["text"])
        serialized = json.dumps(exact, ensure_ascii=False)
        for forbidden in ("PRIVATE_FAN_NAME", "PRIVATE_ROOT_ID", "PRIVATE_REPLY_ID", str(self.root)):
            self.assertNotIn(forbidden, serialized)

    def test_exact_refs_paginate_threads_without_reporting_unread_as_unavailable(self):
        one = self.reply("独立发言一", reply_depth=0, root_source_comment_id="", source_comment_id="one")
        two = self.reply("独立发言二", reply_depth=0, root_source_comment_id="", source_comment_id="two")
        self.project([one, two])
        refs = [author_reply_ref(one), author_reply_ref(two)]
        first = self.call_replies(reply_refs=refs, limit=1)["result"]["structuredContent"]
        self.assertEqual(first["unavailable_reply_refs"], [])
        self.assertEqual(first["next_offset"], 1)
        second = self.call_replies(reply_refs=refs, limit=1, offset=1)["result"]["structuredContent"]
        self.assertIsNone(second["next_offset"])
        self.assertNotEqual(first["items"][0]["author_messages"], second["items"][0]["author_messages"])

    def test_invalid_reply_refs_are_rejected_and_readonly_tool_count_is_unchanged(self):
        self.project([self.new])
        for invalid in (None, "bad", [], [{}], [False], ["reply-x"], ["reply-" + "a" * 64] * 101):
            with self.subTest(invalid=invalid):
                response = self.call_replies(reply_refs=invalid)
                self.assertTrue(response.get("error") or response.get("result", {}).get("isError"))
        with self.assertRaisesRegex(ValueError, "reply_refs_invalid"):
            self.library.get_author_replies_for_mcp("model-mr-work:1000000", reply_refs=[{}])
        definitions = tool_definitions()
        self.assertEqual(len(definitions), 9)
        definition = next(tool for tool in definitions if tool["name"] == "get_model_mr_author_replies")
        self.assertTrue(definition["annotations"]["readOnlyHint"])
        self.assertIn("reply_refs", definition["inputSchema"]["properties"])

    def test_event_discovery_and_unauthorized_subscription(self):
        definitions = self.manager.list_events({})["events"]
        self.assertEqual({item["name"] for item in definitions}, {EVENT_NAME, AUTHOR_REPLIES_EVENT})
        definition = next(item for item in definitions if item["name"] == AUTHOR_REPLIES_EVENT)
        self.assertEqual(set(definition["payloadSchema"]["properties"]), {"record_id", "reply_refs", "reply_count"})
        denied = handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "events/subscribe", "params": {
                "name": AUTHOR_REPLIES_EVENT, "arguments": {},
                "delivery": {"mode": "webhook", "url": self.callback, "secret": self.secret},
            }}, library=Mock(), model_mr_library=self.library, version="test", authenticated=False,
            events=self.manager,
        )
        self.assertEqual(denied["error"]["code"], -32001)
        self.assertFalse(self.manager.path.exists())

    def test_projector_passes_observer_only_through_existing_verified_arrival_path(self):
        model = Mock()
        model.import_beijing_work.return_value = {"status": "stale", "work_id": 1000000}
        observer = Mock()
        projector = ModelMrTransferProjector(
            blogger_root=self.root / "unused", model_mr=model, comments_observer=observer,
        )
        transfer = {
            "transport_status": "transport_completed",
            "manifest": {
                "creator": {"creator_id": MODEL_MR_TRANSFER_CREATOR_ID},
                "work": {"source_work_id": "778901", "revision": 2},
                "media": [{"media_id": "video", "role": "video", "mime_type": "video/mp4", "sha256": "a" * 64}],
                "comment_snapshot": {"bundle": {"bundle_id": "comments"}},
            },
            "artifacts": [{"artifact_id": "video"}, {"artifact_id": "comments"}],
        }
        store = Mock()
        store.get_transfer.return_value = transfer
        with (
            patch.object(projector, "_store", return_value=store),
            patch.object(projector, "_artifact_path", return_value=self.media),
            patch.object(projector, "_comments", return_value=[self.new]),
            patch("instant_ai.model_mr_processing.ModelMrProcessor.enqueue_arrival") as enqueue,
        ):
            projector.project("synthetic-transfer")
        self.assertIs(model.import_beijing_work.call_args.kwargs["comments_observer"], observer)
        enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
