from __future__ import annotations

import unittest
import json
import os
import tempfile
import http.client
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import URLError

from instant_ai.auth import OwnerAuth
from instant_ai.model_mr import ModelMrClient
from instant_ai.server import InstantAIHandler


class ModelMrGatewayTests(unittest.TestCase):
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
