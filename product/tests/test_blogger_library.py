from __future__ import annotations

import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from instant_ai.blogger_ingest import BloggerIngestStore, MANIFEST_SCHEMA_VERSION, opaque_work_key
from instant_ai.blogger_library import BloggerLibrary
from instant_ai.server import InstantAIHandler


CREATOR_ID = "11111111-2222-4333-8444-555555555555"


class FakeOwnerAuth:
    required = True
    setup_required = False

    @staticmethod
    def session(cookie: str):
        return object() if cookie == "owner=yes" else None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class BloggerLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "blogger-agent"
        self.store = BloggerIngestStore(self.root)
        self.library = BloggerLibrary(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _insert_work(
        self,
        name: str,
        *,
        source_work_id: str,
        revision: int,
        state: str = "accepted",
        is_current: bool = True,
        transport_status: str = "accepted",
        media_state: str = "pending",
        comments_state: str = "pending",
        processing_status: str | None = None,
        received_at: int = 1_788_000_000,
        source_url: str = "https://example.test/work",
    ) -> tuple[str, str]:
        transfer_id = _digest(f"transfer:{name}")
        revision_sha256 = _digest(f"revision:{name}")
        work_key = opaque_work_key("douyin", CREATOR_ID, source_work_id)
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "captured_at": "2026-08-30T10:00:00Z",
            "collector": {
                "node_id": "beijing-private-node",
                "key_id": "private-key-id",
                "version": "secret-version",
                "source_sequence": revision,
            },
            "creator": {
                "creator_id": CREATOR_ID,
                "display_name": "测试博主",
                "platform": "douyin",
                "platform_user_id": "private-platform-user",
            },
            "work": {
                "platform": "douyin",
                "source_work_id": source_work_id,
                "revision": revision,
                "work_type": "video",
                "title": f"作品 {name}",
                "description": "只读详情",
                "source_url": source_url,
                "cover_url": r"C:\Users\owner\private-cover.jpg",
                "published_at": "2026-08-30T09:00:00Z",
            },
            "media": [],
            "comment_snapshot": {
                "snapshot_id": "snapshot-private-id",
                "captured_at": "2026-08-30T10:00:00Z",
                "complete": True,
                "expected_total": 3,
                "captured_count": 2,
                "top_level_count": 1,
                "reply_groups": 1,
                "reply_groups_incomplete": 0,
                "missing_replies": 1,
                "orphan_replies": 0,
                "rules_version": "rules-private",
                "bundle": {
                    "bundle_id": _digest(f"bundle:{name}"),
                    "format": "blogger-comments/v1+ndjson",
                    "content_encoding": "gzip",
                    "item_count": 2,
                    "size_bytes": 20,
                    "sha256": _digest(f"compressed:{name}"),
                    "uncompressed_size_bytes": 30,
                    "uncompressed_sha256": _digest(f"plain:{name}"),
                },
            },
            "revision_sha256": revision_sha256,
            "transfer_id": transfer_id,
        }
        with closing(sqlite3.connect(self.store.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO transfers(
                    transfer_id, schema_version, collector_node_id, collector_key_id,
                    collector_source_sequence, request_sha256, revision_sha256,
                    creator_id, creator_display_name, creator_platform, platform_user_id,
                    work_platform, source_work_id, source_revision, opaque_work_key,
                    state, is_current, transport_status, received_at, completed_at, manifest_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transfer_id,
                    MANIFEST_SCHEMA_VERSION,
                    "beijing-private-node",
                    "private-key-id",
                    revision,
                    _digest(f"request:{name}"),
                    revision_sha256,
                    CREATOR_ID,
                    "测试博主",
                    "douyin",
                    "private-platform-user",
                    "douyin",
                    source_work_id,
                    revision,
                    work_key,
                    state,
                    int(is_current),
                    transport_status,
                    received_at,
                    received_at if transport_status == "transport_completed" else None,
                    json.dumps(manifest, ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                INSERT INTO artifacts(
                    transfer_id, artifact_id, artifact_kind, expected_size_bytes,
                    expected_sha256, mime_type, source_filename, media_role,
                    ordinal, state, stored_relative_path, verified_at
                ) VALUES(?, ?, 'media', 12, ?, 'video/mp4', ?, 'video', 0, ?, ?, ?)
                """,
                (
                    transfer_id,
                    f"media-{name}",
                    _digest(f"media:{name}"),
                    r"C:\Users\owner\private-video.mp4",
                    media_state,
                    f"artifacts/{transfer_id}/private.mp4" if media_state == "verified" else None,
                    received_at if media_state == "verified" else None,
                ),
            )
            connection.execute(
                """
                INSERT INTO artifacts(
                    transfer_id, artifact_id, artifact_kind, expected_size_bytes,
                    expected_sha256, mime_type, source_filename, media_role,
                    ordinal, uncompressed_size_bytes, uncompressed_sha256,
                    item_count, state, stored_relative_path, verified_at
                ) VALUES(?, ?, 'comment_bundle', 20, ?, 'application/gzip', '', '',
                         0, 30, ?, 2, ?, ?, ?)
                """,
                (
                    transfer_id,
                    _digest(f"bundle:{name}"),
                    _digest(f"compressed:{name}"),
                    _digest(f"plain:{name}"),
                    comments_state,
                    f"artifacts/{transfer_id}/private.ndjson.gz" if comments_state == "verified" else None,
                    received_at if comments_state == "verified" else None,
                ),
            )
            if processing_status is not None:
                connection.execute(
                    """
                    INSERT INTO processing_queue(
                        transfer_id, opaque_work_key, processing_status, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (transfer_id, work_key, processing_status, received_at, received_at),
                )
        return transfer_id, work_key

    def test_missing_and_empty_databases_are_unavailable_without_initializing_storage(self) -> None:
        missing_root = Path(self.temporary.name) / "missing-library"
        missing = BloggerLibrary(missing_root)
        self.assertFalse(missing.status()["available"])
        self.assertFalse(missing_root.exists())
        self.assertEqual(missing.creators(), {"items": [], "count": 0})

        empty_root = Path(self.temporary.name) / "empty-library"
        database_root = empty_root / "database"
        database_root.mkdir(parents=True)
        database_path = database_root / "blogger_ingest.db"
        database_path.touch()
        before = set(database_root.iterdir())
        status = BloggerLibrary(empty_root).status()
        self.assertFalse(status["available"])
        self.assertEqual(status["counts"]["works"], 0)
        self.assertEqual(set(database_root.iterdir()), before)

    def test_only_current_accepted_revision_is_visible_and_fields_match_client_contract(self) -> None:
        self._insert_work(
            "old",
            source_work_id="work-1",
            revision=1,
            state="superseded",
            is_current=False,
            received_at=1_787_999_000,
        )
        _, current_key = self._insert_work(
            "current",
            source_work_id="work-1",
            revision=2,
            transport_status="transport_completed",
            media_state="verified",
            comments_state="verified",
            processing_status="awaiting_asr_approval",
        )
        self._insert_work(
            "future-not-current",
            source_work_id="work-1",
            revision=3,
            is_current=False,
            received_at=1_788_001_000,
        )
        self._insert_work(
            "stale-current-flag",
            source_work_id="work-stale",
            revision=9,
            state="stale",
            is_current=True,
        )

        status = self.library.status()
        self.assertTrue(status["available"])
        self.assertEqual(
            set(status),
            {"available", "module", "mode", "message", "counts"},
        )
        self.assertEqual(status["counts"], {
            "creators": 1,
            "works": 1,
            "transferring": 0,
            "awaiting_asr_approval": 1,
            "ready": 0,
            "failed": 0,
        })

        creators = self.library.creators()
        self.assertEqual(set(creators), {"items", "count"})
        self.assertEqual(creators["count"], 1)
        creator = creators["items"][0]
        self.assertEqual(set(creator), {
            "creator_id", "display_name", "platform", "work_count",
            "latest_published_at", "latest_captured_at", "status_counts",
        })
        self.assertEqual(set(creator["status_counts"]), {
            "works", "transferring", "awaiting_asr_approval", "ready", "failed",
        })

        response = self.library.creator_works(CREATOR_ID)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(set(response), {"creator", "items", "count"})
        self.assertEqual(response["count"], 1)
        work = response["items"][0]
        self.assertEqual(work["work_key"], current_key)
        self.assertEqual(work["title"], "作品 current")
        self.assertEqual(set(work), {
            "work_key", "creator_id", "source_work_id", "platform", "work_type",
            "title", "description", "source_url", "published_at", "captured_at",
            "transfer", "processing_status",
        })
        self.assertEqual(set(work["transfer"]), {
            "status", "source_revision", "received_at", "media_expected",
            "media_received", "comments_expected", "comments_received",
        })
        self.assertEqual(work["transfer"]["source_revision"], 2)
        self.assertEqual(work["transfer"]["status"], "verified")
        self.assertEqual(work["processing_status"], "awaiting_asr_approval")

        detail = self.library.work_detail(current_key)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(set(detail), set(work) | {"comment_snapshot"})
        self.assertEqual(set(detail["comment_snapshot"]), {
            "captured_at", "complete", "expected_total", "captured_count",
            "top_level_count", "reply_groups", "missing_replies",
        })

    def test_whitelist_never_exposes_paths_manifest_or_machine_identity(self) -> None:
        _, work_key = self._insert_work(
            "private",
            source_work_id="work-private",
            revision=1,
            source_url=r"C:\Users\owner\source.json",
        )
        payload = self.library.work_detail(work_key)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["source_url"], "")
        encoded = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            str(self.root),
            "C:\\Users",
            "manifest_json",
            "collector_node_id",
            "collector_key_id",
            "private-key-id",
            "beijing-private-node",
            "stored_relative_path",
            "source_filename",
            "snapshot-private-id",
            "private-platform-user",
            "nonce",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_awaiting_asr_approval_is_read_only_from_processing_queue(self) -> None:
        transfer_id, work_key = self._insert_work(
            "complete-without-job",
            source_work_id="work-no-job",
            revision=1,
            transport_status="transport_completed",
            media_state="verified",
            comments_state="verified",
        )
        detail = self.library.work_detail(work_key)
        self.assertEqual(detail["processing_status"], "awaiting_transfer")

        with closing(sqlite3.connect(self.store.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO processing_queue(
                    transfer_id, opaque_work_key, processing_status, created_at, updated_at
                ) VALUES(?, ?, 'awaiting_asr_approval', 1, 1)
                """,
                (transfer_id, work_key),
            )
        detail = self.library.work_detail(work_key)
        self.assertEqual(detail["processing_status"], "awaiting_asr_approval")

    def test_four_get_routes_require_owner_cookie_and_have_no_write_variant(self) -> None:
        _, work_key = self._insert_work(
            "route",
            source_work_id="work-route",
            revision=1,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
        server.blogger_transfer = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        owner_auth = FakeOwnerAuth()
        routes = (
            "/api/blogger-library/status",
            "/api/blogger-library/creators",
            f"/api/blogger-library/creators/{CREATOR_ID}/works",
            f"/api/blogger-library/works/{work_key}",
        )
        with patch("instant_ai.server.AUTH", owner_auth), patch(
            "instant_ai.server.BLOGGER_LIBRARY", self.library
        ), patch("instant_ai.server.queue_analysis", Mock(side_effect=AssertionError("AI called"))):
            thread.start()
            try:
                for route in routes:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request("GET", route)
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 401, route)
                    connection.close()

                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request(
                        "GET",
                        route,
                        headers={"X-Blogger-Node-Id": "beijing-private-node"},
                    )
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 401, route)
                    connection.close()

                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request("GET", route, headers={"Cookie": "owner=yes"})
                    response = connection.getresponse()
                    json.loads(response.read())
                    self.assertEqual(response.status, 200, route)
                    connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(
                    "POST",
                    "/api/blogger-library/status",
                    body=b"{}",
                    headers={
                        "Cookie": "owner=yes",
                        "Content-Length": "2",
                        "Content-Type": "application/json",
                        "X-Instant-AI": "1",
                    },
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 404)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
