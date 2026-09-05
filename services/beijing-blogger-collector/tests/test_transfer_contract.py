from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from mx_agent.transfer_contract import (
    COMMENT_ITEM_FIELDS,
    TransferContractError,
    V1_MEDIA_MIME_TYPES,
    build_comment_bundle,
    canonical_json_bytes,
    new_manifest,
    revision_decision,
    safe_cross_platform_filename,
    sha256_file,
    signed_headers,
    verify_request_signature,
)


CREATOR_UUID = "7de6f4d1-3bf6-4c8b-b2e4-3d8b285dcb5e"
CAPTURED_AT = "2026-08-30T10:05:00+08:00"


def sample_comments():
    return [
        {
            "source_comment_id": "reply-1",
            "parent_source_comment_id": "root-1",
            "root_source_comment_id": "root-1",
            "reply_to_comment_id": "root-1",
            "author_uid": "creator-sec-uid",
            "author": "贵族之路",
            "is_creator": True,
            "text": "作者回复",
            "like_count": 5,
            "reply_count": 0,
            "published_at": "2026-08-30T10:02:00+08:00",
            "captured_at": CAPTURED_AT,
            "kind": "author_reply",
            "section": "author_interaction",
            "author_liked": None,
            "low_value": False,
            "display_order": 20,
        },
        {
            "source_comment_id": "root-1",
            "parent_source_comment_id": "",
            "root_source_comment_id": "root-1",
            "reply_to_comment_id": "",
            "author_uid": "fan-sec-uid",
            "author": "公开用户",
            "is_creator": False,
            "text": "示例评论",
            "like_count": 3,
            "reply_count": 1,
            "published_at": "2026-08-30T10:01:00+08:00",
            "captured_at": CAPTURED_AT,
            "kind": "user_comment",
            "section": "author_interaction",
            "author_liked": True,
            "low_value": False,
            "display_order": 10,
        },
    ]


def sample_snapshot(comments=None):
    values = sample_comments() if comments is None else comments
    bundle, descriptor = build_comment_bundle(values)
    return bundle, {
        "snapshot_id": "snapshot-1",
        "captured_at": CAPTURED_AT,
        "complete": False,
        "expected_total": 3,
        "captured_count": len(values),
        "top_level_count": 1,
        "reply_groups": 1,
        "reply_groups_incomplete": 1,
        "missing_replies": 1,
        "orphan_replies": 0,
        "rules_version": "comment-rules/v1",
        "bundle": descriptor,
    }


def sample_manifest(**overrides):
    _bundle, snapshot = sample_snapshot()
    values = {
        "collector_node_id": "beijing-collector-1",
        "collector_key_id": "hmac-2026-01",
        "collector_version": "0.1.0",
        "source_sequence": 41,
        "creator": {
            "creator_id": CREATOR_UUID,
            "display_name": "贵族之路",
            "platform": "douyin",
            "platform_user_id": "MS4wLjABAAAA-example",
        },
        "work": {
            "platform": "douyin",
            "source_work_id": "7654321098765432100",
            "work_type": "video",
            "title": "示例作品",
            "description": "公开作品说明",
            "source_url": "https://www.douyin.com/video/7654321098765432100",
            "published_at": "2026-08-30T10:00:00+08:00",
        },
        "work_revision": 7,
        "media": [
            {
                "media_id": "b" * 64,
                "role": "cover",
                "filename": "封面.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 1024,
                "sha256": "b" * 64,
                "ordinal": 1,
            },
            {
                "media_id": "a" * 64,
                "role": "video",
                "filename": "sample.mp4",
                "mime_type": "video/mp4",
                "size_bytes": 2048,
                "sha256": "a" * 64,
                "ordinal": 0,
            },
        ],
        "comment_snapshot": snapshot,
        "captured_at": CAPTURED_AT,
    }
    values.update(overrides)
    return new_manifest(**values)


class TransferContractTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_has_stable_ids(self) -> None:
        first = sample_manifest()
        second = sample_manifest()
        self.assertEqual(first, second)
        self.assertEqual(len(first["transfer_id"]), 64)
        self.assertEqual(len(first["revision_sha256"]), 64)
        self.assertEqual(first["creator"]["creator_id"], CREATOR_UUID)
        self.assertEqual(first["collector"]["source_sequence"], 41)
        self.assertEqual(first["work"]["revision"], 7)
        self.assertEqual([item["role"] for item in first["media"]], ["video", "cover"])

    def test_comment_bundle_is_stable_sorted_and_preserves_tree_and_tristate(self) -> None:
        first_bytes, first_descriptor = build_comment_bundle(sample_comments())
        second_bytes, second_descriptor = build_comment_bundle(reversed(sample_comments()))
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first_descriptor, second_descriptor)
        rows = [
            json.loads(line)
            for line in gzip.decompress(first_bytes).decode("utf-8").splitlines()
        ]
        self.assertEqual([row["source_comment_id"] for row in rows], ["root-1", "reply-1"])
        self.assertEqual(rows[1]["parent_source_comment_id"], "root-1")
        self.assertEqual(rows[1]["root_source_comment_id"], "root-1")
        self.assertIsNone(rows[1]["author_liked"])
        self.assertTrue(rows[0]["author_liked"])

    def test_comment_bundle_has_exact_whitelist_and_no_account_or_local_leaks(self) -> None:
        expected_fields = (
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
        )
        self.assertEqual(COMMENT_ITEM_FIELDS, expected_fields)
        comments = sample_comments()
        comments[0].update(
            {
                "author_uid": "creator-account-id-must-not-leak",
                "sec_uid": "sec-uid-must-not-leak",
                "user_id": "user-id-must-not-leak",
                "raw_json": {"cookie": "cookie-must-not-leak"},
                "local_path": "C:/private/comments.json",
                "unknown_wire_field": "unknown-must-not-leak",
            }
        )
        bundle, _descriptor = build_comment_bundle(comments)
        rows = [
            json.loads(line)
            for line in gzip.decompress(bundle).decode("utf-8").splitlines()
        ]
        self.assertTrue(rows)
        self.assertTrue(all(tuple(row) == tuple(sorted(expected_fields)) for row in rows))
        encoded = gzip.decompress(bundle).decode("utf-8")
        for forbidden in (
            "author_uid",
            "creator-account-id-must-not-leak",
            "sec_uid",
            "sec-uid-must-not-leak",
            "user_id",
            "user-id-must-not-leak",
            "raw_json",
            "cookie-must-not-leak",
            "local_path",
            "C:/private",
            "unknown_wire_field",
            "unknown-must-not-leak",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, encoded)

    def test_v1_media_role_and_mime_matrix_is_frozen(self) -> None:
        self.assertEqual(
            V1_MEDIA_MIME_TYPES,
            {
                "video": frozenset({"video/mp4"}),
                "image": frozenset({"image/jpeg", "image/png", "image/webp"}),
                "cover": frozenset({"image/jpeg", "image/png", "image/webp"}),
            },
        )
        valid_pairs = (
            ("video", "video/mp4"),
            ("image", "image/jpeg"),
            ("image", "image/png"),
            ("image", "image/webp"),
            ("cover", "image/jpeg"),
            ("cover", "image/png"),
            ("cover", "image/webp"),
        )
        for index, (role, mime_type) in enumerate(valid_pairs):
            with self.subTest(valid=(role, mime_type)):
                manifest = sample_manifest(
                    media=[
                        {
                            "media_id": f"media-{index}",
                            "role": role,
                            "filename": f"asset-{index}.bin",
                            "mime_type": mime_type,
                            "size_bytes": 1,
                            "sha256": f"{index + 1:x}" * 64,
                        }
                    ]
                )
                self.assertEqual(manifest["media"][0]["mime_type"], mime_type)

        invalid_pairs = (
            ("audio", "audio/mpeg"),
            ("audio", "audio/mp4"),
            ("video", "application/octet-stream"),
            ("video", "application/mp4"),
            ("image", "application/octet-stream"),
            ("image", "image/gif"),
            ("video", "image/jpeg"),
            ("image", "video/mp4"),
        )
        for index, (role, mime_type) in enumerate(invalid_pairs):
            with self.subTest(invalid=(role, mime_type)), self.assertRaises(
                TransferContractError
            ):
                sample_manifest(
                    media=[
                        {
                            "media_id": f"invalid-{index}",
                            "role": role,
                            "filename": f"invalid-{index}.bin",
                            "mime_type": mime_type,
                            "size_bytes": 1,
                            "sha256": "f" * 64,
                        }
                    ]
                )

    def test_manifest_whitelists_fields_and_never_exports_local_paths(self) -> None:
        manifest = sample_manifest(
            work={
                "platform": "douyin",
                "source_work_id": "7654321098765432100",
                "work_type": "video",
                "title": "示例作品",
                "local_path": "C:/secret/video.mp4",
                "cookie": "forbidden",
            },
            media=[
                {
                    "media_id": "a" * 64,
                    "role": "video",
                    "filename": "sample.mp4",
                    "mime_type": "video/mp4",
                    "size_bytes": 2048,
                    "sha256": "a" * 64,
                    "local_path": "C:/secret/video.mp4",
                }
            ],
        )
        encoded = canonical_json_bytes(manifest).decode("utf-8")
        self.assertNotIn("local_path", encoded)
        self.assertNotIn("cookie", encoded)
        self.assertNotIn("C:/secret", encoded)

    def test_cross_platform_filename_rejects_both_separator_styles(self) -> None:
        self.assertEqual(safe_cross_platform_filename("视频.mp4"), "视频.mp4")
        for filename in ("../secret.mp4", "..\\secret.mp4", "CON.txt", "bad."):
            with self.subTest(filename=filename), self.assertRaises(TransferContractError):
                safe_cross_platform_filename(filename)

    def test_creator_id_must_be_uuid_and_platform_user_id_is_required(self) -> None:
        with self.assertRaises(TransferContractError):
            sample_manifest(
                creator={
                    "creator_id": "guizu-zhilu",
                    "display_name": "贵族之路",
                    "platform": "douyin",
                    "platform_user_id": "sec-uid",
                }
            )
        with self.assertRaises(TransferContractError):
            sample_manifest(
                creator={
                    "creator_id": CREATOR_UUID,
                    "display_name": "贵族之路",
                    "platform": "douyin",
                }
            )

    def test_snapshot_count_must_match_bundle(self) -> None:
        _bundle, snapshot = sample_snapshot()
        snapshot["captured_count"] = 99
        with self.assertRaises(TransferContractError):
            sample_manifest(comment_snapshot=snapshot)

    def test_revision_decision_handles_apply_duplicate_stale_and_conflict(self) -> None:
        digest = "a" * 64
        self.assertEqual(
            revision_decision(
                existing_revision=None,
                existing_sha256=None,
                incoming_revision=1,
                incoming_sha256=digest,
            ),
            "apply",
        )
        self.assertEqual(
            revision_decision(
                existing_revision=2,
                existing_sha256=digest,
                incoming_revision=2,
                incoming_sha256=digest,
            ),
            "duplicate",
        )
        self.assertEqual(
            revision_decision(
                existing_revision=3,
                existing_sha256=digest,
                incoming_revision=2,
                incoming_sha256="b" * 64,
            ),
            "stale",
        )
        with self.assertRaises(TransferContractError):
            revision_decision(
                existing_revision=2,
                existing_sha256=digest,
                incoming_revision=2,
                incoming_sha256="b" * 64,
            )

    def test_signature_binds_method_path_node_key_and_body(self) -> None:
        secret = b"unit-test-secret"
        body = canonical_json_bytes(sample_manifest())
        common = {
            "method": "POST",
            "path": "/internal/v1/transfers",
            "node_id": "beijing-collector-1",
            "key_id": "hmac-2026-01",
        }
        headers = signed_headers(
            secret,
            body,
            **common,
            now=1_800_000_000,
            nonce="0123456789abcdef0123456789abcdef",
        )
        verify_request_signature(
            secret,
            **common,
            timestamp=headers["X-Blogger-Timestamp"],
            nonce=headers["X-Blogger-Nonce"],
            body=body,
            body_sha256=headers["X-Blogger-Content-SHA256"],
            signature=headers["X-Blogger-Signature"],
            now=1_800_000_100,
        )
        for changed in (
            {"method": "PUT"},
            {"path": "/internal/v1/other"},
            {"node_id": "beijing-collector-2"},
            {"key_id": "hmac-2026-02"},
        ):
            with self.subTest(changed=changed), self.assertRaises(TransferContractError):
                verify_request_signature(
                    secret,
                    **{**common, **changed},
                    timestamp=headers["X-Blogger-Timestamp"],
                    nonce=headers["X-Blogger-Nonce"],
                    body=body,
                    body_sha256=headers["X-Blogger-Content-SHA256"],
                    signature=headers["X-Blogger-Signature"],
                    now=1_800_000_100,
                )

    def test_signature_nonce_claim_rejects_replay(self) -> None:
        secret = b"unit-test-secret"
        body = b"{}"
        common = {
            "method": "POST",
            "path": "/internal/v1/transfers",
            "node_id": "beijing-collector-1",
            "key_id": "hmac-2026-01",
        }
        headers = signed_headers(
            secret,
            body,
            **common,
            now=1_800_000_000,
            nonce="0123456789abcdef0123456789abcdef",
        )
        seen = set()

        def claim(node_id, nonce, _expires_at):
            key = (node_id, nonce)
            if key in seen:
                return False
            seen.add(key)
            return True

        arguments = {
            **common,
            "timestamp": headers["X-Blogger-Timestamp"],
            "nonce": headers["X-Blogger-Nonce"],
            "body": body,
            "body_sha256": headers["X-Blogger-Content-SHA256"],
            "signature": headers["X-Blogger-Signature"],
            "now": 1_800_000_100,
            "nonce_claim": claim,
        }
        verify_request_signature(secret, **arguments)
        with self.assertRaises(TransferContractError):
            verify_request_signature(secret, **arguments)

    def test_signature_rejects_expired_request(self) -> None:
        body = b"{}"
        common = {
            "method": "POST",
            "path": "/internal/v1/transfers",
            "node_id": "beijing-collector-1",
            "key_id": "hmac-2026-01",
        }
        headers = signed_headers(
            b"secret",
            body,
            **common,
            now=1_800_000_000,
            nonce="0123456789abcdef0123456789abcdef",
        )
        with self.assertRaises(TransferContractError):
            verify_request_signature(
                b"secret",
                **common,
                timestamp=headers["X-Blogger-Timestamp"],
                nonce=headers["X-Blogger-Nonce"],
                body=body,
                body_sha256=headers["X-Blogger-Content-SHA256"],
                signature=headers["X-Blogger-Signature"],
                now=1_800_000_301,
            )

    def test_file_hash_is_streamed_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.bin"
            path.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(path, chunk_size=1),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )


if __name__ == "__main__":
    unittest.main()
