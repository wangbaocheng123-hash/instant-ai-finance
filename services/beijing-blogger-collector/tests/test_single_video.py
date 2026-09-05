from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from mx_agent import creator_sync
from mx_agent.creator_sync import CreatorSyncService
from mx_agent.single_video import VideoLinkError, normalize_video_link, resolve_video_link
from mx_agent.storage import Storage


VIDEO_ID = "7678988051075051365"
URL = f"https://www.douyin.com/video/{VIDEO_ID}"
SHARE = "5.61 :4pm j@p.qE UyT:/ 06/06 35的紫金矿业包含了什么？ # 紫金矿业 https://v.douyin.com/l1Z_BDCPdMM/ 复制此链接，打开抖音搜索，直接观看视频！"


class SingleVideoLinkTests(unittest.TestCase):
    def test_accepts_share_text_canonical_and_modal_links(self):
        self.assertEqual(normalize_video_link(SHARE), "https://v.douyin.com/l1Z_BDCPdMM/")
        for value in (URL, URL + "?from=share", f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/",
                      f"https://www.douyin.com/user/test?modal_id={VIDEO_ID}"):
            self.assertEqual(normalize_video_link(value), URL)
        with patch("mx_agent.single_video.requests.get") as get:
            self.assertEqual(resolve_video_link(URL), {"video_id": VIDEO_ID, "video_url": URL})
        get.assert_not_called()

    def test_rejects_unsupported_and_ambiguous_input(self):
        for value in ("", None, 123, "x" * 4097, "https://www.douyin.com/user/test",
                      f"https://www.douyin.com/note/{VIDEO_ID}", "https://localhost/",
                      "https://v.douyin.com.evil.invalid/link", "https://v.douyin.com:1234/abc",
                      "https://user@v.douyin.com/abc", URL + " https://v.douyin.com/abc/"):
            with self.subTest(value=str(value)[:70]), self.assertRaises(VideoLinkError):
                normalize_video_link(value)

    def test_expands_share_redirect_without_loading_browser_or_body(self):
        response = MagicMock()
        response.status_code = 302
        response.headers = {"Location": f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/?region=CN"}
        response.__enter__.return_value = response
        with patch("mx_agent.single_video.requests.get", return_value=response) as get:
            result = resolve_video_link(SHARE)
        self.assertEqual(result["video_url"], URL)
        self.assertEqual(get.call_count, 1)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertTrue(get.call_args.kwargs["stream"])
        response.iter_content.assert_not_called()
        response.__exit__.assert_called_once()

    def test_never_follows_redirect_to_another_host(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 302
        response.headers = {"Location": "http://127.0.0.1/private"}
        with patch("mx_agent.single_video.requests.get", return_value=response) as get:
            with self.assertRaises(VideoLinkError):
                resolve_video_link(SHARE)
        self.assertEqual(get.call_count, 1)

    def test_network_failure_and_redirect_loop_are_bounded(self):
        with patch("mx_agent.single_video.requests.get", side_effect=requests.Timeout("sensitive-url")):
            with self.assertRaises(VideoLinkError) as result:
                resolve_video_link(SHARE)
            self.assertNotIn("sensitive-url", str(result.exception))
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 302
        response.headers = {"Location": "https://v.douyin.com/loop/"}
        with patch("mx_agent.single_video.requests.get", return_value=response) as get:
            with self.assertRaises(VideoLinkError):
                resolve_video_link(SHARE)
        self.assertEqual(get.call_count, 5)


class SingleVideoCollectionTests(unittest.TestCase):
    @contextmanager
    def service(self, *, mode="count"):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = SimpleNamespace(
                source_account_name="指定博主", creator_profile_url="https://www.douyin.com/user/fixture",
                creator_sync_enabled=False, creator_sync_mode=mode, creator_sync_history_limit=50,
                creator_sync_interval_minutes=10, creator_comments_enabled=True,
                creator_comment_limit=20, creator_comment_refresh_minutes=60, creator_comment_tracking_hours=1,
            )
            with patch.object(creator_sync, "RUNTIME_DIR", root / "runtime"), \
                 patch.object(creator_sync, "PROFILE_DIR", root / "profile"), \
                 patch.object(creator_sync, "STATE_PATH", root / "state.json"):
                callback = MagicMock(return_value=True)
                service = CreatorSyncService(
                    settings, Storage(root / "fixture.sqlite3"), content_root=root / "creators",
                    execution_lock=threading.Lock(), managed_schedule=True, on_content_ready=callback,
                )
                yield service, callback

    @staticmethod
    def fake_download(result, directory, *, custom_filename):
        path = directory / (custom_filename + ".mp4")
        path.write_bytes(b"synthetic-video" * 300)
        return path

    def test_one_old_video_reuses_download_comments_and_callback_without_scanning_profile(self):
        for mode in ("count", "realtime"):
            with self.subTest(mode=mode), self.service(mode=mode) as (service, callback):
                with patch.object(creator_sync, "ProfileScanner") as scanner, \
                     patch.object(creator_sync, "DouyinResolver") as resolver, \
                     patch.object(creator_sync, "download_video", side_effect=self.fake_download) as download, \
                     patch.object(creator_sync, "inspect_mp4", return_value={"has_mdat": True, "has_video": True}), \
                     patch.object(service, "_collect_and_merge_comments", return_value={"seen": 1, "created": 1, "updated": 0}) as comments:
                    resolver.return_value.resolve.return_value = SimpleNamespace(video_id=VIDEO_ID, title="指定作品")
                    service._perform_cycle(manual=True, force_comments=False, video_url=URL)
                    self.assertFalse(service.status()["last_error"])
                    self.assertEqual(service.storage.counts()["videos"], 1)
                    self.assertEqual(service.status()["works_seen"], 1)
                    self.assertEqual(service.status()["new_downloads"], 1)
                    self.assertEqual(service.settings.creator_sync_history_limit, 50)
                    self.assertNotIn("realtime_profiles", service._state)
                    comments.assert_called_once()
                    callback.assert_called_once()
                    resolver.return_value.close.assert_called_once()
                    self.assertEqual(download.call_count, 1)
                    scanner.assert_not_called()
                    # Repeating the same target refreshes only its comments and
                    # preserves the title/file; no second browser/download.
                    service._perform_cycle(manual=True, force_comments=False, video_url=URL)
                    self.assertEqual(download.call_count, 1)
                    self.assertEqual(resolver.call_count, 1)
                    self.assertEqual(service.storage.counts()["videos"], 1)
                    self.assertEqual(comments.call_count, 2)
                    self.assertEqual(service.status()["new_downloads"], 0)
                    row = service.storage.get_video(callback.call_args.args[0])
                    self.assertEqual(row["title"], "指定作品")
                    self.assertEqual(row["author"], "指定博主")
                    self.assertFalse(service._execution_lock.locked())

    def test_wrong_resolved_video_never_downloads_and_releases_lock(self):
        with self.service() as (service, callback):
            with patch.object(creator_sync, "DouyinResolver") as resolver, \
                 patch.object(creator_sync, "download_video") as download:
                resolver.return_value.resolve.return_value = SimpleNamespace(video_id="7678988051075051000", title="wrong")
                service._perform_cycle(manual=True, force_comments=False, video_url=URL)
            download.assert_not_called()
            callback.assert_not_called()
            self.assertEqual(service.storage.counts()["videos"], 0)
            self.assertEqual(service._state["last_cycle"]["failures"], 1)
            self.assertIn("ID 不一致", service.status()["last_error"])
            self.assertFalse(service._execution_lock.locked())
            resolver.return_value.close.assert_called_once()

    def test_queue_is_explicit_consumed_once_and_rejects_duplicate(self):
        with self.service() as (service, _):
            with patch.object(creator_sync, "ProfileScanner") as scanner, patch.object(creator_sync, "DouyinResolver") as resolver:
                service.run_now(video_url=SHARE, videos_only=True)
                with self.assertRaises(VideoLinkError):
                    service.run_now(video_url=URL)
                self.assertTrue(service.status()["queued"])
                scanner.assert_not_called()
                resolver.assert_not_called()
                with patch.object(service, "_perform_cycle", side_effect=lambda **kw: service.stop()) as cycle:
                    service._run_loop()
                self.assertEqual(cycle.call_count, 1)
                self.assertEqual(cycle.call_args.kwargs["video_url"], "https://v.douyin.com/l1Z_BDCPdMM/")
                self.assertTrue(cycle.call_args.kwargs["videos_only"])
                self.assertEqual(service._manual_video_url, "")
                self.assertFalse(service.status()["queued"])

    def test_invalid_target_does_not_start_bulk_fallback(self):
        with self.service() as (service, _):
            with self.assertRaises(VideoLinkError):
                service.run_now(video_url="not a link")
            with self.assertRaises(ValueError):
                service.run_now(video_url=URL, start_date="2026-01-01")
            self.assertFalse(service.status()["queued"])

    def test_video_only_target_skips_comments_but_still_notifies_transfer(self):
        with self.service() as (service, callback):
            with patch.object(service, "_ensure_video", return_value=(7, True)), \
                 patch.object(service, "_collect_and_merge_comments") as comments:
                service._perform_cycle(manual=True, force_comments=False, video_url=URL, videos_only=True)
            comments.assert_not_called()
            callback.assert_called_once_with(7, service.runtime_key)


if __name__ == "__main__":
    unittest.main()
