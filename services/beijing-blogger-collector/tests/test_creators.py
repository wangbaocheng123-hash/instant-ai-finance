from __future__ import annotations

import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from mx_agent.creators import CreatorRegistry, CreatorSyncManager
from mx_agent.investment_thoughts import InvestmentThoughtService
from mx_agent.settings import load_settings
from mx_agent.storage import Storage


class CreatorRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = load_settings()
        self.registry_path = self.root / "creators.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_migrates_existing_single_creator_as_primary(self) -> None:
        registry = CreatorRegistry(self.settings, path=self.registry_path)
        creators = registry.list()
        self.assertEqual(len(creators), 1)
        self.assertEqual(creators[0]["id"], "primary")
        self.assertEqual(creators[0]["name"], self.settings.source_account_name)
        self.assertEqual(creators[0]["profile_url"], self.settings.creator_profile_url)

    def test_new_creator_has_independent_sync_settings(self) -> None:
        registry = CreatorRegistry(self.settings, path=self.registry_path)
        created = registry.create(
            {
                "name": "艾琳的财经",
                "profile_url": "https://www.douyin.com/user/test-creator",
            }
        )
        updated, old_name = registry.update(
            created["id"],
            {
                "creator_sync_mode": "realtime",
                "creator_sync_interval_minutes": 17,
                "creator_sync_history_limit": 88,
            },
        )
        primary = registry.get("primary")
        self.assertEqual(old_name, "艾琳的财经")
        self.assertEqual(updated["creator_sync_mode"], "realtime")
        self.assertEqual(updated["creator_sync_interval_minutes"], 17)
        self.assertEqual(updated["creator_sync_history_limit"], 88)
        self.assertNotEqual(updated["profile_url"], primary["profile_url"])

    def test_creator_identity_is_derived_persisted_and_rename_safe(self) -> None:
        registry = CreatorRegistry(self.settings, path=self.registry_path)
        created = registry.create(
            {
                "name": "贵族之路",
                "profile_url": (
                    "https://www.douyin.com/user/MS4wLjABAAAAstable-sec"
                    "?uid=platform-10086"
                ),
            }
        )
        creator_uuid = created["creator_uuid"]
        self.assertEqual(str(uuid.UUID(creator_uuid)), creator_uuid)
        self.assertEqual(created["platform"], "douyin")
        self.assertEqual(created["platform_user_id"], "platform-10086")
        self.assertEqual(created["sec_uid"], "MS4wLjABAAAAstable-sec")

        renamed, old_name = registry.update(created["id"], {"name": "贵族之路新名称"})
        self.assertEqual(old_name, "贵族之路")
        self.assertEqual(renamed["creator_uuid"], creator_uuid)
        self.assertEqual(renamed["platform_user_id"], "platform-10086")
        self.assertEqual(renamed["sec_uid"], "MS4wLjABAAAAstable-sec")

        reloaded = CreatorRegistry(self.settings, path=self.registry_path).get(created["id"])
        self.assertEqual(reloaded["creator_uuid"], creator_uuid)
        saved = json.loads(self.registry_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)

    def test_new_creator_requires_profile_url_for_automatic_sync(self) -> None:
        registry = CreatorRegistry(self.settings, path=self.registry_path)

        with self.assertRaisesRegex(ValueError, "请填写抖音博主主页链接"):
            registry.create({"name": "没有主页的博主"})

        self.assertEqual(len(registry.list()), 1)

    def test_manager_shares_one_lock_and_callback_across_creator_services(self) -> None:
        registry = CreatorRegistry(self.settings, path=self.registry_path)
        second = registry.create(
            {
                "name": "第二位博主",
                "profile_url": "https://www.douyin.com/user/second-sec-uid",
            }
        )
        storage = Storage(self.root / "data" / "manager.sqlite3")
        execution_lock = threading.Lock()
        callback = lambda _video_id, _runtime_key: None
        created_services: list[object] = []

        class FakeService:
            def __init__(self, settings, service_storage, **kwargs) -> None:
                self.settings = settings
                self.storage = service_storage
                self.kwargs = kwargs
                created_services.append(self)

            def start(self) -> None:
                return None

            def wake(self) -> None:
                return None

        with patch("mx_agent.creators.CreatorSyncService", FakeService):
            manager = CreatorSyncManager(
                registry,
                storage,
                execution_lock=execution_lock,
                on_content_ready=callback,
            )
            manager.ensure("primary")
            manager.ensure(second["id"])

        self.assertEqual(len(created_services), 2)
        self.assertTrue(
            all(
                service.kwargs["execution_lock"] is execution_lock
                for service in created_services
            )
        )
        self.assertTrue(
            all(service.kwargs["managed_schedule"] for service in created_services)
        )
        self.assertTrue(
            all(
                service.kwargs["on_content_ready"] is callback
                for service in created_services
            )
        )
        self.assertEqual(
            {service.kwargs["runtime_key"] for service in created_services},
            {"primary", second["id"]},
        )

    def test_collector_manager_can_stay_available_without_automatic_rotation(self) -> None:
        registry = CreatorRegistry(self.settings, path=self.registry_path)
        storage = Storage(self.root / "data" / "manual-only.sqlite3")

        class FakeService:
            def __init__(self, settings, service_storage, **kwargs) -> None:
                self.settings = settings

            def start(self) -> None:
                return None

            def wake(self) -> None:
                return None

        with patch("mx_agent.creators.CreatorSyncService", FakeService):
            manager = CreatorSyncManager(
                registry,
                storage,
                automatic_rotation=False,
            )
            manager.start()

        status = manager.rotation_status()
        self.assertFalse(status["automatic"])
        self.assertFalse(status["running"])
        self.assertEqual(status["eligible_count"], 0)
        self.assertIsNone(manager._rotation_thread)
        self.assertIn("仅手动采集", status["message"])

    def test_rename_video_author_keeps_existing_library_attached(self) -> None:
        storage = Storage(self.root / "data" / "test.sqlite3")
        video_id, _ = storage.upsert_video(
            {
                "source": "test",
                "source_video_id": "one",
                "author": "旧博主",
                "title": "测试作品",
            }
        )
        self.assertEqual(storage.rename_video_author("旧博主", "新博主"), 1)
        self.assertEqual(storage.get_video(video_id)["author"], "新博主")
        self.assertEqual(len(storage.list_videos(account="新博主")), 1)
        self.assertEqual(len(storage.list_videos(account="旧博主")), 0)

    def test_investment_library_only_returns_selected_creator_content(self) -> None:
        storage = Storage(self.root / "data" / "thoughts.sqlite3")
        thoughts = InvestmentThoughtService(storage)
        parent = thoughts.create_category(name="测试大类")
        child = thoughts.create_category(name="测试小类", parent_id=parent["id"])
        first_id, _ = storage.upsert_video(
            {"source": "test", "source_video_id": "a", "author": "博主甲", "title": "甲作品"}
        )
        second_id, _ = storage.upsert_video(
            {"source": "test", "source_video_id": "b", "author": "博主乙", "title": "乙作品"}
        )
        thoughts.sync_video_categories(video_id=first_id, category_ids=[child["id"]])
        thoughts.sync_video_categories(video_id=second_id, category_ids=[child["id"]])

        result = thoughts.list_library(account="博主甲")
        self.assertEqual([item["video_id"] for item in result["items"]], [first_id])
        self.assertEqual(result["links_by_video"], {str(first_id): [child["id"]]})
        category = next(item for item in result["categories"] if item["id"] == child["id"])
        self.assertEqual(category["video_count"], 1)


if __name__ == "__main__":
    unittest.main()
