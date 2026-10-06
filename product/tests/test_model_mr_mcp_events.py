from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instant_ai.blogger_mcp_protocol import handle_message
from instant_ai.model_mr_mcp_events import (
    EVENT_NAME,
    MAX_DIAGNOSTIC_EVENTS,
    ModelMrMcpEvents,
)


SECRET = "whsec_" + base64.b64encode(b"s" * 32).decode("ascii")
SECOND_SECRET = "whsec_" + base64.b64encode(b"n" * 32).decode("ascii")
CALLBACK = "https://events.example.test/callback/owner"


class FakeModelMrLibrary:
    def __init__(self, root: Path):
        self.snapshot_path = root / "public-snapshot.json"

    def get_work_for_mcp(self, record_id: str):
        return {
            "found": True,
            "record_id": record_id,
            "work": {
                "title": "科技股关键位置",
                "published_at": "2026-10-05T08:30:00+08:00",
            },
            "video_original": {
                "text": "本次完整原文",
                "status": "video_text_unconfirmed",
            },
        }


class FakeBloggerLibrary:
    pass


class EventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1_800_000_000.0
        self.requests = []

        def sender(url, body, headers):
            payload = json.loads(body)
            self.requests.append((url, payload, dict(headers), body))
            if payload.get("type") == "verification":
                return 200, json.dumps({"challenge": payload["challenge"]}).encode()
            return 204, b""

        self.sender = sender
        self.manager = ModelMrMcpEvents(
            self.root / "events.sqlite3",
            FakeModelMrLibrary(self.root),
            resolver=lambda host, port: ["8.8.8.8"],
            sender=sender,
            clock=lambda: self.now,
        )

    def subscribe(self, *, secret=SECRET):
        return self.manager.subscribe(
            "owner",
            {
                "name": EVENT_NAME,
                "arguments": {},
                "delivery": {
                    "mode": "webhook",
                    "url": CALLBACK,
                    "secret": secret,
                },
                "cursor": None,
            },
        )

    def test_protocol_discovery_auth_and_event_catalog(self):
        discovered = handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}},
            library=FakeBloggerLibrary(),
            model_mr_library=FakeModelMrLibrary(self.root),
            version="0.25.0",
            authenticated=False,
            events=self.manager,
        )
        self.assertEqual(discovered["result"]["supportedVersions"], ["2026-07-28"])
        self.assertIn("events", discovered["result"]["capabilities"])
        initialized = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2026-07-28"},
            },
            library=FakeBloggerLibrary(),
            model_mr_library=FakeModelMrLibrary(self.root),
            version="0.25.0",
            authenticated=False,
            events=self.manager,
        )
        self.assertIn("events", initialized["result"]["capabilities"])
        denied = handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "events/list", "params": {}},
            library=FakeBloggerLibrary(),
            model_mr_library=FakeModelMrLibrary(self.root),
            version="0.25.0",
            authenticated=False,
            events=self.manager,
        )
        self.assertEqual(denied["error"]["code"], -32001)
        listed = handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "events/list", "params": {}},
            library=FakeBloggerLibrary(),
            model_mr_library=FakeModelMrLibrary(self.root),
            version="0.25.0",
            authenticated=True,
            events=self.manager,
            principal="owner",
        )
        definition = listed["result"]["events"][0]
        self.assertEqual(definition["name"], EVENT_NAME)
        self.assertNotIn("text", definition["payloadSchema"]["properties"])

    def test_subscribe_verifies_idempotently_and_unsubscribe_stops_delivery(self):
        first = self.subscribe()
        second = self.subscribe()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0][1]["type"], "verification")
        self.manager.unsubscribe(
            "owner",
            {
                "name": EVENT_NAME,
                "arguments": {},
                "delivery": {"mode": "webhook", "url": CALLBACK},
            },
        )
        self.manager.record_original_ready(1, "本次完整原文")
        self.assertFalse(self.manager.dispatch_one())

    def test_subscription_diagnostics_are_bounded_sanitized_and_read_only(self):
        result = self.subscribe()
        for _index in range(MAX_DIAGNOSTIC_EVENTS + 8):
            self.manager.record_diagnostic("events_subscribe", "synthetic")

        snapshot = self.manager.diagnostic_snapshot()
        self.assertEqual(snapshot["schema"], "instant-ai-mcp-event-diagnostics/v1")
        self.assertEqual(snapshot["retention"], "memory_only")
        self.assertEqual(len(snapshot["events"]), MAX_DIAGNOSTIC_EVENTS)
        self.assertEqual(
            set(snapshot["events"][0]), {"at", "stage", "outcome"}
        )
        self.assertEqual(snapshot["store"]["status"], "ready")
        self.assertEqual(snapshot["store"]["total_subscriptions"], 1)
        self.assertEqual(snapshot["store"]["active_subscriptions"], 1)
        self.assertEqual(snapshot["store"]["verified_callbacks"], 1)
        serialized = json.dumps(snapshot)
        self.assertNotIn(CALLBACK, serialized)
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn(result["id"], serialized)

    def event_message(self, method, params, *, authenticated=True, principal="owner"):
        return handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            library=FakeBloggerLibrary(),
            model_mr_library=FakeModelMrLibrary(self.root),
            version="test",
            authenticated=authenticated,
            events=self.manager,
            principal=principal,
        )

    def event_params(self, method, metadata):
        delivery = {"mode": "webhook", "url": CALLBACK}
        if method == "events/subscribe":
            delivery["secret"] = SECRET
        return {
            "name": EVENT_NAME,
            "arguments": {},
            "delivery": delivery,
            "_meta": metadata,
        }

    def test_metadata_subscribe_refresh_and_unsubscribe_keep_one_identity(self):
        first = self.event_message(
            "events/subscribe",
            self.event_params("events/subscribe", {"progressToken": "synthetic-progress-one"}),
        )["result"]
        self.assertEqual(self.subscribe()["id"], first["id"])
        for metadata in ({}, {"progressToken": "synthetic-progress-one"},
                         {"openai/session": "synthetic-session-two"}):
            result = self.event_message(
                "events/subscribe", self.event_params("events/subscribe", metadata)
            )
            self.assertEqual(result["result"]["id"], first["id"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.manager.diagnostic_snapshot()["store"]["total_subscriptions"], 1)
        with self.manager.db() as connection:
            persisted = "\n".join(connection.iterdump())
        self.assertNotIn("synthetic-progress-one", persisted)
        self.assertNotIn("synthetic-session-two", persisted)
        self.assertNotIn("_meta", persisted)

        self.manager.record_original_ready(1, "完整原文")
        params = self.event_params("events/unsubscribe", {"progressToken": "changed"})
        result = self.event_message("events/unsubscribe", params)
        self.assertEqual(result["result"], {})
        self.assertEqual(self.event_message("events/unsubscribe", params)["result"], {})
        self.assertFalse(self.manager.dispatch_one())
        self.assertEqual(self.manager.diagnostic_snapshot()["store"]["active_subscriptions"], 0)

    def test_invalid_metadata_and_extra_parameters_fail_before_callback(self):
        for method in ("events/subscribe", "events/unsubscribe"):
            for metadata in (None, [], "invalid", True, 1):
                with self.subTest(method=method, metadata=metadata):
                    result = self.event_message(method, self.event_params(method, metadata))
                    self.assertEqual(result["error"]["code"], -32602)
                    self.assertEqual(result["error"]["data"]["reason"], "metadata_invalid")
            for changes, reason in (
                ({"unexpected": "value"}, "params_invalid"),
                ({"arguments": {"unexpected": True}}, "arguments_invalid"),
                ({"name": "not_an_event"}, "event_not_found"),
            ):
                with self.subTest(method=method, changes=changes):
                    params = {**self.event_params(method, {}), **changes}
                    result = self.event_message(method, params)
                    self.assertEqual(result["error"]["code"], -32602)
                    self.assertEqual(result["error"]["data"]["reason"], reason)
        self.assertFalse(self.requests)
        self.assertFalse(self.manager.path.exists())

    def test_metadata_cannot_grant_auth_or_change_subscription_owner(self):
        self.subscribe()
        metadata = {"principal": "owner", "authenticated": True, "skipVerification": True}
        for method in ("events/subscribe", "events/unsubscribe"):
            params = self.event_params(method, metadata)
            denied = self.event_message(method, params, authenticated=False)
            self.assertEqual(denied["error"]["code"], -32001)
        stopped = self.event_message(
            "events/unsubscribe", self.event_params("events/unsubscribe", metadata),
            principal="different-principal",
        )
        self.assertEqual(stopped["result"], {})
        self.assertEqual(self.manager.diagnostic_snapshot()["store"]["active_subscriptions"], 1)
        self.assertEqual(len(self.requests), 1)

    def test_parameter_diagnostics_never_retain_metadata_or_unknown_field_names(self):
        params = self.event_params("events/subscribe", {"private-key-name": SECRET})
        params[CALLBACK] = {"private-value": "sensitive-value"}
        result = self.event_message("events/subscribe", params)
        self.assertEqual(result["error"]["data"]["reason"], "params_invalid")
        snapshot = self.manager.diagnostic_snapshot()
        shape = snapshot["events"][0]["parameter_shape"]
        self.assertEqual(shape, {
            "metadata_type": "object", "arguments_type": "object",
            "delivery_type": "object", "extra_field_count": 1,
        })
        serialized = json.dumps(snapshot)
        for private in (CALLBACK, SECRET, "private-key-name", "sensitive-value"):
            self.assertNotIn(private, serialized)
        shape["metadata_type"] = "tampered"
        self.assertEqual(
            self.manager.diagnostic_snapshot()["events"][0]["parameter_shape"]["metadata_type"],
            "object",
        )
        self.assertFalse(self.manager.path.exists())

    def test_duplicate_original_delivers_once_with_standard_signature(self):
        subscription = self.subscribe()
        created = self.manager.record_original_ready(1, "本次完整原文")
        duplicate = self.manager.record_original_ready(1, "本次完整原文")
        self.assertTrue(created["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(created["event_id"], duplicate["event_id"])
        self.assertTrue(self.manager.dispatch_one())
        self.assertFalse(self.manager.dispatch_one())
        _url, payload, headers, body = self.requests[-1]
        self.assertEqual(payload["eventId"], created["event_id"])
        self.assertEqual(headers["webhook-id"], created["event_id"])
        self.assertEqual(headers["X-MCP-Subscription-Id"], subscription["id"])
        signed = f"{payload['eventId']}.{int(self.now)}.".encode() + body
        expected = base64.b64encode(
            hmac.new(b"s" * 32, signed, hashlib.sha256).digest()
        ).decode("ascii")
        self.assertEqual(headers["webhook-signature"], f"v1,{expected}")
        self.assertEqual(
            set(payload["data"]),
            {"record_id", "title", "published_at", "original_status", "original_sha256"},
        )
        with sqlite3.connect(self.manager.path) as connection:
            stored = connection.execute("SELECT data FROM events").fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertNotIn("本次完整原文", stored)
        self.assertFalse({"reports", "interpretations", "point_tracking"} & tables)

    def test_retry_preserves_event_id_and_refreshes_signature(self):
        deliveries = []

        def sender(url, body, headers):
            payload = json.loads(body)
            if payload.get("type") == "verification":
                return 200, json.dumps({"challenge": payload["challenge"]}).encode()
            deliveries.append((payload, dict(headers)))
            return (500, b"") if len(deliveries) == 1 else (204, b"")

        self.manager._sender = sender
        self.subscribe()
        self.manager.record_original_ready(1, "本次完整原文")
        self.assertTrue(self.manager.dispatch_one())
        self.now += 16
        restarted = ModelMrMcpEvents(
            self.manager.path,
            FakeModelMrLibrary(self.root),
            resolver=lambda host, port: ["8.8.8.8"],
            sender=sender,
            clock=lambda: self.now,
        )
        self.assertTrue(restarted.dispatch_one())
        self.assertEqual(deliveries[0][0]["eventId"], deliveries[1][0]["eventId"])
        self.assertNotEqual(
            deliveries[0][1]["webhook-signature"],
            deliveries[1][1]["webhook-signature"],
        )
        self.assertFalse(restarted.dispatch_one())

    def test_gone_callback_is_terminal_and_never_retried(self):
        deliveries = []

        def sender(url, body, headers):
            payload = json.loads(body)
            if payload.get("type") == "verification":
                return 200, json.dumps({"challenge": payload["challenge"]}).encode()
            deliveries.append(payload["eventId"])
            return 410, b""

        self.manager._sender = sender
        self.subscribe()
        self.manager.record_original_ready(1, "本次完整原文")
        self.assertTrue(self.manager.dispatch_one())
        self.now += 24 * 60 * 60
        self.assertFalse(self.manager.dispatch_one())
        self.assertEqual(len(deliveries), 1)
        with sqlite3.connect(self.manager.path) as connection:
            state, attempts = connection.execute(
                "SELECT state,attempts FROM deliveries"
            ).fetchone()
        self.assertEqual((state, attempts), ("terminal", 1))

    def test_secret_rotation_signs_with_new_and_old_keys(self):
        self.subscribe()
        self.now += 1
        self.subscribe(secret=SECOND_SECRET)
        self.manager.record_original_ready(1, "本次完整原文")
        self.manager.dispatch_one()
        signatures = self.requests[-1][2]["webhook-signature"].split(" ")
        self.assertEqual(len(signatures), 2)

    def test_unpadded_standard_secret_is_accepted(self):
        result = self.subscribe(secret=SECRET.rstrip("="))
        self.assertTrue(result["id"].startswith("sub_"))

    def test_https_transport_fails_over_only_across_validated_public_addresses(self):
        attempted = []

        class Response:
            status = 204

            @staticmethod
            def read(limit):
                return b""

        class Connection:
            def __init__(inner, host, address, port, timeout):
                inner.address = address
                attempted.append(address)

            def request(inner, method, target, body, headers):
                if inner.address == "8.8.4.4":
                    raise OSError("synthetic first address failure")

            def getresponse(inner):
                return Response()

            def close(inner):
                return None

        manager = ModelMrMcpEvents(
            self.root / "transport.sqlite3",
            FakeModelMrLibrary(self.root),
            resolver=lambda host, port: ["8.8.4.4", "8.8.8.8"],
            clock=lambda: self.now,
        )
        with patch(
            "instant_ai.model_mr_mcp_events._PinnedHTTPSConnection", Connection
        ):
            status, body = manager._request(
                CALLBACK,
                b"{}",
                {"Content-Type": "application/json"},
            )
        self.assertEqual((status, body), (204, b""))
        self.assertEqual(attempted, ["8.8.4.4", "8.8.8.8"])

    def test_callback_validation_rejects_private_address_and_bad_challenge(self):
        private = ModelMrMcpEvents(
            self.root / "private.sqlite3",
            FakeModelMrLibrary(self.root),
            resolver=lambda host, port: ["127.0.0.1"],
            sender=self.sender,
            clock=lambda: self.now,
        )
        with self.assertRaisesRegex(RuntimeError, "address_not_public"):
            private.subscribe(
                "owner",
                {
                    "name": EVENT_NAME,
                    "arguments": {},
                    "delivery": {
                        "mode": "webhook",
                        "url": CALLBACK,
                        "secret": SECRET,
                    },
                },
            )
        multicast = ModelMrMcpEvents(
            self.root / "multicast.sqlite3",
            FakeModelMrLibrary(self.root),
            resolver=lambda host, port: ["224.0.0.1"],
            sender=self.sender,
            clock=lambda: self.now,
        )
        with self.assertRaisesRegex(RuntimeError, "address_not_public"):
            multicast.subscribe(
                "owner",
                {
                    "name": EVENT_NAME,
                    "arguments": {},
                    "delivery": {
                        "mode": "webhook",
                        "url": CALLBACK,
                        "secret": SECRET,
                    },
                },
            )
        bad = ModelMrMcpEvents(
            self.root / "bad.sqlite3",
            FakeModelMrLibrary(self.root),
            resolver=lambda host, port: ["8.8.8.8"],
            sender=lambda url, body, headers: (200, b'{"challenge":"wrong"}'),
            clock=lambda: self.now,
        )
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "events/subscribe",
                "params": {
                    "name": EVENT_NAME,
                    "arguments": {},
                    "delivery": {
                        "mode": "webhook",
                        "url": CALLBACK,
                        "secret": SECRET,
                    },
                },
            },
            library=FakeBloggerLibrary(),
            model_mr_library=FakeModelMrLibrary(self.root),
            version="0.25.0",
            authenticated=True,
            events=bad,
            principal="owner",
        )
        self.assertEqual(response["error"]["code"], -32015)
        self.assertEqual(response["error"]["data"]["reason"], "challenge_failed")
        diagnostics = bad.diagnostic_snapshot()["events"]
        self.assertIn(
            {
                "at": int(self.now),
                "stage": "callback_verification",
                "outcome": "error_challenge_failed",
            },
            diagnostics,
        )
        self.assertIn(
            {
                "at": int(self.now),
                "stage": "events_subscribe",
                "outcome": "error_challenge_failed",
            },
            diagnostics,
        )


if __name__ == "__main__":
    unittest.main()
