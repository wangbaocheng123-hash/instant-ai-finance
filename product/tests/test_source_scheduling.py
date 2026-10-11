from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from instant_ai.collectors import FetchResult, Source
from instant_ai.database import connect, initialize, seed_sources, transaction
from instant_ai.service import (
    GOOGLE_NEWS_POLL_SECONDS,
    MAX_SOURCE_BACKOFF_SECONDS,
    _select_due_source_rows,
    _source_failure_delay,
    _source_schedule,
    run_collection,
)


def source_row(source_id: int, key: str, url: str, *, next_fetch_at: str | None = None) -> dict:
    return {
        "id": source_id,
        "key": key,
        "name": key,
        "kind": "rss",
        "url": url,
        "trust_level": 3,
        "topic_hints_json": json.dumps(["全球财经"]),
        "config_json": "{}",
        "etag": None,
        "last_modified": None,
        "next_fetch_at": next_fetch_at,
    }


class SourceSchedulingTests(unittest.TestCase):
    def test_google_news_is_spread_to_one_due_source_per_collection_run(self) -> None:
        now = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
        rows = [
            source_row(1, "official", "https://www.federalreserve.gov/feed.xml"),
            source_row(2, "google-one", "https://news.google.com/rss/search?q=one"),
            source_row(3, "google-two", "https://news.google.com/rss/search?q=two"),
            source_row(4, "google-three", "https://news.google.com/rss/search?q=three"),
        ]
        selected, deferred = _select_due_source_rows(rows, now)
        self.assertEqual([row["key"] for row in selected], ["official", "google-one"])
        self.assertEqual(deferred, 2)

    def test_rate_limit_uses_hourly_exponential_backoff_with_a_six_hour_cap(self) -> None:
        source = Source(
            1,
            "google",
            "Google News",
            "rss",
            "https://news.google.com/rss/search?q=markets",
            3,
            ["全球财经"],
            {},
        )
        error = urllib.error.HTTPError(source.url, 429, "Too Many Requests", {}, None)
        self.assertEqual(_source_schedule(source)[1], GOOGLE_NEWS_POLL_SECONDS)
        self.assertEqual(_source_failure_delay(source, 1, error), 60 * 60)
        self.assertEqual(_source_failure_delay(source, 2, error), 2 * 60 * 60)
        self.assertEqual(_source_failure_delay(source, 3, error), 4 * 60 * 60)
        self.assertEqual(_source_failure_delay(source, 8, error), MAX_SOURCE_BACKOFF_SECONDS)

    def test_schema_thirteen_persists_schedule_and_rotates_google_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "instant-ai.db"
            initialize(path)
            seed_sources(path)
            with transaction(path) as connection:
                connection.execute(
                    """
                    UPDATE sources SET enabled=CASE
                        WHEN key IN ('federal-reserve-press', 'global-financial-wire', 'wall-street-wire')
                        THEN 1 ELSE 0 END
                    """
                )
            calls: list[str] = []

            def fake_collect(source: Source):
                calls.append(source.key)
                return (
                    FetchResult(200, "application/rss+xml", b"<rss/>", None, None),
                    [],
                    "hash",
                    str(root / "raw.xml"),
                )

            with patch("instant_ai.database.DATABASE_PATH", path), patch(
                "instant_ai.service.EVIDENCE_ROOT", root / "evidence"
            ), patch("instant_ai.service.run_retention_cleanup", return_value={}), patch(
                "instant_ai.service.collect_source", side_effect=fake_collect
            ):
                first = run_collection()
                second = run_collection()

            self.assertEqual(first["source_count"], 3)
            self.assertEqual(first["attempted_source_count"], 2)
            self.assertEqual(first["deferred_source_count"], 1)
            self.assertEqual(second["attempted_source_count"], 1)
            self.assertEqual(second["deferred_source_count"], 2)
            self.assertEqual(
                calls,
                ["federal-reserve-press", "global-financial-wire", "wall-street-wire"],
            )
            with connect(path) as connection:
                version = connection.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0]
                scheduled = connection.execute(
                    """
                    SELECT COUNT(*) FROM sources
                    WHERE enabled=1 AND last_attempt_at IS NOT NULL AND next_fetch_at IS NOT NULL
                    """
                ).fetchone()[0]
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(sources)").fetchall()
                }
            self.assertEqual(version, "13")
            self.assertEqual(scheduled, 3)
            self.assertTrue({"last_attempt_at", "next_fetch_at", "failure_count"} <= columns)


if __name__ == "__main__":
    unittest.main()
