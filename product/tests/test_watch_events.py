from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from instant_ai.database import connect, initialize, transaction, utc_now
from instant_ai.watch_events import list_watch_events, scan_watch_events, sync_watch_events


class WatchEventTests(unittest.TestCase):
    def test_macro_aliases_cover_adp_beige_book_and_retail_sales(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro-watch.db"
            initialize(path)
            payload = {
                "ok": True,
                "revision": 401,
                "events": [
                    {
                        "eventKey": "zijin:manual:adp",
                        "scope": "zijin",
                        "date": "2026-09-02",
                        "time": "20:15",
                        "title": "美国8月ADP就业报告",
                    },
                    {
                        "eventKey": "zijin:manual:beige",
                        "scope": "zijin",
                        "date": "2026-09-03",
                        "time": "02:00",
                        "title": "美联储褐皮书",
                    },
                    {
                        "eventKey": "zijin:manual:retail",
                        "scope": "zijin",
                        "date": "2026-09-16",
                        "time": "20:30",
                        "title": "美国8月零售销售",
                    },
                ],
            }
            self.assertEqual(sync_watch_events(path=path, fetcher=lambda _: payload)["synced"], 3)
            with connect(path) as connection:
                rows = connection.execute(
                    "SELECT title, monitor_terms_json FROM watch_events ORDER BY event_date"
                ).fetchall()
            terms = {row["title"]: set(json.loads(row["monitor_terms_json"])) for row in rows}
            self.assertIn("adp", terms["美国8月ADP就业报告"])
            self.assertIn("褐皮书", terms["美联储褐皮书"])
            self.assertIn("retail sales", terms["美国8月零售销售"])

    def test_compass_events_are_isolated_persisted_and_matched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watch.db"
            initialize(path)
            now = utc_now()
            with transaction(path) as connection:
                connection.execute(
                    """
                    INSERT INTO items(
                        id, canonical_key, title, url, summary, published_at,
                        first_seen_at, last_seen_at, topics_json, entities_json
                    ) VALUES (1, 'nvidia-gtc', 'NVIDIA announces GTC Berlin keynote',
                              'https://example.com/gtc', 'AI infrastructure event', ?, ?, ?,
                              '["AI产业链"]', '["英伟达"]')
                    """,
                    (now, now, now),
                )

            payload = {
                "ok": True,
                "revision": 313,
                "events": [
                    {
                        "eventKey": "home:timeline:gtc",
                        "scope": "home",
                        "sourceKind": "timeline",
                        "sourceEventId": "gtc",
                        "date": "2026-10-20",
                        "time": "",
                        "title": "NVIDIA GTC Berlin（10月20—22日）",
                        "category": "AI算力大会",
                        "importance": 5,
                        "status": "planned",
                        "note": "已确认",
                        "sources": [{"name": "NVIDIA", "url": "https://www.nvidia.com/gtc/"}],
                        "analysisFeedback": {
                            "status": "retrying",
                            "message": "本轮核验暂未完成，系统将在 5 分钟后自动重试。",
                            "attemptCount": 1,
                            "maxAttempts": 3,
                            "updatedAt": now,
                        },
                    },
                    {
                        "eventKey": "zijin:research:pmi",
                        "scope": "zijin",
                        "sourceKind": "research",
                        "sourceEventId": "pmi",
                        "date": "2026-09-30",
                        "time": "09:30",
                        "title": "中国9月制造业PMI",
                        "importance": 3,
                        "status": "planned",
                    },
                ],
            }
            result = sync_watch_events(path=path, fetcher=lambda _: payload)
            self.assertTrue(result["ok"])
            self.assertEqual(result["synced"], 2)
            scan = scan_watch_events(path=path)
            self.assertEqual(scan["checked"], 2)
            self.assertGreaterEqual(scan["matches"], 1)

            listing = list_watch_events(path=path)
            self.assertEqual(listing["counts"]["total"], 2)
            self.assertEqual(listing["counts"]["home"], 1)
            self.assertEqual(listing["counts"]["zijin"], 1)
            self.assertEqual(listing["events"][0]["event_key"], "home:timeline:gtc",
                             "最近 24 小时有罗盘反馈的事件必须临时置顶。")
            gtc = next(event for event in listing["events"] if event["scope"] == "home")
            self.assertEqual(gtc["match_count"], 1)
            self.assertEqual(gtc["latest_matches"][0]["item_id"], 1)
            self.assertIn("nvidia", gtc["latest_matches"][0]["matched_terms"])
            self.assertEqual(gtc["analysis_feedback"]["status"], "retrying")
            self.assertEqual(gtc["analysis_feedback"]["attemptCount"], 1)
            self.assertIn("5 分钟后自动重试", gtc["pipeline_status"])
            self.assertNotIn("holdings", gtc)

            reduced = {**payload, "revision": 314, "events": payload["events"][:1]}
            sync_watch_events(path=path, fetcher=lambda _: reduced)
            self.assertEqual(list_watch_events(path=path)["counts"]["total"], 1)

            failed = sync_watch_events(path=path, fetcher=lambda _: (_ for _ in ()).throw(OSError("offline")))
            self.assertFalse(failed["ok"])
            self.assertEqual(list_watch_events(path=path)["counts"]["total"], 1, "同步失败必须保留上次列表。")
            with connect(path) as connection:
                self.assertIn("offline", connection.execute("SELECT last_error FROM watch_sync_state WHERE id=1").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
