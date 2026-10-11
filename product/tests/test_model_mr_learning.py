from __future__ import annotations

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from instant_ai.auth import OwnerAuth, SESSION_COOKIE_NAME, configure_owner
from instant_ai.model_mr import ModelMrClient, ModelMrUnavailable, ModelMrWorkNotFound
from instant_ai.model_mr_learning import ModelMrLearningConflict, ModelMrLearningStore
from instant_ai.server import InstantAIHandler


class ModelMrLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / "model-mr" / "public-snapshot.json"
        self.snapshot.parent.mkdir()
        self.client = ModelMrClient("http://127.0.0.1:9", self.snapshot, self.snapshot.parent / "media")
        self.write_works([1, 2, 3])
        self.offline = patch.object(ModelMrClient, "_json", side_effect=ModelMrUnavailable("offline"))
        self.offline.start()
        self.addCleanup(self.offline.stop)

    def write_works(self, ids: list[int]) -> None:
        self.snapshot.write_text(json.dumps({
            "version": 3, "works": [{"id": item, "title": f"合成作品{item}"} for item in ids], "thoughts": [],
        }), encoding="utf-8")

    def test_initial_read_is_empty_and_does_not_create_storage(self) -> None:
        self.assertEqual(self.client.learning(), {"revision": 0, "learned_work_ids": [], "last_work_id": None})
        self.assertFalse((self.snapshot.parent / "learning.sqlite3").exists())

    def test_marks_persist_across_client_reopen_without_changing_content(self) -> None:
        original = self.snapshot.read_bytes()
        state = self.client.save_learned(2, True, 0)
        reopened = ModelMrClient("http://127.0.0.1:9", self.snapshot, self.client.media_root)
        self.assertEqual(state, {"revision": 1, "learned_work_ids": [2], "last_work_id": 2})
        self.assertEqual(reopened.learning(), state)
        self.assertEqual(self.snapshot.read_bytes(), original)
        self.assertFalse(self.client.details_root.exists())
        self.assertFalse(self.client.media_root.exists())
        self.assertEqual(sorted(path.name for path in self.snapshot.parent.iterdir()), ["learning.sqlite3", "public-snapshot.json"])

    def test_mark_order_is_learning_order_not_publication_order(self) -> None:
        self.client.save_learned(3, True, 0)
        state = self.client.save_learned(1, True, 1)
        self.assertEqual(state["learned_work_ids"], [1, 3])
        self.assertEqual(state["last_work_id"], 1)

    def test_unmark_latest_falls_back_and_re_mark_becomes_latest(self) -> None:
        self.client.save_learned(1, True, 0)
        self.client.save_learned(2, True, 1)
        state = self.client.save_learned(2, False, 2)
        self.assertEqual(state, {"revision": 3, "learned_work_ids": [1], "last_work_id": 1})
        self.assertEqual(self.client.save_learned(2, True, 3)["last_work_id"], 2)
        self.client.save_learned(1, False, 4)
        self.assertEqual(self.client.save_learned(2, False, 5), {"revision": 6, "learned_work_ids": [], "last_work_id": None})

    def test_idempotent_writes_do_not_reorder_or_increment_revision(self) -> None:
        self.assertEqual(self.client.save_learned(1, False, 0)["revision"], 0)
        self.client.save_learned(1, True, 0)
        state = self.client.save_learned(2, True, 1)
        self.assertEqual(self.client.save_learned(1, True, 2), state)
        with self.assertRaises(ModelMrLearningConflict):
            self.client.save_learned(1, True, 1)
        self.assertEqual(self.client.learning(), state)

    def test_invalid_and_unknown_input_never_creates_marks(self) -> None:
        cases = [(True, True, 0), (1.1, True, 0), ("1", True, 0), (0, True, 0), (-1, True, 0),
                 (2**53, True, 0), (1, 1, 0), (1, "true", 0), (1, None, 0),
                 (1, True, True), (1, True, -1), (1, True, 1.5), (1, True, "0"), (1, True, 2**53)]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.client.save_learned(*values)
        with self.assertRaises(ModelMrWorkNotFound):
            self.client.save_learned(999, True, 0)
        self.assertFalse((self.snapshot.parent / "learning.sqlite3").exists())

    def test_first_write_conflict_still_leaves_readable_empty_state(self) -> None:
        with self.assertRaises(ModelMrLearningConflict):
            self.client.save_learned(1, True, 9)
        self.assertEqual(self.client.learning()["revision"], 0)
        self.assertEqual(self.client.save_learned(1, True, 0)["revision"], 1)

    def test_parallel_stale_writes_have_one_winner(self) -> None:
        barrier = threading.Barrier(2)
        path = self.snapshot.parent / "learning.sqlite3"

        def mark(work_id: int) -> str:
            barrier.wait(timeout=5)
            try:
                ModelMrLearningStore(path).set_learned(work_id, True, 0)
                return "saved"
            except ModelMrLearningConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mark, [1, 2]))
        self.assertCountEqual(results, ["saved", "conflict"])
        state = self.client.learning()
        self.assertEqual(state["revision"], 1)
        self.assertEqual(len(state["learned_work_ids"]), 1)

    def test_corrupt_learning_storage_fails_closed_without_resetting(self) -> None:
        path = self.snapshot.parent / "learning.sqlite3"
        path.write_bytes(b"not-a-database")
        for call in (self.client.learning, lambda: self.client.save_learned(1, True, 0)):
            with self.assertRaises(ModelMrUnavailable) as raised:
                call()
            self.assertNotIn(str(path), str(raised.exception))
        self.assertEqual(path.read_bytes(), b"not-a-database")

    def test_deep_around_lookup_only_reads_small_metadata_page(self) -> None:
        self.write_works(list(range(1_500, 0, -1)))
        with patch.object(ModelMrClient, "work_detail", side_effect=AssertionError("details must not be fetched")), patch.object(
            ModelMrClient, "_clean_snapshot_work", wraps=self.client._clean_snapshot_work,
        ) as clean:
            page = self.client.works(limit=24, around_id=100)
        self.assertEqual(page["offset"], 1_388)
        self.assertEqual(page["count"], 24)
        self.assertEqual(page["total"], 1_500)
        self.assertEqual(page["items"][12]["id"], 100)
        self.assertTrue(page["has_more"])
        self.assertEqual(clean.call_count, 24)
        self.assertEqual(self.client.save_learned(100, True, 0)["last_work_id"], 100)

    def test_anchor_relative_pages_resist_insertions_between_requests(self) -> None:
        self.write_works(list(range(100, 0, -1)))
        page = self.client.works(limit=24, around_id=50)
        first_id, last_id = page["items"][0]["id"], page["items"][-1]["id"]
        self.client.save_learned(50, True, 0)
        self.write_works([102, 101, *range(100, 0, -1)])
        refreshed = self.client.works(limit=24, around_id=first_id, relative_offset=0)
        self.assertEqual([item["id"] for item in refreshed["items"]], [item["id"] for item in page["items"]])
        self.assertEqual(refreshed["offset"], page["offset"] + 2)
        following = self.client.works(limit=24, around_id=last_id, relative_offset=1)
        preceding = self.client.works(limit=24, around_id=first_id, relative_offset=-24)
        self.assertEqual(following["items"][0]["id"], last_id - 1)
        self.assertEqual(preceding["items"][-1]["id"], first_id + 1)
        self.assertEqual(self.client.learning()["last_work_id"], 50)

    def test_around_boundaries_and_validation(self) -> None:
        self.write_works(list(range(1, 41)))
        first = self.client.works(limit=24, around_id=1)
        last = self.client.works(limit=24, around_id=40)
        after_last = self.client.works(limit=24, around_id=40, relative_offset=1)
        self.assertEqual(first["offset"], 0)
        self.assertEqual(last["offset"], 27)
        self.assertFalse(last["has_more"])
        self.assertEqual(after_last["items"], [])
        self.assertEqual(after_last["offset"], 40)
        self.assertEqual(self.client.works(limit=24, offset=7)["offset"], 7)
        for kwargs in ({"around_id": 0}, {"around_id": True}, {"around_id": 1.5}, {"relative_offset": 1},
                       {"around_id": 1, "relative_offset": 500_001}, {"around_id": 1, "relative_offset": -500_001},
                       {"around_id": 1, "relative_offset": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.client.works(**kwargs)
        with self.assertRaises(ModelMrWorkNotFound):
            self.client.works(around_id=99)

    def test_legacy_string_index_id_can_be_located_and_marked(self) -> None:
        self.snapshot.write_text(json.dumps({
            "version": 2, "works": [{"id": "700", "title": "旧导出"}], "thoughts": [],
        }), encoding="utf-8")
        self.assertEqual(self.client.works(around_id=700)["items"][0]["id"], 700)
        self.assertEqual(self.client.save_learned(700, True, 0)["last_work_id"], 700)

    def test_live_index_cap_is_explicit_and_never_fetches_details(self) -> None:
        source = {"items": [{"id": item, "title": "合成作品"} for item in range(1, 501)], "total": 700}
        with patch.object(ModelMrClient, "_json", return_value=source) as request:
            self.assertEqual(self.client.works(limit=24, around_id=250)["items"][12]["id"], 250)
            with self.assertRaisesRegex(ModelMrUnavailable, "前500条"):
                self.client.works(around_id=650)
            with self.assertRaisesRegex(ModelMrUnavailable, "前500条"):
                self.client.works(around_id=500, relative_offset=1)
        self.assertTrue(all(call.args[0].startswith("/api/videos?limit=500&") for call in request.call_args_list))

    def test_beijing_reimport_keeps_learning_marks_on_stable_id(self) -> None:
        media = self.root / "synthetic.mp4"
        media.write_bytes(b"synthetic-media-not-playable")
        args = dict(source_work_id="123456", title="合成标题", description="", source_url="https://www.douyin.com/video/123456",
                    published_at="2026-10-01T09:00:00+08:00", comments=[], media_path=media,
                    media_sha256=hashlib.sha256(media.read_bytes()).hexdigest())
        first = self.client.import_beijing_work(source_revision=1, **args)
        state = self.client.save_learned(first["work_id"], True, 0)
        second = self.client.import_beijing_work(source_revision=2, **{**args, "title": "合成更新标题"})
        self.assertEqual(first["work_id"], second["work_id"])
        self.assertEqual(self.client.learning(), state)

    def test_http_auth_write_guard_validation_conflict_and_private_cache(self) -> None:
        auth_path = self.root / "synthetic-auth.json"
        configure_owner("owner", "synthetic-password-only", auth_path)
        auth = OwnerAuth(required=True, path=auth_path)
        token, _ = auth.create_session("owner")
        cookie = f"{SESSION_COOKIE_NAME}={token}"
        server = ThreadingHTTPServer(("127.0.0.1", 0), InstantAIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)

        def request(method: str, path: str, body: object = None, headers: dict[str, str] | None = None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                return response.status, payload
            finally:
                connection.close()

        with patch("instant_ai.server.MODEL_MR", self.client), patch("instant_ai.server.AUTH", auth):
            thread.start()
            try:
                path = "/api/model-mr/works/1/learned"
                body = {"learned": True, "expected_revision": 0}
                headers = {"Cookie": cookie, "X-Instant-AI": "1", "Content-Type": "application/json"}
                self.assertEqual(request("GET", "/api/model-mr/learning")[0], 401)
                self.assertEqual(request("POST", path, body, {"X-Instant-AI": "1"})[0], 401)
                self.assertEqual(request("POST", path, body, {"Cookie": cookie})[0], 403)
                self.assertFalse((self.snapshot.parent / "learning.sqlite3").exists())
                self.assertEqual(request("GET", "/api/model-mr/learning", headers=headers), (200, self.client.learning()))
                for invalid in ([], None, {}, {"learned": 1, "expected_revision": 0}, {**body, "user_id": 1}):
                    with self.subTest(invalid=invalid):
                        self.assertEqual(request("POST", path, invalid, headers)[0], 400)
                self.assertEqual(request("POST", "/api/model-mr/works/99/learned", body, headers)[0], 404)
                status, state = request("POST", path, body, headers)
                self.assertEqual((status, state["last_work_id"]), (200, 1))
                self.assertEqual(request("POST", path, {"learned": False, "expected_revision": 0}, headers)[0], 409)
                self.assertEqual(request("GET", "/api/model-mr/works?around_id=99", headers=headers)[0], 404)
                self.assertEqual(request("GET", "/api/model-mr/works?around_id=invalid", headers=headers)[0], 400)
                self.assertEqual(request("GET", "/api/model-mr/works?around_id=1&relative_offset=500001", headers=headers)[0], 400)
                self.write_works(list(range(1, 101)))
                status, page = request("GET", "/api/model-mr/works?around_id=50&relative_offset=0", headers=headers)
                self.assertEqual((status, page["offset"], page["count"], page["items"][0]["id"]), (200, 49, 24, 50))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
