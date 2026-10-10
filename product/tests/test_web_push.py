from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from instant_ai.collectors import Entry, Source
from instant_ai.database import connect, initialize, transaction, utc_now
from instant_ai.service import (
    _upsert_entry,
    dispatch_web_push_notifications,
    save_web_push_subscription,
)
from instant_ai.web_push import (
    PushResult,
    WebPushSender,
    _hkdf_expand,
    _hkdf_extract,
    base64url_decode,
    base64url_encode,
    validate_endpoint,
    validate_subscription,
)


VALID_SUBSCRIPTION = {
    "endpoint": "https://web.push.apple.com/QB-test-owner-device",
    "keys": {
        "p256dh": base64url_encode(b"\x04" + b"p" * 64),
        "auth": base64url_encode(b"a" * 16),
    },
}


class FakeSender:
    available = True

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def public_key(self) -> str:
        return base64url_encode(b"\x04" + b"v" * 64)

    def send(self, subscription: dict[str, str], notification: dict[str, object]) -> PushResult:
        self.sent.append({"subscription": subscription, "notification": notification})
        return PushResult(True, 201, False, "")


class WebPushTests(unittest.TestCase):
    def test_vapid_key_is_stable_and_private_in_the_runtime_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "notifications" / "vapid-private.pem"
            first = WebPushSender(key_path).public_key()
            second = WebPushSender(key_path).public_key()
            self.assertEqual(first, second)
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(first, key_path.read_text(encoding="ascii"))

    def test_vapid_authorization_is_bound_to_the_push_service_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sender = WebPushSender(Path(directory) / "vapid.pem")
            private_key = sender._private_key()
            authorization = sender._authorization(VALID_SUBSCRIPTION["endpoint"], private_key)
            token, public_text = authorization.removeprefix("vapid t=").split(", k=")
            header, claims_text, signature_text = token.split(".")
            claims = json.loads(base64url_decode(claims_text))
            self.assertEqual(claims["aud"], "https://web.push.apple.com")
            self.assertEqual(claims["sub"], "https://grandpaamu.com/")
            self.assertGreater(claims["exp"], int(time.time()))
            signature = base64url_decode(signature_text)
            der = encode_dss_signature(
                int.from_bytes(signature[:32], "big"),
                int.from_bytes(signature[32:], "big"),
            )
            private_key.public_key().verify(
                der,
                f"{header}.{claims_text}".encode("ascii"),
                ec.ECDSA(hashes.SHA256()),
            )
            self.assertEqual(public_text, sender.public_key())

    def test_rfc8291_payload_can_be_decrypted_by_the_subscribed_device(self) -> None:
        user_private = ec.generate_private_key(ec.SECP256R1())
        user_public = user_private.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        auth_secret = b"s" * 16
        plaintext = b'{"title":"Instant AI","body":"important"}'
        encrypted = WebPushSender._encrypt(plaintext, user_public, auth_secret)

        salt = encrypted[:16]
        self.assertEqual(int.from_bytes(encrypted[16:20], "big"), 4096)
        key_length = encrypted[20]
        server_public = encrypted[21:21 + key_length]
        ciphertext = encrypted[21 + key_length:]
        server_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public)
        shared = user_private.exchange(ec.ECDH(), server_key)
        auth_prk = _hkdf_extract(auth_secret, shared)
        ikm = _hkdf_expand(auth_prk, b"WebPush: info\x00" + user_public + server_public, 32)
        content_prk = _hkdf_extract(salt, ikm)
        content_key = _hkdf_expand(content_prk, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = _hkdf_expand(content_prk, b"Content-Encoding: nonce\x00", 12)
        self.assertEqual(AESGCM(content_key).decrypt(nonce, ciphertext, None), plaintext + b"\x02")

    def test_subscription_validation_rejects_arbitrary_server_targets(self) -> None:
        checked = validate_subscription(VALID_SUBSCRIPTION)
        self.assertEqual(checked["endpoint"], VALID_SUBSCRIPTION["endpoint"])
        for endpoint in (
            "http://web.push.apple.com/test",
            "https://127.0.0.1/test",
            "https://example.com/test",
            "https://web.push.apple.com:8443/test",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_endpoint(endpoint)

    def test_specialist_alert_is_sent_once_after_device_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "push.db"
            initialize(database)
            sender = FakeSender()
            saved = save_web_push_subscription(VALID_SUBSCRIPTION, database, sender=sender)
            self.assertTrue(saved["test_sent"])
            self.assertEqual(len(sender.sent), 1)

            source = Source(
                1,
                "the-elec-semiconductor",
                "THE ELEC 半导体专业报道（韩国）",
                "rss",
                "https://www.thelec.kr/rss/S1N2.xml",
                4,
                ["全球财经", "亚洲市场", "AI产业链"],
                {
                    "notification_min_score": 75,
                    "notification_require_entity": True,
                    "notification_event_types": ["价格/宏观"],
                    "notification_reason": "高相关半导体专业媒体原始报道（非公司公告）",
                },
            )
            with transaction(database) as connection:
                connection.execute(
                    """
                    INSERT INTO sources(
                        id, key, name, kind, url, trust_level, topic_hints_json,
                        config_json, created_at, updated_at
                    ) VALUES (1, ?, ?, 'rss', ?, 4, ?, ?, ?, ?)
                    """,
                    (
                        source.key,
                        source.name,
                        source.url,
                        json.dumps(source.topic_hints, ensure_ascii=False),
                        json.dumps(source.config, ensure_ascii=False),
                        utc_now(),
                        utc_now(),
                    ),
                )
                inserted, _updated = _upsert_entry(
                    connection,
                    source,
                    Entry(
                        "the-elec-63515",
                        "ASML, 유지보수용 노광장비 부품값 10% 일괄 인상",
                        "https://www.thelec.kr/news/articleView.html?idxno=63515",
                        "",
                        "2026-10-10T00:37:29+00:00",
                        publisher="디일렉(THE ELEC)",
                        publisher_url="https://www.thelec.kr/",
                    ),
                    "feed-hash",
                    "/tmp/the-elec.xml",
                    "application/rss+xml",
                    200,
                )
            self.assertTrue(inserted)

            first = dispatch_web_push_notifications(database, sender=sender)
            second = dispatch_web_push_notifications(database, sender=sender)
            self.assertEqual(first["delivered"], 1)
            self.assertEqual(second["attempted"], 0)
            self.assertEqual(len(sender.sent), 2)
            self.assertIn("ASML", str(sender.sent[-1]["notification"]["body"]))
            with connect(database) as connection:
                outbox = connection.execute(
                    "SELECT channel, status FROM notification_outbox ORDER BY channel"
                ).fetchall()
                deliveries = connection.execute(
                    "SELECT state, attempts FROM web_push_deliveries"
                ).fetchall()
            self.assertEqual(
                [(row["channel"], row["status"]) for row in outbox],
                [("in_app", "pending"), ("web_push", "delivered")],
            )
            self.assertEqual(dict(deliveries[0]), {"state": "delivered", "attempts": 1})


if __name__ == "__main__":
    unittest.main()
