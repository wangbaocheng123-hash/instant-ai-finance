from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from instant_ai.database import connect, initialize
from instant_ai.event_signal_delivery import deliver_event_signals, validate_compass_signal_url
from instant_ai.official_monitor import monitor_official_channels, signal_evidence, signal_fingerprint, validate_official_url
from instant_ai.watch_events import list_watch_events, sync_watch_events


class OfficialMonitorTests(unittest.TestCase):
    def test_allowlist_and_targeted_fingerprint_reduce_noise(self) -> None:
        self.assertEqual(validate_official_url("https://www.nvidia.com/en-eu/gtc/"), "https://www.nvidia.com/en-eu/gtc/")
        self.assertEqual(
            validate_official_url("https://adpemploymentreport.com/ner_production.json"),
            "https://adpemploymentreport.com/ner_production.json",
        )
        self.assertEqual(
            validate_official_url("https://www2.census.gov/retail/releases/historical/marts/"),
            "https://www2.census.gov/retail/releases/historical/marts/",
        )
        for unsafe in (
            "http://www.nvidia.com/en-eu/gtc/",
            "https://nvidia.com.evil.example/",
            "https://127.0.0.1/",
            "https://user:secret@www.nvidia.com/",
            "https://adpemploymentreport.com.evil.example/ner_production.json",
            "https://census.gov.evil.example/retail/",
        ):
            with self.assertRaises(ValueError):
                validate_official_url(unsafe)

        first, first_signal = signal_fingerprint(b"<html>unrelated counter 1</html>", "text/html", ["GTC Berlin"])
        second, second_signal = signal_fingerprint(b"<html>unrelated counter 2</html>", "text/html", ["GTC Berlin"])
        self.assertEqual(first, second, "无关页面噪声不得触发重点事件变化。")
        self.assertFalse(first_signal)
        self.assertFalse(second_signal)

    def test_new_macro_release_fingerprints_detect_material_official_changes(self) -> None:
        adp_before = signal_evidence(
            b'{"reportMonth":"July","title":"Private employers added 44,000 jobs in July"}',
            "application/json",
            ["Private employers", 'reportMonth":"August', "August 2026"],
        )
        adp_after = signal_evidence(
            b'{"reportMonth":"August","title":"Private employers added 61,000 jobs in August",'
            b'"card":"August 2026"}',
            "application/json",
            ["Private employers", 'reportMonth":"August', "August 2026"],
        )
        self.assertTrue(adp_before["signal_found"])
        self.assertTrue(adp_after["signal_found"])
        self.assertNotEqual(adp_before["fingerprint"], adp_after["fingerprint"])

        census_before = signal_evidence(
            b'<html><a href="adv2606.pdf">adv2606.pdf</a></html>',
            "text/html",
            ["adv2607.pdf"],
        )
        census_after = signal_evidence(
            b'<html><a href="adv2606.pdf">adv2606.pdf</a><a href="adv2607.pdf">adv2607.pdf</a></html>',
            "text/html",
            ["adv2607.pdf"],
        )
        self.assertFalse(census_before["signal_found"])
        self.assertTrue(census_after["signal_found"])
        self.assertNotEqual(census_before["fingerprint"], census_after["fingerprint"])

    def test_official_channel_is_persisted_baselined_and_change_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "official.db"
            initialize(path)
            payload = {
                "ok": True,
                "revision": 400,
                "events": [{
                    "eventKey": "home:timeline:gtc",
                    "scope": "home",
                    "sourceKind": "timeline",
                    "sourceEventId": "gtc",
                    "date": "2026-10-20",
                    "title": "NVIDIA GTC Berlin（10月20—22日）",
                    "importance": 5,
                    "status": "confirmed",
                    "monitoring": {
                        "contractVersion": 1,
                        "coverage": "verified",
                        "verifiedAt": "2026-08-29",
                        "publisher": {"name": "NVIDIA", "url": "https://www.nvidia.com/en-eu/gtc/"},
                        "release": {
                            "timeStatus": "date-only",
                            "scheduledAt": None,
                            "timeZone": "Asia/Shanghai",
                            "label": "北京时间日期 2026-10-20，具体时刻待主办方公布",
                            "windowStart": "2026-08-29T00:00:00+00:00",
                            "windowEnd": "2026-08-30T23:59:59+00:00",
                        },
                        "channels": [{
                            "key": "nvidia-gtc",
                            "publisher": "NVIDIA",
                            "name": "NVIDIA GTC Berlin 官方页面",
                            "url": "https://www.nvidia.com/en-eu/gtc/",
                            "type": "html",
                            "role": "official-event",
                            "verifiedAt": "2026-08-29",
                            "expectedTerms": ["GTC Berlin"],
                            "signalDeliveryToken": "1700000000000.fixture-token",
                        }],
                        "expectedTerms": ["GTC Berlin"],
                    },
                }],
            }
            sync = sync_watch_events(path=path, fetcher=lambda _: payload)
            self.assertEqual(sync["official_channels"], 1)
            with connect(path) as connection:
                public_monitoring = connection.execute(
                    "SELECT monitoring_json FROM watch_events WHERE event_key='home:timeline:gtc'"
                ).fetchone()[0]
                self.assertNotIn("signalDeliveryToken", public_monitoring)
                self.assertNotIn("fixture-token", public_monitoring)

            bodies = [
                b"<html><main>GTC Berlin 2026 official schedule</main></html>",
                b"<html><main>GTC Berlin 2026 keynote announced</main></html>",
            ]

            def fetcher(url: str, **_: object) -> dict[str, object]:
                return {
                    "status": 200,
                    "not_modified": False,
                    "body": bodies.pop(0),
                    "content_type": "text/html; charset=utf-8",
                    "headers": {"ETag": '"fixture"'},
                }

            first_at = datetime(2026, 8, 29, 6, 0, tzinfo=UTC)
            baseline = monitor_official_channels(path=path, fetcher=fetcher, now=first_at)
            self.assertEqual(baseline["changes"], 0, "首次抓取只能建立基线，不能误报变化。")
            listing = list_watch_events(path=path)
            self.assertEqual(listing["counts"]["configured"], 1)
            self.assertEqual(listing["counts"]["official_reachable"], 1)
            self.assertEqual(listing["events"][0]["official_status"], "reachable")
            self.assertIsNotNone(listing["events"][0]["official_channels"][0]["last_success_at"])

            changed = monitor_official_channels(path=path, fetcher=fetcher, now=first_at + timedelta(minutes=6))
            self.assertEqual(changed["changes"], 1)
            with connect(path) as connection:
                channel = connection.execute(
                    "SELECT last_changed_at, signal_found, last_error FROM watch_event_channels"
                ).fetchone()
                self.assertIsNotNone(channel["last_changed_at"])
                self.assertEqual(channel["signal_found"], 1)
                self.assertIsNone(channel["last_error"])
                signal = connection.execute(
                    "SELECT status, previous_hash, evidence_hash, matched_terms_json FROM watch_event_signals"
                ).fetchone()
                self.assertEqual(signal["status"], "pending")
                self.assertNotEqual(signal["previous_hash"], signal["evidence_hash"])
                self.assertIn("gtc berlin", signal["matched_terms_json"])
                self.assertEqual(connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0], "10")

            sent: list[dict[str, object]] = []

            def sender(url: str, signal: dict[str, object]) -> dict[str, object]:
                self.assertEqual(validate_compass_signal_url(url), url)
                sent.append(signal)
                return {"ok": True, "signal": {"id": "compass-signal-1", "status": "received"}}

            delivery = deliver_event_signals(path=path, sender=sender, now=first_at + timedelta(minutes=7))
            self.assertEqual(delivery["delivered"], 1)
            self.assertEqual(sent[0]["eventKey"], "home:timeline:gtc")
            self.assertEqual(sent[0]["signalKind"], "official-page-change")
            self.assertTrue(sent[0]["signalFound"])
            self.assertEqual(sent[0]["signalToken"], "1700000000000.fixture-token")
            with connect(path) as connection:
                signal = connection.execute(
                    "SELECT status, compass_signal_id, compass_signal_status FROM watch_event_signals"
                ).fetchone()
                self.assertEqual(dict(signal), {
                    "status": "delivered",
                    "compass_signal_id": "compass-signal-1",
                    "compass_signal_status": "received",
                })

            payload["events"][0]["monitoring"]["channels"][0]["expectedTerms"] = ["keynote"]
            sync_watch_events(path=path, fetcher=lambda _: payload)
            with connect(path) as connection:
                reset = connection.execute(
                    "SELECT content_hash, etag, last_checked_at, last_success_at, last_changed_at FROM watch_event_channels"
                ).fetchone()
                self.assertTrue(all(reset[key] is None for key in reset.keys()),
                                "监测关键词变化后必须重新建立基线，不能沿用旧健康状态。")

            def rebaseline_fetcher(url: str, **headers: object) -> dict[str, object]:
                self.assertEqual(headers.get("etag"), "")
                self.assertEqual(headers.get("last_modified"), "")
                return {
                    "status": 200,
                    "not_modified": False,
                    "body": b"<html><main>GTC Berlin keynote announced</main></html>",
                    "content_type": "text/html; charset=utf-8",
                    "headers": {"ETag": '"fixture-2"'},
                }

            rebaseline = monitor_official_channels(
                path=path, fetcher=rebaseline_fetcher, now=first_at + timedelta(minutes=12)
            )
            self.assertEqual(rebaseline["changes"], 0, "配置变更后的首次取样仍只能建立新基线。")


if __name__ == "__main__":
    unittest.main()
