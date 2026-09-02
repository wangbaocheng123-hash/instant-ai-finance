from __future__ import annotations

import hashlib
import http.client
import gzip
import json
import os
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
        media_bytes = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41"
        comments = [
            {
                "source_comment_id": "comment-root",
                "parent_source_comment_id": "",
                "root_source_comment_id": "",
                "reply_to_comment_id": "",
                "author": "读者甲",
                "is_creator": False,
                "text": "这个观点怎么看？",
                "like_count": 23,
                "reply_count": 1,
                "published_at": "2026-08-30T09:10:00Z",
                "captured_at": "2026-08-30T10:00:00Z",
                "kind": "comment",
                "section": "main",
                "sentiment": "neutral",
                "risk_level": "normal",
                "author_liked": True,
                "low_value": False,
                "ip_label": "",
                "public_label": "",
                "actual_reply_user": "",
                "display_order": 0,
            },
            {
                "source_comment_id": "comment-reply",
                "parent_source_comment_id": "comment-root",
                "root_source_comment_id": "comment-root",
                "reply_to_comment_id": "comment-root",
                "author": "测试博主",
                "is_creator": True,
                "text": "谢谢关注。",
                "like_count": 8,
                "reply_count": 0,
                "published_at": "2026-08-30T09:11:00Z",
                "captured_at": "2026-08-30T10:00:00Z",
                "kind": "reply",
                "section": "reply",
                "sentiment": "positive",
                "risk_level": "normal",
                "author_liked": None,
                "low_value": False,
                "ip_label": "",
                "public_label": "作者",
                "actual_reply_user": "读者甲",
                "display_order": 1,
            },
        ]
        plain_comments = b"".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            for item in comments
        )
        compressed_comments = gzip.compress(plain_comments, mtime=0)
        media_sha256 = hashlib.sha256(media_bytes).hexdigest()
        compressed_sha256 = hashlib.sha256(compressed_comments).hexdigest()
        plain_sha256 = hashlib.sha256(plain_comments).hexdigest()
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
                    "size_bytes": len(compressed_comments),
                    "sha256": compressed_sha256,
                    "uncompressed_size_bytes": len(plain_comments),
                    "uncompressed_sha256": plain_sha256,
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
                ) VALUES(?, ?, 'media', ?, ?, 'video/mp4', ?, 'video', 0, ?, ?, ?)
                """,
                (
                    transfer_id,
                    f"media-{name}",
                    len(media_bytes),
                    media_sha256,
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
                ) VALUES(?, ?, 'comment_bundle', ?, ?, 'application/gzip', '', '',
                         0, ?, ?, 2, ?, ?, ?)
                """,
                (
                    transfer_id,
                    _digest(f"bundle:{name}"),
                    len(compressed_comments),
                    compressed_sha256,
                    len(plain_comments),
                    plain_sha256,
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
        artifact_root = self.root / "artifacts" / transfer_id
        artifact_root.mkdir(parents=True, exist_ok=True)
        if media_state == "verified":
            (artifact_root / "private.mp4").write_bytes(media_bytes)
        if comments_state == "verified":
            (artifact_root / "private.ndjson.gz").write_bytes(compressed_comments)
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
            "transfer", "processing_status", "media_available", "video_url",
            "has_video_text", "comment_count",
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
        self.assertEqual(set(detail), set(work) | {
            "comment_snapshot", "video_text", "transcripts", "comments",
            "comment_total", "capabilities",
        })
        self.assertEqual(set(detail["comment_snapshot"]), {
            "captured_at", "complete", "expected_total", "captured_count",
            "top_level_count", "reply_groups", "missing_replies",
        })
        self.assertTrue(detail["media_available"])
        self.assertEqual(detail["comment_total"], 2)
        self.assertEqual(detail["comments"][1]["kind"], "author_reply")
        self.assertEqual(detail["comments"][0]["thread_key"], detail["comments"][1]["thread_key"])
        self.assertNotIn("source_comment_id", json.dumps(detail, ensure_ascii=False))

    def test_owner_title_video_text_and_cached_transcript_are_git_external(self) -> None:
        _, work_key = self._insert_work(
            "owner-content",
            source_work_id="work-owner-content",
            revision=1,
            transport_status="transport_completed",
            media_state="verified",
            comments_state="verified",
            processing_status="awaiting_asr_approval",
        )
        self.assertEqual(self.library.save_title(work_key, "主人修改标题")["title"], "主人修改标题")
        self.assertEqual(self.library.save_video_text(work_key, "正式视频原文")["text"], "正式视频原文")
        detail = self.library.work_detail(work_key)
        assert detail is not None
        self.assertEqual(detail["title"], "主人修改标题")
        self.assertEqual(detail["video_text"]["text"], "正式视频原文")
        self.assertEqual(detail["processing_status"], "ready")
        cached = self.library.transcribe(work_key, "cached")
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["text"], "正式视频原文")
        self.assertTrue(self.library.owner_database_path.is_file())

    def test_mcp_projection_searches_latest_official_text_and_exposes_only_whitelisted_fields(self) -> None:
        _, work_key = self._insert_work(
            "mcp-official",
            source_work_id="work-mcp-official",
            revision=1,
            transport_status="transport_completed",
            media_state="verified",
            comments_state="verified",
            processing_status="ready",
        )
        self.library.save_title(work_key, "全球债市与华尔街交易员")
        self.library.save_video_text(work_key, "债市再掀抛售，加息预期升温，华尔街交易员保持冷静。")

        result = self.library.search_for_mcp("测试博主最新的视频文字", limit=3)
        self.assertEqual(result["query_mode"], "latest")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["record_id"], f"cloud-video:{work_key}")
        self.assertEqual(result["items"][0]["original_status"], "official")

        complete = self.library.get_for_mcp(f"cloud-video:{work_key}")
        self.assertTrue(complete["found"])
        self.assertTrue(complete["video_original"]["verified"])
        self.assertIn("债市再掀抛售", complete["video_original"]["text"])
        encoded = json.dumps(complete, ensure_ascii=False)
        for forbidden in (
            "comments", "manifest", "collector_node_id", "collector_key_id",
            "stored_relative_path", "source_filename", str(self.root), "C:\\Users",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_mcp_creator_search_accepts_li_ailin_name_variant_without_rene_suffix(self) -> None:
        transfer_id, work_key = self._insert_work(
            "li-ailin",
            source_work_id="7680678242151337279",
            revision=1,
            processing_status="ready",
        )
        with closing(sqlite3.connect(self.store.database_path)) as connection, connection:
            row = connection.execute(
                "SELECT manifest_json FROM transfers WHERE transfer_id=?", (transfer_id,)
            ).fetchone()
            manifest = json.loads(row[0])
            manifest["creator"]["display_name"] = "李爱琳rene"
            connection.execute(
                "UPDATE transfers SET creator_display_name=?, manifest_json=? WHERE transfer_id=?",
                ("李爱琳rene", json.dumps(manifest, ensure_ascii=False), transfer_id),
            )
        self.library.save_video_text(work_key, "全球债市再掀抛售，华尔街交易员并不慌张。")

        result = self.library.search_for_mcp("尤其是我们最近抓取的这个李艾琳的这条视频文字", limit=1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["source_work_id"], "7680678242151337279")
        self.assertEqual(result["items"][0]["original_status"], "official")

    def test_mcp_http_requires_dedicated_bearer_token_not_owner_cookie(self) -> None:
        _, work_key = self._insert_work(
            "mcp-route",
            source_work_id="work-mcp-route",
            revision=1,
            processing_status="ready",
        )
        self.library.save_video_text(work_key, "这是云端正式视频原文。")
        server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
        server.blogger_transfer = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        token = "a" * 64
        with patch.dict(os.environ, {"INSTANT_AI_BLOGGER_MCP_TOKEN": token}, clear=False), patch(
            "instant_ai.server.AUTH", FakeOwnerAuth()
        ), patch("instant_ai.server.BLOGGER_LIBRARY", self.library), patch(
            "instant_ai.server.queue_analysis", Mock(side_effect=AssertionError("AI called"))
        ):
            thread.start()
            try:
                body = json.dumps({"question": "测试博主最新视频", "limit": 1}).encode("utf-8")
                for authorization, expected in (("", 401), ("Bearer wrong", 401), (f"Bearer {token}", 200)):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request(
                        "POST",
                        "/api/mcp/blogger/search",
                        body=body,
                        headers={
                            "Authorization": authorization,
                            "Content-Length": str(len(body)),
                            "Content-Type": "application/json",
                            "Cookie": "owner=yes",
                        },
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, expected)
                    if expected == 200:
                        self.assertEqual(payload["items"][0]["record_id"], f"cloud-video:{work_key}")
                    connection.close()

                body = json.dumps({"record_id": f"cloud-video:{work_key}"}).encode("utf-8")
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(
                    "POST",
                    "/api/mcp/blogger/get",
                    body=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Length": str(len(body)),
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["video_original"]["text"], "这是云端正式视频原文。")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

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

    def test_owner_media_range_and_content_writes_require_owner_cookie(self) -> None:
        _, work_key = self._insert_work(
            "owner-routes",
            source_work_id="work-owner-routes",
            revision=1,
            transport_status="transport_completed",
            media_state="verified",
            comments_state="verified",
            processing_status="awaiting_asr_approval",
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
        server.blogger_transfer = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        with patch("instant_ai.server.AUTH", FakeOwnerAuth()), patch(
            "instant_ai.server.BLOGGER_LIBRARY", self.library
        ), patch("instant_ai.server.queue_analysis", Mock(side_effect=AssertionError("AI called"))):
            thread.start()
            try:
                media_path = f"/api/blogger-library/works/{work_key}/video"
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("HEAD", media_path)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 401)
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("HEAD", media_path, headers={"Cookie": "owner=yes"})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Accept-Ranges"), "bytes")
                self.assertEqual(response.getheader("Content-Type"), "video/mp4")
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(
                    "GET",
                    media_path,
                    headers={"Cookie": "owner=yes", "Range": "bytes=0-3"},
                )
                response = connection.getresponse()
                body = response.read()
                self.assertEqual(response.status, 206)
                self.assertEqual(body, b"\x00\x00\x00\x18")
                connection.close()

                for action, payload in (
                    ("title", {"title": "手机修改标题"}),
                    ("video-text", {"text": "手机保存原文"}),
                ):
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request(
                        "POST",
                        f"/api/blogger-library/works/{work_key}/{action}",
                        body=body,
                        headers={
                            "Cookie": "owner=yes",
                            "Content-Length": str(len(body)),
                            "Content-Type": "application/json",
                            "X-Instant-AI": "1",
                        },
                    )
                    response = connection.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                    self.assertTrue(result["saved"])
                    connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
