from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from mx_agent.comment_sync import DouyinCommentSyncService
from mx_agent.creator_sync import CreatorSyncService
from mx_agent.storage import Storage


class BrowserSerializationTests(unittest.TestCase):
    def test_creator_and_comment_jobs_share_one_global_execution_lock(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            execution_lock = threading.Lock()
            state_lock = threading.Lock()
            start = threading.Event()
            active = 0
            maximum_active = 0
            completed: list[str] = []
            errors: list[BaseException] = []

            def operation(label: str) -> str:
                nonlocal active, maximum_active
                start.wait(2)
                with state_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.08)
                with state_lock:
                    active -= 1
                    completed.append(label)
                return label

            creator_service = object.__new__(CreatorSyncService)
            creator_service._execution_lock = execution_lock
            creator_service._perform_cycle_unlocked = (
                lambda **_kwargs: operation("creator")
            )

            comment_service = DouyinCommentSyncService(
                Storage(Path(folder) / "agent.sqlite3"),
                execution_lock=execution_lock,
            )

            class FakeContext:
                closed = False

                def close(self) -> None:
                    self.closed = True

            class FakePlaywright:
                stopped = False

                def stop(self) -> None:
                    self.stopped = True

            context = FakeContext()
            playwright = FakePlaywright()
            comment_service._context = context
            comment_service._playwright = playwright

            def run_creator() -> None:
                try:
                    creator_service._perform_cycle(
                        manual=True,
                        force_comments=False,
                    )
                except BaseException as exc:
                    errors.append(exc)

            def run_comment() -> None:
                try:
                    comment_service._submit_browser_job(
                        lambda: operation("comment"),
                        timeout=5,
                    )
                except BaseException as exc:
                    errors.append(exc)

            creator_thread = threading.Thread(target=run_creator)
            comment_thread = threading.Thread(target=run_comment)
            creator_thread.start()
            comment_thread.start()
            start.set()
            creator_thread.join(5)
            comment_thread.join(5)

            self.assertFalse(creator_thread.is_alive())
            self.assertFalse(comment_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(maximum_active, 1)
            self.assertEqual(set(completed), {"creator", "comment"})
            self.assertTrue(context.closed)
            self.assertTrue(playwright.stopped)
            self.assertIsNone(comment_service._context)
            self.assertIsNone(comment_service._playwright)


if __name__ == "__main__":
    unittest.main()
