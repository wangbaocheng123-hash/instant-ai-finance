from __future__ import annotations

import unittest
import hashlib
import json
import os
import tempfile
import http.client
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.error import URLError

from instant_ai.auth import OwnerAuth
from instant_ai.model_mr import ModelMrClient
from instant_ai.model_mr_mcp import ModelMrMcpLibrary
from instant_ai.model_mr_transfer import ModelMrTransferProjector
from instant_ai.blogger_library import MODEL_MR_TRANSFER_CREATOR_ID
from instant_ai.server import InstantAIHandler


class ModelMrGatewayTests(unittest.TestCase):
    def test_verified_beijing_work_is_idempotent_and_preserves_owner_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "public-snapshot.json"
            snapshot.write_text(
                json.dumps({"version": 2, "works": [], "thoughts": [], "counts": {}}),
                encoding="utf-8",
            )
            media = root / "incoming.mp4"
            media.write_bytes(b"verified-video")
            digest = hashlib.sha256(media.read_bytes()).hexdigest()
            client = ModelMrClient(
                "http://127.0.0.1:8787",
                snapshot,
                root / "media",
            )
            first = client.import_beijing_work(
                source_work_id="778899",
                source_revision=1,
                title="模型先生新作品",
                description="",
                source_url="https://www.douyin.com/video/778899",
                published_at="2026-09-03T09:00:00+08:00",
                comments=[
                    {
                        "author": "读者",
                        "text": "测试评论",
                        "like_count": 2,
                        "author_liked": True,
                    }
                ],
                media_path=media,
                media_sha256=digest,
            )
            self.assertEqual(
                client.beijing_processing_candidate(
                    source_work_id="778899",
                    source_revision=1,
                    media_hash=digest,
                ),
                {"work_id": first["work_id"], "media_hash": digest},
            )
            self.assertIsNone(
                client.beijing_processing_candidate(
                    source_work_id="778899",
                    source_revision=2,
                    media_hash=digest,
                )
            )
            client.save_title(first["work_id"], "主人标题")
            client.save_video_text(first["work_id"], "主人确认原文")
            from instant_ai.model_mr_metadata import keyword_revision
            client.save_keywords(first["work_id"], {"行业与板块": ["主人关键词"]}, [], keyword_revision(None, []))
            repeated = client.import_beijing_work(
                source_work_id="778899",
                source_revision=2,
                title="下载器更新标题",
                description="",
                source_url="https://www.douyin.com/video/778899",
                published_at="2026-09-03T09:00:00+08:00",
                comments=[
                    {
                        "author": "读者",
                        "text": "更新评论",
                        "like_count": 3,
                        "author_liked": False,
                    }
                ],
                media_path=media,
                media_sha256=digest,
            )

            self.assertEqual(first["work_id"], repeated["work_id"])
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                self.assertEqual(client.works(limit=10)["count"], 1)
                detail = client.work_detail(first["work_id"])
                self.assertEqual(detail["work"]["title"], "主人标题")
                self.assertEqual(detail["video_text"]["text"], "主人确认原文")
                self.assertEqual(detail["work"]["keyword_info"]["categories"]["行业与板块"], ["主人关键词"])
                self.assertTrue(detail["work"]["keyword_info"]["edited_by_owner"])
                self.assertEqual(detail["comments"][0]["text"], "更新评论")
                self.assertIs(detail["comments"][0]["author_liked"], False)
                self.assertIsNotNone(client.video_path(first["work_id"]))

    def test_explicit_author_unlike_overrides_legacy_raw_like_marker(self) -> None:
        cleaned = ModelMrClient._clean_comment(
            {
                "author": "读者",
                "text": "作者曾经赞过",
                "author_liked": False,
                "raw_json": {"author_liked": True},
            },
            1,
        )

        self.assertIs(cleaned["author_liked"], False)

    def test_beijing_wire_comment_relationships_survive_sanitized_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "public-snapshot.json"
            snapshot.write_text(
                json.dumps({"version": 2, "works": [], "thoughts": [], "counts": {}}),
                encoding="utf-8",
            )
            media = root / "incoming.mp4"
            media.write_bytes(b"verified-video")
            digest = hashlib.sha256(media.read_bytes()).hexdigest()
            client = ModelMrClient("http://127.0.0.1:8787", snapshot, root / "media")
            comments = [
                {
                    "source_comment_id": "fan-question-1",
                    "parent_source_comment_id": "",
                    "root_source_comment_id": "fan-question-1",
                    "reply_to_comment_id": "",
                    "author": "粉丝甲",
                    "text": "先生判断底部，外围市场是参考因素吗？",
                    "kind": "user_comment",
                    "like_count": 3,
                },
                {
                    "source_comment_id": "author-reply-1",
                    "parent_source_comment_id": "fan-question-1",
                    "root_source_comment_id": "fan-question-1",
                    "reply_to_comment_id": "fan-question-1",
                    "author": "模型先生",
                    "text": "不考虑外部原因。",
                    "kind": "author_reply",
                    "like_count": 18,
                },
            ]

            imported = client.import_beijing_work(
                source_work_id="778900",
                source_revision=1,
                title="评论关系测试",
                description="",
                source_url="https://www.douyin.com/video/778900",
                published_at="2026-09-08T19:00:00+08:00",
                comments=comments,
                media_path=media,
                media_sha256=digest,
            )

            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                detail = client.work_detail(imported["work_id"])
            self.assertEqual(len(detail["comments"]), 2)
            self.assertEqual(detail["comments"][0]["thread_key"], detail["comments"][1]["thread_key"])
            self.assertEqual(detail["comments"][0]["reply_depth"], 0)
            self.assertEqual(detail["comments"][1]["reply_depth"], 1)
            self.assertNotIn("source_comment_id", detail["comments"][0])
            self.assertNotIn("parent_source_comment_id", detail["comments"][1])
            mcp = ModelMrMcpLibrary(snapshot).get_author_replies_for_mcp(
                f"model-mr-work:{imported['work_id']}",
                10,
                0,
            )
            self.assertEqual(mcp["items"][0]["question"]["text"], comments[0]["text"])
            self.assertEqual(mcp["items"][0]["author_messages"][0]["text"], comments[1]["text"])

    def test_old_beijing_projection_repairs_threads_without_overwriting_owner_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "public-snapshot.json"
            snapshot.write_text(
                json.dumps({"version": 2, "works": [], "thoughts": [], "counts": {}}),
                encoding="utf-8",
            )
            media = root / "incoming.mp4"
            media.write_bytes(b"verified-video")
            digest = hashlib.sha256(media.read_bytes()).hexdigest()
            client = ModelMrClient("http://127.0.0.1:8787", snapshot, root / "media")
            comments = [
                {
                    "source_comment_id": "fan-question-2",
                    "parent_source_comment_id": "",
                    "root_source_comment_id": "fan-question-2",
                    "reply_to_comment_id": "",
                    "author": "粉丝乙",
                    "text": "双创指数还要磨底吗？",
                    "kind": "user_comment",
                },
                {
                    "source_comment_id": "author-reply-2",
                    "parent_source_comment_id": "fan-question-2",
                    "root_source_comment_id": "fan-question-2",
                    "reply_to_comment_id": "fan-question-2",
                    "author": "模型先生",
                    "text": "这个底不是共振底，估计要磨一下。",
                    "kind": "author_reply",
                },
            ]
            imported = client.import_beijing_work(
                source_work_id="778901",
                source_revision=3,
                title="旧评论关系测试",
                description="",
                source_url="https://www.douyin.com/video/778901",
                published_at="2026-09-08T19:00:00+08:00",
                comments=comments,
                media_path=media,
                media_sha256=digest,
            )
            client.save_title(imported["work_id"], "主人保留标题")
            client.save_video_text(imported["work_id"], "主人确认的视频原文")

            mapping = json.loads(client.transfer_map_path.read_text(encoding="utf-8"))
            mapping["778901"].pop("comment_projection_version")
            client.transfer_map_path.write_text(json.dumps(mapping), encoding="utf-8")
            detail_path = client.details_root / f"{imported['work_id']}.json"
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
            detail["comments"][0].update(thread_key="111111111111", reply_depth=0)
            detail["comments"][1].update(thread_key="222222222222", reply_depth=0)
            detail_path.write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")

            repaired = client.repair_beijing_comment_projection(
                source_work_id="778901",
                source_revision=3,
                comments=comments,
            )

            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                fixed = client.work_detail(imported["work_id"])
            self.assertEqual(repaired["status"], "repaired")
            self.assertEqual(fixed["work"]["title"], "主人保留标题")
            self.assertEqual(fixed["video_text"]["text"], "主人确认的视频原文")
            self.assertEqual(fixed["comments"][0]["thread_key"], fixed["comments"][1]["thread_key"])
            self.assertEqual(fixed["comments"][1]["reply_depth"], 1)
            self.assertEqual(
                json.loads(client.transfer_map_path.read_text(encoding="utf-8"))["778901"][
                    "comment_projection_version"
                ],
                2,
            )

    def test_startup_thread_repair_uses_retained_bundle_without_enqueuing_processing(self) -> None:
        model_mr = Mock()
        model_mr.pending_beijing_comment_projection_sources.return_value = ["778902"]
        model_mr.repair_beijing_comment_projection.return_value = {
            "ok": True,
            "status": "repaired",
        }
        projector = ModelMrTransferProjector(
            blogger_root=Path("/not-used"),
            model_mr=model_mr,
        )
        transfer = {
            "is_current": True,
            "transport_status": "transport_completed",
            "manifest": {
                "creator": {"creator_id": MODEL_MR_TRANSFER_CREATOR_ID},
                "work": {"source_work_id": "778902", "revision": 4},
                "comment_snapshot": {
                    "bundle": {
                        "bundle_id": "bundle-1",
                        "item_count": 2,
                        "uncompressed_size_bytes": 10,
                    }
                },
            },
            "artifacts": [
                {
                    "artifact_id": "bundle-1",
                    "stored_relative_path": "artifacts/comments.gz",
                }
            ],
        }
        store = Mock()
        store.get_current.return_value = transfer
        comments = [{"source_comment_id": "fan-1"}, {"source_comment_id": "reply-1"}]

        with (
            patch("instant_ai.model_mr_transfer.BloggerIngestStore", return_value=store),
            patch.object(projector, "_artifact_path", return_value=Path("comments.gz")),
            patch.object(projector, "_comments", return_value=comments),
        ):
            result = projector.repair_pending_comment_threads()

        self.assertEqual(result, {"pending": 1, "repaired": 1, "skipped": 0, "errors": 0})
        store.get_current.assert_called_once_with(
            work_platform="douyin",
            creator_id=MODEL_MR_TRANSFER_CREATOR_ID,
            source_work_id="778902",
        )
        model_mr.repair_beijing_comment_projection.assert_called_once_with(
            source_work_id="778902",
            source_revision=4,
            comments=comments,
        )

    def test_processing_reconciliation_keeps_unprojected_completion_visible(self) -> None:
        model_mr = Mock()
        model_mr.beijing_processing_candidate.side_effect = [
            {"work_id": 1001, "media_hash": "a" * 64},
            None,
        ]
        projector = ModelMrTransferProjector(
            blogger_root=Path("/not-used"),
            model_mr=model_mr,
        )
        store = Mock()
        store.completed_video_arrivals_since.return_value = [
            {
                "source_work_id": "778903",
                "source_revision": 1,
                "media_hash": "a" * 64,
                "completed_at": 101,
            },
            {
                "source_work_id": "778904",
                "source_revision": 2,
                "media_hash": "b" * 64,
                "completed_at": 102,
            },
        ]
        with patch("instant_ai.model_mr_transfer.BloggerIngestStore", return_value=store):
            arrivals = projector.processing_arrivals_since(100, 25)

        self.assertEqual(arrivals[0], {
            "work_id": 1001,
            "media_hash": "a" * 64,
            "ready": True,
            "completed_at": 101,
        })
        self.assertEqual(arrivals[1], {"ready": False, "completed_at": 102})
        store.completed_video_arrivals_since.assert_called_once_with(
            creator_id=MODEL_MR_TRANSFER_CREATOR_ID,
            completed_since=100,
            limit=25,
        )

    def test_work_summary_removes_local_paths_raw_payload_and_admin_fields(self) -> None:
        cleaned = ModelMrClient._clean_work(
            {
                "id": 12,
                "title": "raw filename",
                "active_title": "模型先生谈科技股",
                "description": "由 model-video-drop 自动导入的本地下载文件。",
                "url": "https://www.douyin.com/video/12",
                "published_at": "2026-08-30T08:00:00+08:00",
                "has_video_text": True,
                "has_interpretation": False,
                "raw_json": '{"source_path":"H:/private/video.mp4"}',
                "comment_count": 300,
                "primary_asset": {"file_url": "/api/assets/99/file"},
                "keyword_info": {"keywords": ["科技", "AI"]},
            }
        )
        self.assertEqual(cleaned["title"], "模型先生谈科技股")
        self.assertEqual(cleaned["description"], "")
        self.assertEqual(cleaned["keywords"], ["科技", "AI"])
        self.assertNotIn("raw_json", cleaned)
        self.assertEqual(cleaned["comment_count"], 300)
        self.assertFalse(cleaned["media_available"])
        self.assertNotIn("primary_asset", cleaned)
        self.assertNotIn("source_path", str(cleaned))

    def test_unavailable_sidecar_returns_a_safe_module_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ModelMrClient("http://127.0.0.1:8787", Path(directory) / "missing.json")
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                status = client.status()
            self.assertFalse(status["available"])
            self.assertEqual(status["mode"], "independent-owner")
            self.assertNotIn("127.0.0.1", status["message"])

    def test_sanitized_snapshot_works_without_the_private_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "public-snapshot.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "exported_at": 123,
                        "works": [
                            {
                                "id": 1,
                                "title": "黄金策略",
                                "url": "https://example.com/1",
                                "keywords": ["黄金"],
                                "private_path": "H:/secret.mp4",
                            }
                        ],
                        "thoughts": [{"id": 2, "name": "趋势", "level": 1}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:8787", path)
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                status = client.status()
                works = client.works(limit=10)
                thoughts = client.thoughts(limit=10)
                chat = client.chat_config()

            self.assertTrue(status["available"])
            self.assertEqual(status["mode"], "sanitized-snapshot")
            self.assertEqual(works["items"][0]["title"], "黄金策略")
            self.assertNotIn("private_path", works["items"][0])
            self.assertEqual(thoughts["categories"][0]["name"], "趋势")
            self.assertFalse(chat["enabled"])

    def test_snapshot_works_supports_incremental_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "public-snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "works": [
                            {"id": work_id, "title": f"作品 {work_id}"}
                            for work_id in range(1, 56)
                        ],
                        "thoughts": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:9", snapshot, root / "media")
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                middle = client.works(limit=24, offset=24)
                last = client.works(limit=24, offset=48)

            self.assertEqual(middle["count"], 24)
            self.assertEqual(middle["total"], 55)
            self.assertEqual(middle["offset"], 24)
            self.assertTrue(middle["has_more"])
            self.assertEqual([item["id"] for item in middle["items"]], list(range(25, 49)))
            self.assertEqual(last["count"], 7)
            self.assertEqual(last["total"], 55)
            self.assertFalse(last["has_more"])
            self.assertEqual([item["id"] for item in last["items"]], list(range(49, 56)))

    def test_owner_works_http_endpoint_accepts_offset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "public-snapshot.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "works": [
                            {"id": work_id, "title": f"作品 {work_id}"}
                            for work_id in range(1, 31)
                        ],
                        "thoughts": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:9", root / "public-snapshot.json", root / "media")
            auth = OwnerAuth(required=False, path=root / "missing-auth.json")
            server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with (
                patch("instant_ai.server.MODEL_MR", client),
                patch("instant_ai.server.AUTH", auth),
                patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")),
            ):
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request("GET", "/api/model-mr/works?limit=5&offset=24")
                    response = connection.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(payload["offset"], 24)
                    self.assertEqual(payload["total"], 30)
                    self.assertTrue(payload["has_more"])
                    self.assertEqual([item["id"] for item in payload["items"]], [25, 26, 27, 28, 29])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_owner_library_serves_local_video_text_and_comments_without_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "public-snapshot.json"
            details = root / "details"
            media = root / "media" / "模型视频"
            details.mkdir()
            media.mkdir(parents=True)
            (media / "sample.mp4").write_bytes(b"video")
            snapshot.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "exported_at": 456,
                        "counts": {"works": 1, "media": 1, "transcripts": 1, "comments": 1},
                        "works": [
                            {
                                "id": 7,
                                "title": "长鑫科技",
                                "media_file": "模型视频/sample.mp4",
                                "media_available": True,
                                "has_video_text": True,
                                "comment_count": 1,
                            }
                        ],
                        "thoughts": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (details / "7.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "work": {
                            "id": 7,
                            "title": "长鑫科技",
                            "media_file": "模型视频/sample.mp4",
                            "media_available": True,
                        },
                        "video_text": {"text": "正式原文", "official": True},
                        "transcripts": [{"text": "豆包原文", "source": "doubao-recording-asr-2.0"}],
                        "comments": [
                            {
                                "author": "测试用户",
                                "text": "测试评论",
                                "raw_json": {"source_path": "H:/private", "thread_id": "private-id"},
                            }
                        ],
                        "comment_total": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:8787", snapshot, root / "media")
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                status = client.status()
                work = client.works(limit=10)["items"][0]
                detail = client.work_detail(7)
                transcription = client.transcribe(7, "doubao")
                video_path = client.video_path(7)

            self.assertEqual(status["mode"], "owner-mobile-library")
            self.assertEqual(work["video_url"], "/api/model-mr/works/7/video")
            self.assertEqual(video_path[0], media / "sample.mp4")
            self.assertEqual(detail["video_text"]["text"], "正式原文")
            self.assertEqual(detail["comments"][0]["text"], "测试评论")
            self.assertNotIn("private-id", str(detail))
            self.assertNotIn("H:/private", str(detail))
            self.assertTrue(transcription["cached"])
            self.assertEqual(transcription["text"], "豆包原文")

    def test_owner_video_endpoint_supports_private_byte_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "details").mkdir()
            (root / "media").mkdir()
            (root / "media" / "sample.mp4").write_bytes(b"video-data")
            (root / "public-snapshot.json").write_text(
                json.dumps({"version": 2, "works": [{"id": 9}], "thoughts": []}),
                encoding="utf-8",
            )
            (root / "details" / "9.json").write_text(
                json.dumps(
                    {
                        "work": {
                            "id": 9,
                            "title": "测试视频",
                            "media_file": "sample.mp4",
                            "media_available": True,
                        },
                        "comments": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:9", root / "public-snapshot.json", root / "media")
            auth = OwnerAuth(required=False, path=root / "missing-auth.json")
            server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch("instant_ai.server.MODEL_MR", client), patch("instant_ai.server.AUTH", auth):
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request(
                        "GET",
                        "/api/model-mr/works/9/video",
                        headers={"Range": "bytes=1-3"},
                    )
                    response = connection.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.getheader("Content-Range"), "bytes 1-3/10")
                    self.assertEqual(body, b"ide")
                    connection.request("HEAD", "/api/model-mr/works/9/video")
                    head = connection.getresponse()
                    head_body = head.read()
                    self.assertEqual(head.status, 200)
                    self.assertEqual(head.getheader("Content-Type"), "video/mp4")
                    self.assertEqual(head.getheader("Content-Length"), "10")
                    self.assertEqual(head.getheader("Accept-Ranges"), "bytes")
                    self.assertEqual(head_body, b"")
                    connection.request(
                        "HEAD",
                        "/api/model-mr/works/9/video",
                        headers={"Range": "bytes=0-1"},
                    )
                    range_head = connection.getresponse()
                    range_head_body = range_head.read()
                    self.assertEqual(range_head.status, 206)
                    self.assertEqual(range_head.getheader("Content-Range"), "bytes 0-1/10")
                    self.assertEqual(range_head.getheader("Content-Length"), "2")
                    self.assertEqual(range_head_body, b"")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_owner_library_can_run_live_doubao_asr_with_git_external_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "details").mkdir()
            (root / "media").mkdir()
            video = root / "media" / "sample.mp4"
            video.write_bytes(b"video")
            (root / "public-snapshot.json").write_text(
                json.dumps({"version": 2, "works": [{"id": 10}], "thoughts": []}),
                encoding="utf-8",
            )
            (root / "details" / "10.json").write_text(
                json.dumps(
                    {
                        "work": {"id": 10, "title": "识别测试", "media_file": "sample.mp4", "media_available": True},
                        "transcripts": [],
                        "comments": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:9", root / "public-snapshot.json", root / "media")
            result = {"text": "现场识别原文", "engine": "doubao-recording-asr-2.0", "cached": False, "message": "完成"}
            with (
                patch.dict(os.environ, {"INSTANT_AI_DOUBAO_ASR_API_KEY": "test-key"}),
                patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")),
                patch("instant_ai.model_mr.transcribe_video", return_value=result) as transcribe,
            ):
                detail = client.work_detail(10)
                transcription = client.transcribe(10, "doubao")

            self.assertTrue(detail["capabilities"]["doubao_asr"])
            self.assertFalse(transcription["cached"])
            self.assertEqual(transcription["text"], "现场识别原文")
            transcribe.assert_called_once_with(video, 10)

    def test_owner_library_preserves_comment_threads_and_sanitizes_stock_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "details").mkdir()
            (root / "media").mkdir()
            (root / "public-snapshot.json").write_text(
                json.dumps({"version": 2, "works": [{"id": 11, "title": "评论测试"}], "thoughts": []}),
                encoding="utf-8",
            )
            (root / "details" / "11.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "work": {"id": 11, "title": "评论测试"},
                        "comments": [
                            {
                                "id": 1,
                                "author": "粉丝",
                                "text": "中芯国际怎么看？",
                                "kind": "user_comment",
                                "reply_depth": 0,
                                "thread_key": "012345abcdef",
                                "author_liked": True,
                            },
                            {
                                "id": 2,
                                "author": "模型先生",
                                "text": "注意估值和周期。",
                                "kind": "author_reply",
                                "reply_depth": 1,
                                "thread_key": "012345abcdef",
                            },
                        ],
                        "stock_mentions": {
                            "total_comments": 2,
                            "items": [
                                {
                                    "rank": 1,
                                    "name": "中芯国际",
                                    "code": "688981",
                                    "comment_count": 1,
                                    "mention_count": 1,
                                    "fan_comment_count": 1,
                                    "author_comment_count": 0,
                                    "comment_ids": [1],
                                    "examples": ["中芯国际怎么看？"],
                                    "private_path": "H:/private/master.json",
                                }
                            ],
                            "api_used": True,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:9", root / "public-snapshot.json", root / "media")
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                detail = client.work_detail(11)

            self.assertEqual({item["thread_key"] for item in detail["comments"]}, {"012345abcdef"})
            self.assertTrue(detail["comments"][0]["author_liked"])
            self.assertEqual(detail["stock_mentions"]["items"][0]["name"], "中芯国际")
            self.assertEqual(detail["stock_mentions"]["items"][0]["comment_ids"], [1])
            self.assertFalse(detail["stock_mentions"]["api_used"])
            self.assertNotIn("private_path", str(detail))
            self.assertNotIn("H:/private", str(detail))

    def test_owner_library_title_edit_updates_detail_and_work_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "details").mkdir()
            (root / "media").mkdir()
            snapshot_path = root / "public-snapshot.json"
            snapshot_path.write_text(
                json.dumps({"version": 2, "works": [{"id": 12, "title": "旧标题"}], "thoughts": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "details" / "12.json").write_text(
                json.dumps({"version": 2, "work": {"id": 12, "title": "旧标题"}, "comments": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:9", snapshot_path, root / "media")
            with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
                result = client.save_title(12, "新标题")
                works = client.works(limit=10)
                detail = client.work_detail(12)

            self.assertEqual(result["title"], "新标题")
            self.assertEqual(works["items"][0]["title"], "新标题")
            self.assertEqual(detail["work"]["title"], "新标题")

    def test_owner_title_edit_http_endpoint_is_available_to_the_mobile_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "details").mkdir()
            (root / "media").mkdir()
            (root / "public-snapshot.json").write_text(
                json.dumps({"version": 2, "works": [{"id": 13, "title": "旧标题"}], "thoughts": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            (root / "details" / "13.json").write_text(
                json.dumps({"version": 2, "work": {"id": 13, "title": "旧标题"}, "comments": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            client = ModelMrClient("http://127.0.0.1:9", root / "public-snapshot.json", root / "media")
            auth = OwnerAuth(required=False, path=root / "missing-auth.json")
            server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with (
                patch("instant_ai.server.MODEL_MR", client),
                patch("instant_ai.server.AUTH", auth),
                patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")),
            ):
                thread.start()
                try:
                    body = json.dumps({"title": "手机新标题"}, ensure_ascii=False).encode("utf-8")
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request(
                        "POST",
                        "/api/model-mr/works/13/title",
                        body=body,
                        headers={
                            "Content-Type": "application/json; charset=utf-8",
                            "Content-Length": str(len(body)),
                            "X-Instant-AI": "1",
                        },
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(payload["title"], "手机新标题")
                    self.assertEqual(client.work_detail(13)["work"]["title"], "手机新标题")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
