from __future__ import annotations

import json
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from mx_agent.transfer_contract import build_comment_bundle, new_manifest, sha256_bytes
from mx_agent.transfer_outbox import TransferOutbox
from mx_agent.transfer_sender import (
    HTTPSCollectorTransport,
    TransferDeliveryError,
    TransferSender,
    TransportResponse,
)


class FakeTransport:
    def __init__(self, *, fail_status: int | None = None) -> None:
        self.fail_status = fail_status
        self.calls: list[tuple[str, str, bytes | Path]] = []
        self.manifest = None

    def send_bytes(self, method, path, body, *, content_type):
        self.calls.append((method, path, body))
        if self.fail_status:
            return TransportResponse(self.fail_status, {"error_code": "receiver_error"})
        if path.endswith("/complete"):
            transfer_id = json.loads(body)["transfer_id"]
            return TransportResponse(
                200,
                {
                    "status": "completed",
                    "receipt_id": "receipt-1",
                    "transfer_id": transfer_id,
                    "transport_completed": True,
                    "artifacts_verified": True,
                    "intelligence_status": "awaiting_asr_approval",
                },
            )
        self.manifest = json.loads(body)
        missing = [
            {"artifact_id": item["media_id"], "artifact_kind": "media"}
            for item in sorted(self.manifest["media"], key=lambda item: item["media_id"])
        ]
        bundle = self.manifest["comment_snapshot"]["bundle"]
        missing.append(
            {"artifact_id": bundle["bundle_id"], "artifact_kind": "comment_bundle"}
        )
        revision = self.manifest["work"]["revision"]
        return TransportResponse(
            202,
            {
                "status": "accepted",
                "receipt_id": "receipt-1",
                "transfer_id": self.manifest["transfer_id"],
                "revision_sha256": self.manifest["revision_sha256"],
                "work_revision": revision,
                "current_revision": revision,
                "missing_artifacts": missing,
            },
        )

    def send_file(self, method, path, local_path, *, size_bytes, sha256, content_type):
        self.calls.append((method, path, local_path))
        artifact_id = unquote(path.rsplit("/", 1)[-1])
        transfer_id = unquote(path.split("/")[4])
        artifact_kind = "comment_bundle" if "/comments/" in path else "media"
        return TransportResponse(
            201,
            {
                "status": "verified",
                "receipt_id": "receipt-1",
                "transfer_id": transfer_id,
                "artifact_id": artifact_id,
                "artifact_kind": artifact_kind,
                "size_bytes": size_bytes,
                "sha256": sha256,
            },
        )


class MutatingTransport(FakeTransport):
    def __init__(self, stage, mutate):
        super().__init__()
        self.stage = stage
        self.mutate = mutate

    def send_bytes(self, method, path, body, *, content_type):
        response = super().send_bytes(method, path, body, content_type=content_type)
        stage = "complete" if path.endswith("/complete") else "manifest"
        if stage == self.stage and 200 <= response.status < 300:
            payload = dict(response.payload)
            self.mutate(payload)
            return TransportResponse(response.status, payload)
        return response

    def send_file(self, method, path, local_path, *, size_bytes, sha256, content_type):
        response = super().send_file(
            method,
            path,
            local_path,
            size_bytes=size_bytes,
            sha256=sha256,
            content_type=content_type,
        )
        if self.stage == "artifact":
            payload = dict(response.payload)
            self.mutate(payload)
            return TransportResponse(response.status, payload)
        return response


class WireResponse:
    status = 201

    def __init__(self):
        self._body = b"{}"

    def read(self, _limit):
        body, self._body = self._body, b""
        return body


class RecordingConnection:
    def __init__(self, *, on_send=None):
        self.sent = bytearray()
        self.closed = False
        self.on_send = on_send

    def request(self, *_args, **_kwargs):
        return None

    def putrequest(self, *_args, **_kwargs):
        return None

    def putheader(self, *_args, **_kwargs):
        return None

    def endheaders(self):
        return None

    def send(self, chunk):
        self.sent.extend(chunk)
        if self.on_send is not None:
            callback, self.on_send = self.on_send, None
            callback()

    def getresponse(self):
        return WireResponse()

    def close(self):
        self.closed = True


class TLSFailureConnection(RecordingConnection):
    def request(self, *_args, **_kwargs):
        raise ssl.SSLCertVerificationError(1, "certificate verify failed")


def sample_manifest(sequence: int, revision: int, bundle_descriptor: dict) -> dict:
    return new_manifest(
        collector_node_id="beijing-collector-1",
        collector_key_id="key-1",
        collector_version="0.1.0",
        source_sequence=sequence,
        creator={
            "creator_id": "5d64af4e-72d6-4437-9c9c-84548c5ac209",
            "display_name": "测试博主",
            "platform": "douyin",
            "platform_user_id": "MS4wLjABAAAA-test",
        },
        work={
            "platform": "douyin",
            "source_work_id": "7390000000000000001",
            "work_type": "video",
            "title": "测试作品",
            "description": "",
            "source_url": "https://www.douyin.com/video/7390000000000000001",
            "cover_url": "",
            "published_at": "2026-08-30T10:00:00+08:00",
        },
        work_revision=revision,
        media=[],
        comment_snapshot={
            "snapshot_id": "snapshot-1",
            "captured_at": "2026-08-30T10:05:00+08:00",
            "complete": True,
            "expected_total": 0,
            "captured_count": 0,
            "top_level_count": 0,
            "reply_groups": 0,
            "reply_groups_incomplete": 0,
            "missing_replies": 0,
            "orphan_replies": 0,
            "rules_version": "comment-rules/v1",
            "bundle": bundle_descriptor,
        },
    )


class TransferSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.outbox = TransferOutbox(self.root / "outbox.sqlite3")
        bundle, descriptor = build_comment_bundle([])
        self.bundle_path = self.root / "comments.ndjson.gz"
        self.bundle_path.write_bytes(bundle)
        reservation = self.outbox.reserve(
            node_id="beijing-collector-1",
            creator_id="5d64af4e-72d6-4437-9c9c-84548c5ac209",
            platform="douyin",
            source_work_id="7390000000000000001",
        )
        manifest = sample_manifest(
            reservation.source_sequence,
            reservation.work_revision,
            descriptor,
        )
        self.transfer_id = manifest["transfer_id"]
        self.outbox.enqueue(
            manifest,
            artifacts=[
                {
                    "artifact_id": descriptor["bundle_id"],
                    "artifact_kind": "comment_bundle",
                    "local_path": str(self.bundle_path),
                    "size_bytes": len(bundle),
                    "sha256": sha256_bytes(bundle),
                }
            ],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_delivers_manifest_bundle_and_complete(self) -> None:
        transport = FakeTransport()
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "delivered")
        self.assertEqual([call[0] for call in transport.calls], ["POST", "PUT", "POST"])
        self.assertEqual(self.outbox.artifacts_for(self.transfer_id)[0]["status"], "verified")

    def test_transient_server_error_is_retried(self) -> None:
        result = TransferSender(self.outbox, FakeTransport(fail_status=503)).run_once()
        self.assertEqual(result[0]["status"], "retry_wait")
        self.assertEqual(result[0]["last_error_code"], "receiver_error")

    def test_authentication_error_enters_dead_letter(self) -> None:
        result = TransferSender(self.outbox, FakeTransport(fail_status=401)).run_once()
        self.assertEqual(result[0]["status"], "dead_letter")

    def test_in_progress_row_is_resumed_after_process_restart(self) -> None:
        self.outbox.transition(
            self.transfer_id,
            "manifest_accepted",
            receipt_id="receipt-1",
        )
        transport = FakeTransport()
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "delivered")
        self.assertEqual([call[0] for call in transport.calls], ["PUT", "POST"])

    def test_manifest_2xx_identity_mismatch_is_dead_lettered(self) -> None:
        transport = MutatingTransport(
            "manifest", lambda payload: payload.__setitem__("transfer_id", "wrong")
        )
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "dead_letter")
        self.assertEqual(result[0]["last_error_code"], "invalid_receipt")

    def test_artifact_2xx_hash_mismatch_is_dead_lettered(self) -> None:
        transport = MutatingTransport(
            "artifact", lambda payload: payload.__setitem__("sha256", "f" * 64)
        )
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "dead_letter")
        self.assertNotEqual(
            self.outbox.artifacts_for(self.transfer_id)[0]["status"], "verified"
        )

    def test_complete_requires_transport_flags_and_asr_approval_state(self) -> None:
        transport = MutatingTransport(
            "complete",
            lambda payload: payload.__setitem__("intelligence_status", "queued_intelligence"),
        )
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "dead_letter")
        self.assertEqual(result[0]["last_error_code"], "invalid_receipt")

    def test_only_declared_missing_artifacts_are_uploaded(self) -> None:
        transport = MutatingTransport(
            "manifest", lambda payload: payload.__setitem__("missing_artifacts", [])
        )
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "delivered")
        self.assertEqual([call[0] for call in transport.calls], ["POST", "POST"])
        self.assertEqual(
            self.outbox.artifacts_for(self.transfer_id)[0]["status"], "verified"
        )

    def test_bad_file_is_redacted_and_does_not_block_next_transfer(self) -> None:
        second_path = self.root / "comments-second.ndjson.gz"
        second_path.write_bytes(self.bundle_path.read_bytes())
        first_manifest = self.outbox.get(self.transfer_id)["manifest"]
        second_manifest = sample_manifest(2, 2, first_manifest["comment_snapshot"]["bundle"])
        self.outbox.enqueue(
            second_manifest,
            artifacts=[
                {
                    "artifact_id": second_manifest["comment_snapshot"]["bundle"]["bundle_id"],
                    "artifact_kind": "comment_bundle",
                    "local_path": str(second_path),
                    "size_bytes": second_path.stat().st_size,
                    "sha256": sha256_bytes(second_path.read_bytes()),
                }
            ],
        )
        self.bundle_path.unlink()
        result = TransferSender(self.outbox, FakeTransport()).run_once(limit=2)
        self.assertEqual([item["status"] for item in result], ["dead_letter", "delivered"])
        self.assertNotIn(str(self.root), result[0]["last_error_message"])

    def test_tls_certificate_failure_is_permanent(self) -> None:
        transport = HTTPSCollectorTransport(
            "https://receiver.example.invalid",
            node_id="beijing-collector-1",
            key_id="key-1",
            secret=b"x" * 32,
            connection_factory=TLSFailureConnection,
        )
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "dead_letter")
        self.assertEqual(result[0]["last_error_code"], "tls_verification_failed")

    def test_receipt_id_rejects_control_characters(self) -> None:
        transport = MutatingTransport(
            "manifest", lambda payload: payload.__setitem__("receipt_id", "bad\nreceipt")
        )
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "dead_letter")
        self.assertEqual(result[0]["last_error_code"], "invalid_receipt")

    def test_receipt_id_rejects_more_than_128_characters(self) -> None:
        transport = MutatingTransport(
            "manifest", lambda payload: payload.__setitem__("receipt_id", "r" * 129)
        )
        result = TransferSender(self.outbox, transport).run_once()
        self.assertEqual(result[0]["status"], "dead_letter")
        self.assertEqual(result[0]["last_error_code"], "invalid_receipt")

    def test_send_file_rechecks_same_descriptor_after_streaming(self) -> None:
        payload = b"stable artifact bytes"
        path = self.root / "wire-artifact.bin"
        path.write_bytes(payload)
        connection = RecordingConnection()
        transport = HTTPSCollectorTransport(
            "https://receiver.example.invalid",
            node_id="beijing-collector-1",
            key_id="key-1",
            secret=b"x" * 32,
            connection_factory=lambda: connection,
        )
        response = transport.send_file(
            "PUT",
            "/internal/v1/transfers/t/media/a",
            path,
            size_bytes=len(payload),
            sha256=sha256_bytes(payload),
            content_type="application/octet-stream",
        )
        self.assertEqual(response.status, 201)
        self.assertEqual(bytes(connection.sent), payload)
        self.assertTrue(connection.closed)

    def test_send_file_detects_in_place_change_without_leaking_path(self) -> None:
        payload = b"original artifact bytes"
        path = self.root / "mutating-artifact.bin"
        path.write_bytes(payload)
        connection = RecordingConnection(on_send=lambda: path.write_bytes(b"tampered"))
        transport = HTTPSCollectorTransport(
            "https://receiver.example.invalid",
            node_id="beijing-collector-1",
            key_id="key-1",
            secret=b"x" * 32,
            connection_factory=lambda: connection,
        )
        with self.assertRaises(TransferDeliveryError) as caught:
            transport.send_file(
                "PUT",
                "/internal/v1/transfers/t/media/a",
                path,
                size_bytes=len(payload),
                sha256=sha256_bytes(payload),
                content_type="application/octet-stream",
            )
        self.assertEqual(caught.exception.code, "artifact_changed")
        self.assertNotIn(str(path), str(caught.exception))

    @unittest.skipUnless(os.name == "posix", "O_NOFOLLOW is a POSIX contract")
    def test_send_file_rejects_symlink_with_no_path_disclosure(self) -> None:
        payload = b"artifact"
        target = self.root / "target.bin"
        target.write_bytes(payload)
        link = self.root / "link.bin"
        link.symlink_to(target)
        transport = HTTPSCollectorTransport(
            "https://receiver.example.invalid",
            node_id="beijing-collector-1",
            key_id="key-1",
            secret=b"x" * 32,
            connection_factory=RecordingConnection,
        )
        with self.assertRaises(TransferDeliveryError) as caught:
            transport.send_file(
                "PUT",
                "/internal/v1/transfers/t/media/a",
                link,
                size_bytes=len(payload),
                sha256=sha256_bytes(payload),
                content_type="application/octet-stream",
            )
        self.assertEqual(caught.exception.code, "artifact_missing")
        self.assertNotIn(str(link), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
