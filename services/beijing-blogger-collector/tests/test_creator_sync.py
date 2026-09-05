from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mx_agent import creator_sync
from mx_agent.creator_sync import CreatorSyncService
from mx_agent.downloader_engine.profile_monitor import ProfileVideo
from mx_agent.storage import Storage


class FakeCollector:
    def __init__(self, *_args, **_kwargs) -> None:
        self.last_summary = {
            "rows_seen": 2,
            "complete": True,
            "creator_rows": 1,
        }

    def collect(self, *_args, **_kwargs):
        return [
            {
                "comment_id": "fan-1",
                "parent_comment_id": "",
                "author_name": "粉丝甲",
                "author_uid": "fan-uid",
                "text": "怎么看这条作品？",
                "created_at": "2026-08-07T08:00:00",
                "digg_count": 3,
                "reply_count": 1,
                "ip_label": "北京",
                "is_creator": False,
                "is_author_digged": True,
                "reply_to_comment_id": "",
                "reply_to_user_name": "",
                "label_text": "作者赞过",
            },
            {
                "comment_id": "creator-1",
                "parent_comment_id": "fan-1",
                "author_name": "新博主",
                "author_uid": "creator-uid",
                "text": "需要结合估值判断。",
                "created_at": "2026-08-07T08:01:00",
                "digg_count": 8,
                "reply_count": 0,
                "ip_label": "上海",
                "is_creator": True,
                "is_author_digged": None,
                "reply_to_comment_id": "fan-1",
                "reply_to_user_name": "粉丝甲",
                "label_text": "作者",
            },
        ]

    def close(self) -> None:
        return None


class FakeProfileScanner:
    videos: list[ProfileVideo] = []
    requested_limits: list[int] = []

    def __init__(self, *_args, **_kwargs) -> None:
        return None

    def scan(self, _profile_url: str, *, timeout: float, limit: int):
        self.requested_limits.append(limit)
        return "新博主", list(self.videos)

    def close(self) -> None:
        return None


class CreatorSyncTests(unittest.TestCase):
    def make_settings(self):
        return SimpleNamespace(
            source_account_name="新博主",
            creator_sync_enabled=False,
            creator_profile_url="https://www.douyin.com/user/creator-uid",
            creator_sync_interval_minutes=10,
            creator_sync_history_limit=500,
            creator_comments_enabled=True,
            creator_comment_limit=5000,
            creator_comment_refresh_minutes=60,
            creator_comment_tracking_hours=24,
        )

    def test_creators_share_one_login_profile_but_keep_runtime_state_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            storage = Storage(root / "agent.sqlite3")
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
            ):
                primary = CreatorSyncService(
                    self.make_settings(),
                    storage,
                    runtime_key="primary",
                    content_root=root / "creator-data",
                )
                second = CreatorSyncService(
                    self.make_settings(),
                    storage,
                    runtime_key="creator-second",
                    content_root=root / "creator-data",
                )

            self.assertEqual(primary.profile_dir, profile_dir)
            self.assertEqual(second.profile_dir, profile_dir)
            self.assertNotEqual(primary.runtime_dir, second.runtime_dir)
            self.assertNotEqual(primary.state_path, second.state_path)

    def test_video_and_comments_are_upserted_without_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video_dir = root / "videos"
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            database = root / "agent.sqlite3"
            video_dir.mkdir()
            source_file = video_dir / "20260807_0800_7000000000000000001.mp4"
            source_file.write_bytes(b"test-video" * 300)
            storage = Storage(database)
            video = ProfileVideo(
                video_id="7000000000000000001",
                url="https://www.douyin.com/video/7000000000000000001",
                title="测试作品",
                created_at=datetime(2026, 8, 7, 8, 0),
            )
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
                patch.object(creator_sync, "CommentCollector", FakeCollector),
            ):
                service = CreatorSyncService(
                    self.make_settings(), storage, content_root=root / "creator-data"
                )
                local_id = service._register_video(
                    video,
                    output=source_file,
                    creator="新博主",
                    title="测试作品",
                )
                second_id = service._register_video(
                    video,
                    output=source_file,
                    creator="新博主",
                    title="测试作品",
                )
                self.assertEqual(local_id, second_id)
                first = service._collect_and_merge_comments(
                    local_id,
                    video,
                    creator="新博主",
                    profile_url=self.make_settings().creator_profile_url,
                )
                second = service._collect_and_merge_comments(
                    local_id,
                    video,
                    creator="新博主",
                    profile_url=self.make_settings().creator_profile_url,
                )

            self.assertEqual(first, {"seen": 2, "created": 2, "updated": 0})
            self.assertEqual(second, {"seen": 2, "created": 0, "updated": 2})
            self.assertEqual(storage.counts()["videos"], 1)
            self.assertEqual(storage.count_comments(local_id), 2)
            self.assertEqual(len(storage.list_assets(local_id)), 1)
            comments = storage.list_comments(local_id, limit=10)
            reply = next(item for item in comments if item["author"] == "新博主")
            self.assertEqual(reply["raw_json"]["parent_source_comment_id"], "fan-1")
            self.assertEqual(reply["raw_json"]["section"], "author_interaction")
            self.assertTrue(reply["raw_json"]["automatic_sync"])

    def test_realtime_mode_establishes_baseline_without_downloading_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            video_dir = root / "videos"
            storage = Storage(root / "agent.sqlite3")
            settings = self.make_settings()
            settings.creator_sync_mode = "realtime"
            settings.creator_sync_enabled = True
            settings.creator_comments_enabled = False
            older = ProfileVideo(
                video_id="7000000000000000001",
                url="https://www.douyin.com/video/7000000000000000001",
                title="历史作品",
                created_at=datetime(2026, 8, 7, 8, 0),
            )
            newer = ProfileVideo(
                video_id="7000000000000000002",
                url="https://www.douyin.com/video/7000000000000000002",
                title="新作品",
                created_at=datetime(2026, 8, 7, 9, 0),
            )
            FakeProfileScanner.videos = [older]
            FakeProfileScanner.requested_limits = []
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
                patch.object(creator_sync, "ProfileScanner", FakeProfileScanner),
            ):
                service = CreatorSyncService(
                    settings, storage, content_root=root / "creator-data"
                )
                with patch.object(service, "_ensure_video") as ensure_video:
                    service._perform_cycle(manual=True, force_comments=False)
                    ensure_video.assert_not_called()
                    self.assertTrue(service.status()["realtime_baseline_ready"])

                    FakeProfileScanner.videos = [newer, older]
                    ensure_video.return_value = (1, True)
                    service._perform_cycle(manual=False, force_comments=False)
                    ensure_video.assert_called_once()
                    self.assertEqual(
                        ensure_video.call_args.args[0].video_id,
                        newer.video_id,
                    )

            self.assertEqual(FakeProfileScanner.requested_limits, [50, 50])
            self.assertEqual(service.status()["new_downloads"], 1)

    def test_content_ready_callback_runs_once_after_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            storage = Storage(root / "agent.sqlite3")
            settings = self.make_settings()
            settings.creator_sync_mode = "count"
            settings.creator_sync_history_limit = 1
            video = ProfileVideo(
                video_id="7000000000000000101",
                url="https://www.douyin.com/video/7000000000000000101",
                title="待通知作品",
                created_at=datetime(2026, 8, 7, 10, 0),
            )
            order: list[str] = []

            def on_content_ready(video_id: int, runtime_key: str) -> None:
                order.append(f"callback:{video_id}:{runtime_key}")

            def collect_comments(*_args, **_kwargs):
                order.append("comments")
                return {"seen": 2, "created": 1, "updated": 1}

            FakeProfileScanner.videos = [video]
            FakeProfileScanner.requested_limits = []
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
                patch.object(creator_sync, "ProfileScanner", FakeProfileScanner),
            ):
                service = CreatorSyncService(
                    settings,
                    storage,
                    runtime_key="creator-runtime-1",
                    content_root=root / "creator-data",
                    on_content_ready=on_content_ready,
                )
                with (
                    patch.object(service, "_ensure_video", return_value=(91, True)),
                    patch.object(
                        service,
                        "_collect_and_merge_comments",
                        side_effect=collect_comments,
                    ),
                ):
                    service._perform_cycle(manual=True, force_comments=False)

            self.assertEqual(order, ["comments", "callback:91:creator-runtime-1"])
            status = service.status()
            self.assertEqual(status["content_ready_callbacks"], 1)
            self.assertEqual(status["content_ready_callback_failures"], 0)
            self.assertEqual(status["last_content_ready_video_id"], 91)
            self.assertEqual(status["last_content_ready_error"], "")
            state = json.loads(service.state_path.read_text(encoding="utf-8"))
            self.assertFalse(
                state["videos"][video.video_id]["content_ready_pending"]
            )

    def test_content_ready_callback_failure_preserves_collection_and_is_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            storage = Storage(root / "agent.sqlite3")
            settings = self.make_settings()
            settings.creator_sync_mode = "count"
            settings.creator_sync_history_limit = 1
            video = ProfileVideo(
                video_id="7000000000000000102",
                url="https://www.douyin.com/video/7000000000000000102",
                title="回调失败作品",
                created_at=datetime(2026, 8, 7, 10, 0),
            )

            def failing_callback(_video_id: int, _runtime_key: str) -> None:
                raise RuntimeError("outbox unavailable")

            FakeProfileScanner.videos = [video]
            FakeProfileScanner.requested_limits = []
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
                patch.object(creator_sync, "ProfileScanner", FakeProfileScanner),
            ):
                service = CreatorSyncService(
                    settings,
                    storage,
                    runtime_key="creator-runtime-2",
                    content_root=root / "creator-data",
                    on_content_ready=failing_callback,
                )
                with (
                    patch.object(service, "_ensure_video", return_value=(92, True)),
                    patch.object(
                        service,
                        "_collect_and_merge_comments",
                        return_value={"seen": 1, "created": 1, "updated": 0},
                    ) as comments,
                ):
                    service._perform_cycle(manual=True, force_comments=False)

            comments.assert_called_once()
            status = service.status()
            self.assertFalse(status["busy"])
            self.assertEqual(status["new_downloads"], 1)
            self.assertEqual(status["content_ready_callbacks"], 1)
            self.assertEqual(status["content_ready_callback_failures"], 1)
            self.assertIn("outbox unavailable", status["last_content_ready_error"])
            state = json.loads(service.state_path.read_text(encoding="utf-8"))
            entry = state["videos"][video.video_id]
            self.assertEqual(entry["local_video_id"], 92)
            self.assertTrue(entry["content_ready_pending"])
            self.assertIn("outbox unavailable", entry["last_content_ready_error"])
            self.assertEqual(state["last_cycle"]["failures"], 0)
            self.assertEqual(
                state["last_cycle"]["content_ready_callback_failures"],
                1,
            )

    def test_date_batch_downloads_only_videos_on_or_after_start_date(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            video_dir = root / "videos"
            storage = Storage(root / "agent.sqlite3")
            settings = self.make_settings()
            settings.creator_comments_enabled = True
            before = ProfileVideo(
                video_id="6999999999999999999",
                url="https://www.douyin.com/video/6999999999999999999",
                title="日期前作品",
                created_at=datetime(2025, 10, 29, 23, 59),
            )
            boundary = ProfileVideo(
                video_id="7000000000000000001",
                url="https://www.douyin.com/video/7000000000000000001",
                title="边界日期作品",
                created_at=datetime(2025, 10, 30, 0, 0),
            )
            recent = ProfileVideo(
                video_id="7000000000000000002",
                url="https://www.douyin.com/video/7000000000000000002",
                title="近期作品",
                created_at=datetime(2026, 8, 7, 9, 0),
            )
            FakeProfileScanner.videos = [recent, boundary, before]
            FakeProfileScanner.requested_limits = []
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
                patch.object(creator_sync, "ProfileScanner", FakeProfileScanner),
            ):
                service = CreatorSyncService(
                    settings, storage, content_root=root / "creator-data"
                )
                with (
                    patch.object(
                        service,
                        "_ensure_video",
                        side_effect=[(1, True), (2, True)],
                    ) as ensure_video,
                    patch.object(service, "_collect_and_merge_comments") as comments,
                ):
                    service._perform_cycle(
                        manual=True,
                        force_comments=False,
                        start_date=date(2025, 10, 30),
                        videos_only=True,
                    )

            self.assertEqual(FakeProfileScanner.requested_limits, [1000])
            self.assertEqual(
                [call.args[0].video_id for call in ensure_video.call_args_list],
                [recent.video_id, boundary.video_id],
            )
            comments.assert_not_called()
            self.assertEqual(service.status()["works_seen"], 3)
            self.assertEqual(service.status()["works_selected"], 2)

    def test_count_mode_downloads_requested_new_video_count_and_skips_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            storage = Storage(root / "agent.sqlite3")
            settings = self.make_settings()
            settings.creator_sync_mode = "count"
            settings.creator_sync_history_limit = 2
            settings.creator_comments_enabled = False
            newest_image = ProfileVideo(
                video_id="7000000000000000005",
                url="https://www.douyin.com/note/7000000000000000005",
                title="最新图文",
                created_at=datetime(2026, 8, 7, 12, 0),
                work_type="image",
            )
            existing_video = ProfileVideo(
                video_id="7000000000000000004",
                url="https://www.douyin.com/video/7000000000000000004",
                title="已有视频",
                created_at=datetime(2026, 8, 7, 11, 0),
            )
            another_image = ProfileVideo(
                video_id="7000000000000000003",
                url="https://www.douyin.com/note/7000000000000000003",
                title="另一图文",
                created_at=datetime(2026, 8, 7, 10, 0),
                work_type="image",
            )
            missing_video = ProfileVideo(
                video_id="7000000000000000002",
                url="https://www.douyin.com/video/7000000000000000002",
                title="缺失视频",
                created_at=datetime(2026, 8, 7, 9, 0),
            )
            older_video = ProfileVideo(
                video_id="7000000000000000001",
                url="https://www.douyin.com/video/7000000000000000001",
                title="更早视频",
                created_at=datetime(2026, 8, 7, 8, 0),
            )
            FakeProfileScanner.videos = [
                newest_image,
                existing_video,
                another_image,
                missing_video,
                older_video,
            ]
            FakeProfileScanner.requested_limits = []
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
                patch.object(creator_sync, "ProfileScanner", FakeProfileScanner),
            ):
                service = CreatorSyncService(
                    settings, storage, content_root=root / "creator-data"
                )
                existing_path = service.video_dir / (
                    "20260807_1100_7000000000000000004.mp4"
                )
                existing_path.write_bytes(b"existing" * 300)
                service._state["videos"] = {
                    existing_video.video_id: {
                        "local_video_id": 41,
                        "file_path": str(existing_path),
                    }
                }
                with patch.object(
                    service,
                    "_ensure_video",
                    return_value=(42, True),
                ) as ensure_video:
                    service._perform_cycle(manual=True, force_comments=False)

            self.assertEqual(FakeProfileScanner.requested_limits, [50])
            self.assertEqual(ensure_video.call_count, 2)
            self.assertEqual(
                [call.args[0].video_id for call in ensure_video.call_args_list],
                [missing_video.video_id, older_video.video_id],
            )
            self.assertEqual(service.status()["works_seen"], 5)
            self.assertEqual(service.status()["works_selected"], 2)

    def test_image_work_registers_all_original_images_without_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video_dir = root / "videos"
            image_dir = root / "images"
            runtime_dir = root / "runtime"
            profile_dir = runtime_dir / "chrome-profile"
            state_path = runtime_dir / "state.json"
            image_dir.mkdir()
            first = image_dir / "20260807_0800_7000000000000000003_01.jpg"
            second = image_dir / "20260807_0800_7000000000000000003_02.jpg"
            first.write_bytes(b"first-image" * 300)
            second.write_bytes(b"second-image" * 300)
            storage = Storage(root / "agent.sqlite3")
            work = ProfileVideo(
                video_id="7000000000000000003",
                url="https://www.douyin.com/note/7000000000000000003",
                title="测试图文",
                created_at=datetime(2026, 8, 7, 8, 0),
                work_type="image",
            )
            with (
                patch.object(creator_sync, "RUNTIME_DIR", runtime_dir),
                patch.object(creator_sync, "PROFILE_DIR", profile_dir),
                patch.object(creator_sync, "STATE_PATH", state_path),
            ):
                service = CreatorSyncService(
                    self.make_settings(), storage, content_root=root / "creator-data"
                )
                first_id = service._register_image_work(
                    work,
                    outputs=[first, second],
                    creator="新博主",
                    title="测试图文",
                )
                second_id = service._register_image_work(
                    work,
                    outputs=[first, second],
                    creator="新博主",
                    title="测试图文",
                )
                third_id = service._register_image_work(
                    ProfileVideo(
                        video_id=work.video_id,
                        url=work.url,
                        title=f"抖音图文_{work.video_id}",
                        created_at=datetime(2026, 8, 7, 8, 0),
                        work_type="image",
                    ),
                    outputs=[first, second],
                    creator="新博主",
                    title=f"抖音图文_{work.video_id}",
                )

            self.assertEqual(first_id, second_id)
            self.assertEqual(first_id, third_id)
            assets = storage.list_assets(first_id)
            self.assertEqual(len(assets), 2)
            self.assertTrue(all(item["mime_type"] == "image/jpeg" for item in assets))
            video = storage.get_video(first_id)
            self.assertEqual(video["title"], "测试图文")
            raw_json = json.loads(video["raw_json"])
            self.assertEqual(raw_json["douyin_work_type"], "image")
            self.assertEqual(raw_json["image_count"], 2)


if __name__ == "__main__":
    unittest.main()
