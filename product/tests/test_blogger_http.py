from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.parse import quote

from instant_ai.blogger_http import BloggerTransferHTTP
from instant_ai.blogger_library import MODEL_MR_TRANSFER_CREATOR_ID
from instant_ai.blogger_ingest import (
    BloggerIngestError,
    DEFAULT_MANIFEST_PATH,
    HEADER_CONTENT_SHA256,
    HEADER_KEY_ID,
    HEADER_NODE_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    V1_COMMENT_ITEM_FIELDS,
    V1_MEDIA_MIME_TYPES,
    sign_manifest,
)
from instant_ai.server import (
    BoundedThreadingHTTPServer,
    InstantAIHandler,
    REQUEST_READ_TIMEOUT_SECONDS,
)
from instant_ai.model_mr import ModelMrClient
from instant_ai.model_mr_mcp import ModelMrMcpLibrary
from instant_ai.model_mr_transfer import ModelMrTransferProjector


NODE_ID = "beijing-collector-1"
KEY_ID = "hmac-2026-01"
SECRET = bytes(range(32))
NOW = 1_800_000_100
CREATOR_ID = "7de6f4d1-3bf6-4c8b-b2e4-3d8b285dcb5e"


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def gzip_bytes(plain: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as stream:
        stream.write(plain)
    return output.getvalue()


def comment_item(source_comment_id: str = "comment-1", *, display_order: int = 1) -> dict:
    return {
        "source_comment_id": source_comment_id,
        "parent_source_comment_id": "",
        "root_source_comment_id": source_comment_id,
        "reply_to_comment_id": "",
        "author": "公开用户",
        "is_creator": False,
        "text": "hello",
        "like_count": 0,
        "reply_count": 0,
        "published_at": "2026-08-30T10:00:00+08:00",
        "captured_at": "2026-08-30T10:05:00+08:00",
        "kind": "user_comment",
        "section": "fan_comment",
        "sentiment": "neutral",
        "risk_level": "normal",
        "author_liked": None,
        "low_value": False,
        "ip_label": "",
        "public_label": "",
        "actual_reply_user": "",
        "display_order": display_order,
    }


class ExplodingStream:
    def __init__(self) -> None:
        self.read_calls = 0

    def read(self, _size: int = -1) -> bytes:
        self.read_calls += 1
        raise AssertionError("unauthenticated request body was read")


def manifest_for(
    media_body: bytes,
    comment_plain: bytes,
    *,
    media_mime: str = "video/mp4",
    creator_id: str = CREATOR_ID,
    creator_name: str = "测试博主",
) -> dict:
    media_sha = hashlib.sha256(media_body).hexdigest()
    comment_body = gzip_bytes(comment_plain)
    comment_sha = hashlib.sha256(comment_body).hexdigest()
    item_count = comment_plain.count(b"\n")
    value = {
        "schema_version": "blogger-transfer/v1",
        "captured_at": "2026-08-30T10:05:00+08:00",
        "collector": {
            "node_id": NODE_ID,
            "key_id": KEY_ID,
            "version": "0.1.0",
            "source_sequence": 41,
        },
        "creator": {
            "creator_id": creator_id,
            "display_name": creator_name,
            "platform": "douyin",
            "platform_user_id": "MS4wLjABAAAA-example",
        },
        "work": {
            "platform": "douyin",
            "source_work_id": "7654321098765432100",
            "revision": 7,
            "work_type": "video",
            "title": "测试作品",
            "description": "公开描述",
            "source_url": "https://www.douyin.com/video/7654321098765432100",
            "cover_url": "",
            "published_at": "2026-08-30T10:00:00+08:00",
        },
        "media": [
            {
                "media_id": f"asset:1:{media_sha[:24]}",
                "role": "video",
                "filename": "source-controlled-name.mp4",
                "mime_type": media_mime,
                "size_bytes": len(media_body),
                "sha256": media_sha,
                "ordinal": 0,
            }
        ],
        "comment_snapshot": {
            "snapshot_id": "snapshot-1",
            "captured_at": "2026-08-30T10:05:00+08:00",
            "complete": True,
            "expected_total": item_count,
            "captured_count": item_count,
            "top_level_count": item_count,
            "reply_groups": 0,
            "reply_groups_incomplete": 0,
            "missing_replies": 0,
            "orphan_replies": 0,
            "rules_version": "comment-rules/v1",
            "bundle": {
                "bundle_id": comment_sha,
                "format": "blogger-comments/v1+ndjson",
                "content_encoding": "gzip",
                "item_count": item_count,
                "size_bytes": len(comment_body),
                "sha256": comment_sha,
                "uncompressed_size_bytes": len(comment_plain),
                "uncompressed_sha256": hashlib.sha256(comment_plain).hexdigest(),
            },
        },
    }
    revision_sha256 = hashlib.sha256(canonical(value)).hexdigest()
    identity = "\n".join(
        (
            NODE_ID,
            creator_id,
            value["work"]["platform"],
            value["work"]["source_work_id"],
            str(value["work"]["revision"]),
            revision_sha256,
        )
    ).encode("utf-8")
    value["revision_sha256"] = revision_sha256
    value["transfer_id"] = hashlib.sha256(identity).hexdigest()
    return value


class FakeOwnerAuth:
    required = True
    setup_required = False

    def __init__(self) -> None:
        self.session_calls = 0

    def session(self, cookie: str):
        self.session_calls += 1
        return object() if cookie == "owner=yes" else None


class BloggerTransferHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "blogger-agent"
        self.application = BloggerTransferHTTP(
            root=self.root,
            secrets={(NODE_ID, KEY_ID): SECRET},
            clock=lambda: NOW,
        )
        self.nonce = 1000
        self.media = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        self.comment_plain = canonical(comment_item()) + b"\n"
        self.manifest = manifest_for(self.media, self.comment_plain)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def headers(self, body: bytes, *, method: str, path: str, content_type: str) -> dict[str, str]:
        self.nonce += 1
        authentication = sign_manifest(
            SECRET,
            body,
            node_id=NODE_ID,
            key_id=KEY_ID,
            timestamp=NOW,
            nonce=f"{self.nonce:032x}",
            method=method,
            path=path,
        )
        return {
            **authentication.as_headers(),
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        }

    def call(self, method: str, path: str, body: bytes, *, content_type: str):
        return self.application.handle(
            method,
            path,
            self.headers(body, method=method, path=path, content_type=content_type),
            io.BytesIO(body),
        )

    def accept_manifest(self):
        body = canonical(self.manifest)
        response = self.call("POST", DEFAULT_MANIFEST_PATH, body, content_type="application/json")
        self.assertEqual(response.status, 202, response.payload)
        return response

    def artifact_paths(self) -> tuple[str, str, bytes]:
        transfer_id = self.manifest["transfer_id"]
        media_id = self.manifest["media"][0]["media_id"]
        bundle = self.manifest["comment_snapshot"]["bundle"]
        return (
            f"{DEFAULT_MANIFEST_PATH}/{transfer_id}/media/{quote(media_id, safe='')}",
            f"{DEFAULT_MANIFEST_PATH}/{transfer_id}/comments/{bundle['bundle_id']}",
            gzip_bytes(self.comment_plain),
        )

    def upload_all(self) -> None:
        media_path, comments_path, comments = self.artifact_paths()
        media = self.call("PUT", media_path, self.media, content_type="video/mp4")
        self.assertEqual(media.status, 201, media.payload)
        comment = self.call("PUT", comments_path, comments, content_type="application/gzip")
        self.assertEqual(comment.status, 201, comment.payload)

    def upload_comment_case(self, plain: bytes, *, case_number: int):
        application = BloggerTransferHTTP(
            root=Path(self.temporary.name) / f"comment-contract-{case_number}",
            secrets={(NODE_ID, KEY_ID): SECRET},
            clock=lambda: NOW,
        )
        manifest = manifest_for(self.media, plain)
        manifest_body = canonical(manifest)
        accepted = application.handle(
            "POST",
            DEFAULT_MANIFEST_PATH,
            self.headers(
                manifest_body,
                method="POST",
                path=DEFAULT_MANIFEST_PATH,
                content_type="application/json",
            ),
            io.BytesIO(manifest_body),
        )
        self.assertEqual(accepted.status, 202, accepted.payload)
        bundle = manifest["comment_snapshot"]["bundle"]
        path = f"{DEFAULT_MANIFEST_PATH}/{manifest['transfer_id']}/comments/{bundle['bundle_id']}"
        compressed = gzip_bytes(plain)
        return application.handle(
            "PUT",
            path,
            self.headers(
                compressed,
                method="PUT",
                path=path,
                content_type="application/gzip",
            ),
            io.BytesIO(compressed),
        )

    def test_end_to_end_artifacts_are_atomic_idempotent_and_complete_only_queues_approval(self) -> None:
        manifest_response = self.accept_manifest()
        self.assertEqual(
            [item["artifact_kind"] for item in manifest_response.payload["missing_artifacts"]],
            ["media", "comment_bundle"],
        )
        media_path, comments_path, comments = self.artifact_paths()
        first = self.call("PUT", media_path, self.media, content_type="video/mp4")
        self.assertEqual(first.status, 201)
        self.assertEqual(first.payload["status"], "verified")
        duplicate = self.call("PUT", media_path, self.media, content_type="video/mp4")
        self.assertEqual(duplicate.status, 200)
        self.assertEqual(duplicate.payload["status"], "duplicate")

        conflicting = bytearray(self.media)
        conflicting[-1] ^= 1
        conflict = self.call("PUT", media_path, bytes(conflicting), content_type="video/mp4")
        self.assertEqual(conflict.status, 409)
        self.assertEqual(conflict.payload["error_code"], "artifact_conflict")

        comment = self.call("PUT", comments_path, comments, content_type="application/gzip")
        self.assertEqual(comment.status, 201)
        complete_path = f"{DEFAULT_MANIFEST_PATH}/{self.manifest['transfer_id']}/complete"
        complete_body = canonical({"transfer_id": self.manifest["transfer_id"]})
        model_mr = Mock()
        model_mr.transcribe.side_effect = AssertionError("ASR called")
        with (
            patch("instant_ai.server.MODEL_MR", model_mr),
            patch("instant_ai.server.queue_analysis", side_effect=AssertionError("AI called")) as ai,
            patch(
                "instant_ai.doubao_asr.transcribe_video",
                side_effect=AssertionError("Doubao called"),
            ) as doubao,
        ):
            complete = self.call("POST", complete_path, complete_body, content_type="application/json")
        self.assertEqual(
            complete.payload,
            {
                "status": "completed",
                "receipt_id": self.manifest["transfer_id"],
                "transfer_id": self.manifest["transfer_id"],
                "transport_completed": True,
                "artifacts_verified": True,
                "intelligence_status": "awaiting_asr_approval",
            },
        )
        model_mr.transcribe.assert_not_called()
        ai.assert_not_called()
        doubao.assert_not_called()
        repeated = self.call("POST", complete_path, complete_body, content_type="application/json")
        self.assertEqual(repeated.status, 200)
        jobs = self.application.store.processing_jobs(self.manifest["transfer_id"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["processing_status"], "awaiting_asr_approval")
        stored = self.application.store.get_transfer(self.manifest["transfer_id"])
        self.assertEqual(stored["transport_status"], "transport_completed")
        self.assertEqual(stored["processing_status"], "awaiting_asr_approval")
        media_artifact = next(item for item in stored["artifacts"] if item["artifact_kind"] == "media")
        self.assertNotIn("source-controlled-name", media_artifact["stored_relative_path"])

    def test_complete_with_missing_artifacts_returns_409_and_never_creates_a_job(self) -> None:
        self.accept_manifest()
        path = f"{DEFAULT_MANIFEST_PATH}/{self.manifest['transfer_id']}/complete"
        body = canonical({"transfer_id": self.manifest["transfer_id"]})
        response = self.call("POST", path, body, content_type="application/json")
        self.assertEqual(response.status, 409)
        self.assertEqual(response.payload["error_code"], "artifacts_missing")
        self.assertEqual(self.application.store.processing_jobs(), [])

    def test_reserved_model_mr_transfer_projects_verified_video_comments_and_mcp_index(self) -> None:
        model_root = Path(self.temporary.name) / "model-mr"
        snapshot = model_root / "public-snapshot.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(
            json.dumps(
                {
                    "version": 2,
                    "works": [],
                    "thoughts": [],
                    "counts": {},
                }
            ),
            encoding="utf-8",
        )
        model_mr = ModelMrClient(
            "http://127.0.0.1:8787",
            snapshot,
            model_root / "media",
        )
        self.application.on_complete = ModelMrTransferProjector(
            blogger_root=self.root,
            model_mr=model_mr,
        ).project
        self.manifest = manifest_for(
            self.media,
            self.comment_plain,
            creator_id=MODEL_MR_TRANSFER_CREATOR_ID,
            creator_name="模型先生",
        )
        self.accept_manifest()
        self.upload_all()
        complete_path = f"{DEFAULT_MANIFEST_PATH}/{self.manifest['transfer_id']}/complete"
        complete_body = canonical({"transfer_id": self.manifest["transfer_id"]})

        response = self.call(
            "POST",
            complete_path,
            complete_body,
            content_type="application/json",
        )

        self.assertEqual(response.status, 200, response.payload)
        repeated = self.call(
            "POST",
            complete_path,
            complete_body,
            content_type="application/json",
        )
        self.assertEqual(repeated.status, 200, repeated.payload)
        with patch("instant_ai.model_mr.urlopen", side_effect=URLError("offline")):
            imported = model_mr.works(limit=10)
            self.assertEqual(imported["count"], 1)
            work = imported["items"][0]
            detail = model_mr.work_detail(work["id"])
            self.assertEqual(detail["work"]["title"], "测试作品")
            self.assertEqual(detail["comments"][0]["text"], "hello")
            self.assertIsNotNone(model_mr.video_path(work["id"]))
        mcp = ModelMrMcpLibrary(snapshot)
        search = mcp.search_works_for_mcp("最新", limit=1)
        self.assertEqual(search["items"][0]["record_id"], f"model-mr-work:{work['id']}")

    def test_comment_rows_freeze_beijing_v1_fields_types_boundaries_and_sorting(self) -> None:
        expected_fields = frozenset(
            {
                "source_comment_id",
                "parent_source_comment_id",
                "root_source_comment_id",
                "reply_to_comment_id",
                "author",
                "is_creator",
                "text",
                "like_count",
                "reply_count",
                "published_at",
                "captured_at",
                "kind",
                "section",
                "sentiment",
                "risk_level",
                "author_liked",
                "low_value",
                "ip_label",
                "public_label",
                "actual_reply_user",
                "display_order",
            }
        )
        self.assertEqual(V1_COMMENT_ITEM_FIELDS, expected_fields)
        self.assertNotIn("author_uid", V1_COMMENT_ITEM_FIELDS)

        malformed_rows: list[bytes] = []
        for forbidden in ("author_uid", "account_id", "raw_json", "local_path"):
            row = comment_item()
            row[forbidden] = "forbidden"
            malformed_rows.append(canonical(row) + b"\n")
        missing = comment_item()
        missing.pop("captured_at")
        malformed_rows.append(canonical(missing) + b"\n")
        wrong_integer = comment_item()
        wrong_integer["like_count"] = True
        malformed_rows.append(canonical(wrong_integer) + b"\n")
        too_long = comment_item()
        too_long["text"] = "x" * 20_001
        malformed_rows.append(canonical(too_long) + b"\n")
        bad_kind = comment_item()
        bad_kind["kind"] = "moderator_note"
        malformed_rows.append(canonical(bad_kind) + b"\n")
        bad_time = comment_item()
        bad_time["captured_at"] = "2026-08-30T10:05:00"
        malformed_rows.append(canonical(bad_time) + b"\n")
        bad_tristate = comment_item()
        bad_tristate["author_liked"] = "unknown"
        malformed_rows.append(canonical(bad_tristate) + b"\n")
        duplicate_key = canonical(comment_item())[:-1] + b',"source_comment_id":"comment-1"}\n'
        malformed_rows.append(duplicate_key)
        malformed_rows.append(
            canonical(comment_item()).replace(b'"like_count":0', b'"like_count":NaN') + b"\n"
        )
        malformed_rows.append(
            canonical(comment_item()).replace(b'"reply_count":0', b'"reply_count":Infinity') + b"\n"
        )
        duplicate_id = canonical(comment_item("same", display_order=1)) + b"\n" + canonical(
            comment_item("same", display_order=2)
        ) + b"\n"
        malformed_rows.append(duplicate_id)
        unsorted = canonical(comment_item("later", display_order=2)) + b"\n" + canonical(
            comment_item("earlier", display_order=1)
        ) + b"\n"
        malformed_rows.append(unsorted)

        for index, plain in enumerate(malformed_rows):
            with self.subTest(index=index):
                response = self.upload_comment_case(plain, case_number=index)
                self.assertEqual(response.status, 422, response.payload)
                self.assertEqual(response.payload["error_code"], "invalid_comment_bundle")

    def test_complete_rehashes_official_artifacts_and_rejects_same_length_tampering(self) -> None:
        self.accept_manifest()
        self.upload_all()
        stored = self.application.store.get_transfer(self.manifest["transfer_id"])
        media = next(item for item in stored["artifacts"] if item["artifact_kind"] == "media")
        target = self.root / media["stored_relative_path"]
        tampered = bytearray(target.read_bytes())
        tampered[-1] ^= 1
        self.assertEqual(len(tampered), target.stat().st_size)
        target.write_bytes(tampered)

        path = f"{DEFAULT_MANIFEST_PATH}/{self.manifest['transfer_id']}/complete"
        body = canonical({"transfer_id": self.manifest["transfer_id"]})
        response = self.call("POST", path, body, content_type="application/json")
        self.assertEqual(response.status, 409, response.payload)
        self.assertEqual(response.payload["error_code"], "artifact_conflict")
        self.assertEqual(self.application.store.processing_jobs(), [])
        self.assertEqual(
            self.application.store.get_transfer(self.manifest["transfer_id"])["transport_status"],
            "accepted",
        )

    def test_complete_rejects_a_symlink_instead_of_following_it(self) -> None:
        self.accept_manifest()
        self.upload_all()
        stored = self.application.store.get_transfer(self.manifest["transfer_id"])
        media = next(item for item in stored["artifacts"] if item["artifact_kind"] == "media")
        target = self.root / media["stored_relative_path"]
        replacement = self.root / "outside-artifact.mp4"
        replacement.write_bytes(target.read_bytes())
        target.unlink()
        try:
            os.symlink(replacement, target)
        except (NotImplementedError, OSError):
            self.skipTest("当前 Windows 环境不允许创建测试 symlink")

        path = f"{DEFAULT_MANIFEST_PATH}/{self.manifest['transfer_id']}/complete"
        body = canonical({"transfer_id": self.manifest["transfer_id"]})
        response = self.call("POST", path, body, content_type="application/json")
        self.assertEqual(response.status, 503, response.payload)
        self.assertEqual(response.payload["error_code"], "artifact_storage_error")
        self.assertEqual(self.application.store.processing_jobs(), [])

    def test_complete_verifier_rejects_symlink_metadata_in_any_parent(self) -> None:
        self.accept_manifest()
        self.upload_all()
        stored = self.application.store.get_transfer(self.manifest["transfer_id"])
        media = next(item for item in stored["artifacts"] if item["artifact_kind"] == "media")
        target = self.root / media["stored_relative_path"]
        linked_parent = target.parent
        original_lstat = os.lstat

        def parent_link_metadata(path):
            if Path(path) == linked_parent:
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
            return original_lstat(path)

        with (
            patch("instant_ai.blogger_ingest.os.supports_dir_fd", set()),
            patch("instant_ai.blogger_ingest.os.lstat", side_effect=parent_link_metadata),
            self.assertRaises(BloggerIngestError) as captured,
        ):
            self.application.store._verify_stored_artifact(media)
        self.assertEqual(captured.exception.code, "artifact_storage_error")

    def test_interrupted_upload_removes_staging_and_stays_missing(self) -> None:
        self.accept_manifest()
        media_path, _, _ = self.artifact_paths()
        headers = self.headers(self.media, method="PUT", path=media_path, content_type="video/mp4")
        response = self.application.handle("PUT", media_path, headers, io.BytesIO(self.media[:-3]))
        self.assertEqual(response.status, 400)
        self.assertEqual(response.payload["error_code"], "incomplete_body")
        self.assertEqual(list(self.application.store.staging_root.iterdir()), [])
        repeated = self.call(
            "POST",
            DEFAULT_MANIFEST_PATH,
            canonical(self.manifest),
            content_type="application/json",
        )
        self.assertIn(
            self.manifest["media"][0]["media_id"],
            {item["artifact_id"] for item in repeated.payload["missing_artifacts"]},
        )

    def test_query_chunked_and_wrong_exact_content_length_are_rejected(self) -> None:
        body = canonical(self.manifest)
        headers = self.headers(body, method="POST", path=DEFAULT_MANIFEST_PATH, content_type="application/json")
        query = self.application.handle(
            "POST",
            DEFAULT_MANIFEST_PATH + "?debug=1",
            headers,
            io.BytesIO(body),
        )
        self.assertEqual(query.status, 400)
        self.assertEqual(query.payload["error_code"], "query_not_allowed")
        chunked_headers = dict(headers)
        chunked_headers["Transfer-Encoding"] = "chunked"
        chunked = self.application.handle(
            "POST",
            DEFAULT_MANIFEST_PATH,
            chunked_headers,
            io.BytesIO(body),
        )
        self.assertEqual(chunked.status, 400)
        self.assertEqual(chunked.payload["error_code"], "chunked_not_allowed")

        self.accept_manifest()
        media_path, _, _ = self.artifact_paths()
        wrong = self.headers(self.media, method="PUT", path=media_path, content_type="video/mp4")
        wrong["Content-Length"] = str(len(self.media) - 1)
        length = self.application.handle("PUT", media_path, wrong, io.BytesIO(self.media))
        self.assertEqual(length.status, 409)
        self.assertEqual(length.payload["error_code"], "artifact_length_mismatch")

    def test_manifest_and_complete_authenticate_declared_digest_before_reading_body(self) -> None:
        manifest_body = canonical(self.manifest)
        manifest_headers = self.headers(
            manifest_body,
            method="POST",
            path=DEFAULT_MANIFEST_PATH,
            content_type="application/json",
        )
        manifest_headers[HEADER_SIGNATURE] = "0" * 64
        manifest_stream = ExplodingStream()
        manifest_response = self.application.handle(
            "POST",
            DEFAULT_MANIFEST_PATH,
            manifest_headers,
            manifest_stream,
        )
        self.assertEqual(manifest_response.status, 401, manifest_response.payload)
        self.assertEqual(manifest_response.payload["error_code"], "invalid_signature")
        self.assertEqual(manifest_stream.read_calls, 0)

        transfer_id = self.manifest["transfer_id"]
        complete_path = f"{DEFAULT_MANIFEST_PATH}/{transfer_id}/complete"
        complete_body = canonical({"transfer_id": transfer_id})
        complete_headers = self.headers(
            complete_body,
            method="POST",
            path=complete_path,
            content_type="application/json",
        )
        complete_headers[HEADER_NODE_ID] = "unknown-collector"
        complete_stream = ExplodingStream()
        complete_response = self.application.handle(
            "POST",
            complete_path,
            complete_headers,
            complete_stream,
        )
        self.assertEqual(complete_response.status, 401, complete_response.payload)
        self.assertEqual(complete_response.payload["error_code"], "invalid_signature")
        self.assertEqual(complete_stream.read_calls, 0)

    def test_shared_server_sets_idle_read_timeout_and_rejects_over_capacity(self) -> None:
        server = BoundedThreadingHTTPServer.__new__(BoundedThreadingHTTPServer)
        accepted_socket = Mock()
        with patch(
            "socketserver.TCPServer.get_request",
            return_value=(accepted_socket, ("127.0.0.1", 12345)),
        ):
            request, address = server.get_request()
        self.assertIs(request, accepted_socket)
        self.assertEqual(address, ("127.0.0.1", 12345))
        accepted_socket.settimeout.assert_called_once_with(REQUEST_READ_TIMEOUT_SECONDS)

        server._request_slots = threading.BoundedSemaphore(1)
        self.assertTrue(server._request_slots.acquire(blocking=False))
        server.shutdown_request = Mock()
        rejected_socket = Mock()
        server.process_request(rejected_socket, ("127.0.0.1", 54321))
        sent = rejected_socket.sendall.call_args.args[0]
        self.assertIn(b"503 Service Unavailable", sent)
        self.assertIn(b'"server_overloaded"', sent)
        server.shutdown_request.assert_called_once_with(rejected_socket)

    def test_git_external_environment_configuration_is_all_or_nothing(self) -> None:
        self.assertIsNone(BloggerTransferHTTP.from_environment({}))
        with self.assertRaises(BloggerIngestError):
            BloggerTransferHTTP.from_environment({"INSTANT_AI_BLOGGER_NODE_ID": NODE_ID})
        configured = BloggerTransferHTTP.from_environment(
            {
                "INSTANT_AI_BLOGGER_NODE_ID": NODE_ID,
                "INSTANT_AI_BLOGGER_KEY_ID": KEY_ID,
                "INSTANT_AI_BLOGGER_HMAC_SECRET_HEX": SECRET.hex(),
                "INSTANT_AI_BLOGGER_ROOT": str(Path(self.temporary.name) / "configured"),
            },
            clock=lambda: NOW,
        )
        self.assertIsNotNone(configured)
        self.assertNotIn(SECRET, configured.store.database_path.read_bytes())

    def test_media_signature_and_comment_uncompressed_contract_are_enforced(self) -> None:
        invalid_media = b"not-an-mp4-file"
        self.manifest = manifest_for(invalid_media, self.comment_plain)
        self.media = invalid_media
        self.accept_manifest()
        media_path, _, _ = self.artifact_paths()
        media = self.call("PUT", media_path, invalid_media, content_type="video/mp4")
        self.assertEqual(media.status, 422)
        self.assertEqual(media.payload["error_code"], "invalid_media_signature")
        self.assertEqual(list(self.application.store.staging_root.iterdir()), [])

        other_root = Path(self.temporary.name) / "comment-count"
        application = BloggerTransferHTTP(
            root=other_root,
            secrets={(NODE_ID, KEY_ID): SECRET},
            clock=lambda: NOW,
        )
        malformed = manifest_for(self.media, self.comment_plain)
        malformed["comment_snapshot"]["captured_count"] = 2
        malformed["comment_snapshot"]["expected_total"] = 2
        malformed["comment_snapshot"]["top_level_count"] = 2
        malformed["comment_snapshot"]["bundle"]["item_count"] = 2
        unsigned = dict(malformed)
        unsigned.pop("revision_sha256")
        unsigned.pop("transfer_id")
        revision = hashlib.sha256(canonical(unsigned)).hexdigest()
        malformed["revision_sha256"] = revision
        malformed["transfer_id"] = hashlib.sha256(
            "\n".join((NODE_ID, CREATOR_ID, "douyin", "7654321098765432100", "7", revision)).encode()
        ).hexdigest()
        manifest_body = canonical(malformed)
        manifest_response = application.handle(
            "POST",
            DEFAULT_MANIFEST_PATH,
            self.headers(manifest_body, method="POST", path=DEFAULT_MANIFEST_PATH, content_type="application/json"),
            io.BytesIO(manifest_body),
        )
        self.assertEqual(manifest_response.status, 202)
        bundle = malformed["comment_snapshot"]["bundle"]
        path = f"{DEFAULT_MANIFEST_PATH}/{malformed['transfer_id']}/comments/{bundle['bundle_id']}"
        compressed = gzip_bytes(self.comment_plain)
        response = application.handle(
            "PUT",
            path,
            self.headers(compressed, method="PUT", path=path, content_type="application/gzip"),
            io.BytesIO(compressed),
        )
        self.assertEqual(response.status, 422)
        self.assertEqual(response.payload["error_code"], "comment_count_mismatch")

    def test_server_handler_keeps_machine_and_owner_authorization_disjoint(self) -> None:
        owner_auth = FakeOwnerAuth()

        def request(method: str, path: str, body: bytes, headers: dict[str, str]):
            handler = InstantAIHandler.__new__(InstantAIHandler)
            handler.command = method
            handler.path = path
            message = Message()
            message["Host"] = "127.0.0.1"
            for name, value in headers.items():
                message[name] = value
            handler.headers = message
            handler.rfile = io.BytesIO(body)
            handler.wfile = io.BytesIO()
            handler.server = SimpleNamespace(blogger_transfer=self.application)
            handler.client_address = ("127.0.0.1", 12345)
            handler.close_connection = False
            statuses: list[int] = []
            handler.send_response = lambda status, *args, **kwargs: statuses.append(int(status))
            handler.send_header = lambda *args, **kwargs: None
            handler.end_headers = lambda: None
            getattr(handler, f"do_{method}")()
            return statuses[-1], json.loads(handler.wfile.getvalue().decode("utf-8"))

        manifest_body = canonical(self.manifest)
        machine_headers = self.headers(
            manifest_body,
            method="POST",
            path=DEFAULT_MANIFEST_PATH,
            content_type="application/json",
        )
        with patch("instant_ai.server.AUTH", owner_auth), patch(
            "instant_ai.server.create_backup", Mock(side_effect=AssertionError("owner API called"))
        ):
            status, _ = request("POST", DEFAULT_MANIFEST_PATH, manifest_body, machine_headers)
            self.assertEqual(status, 202)
            self.assertEqual(owner_auth.session_calls, 0)

            api_status, _ = request(
                "POST",
                "/api/backup",
                b"{}",
                {**machine_headers, "Content-Length": "2", "Content-Type": "application/json"},
            )
            self.assertEqual(api_status, 403)

            get_status, _ = request("GET", "/api/status", b"", machine_headers)
            self.assertEqual(get_status, 401)

            owner_headers = {
                "X-Instant-AI": "1",
                "Cookie": "owner=yes",
                "Content-Length": "2",
                "Content-Type": "application/json",
            }
            internal_status, payload = request(
                "POST",
                DEFAULT_MANIFEST_PATH,
                b"{}",
                owner_headers,
            )
            self.assertEqual(internal_status, 401)
            self.assertEqual(payload["error_code"], "invalid_authentication")
            self.assertEqual(owner_auth.session_calls, 1)


if __name__ == "__main__":
    unittest.main()
