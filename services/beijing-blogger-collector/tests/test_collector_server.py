from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mx_agent import collector_server


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FakeRegistry:
    def __init__(self) -> None:
        self.creators = [
            {
                "id": "creator-one",
                "name": "贵族\u200b之路",
                "platform": "douyin",
                "profile_url": "https://must-not-be-returned.invalid/private-profile",
                "creator_sync_history_limit": 7,
                "creator_comments_enabled": True,
                "creator_comment_limit": 5000,
                "creator_comment_tracking_hours": 24,
            }
        ]

    def list(self):
        return list(self.creators)

    def get(self, creator_id):
        for creator in self.creators:
            if creator_id == creator["id"]:
                return dict(creator)
        raise ValueError("missing")

    def create(self, payload):
        creator = {
            "id": "creator-two",
            "name": payload["name"],
            "platform": "douyin",
            "profile_url": payload["profile_url"],
            "creator_sync_history_limit": payload.get("creator_sync_history_limit", 1),
            "creator_comments_enabled": payload.get("creator_comments_enabled", True),
            "creator_comment_limit": payload.get("creator_comment_limit", 5000),
            "creator_comment_tracking_hours": payload.get("creator_comment_tracking_hours", 24),
        }
        self.creators.append(creator)
        return dict(creator)

    def update(self, creator_id, payload):
        creator = self.get(creator_id)
        old_name = creator["name"]
        field_map = {
            "name": "name",
            "profile_url": "profile_url",
            "creator_sync_history_limit": "creator_sync_history_limit",
            "creator_comments_enabled": "creator_comments_enabled",
            "creator_comment_limit": "creator_comment_limit",
            "creator_comment_tracking_hours": "creator_comment_tracking_hours",
        }
        for source, target in field_map.items():
            if source in payload:
                creator[target] = payload[source]
        index = next(index for index, item in enumerate(self.creators) if item["id"] == creator_id)
        self.creators[index] = creator
        return dict(creator), old_name


class FakeSyncManager:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.run_requests: list[str] = []
        self.run_options: list[dict[str, object]] = []

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def ensure(self, creator_id):
        return creator_id

    def run_now(self, creator_id, **kwargs):
        self.run_requests.append(creator_id)
        self.run_options.append(dict(kwargs))
        return {"profile_url": "https://must-not-be-returned.invalid/private"}

    def status(self, creator_id):
        if creator_id != "creator-one":
            raise ValueError("missing")
        return {
            "busy": False,
            "queued": False,
            "phase": "waiting",
            "message": "等待手动操作。",
            "last_started_at": "",
            "last_finished_at": "2026-08-31T10:00:00+08:00",
            "last_error": "",
            "works_seen": 7,
            "works_selected": 7,
            "new_downloads": 7,
            "comments_created": 734,
            "comments_updated": 0,
            "profile_url": "https://must-not-be-returned.invalid/private-profile",
            "video_dir": "C:/private/videos",
            "events": [
                {
                    "at": "2026-08-31T10:00:00+08:00",
                    "level": "info",
                    "message": "采集完成。",
                }
            ],
        }

    def rotation_status(self):
        return {
            "running": True,
            "single_browser": True,
            "eligible_count": 1,
            "slot_seconds": 600,
            "profile_dir": "C:/private/chrome-profile",
        }


class FakeSender:
    def __init__(self) -> None:
        self.called = threading.Event()
        self.calls = 0

    def run_once(self, *, limit):
        self.calls += 1
        self.called.set()
        return []


class FakeStorage:
    def __init__(self, media_file: Path) -> None:
        self.media_file = media_file
        self.author = "贵族之路"

    def counts(self):
        return {"videos": 1, "assets": 1, "comments": 1}

    def list_videos(self, limit=50, account=None):
        if account not in {None, self.author}:
            return []
        return [
            {
                "id": 1,
                "source": "douyin",
                "source_video_id": "work-1",
                "author": self.author,
                "title": "采集作品",
                "description": "作品说明",
                "published_at": "2026-08-31T09:30:00+08:00",
                "discovered_at": "2026-08-31T10:00:00+08:00",
                "status": "new",
                "asset_count": 1,
                "comment_count": 1,
                "primary_asset_id": 9,
                "primary_asset_type": "video",
                "primary_asset_mime": "video/mp4",
                "primary_asset_size": self.media_file.stat().st_size,
                "raw_json": {"private": str(self.media_file)},
            }
        ][:limit]

    def get_video_detail(self, video_id):
        if int(video_id) != 1:
            return None
        return {
            "video": self.list_videos()[0],
            "assets": [
                {
                    "id": 9,
                    "asset_type": "video",
                    "mime_type": "video/mp4",
                    "size_bytes": self.media_file.stat().st_size,
                    "status": "stored",
                    "local_path": str(self.media_file),
                    "raw_json": {"private": str(self.media_file)},
                }
            ],
            "comments": [
                {
                    "id": 11,
                    "author": "公开用户",
                    "text": "评论正文",
                    "like_count": 5,
                    "reply_count": 1,
                    "sentiment": "neutral",
                    "risk_level": "normal",
                    "published_at": "2026-08-31T09:40:00+08:00",
                    "captured_at": "2026-08-31T10:00:00+08:00",
                    "raw_json": {
                        "kind": "user_comment",
                        "author_liked": True,
                        "reply_depth": 0,
                        "private": str(self.media_file),
                    },
                }
            ],
            "comment_total": 1,
        }

    def get_asset(self, asset_id):
        if int(asset_id) != 9:
            return None
        return {
            "id": 9,
            "local_path": str(self.media_file),
            "mime_type": "video/mp4",
        }

    def rename_video_author(self, old_name, new_name):
        if self.author == str(old_name).replace("\u200b", ""):
            self.author = new_name
            return 1
        return 0


class FakeOutbox:
    def __init__(self) -> None:
        self.expedited = 0

    def status_counts(self):
        return {"delivered": 2, "retry_wait": 1}

    def list_recent(self, *, limit):
        return [
            {
                "transfer_id": "transfer-1",
                "creator_id": "creator-one",
                "source_work_id": "work-1",
                "status": "retry_wait",
                "attempt_count": 1,
                "last_error_code": "NETWORK_TIMEOUT",
                "updated_at": "2026-08-31T10:00:00+08:00",
                "delivered_at": "",
                "manifest": {"must_not": "appear"},
            }
        ][:limit]

    def expedite_retries(self):
        self.expedited += 1
        return 1


class CollectorServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def configuration(self, *, configured: bool) -> collector_server.CollectorConfiguration:
        return collector_server.CollectorConfiguration(
            media_dir=self.root / "media-private",
            artifact_dir=self.root / "outbox-private" / "artifacts",
            outbox_path=self.root / "outbox-private" / "transfer.sqlite3",
            creators_path=self.root / "config-private" / "creators.json",
            singapore_base_url=(
                "https://receiver.example.invalid" if configured else ""
            ),
            collector_node_id="beijing-node-1" if configured else "",
            transfer_key_id="beijing-key-1" if configured else "",
            transfer_secret=b"top-secret-never-returned-1234567890" if configured else b"",
            collector_version="test-version",
            sender_interval_seconds=0.01,
            sender_batch_size=3,
            outbox_lease_seconds=60,
        )

    def runtime(
        self,
        *,
        configured: bool,
        sender=None,
        storage=None,
        outbox=None,
    ) -> tuple[collector_server.CollectorRuntime, FakeSyncManager]:
        manager = FakeSyncManager()
        issues = () if configured else (
            "https_endpoint",
            "collector_node_id",
            "transfer_key_id",
            "hmac_secret_32_bytes",
        )
        runtime = collector_server.CollectorRuntime(
            configuration=self.configuration(configured=configured),
            storage=storage or object(),
            registry=FakeRegistry(),
            outbox=outbox or object(),
            adapter=object(),
            sync_manager=manager,
            sender=sender,
            transfer_issues=issues,
        )
        return runtime, manager

    def test_import_does_not_load_ai_or_public_server_modules(self) -> None:
        code = """
import json, sys
import mx_agent.collector_server
forbidden = [
    'mx_agent.analysis',
    'mx_agent.auto_transcription',
    'mx_agent.chat',
    'mx_agent.doubao',
    'mx_agent.knowledge',
    'mx_agent.mcp_server',
    'mx_agent.server',
]
loaded = [name for name in forbidden if name in sys.modules]
loaded.extend(name for name in sys.modules if name == 'openai' or name.startswith('openai.'))
print(json.dumps(sorted(set(loaded))))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
            env={**os.environ, "BLOGGER_AGENT_ROLE": "collector"},
        )
        self.assertEqual(json.loads(result.stdout), [])

    def test_role_guard_runs_before_initialization(self) -> None:
        config = self.configuration(configured=False)
        with patch.dict(os.environ, {"BLOGGER_AGENT_ROLE": "intelligence"}, clear=False):
            with patch.object(collector_server, "load_settings") as load_settings:
                with self.assertRaises(RuntimeError):
                    collector_server.build_collector_runtime(config)
        load_settings.assert_not_called()
        self.assertFalse(config.media_dir.exists())
        self.assertFalse(config.artifact_dir.exists())

    def test_build_wires_shared_lock_adapter_and_sender(self) -> None:
        config = self.configuration(configured=True)
        fake_settings = SimpleNamespace(database_path=self.root / "collector.sqlite3")
        storage = object()
        registry = MagicMock()
        outbox = object()
        adapter = object()
        manager = FakeSyncManager()
        transport = object()
        sender = FakeSender()
        with patch.dict(os.environ, {"BLOGGER_AGENT_ROLE": "collector"}, clear=False), \
             patch.object(collector_server, "load_settings", return_value=fake_settings), \
             patch.object(collector_server, "Storage", return_value=storage) as storage_type, \
             patch.object(collector_server, "CreatorRegistry", return_value=registry) as registry_type, \
             patch.object(collector_server, "TransferOutbox", return_value=outbox) as outbox_type, \
             patch.object(collector_server, "CollectorContentReadyAdapter", return_value=adapter) as adapter_type, \
             patch.object(collector_server, "CreatorSyncManager", return_value=manager) as manager_type, \
             patch.object(collector_server, "HTTPSCollectorTransport", return_value=transport) as transport_type, \
             patch.object(collector_server, "TransferSender", return_value=sender) as sender_type:
            runtime = collector_server.build_collector_runtime(config)

        self.assertTrue(config.media_dir.is_dir())
        self.assertTrue(config.artifact_dir.is_dir())
        storage_type.assert_called_once_with(fake_settings.database_path)
        registry_type.assert_called_once_with(fake_settings, path=config.creators_path)
        outbox_type.assert_called_once_with(
            config.outbox_path,
            allowed_artifact_roots=(config.media_dir, config.artifact_dir),
            lease_seconds=config.outbox_lease_seconds,
        )
        adapter_type.assert_called_once_with(
            storage,
            registry,
            outbox,
            artifact_dir=config.artifact_dir,
            collector_node_id=config.collector_node_id,
            collector_key_id=config.transfer_key_id,
            collector_version=config.collector_version,
        )
        manager_kwargs = manager_type.call_args.kwargs
        self.assertIs(manager_kwargs["on_content_ready"], adapter)
        self.assertIsInstance(manager_kwargs["execution_lock"], type(threading.Lock()))
        self.assertFalse(manager_kwargs["automatic_rotation"])
        transport_type.assert_called_once_with(
            config.singapore_base_url,
            node_id=config.collector_node_id,
            key_id=config.transfer_key_id,
            secret=config.transfer_secret,
        )
        sender_type.assert_called_once_with(outbox, transport)
        self.assertIs(runtime.sender, sender)

    def test_missing_transfer_configuration_waits_without_transport_or_network(self) -> None:
        config = self.configuration(configured=False)
        fake_settings = SimpleNamespace(database_path=self.root / "collector.sqlite3")
        registry = FakeRegistry()
        manager = FakeSyncManager()
        with patch.dict(os.environ, {"BLOGGER_AGENT_ROLE": "collector"}, clear=False), \
             patch.object(collector_server, "load_settings", return_value=fake_settings), \
             patch.object(collector_server, "Storage", return_value=object()), \
             patch.object(collector_server, "CreatorRegistry", return_value=registry), \
             patch.object(collector_server, "TransferOutbox", return_value=object()), \
             patch.object(collector_server, "CollectorContentReadyAdapter", return_value=object()), \
             patch.object(collector_server, "CreatorSyncManager", return_value=manager), \
             patch.object(collector_server, "HTTPSCollectorTransport") as transport_type, \
             patch.object(collector_server, "TransferSender") as sender_type:
            runtime = collector_server.build_collector_runtime(config)

        runtime.start()
        try:
            status = runtime.status()
            self.assertEqual(status["transfer"]["state"], "waiting_config")
            self.assertFalse(status["transfer"]["configured"])
            self.assertEqual(
                set(status["transfer"]["waiting_for"]),
                {
                    "https_endpoint",
                    "collector_node_id",
                    "transfer_key_id",
                    "hmac_secret_32_bytes",
                },
            )
            transport_type.assert_not_called()
            sender_type.assert_not_called()
        finally:
            runtime.close()
        self.assertEqual(manager.started, 1)
        self.assertEqual(manager.stopped, 1)

    def test_sender_loop_runs_and_shutdown_stops_components(self) -> None:
        sender = FakeSender()
        runtime, manager = self.runtime(configured=True, sender=sender)
        runtime.start()
        self.assertTrue(sender.called.wait(1))
        runtime.close()
        self.assertGreaterEqual(sender.calls, 1)
        self.assertEqual(manager.started, 1)
        self.assertEqual(manager.stopped, 1)

    def test_status_is_redacted(self) -> None:
        runtime, _manager = self.runtime(configured=True, sender=FakeSender())
        runtime.start()
        try:
            encoded = json.dumps(runtime.status(), ensure_ascii=False)
        finally:
            runtime.close()
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("top-secret-never-returned", encoded)
        self.assertNotIn("receiver.example.invalid", encoded)
        self.assertNotIn("beijing-key-1", encoded)
        self.assertNotIn("chrome-profile", encoded)

    def test_single_video_preview_is_read_only_and_target_is_forwarded_to_selected_creator(self) -> None:
        runtime, manager = self.runtime(configured=False)
        server = collector_server.create_http_server(runtime, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "https://www.douyin.com/video/7678988051075051365"

        def post(path, payload):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("POST", path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result

        try:
            before = runtime.registry.list()
            status, preview = post("/collector/api/collector/resolve-video", {"creator_id": "creator-one", "video_url": url})
            self.assertEqual(status, 200)
            self.assertEqual(preview, {"creator_id": "creator-one", "video_url": url, "video_id": "7678988051075051365"})
            self.assertEqual(manager.run_requests, [])
            self.assertEqual(runtime.registry.list(), before)
            status, result = post("/collector/api/collector/run-once", {
                "creator_id": "creator-one", "video_url": url, "force_comments": True, "videos_only": False,
            })
            self.assertEqual(status, 202)
            self.assertTrue(result["accepted"])
            self.assertEqual(manager.run_requests, ["creator-one"])
            self.assertEqual(manager.run_options, [{"video_url": url, "force_comments": True, "videos_only": False}])
            for bad in ("", "https://www.douyin.com/user/test", 12, "https://localhost/private"):
                status, _ = post("/api/collector/run-once", {"creator_id": "creator-one", "video_url": bad})
                self.assertEqual(status, 400)
            status, _ = post("/api/collector/resolve-video", {"creator_id": "missing", "video_url": url})
            self.assertEqual(status, 400)
            self.assertEqual(manager.run_requests, ["creator-one"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_http_server_rejects_non_loopback_bind_and_accepts_local_trigger(self) -> None:
        runtime, manager = self.runtime(configured=False)
        with self.assertRaises(ValueError):
            collector_server.create_http_server(runtime, host="0.0.0.0", port=0)

        server = collector_server.create_http_server(runtime, port=0)
        self.assertEqual(server.server_address[0], collector_server.LOOPBACK_HOST)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                collector_server.LOOPBACK_HOST,
                server.server_port,
                timeout=3,
            )
            body = json.dumps({"creator_id": "creator-one"}).encode("utf-8")
            connection.request(
                "POST",
                "/api/collector/run-once",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 202)
            self.assertEqual(
                payload,
                {"accepted": True, "creator_id": "creator-one", "state": "queued"},
            )
            self.assertEqual(manager.run_requests, ["creator-one"])
            self.assertEqual(
                manager.run_options,
                [{"force_comments": False, "videos_only": False}],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            runtime.close()

    def test_management_centre_lists_safe_data_and_streams_media_by_range(self) -> None:
        media_dir = self.root / "media-private"
        media_dir.mkdir(parents=True)
        media_file = media_dir / "work-1.mp4"
        media_file.write_bytes(b"video-data")
        outbox = FakeOutbox()
        runtime, _manager = self.runtime(
            configured=False,
            storage=FakeStorage(media_file),
            outbox=outbox,
        )
        runtime.start()
        server = collector_server.create_http_server(runtime, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                collector_server.LOOPBACK_HOST,
                server.server_port,
                timeout=3,
            )
            connection.request("GET", "/")
            response = connection.getresponse()
            page = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("width=device-width", page)
            self.assertIn("仅手动采集", page)
            self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))

            connection.request("GET", "/collector/")
            response = connection.getresponse()
            prefixed_page = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("博主采集端", prefixed_page)

            connection.request("GET", "/collector/api/collector/dashboard")
            response = connection.getresponse()
            prefixed_dashboard = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(prefixed_dashboard["status"]["collection_mode"], "manual_only")

            connection.request("GET", "/hub")
            response = connection.getresponse()
            hub = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("北京统一版本", hub)
            self.assertIn('href="/model"', hub)
            self.assertIn('href="/collector/"', hub)
            self.assertIn("模型下载器", hub)

            connection.request("GET", "/hub.css")
            response = connection.getresponse()
            hub_css = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn(".grid", hub_css)

            connection.request("GET", "/manifest.webmanifest")
            response = connection.getresponse()
            manifest = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(manifest["display"], "standalone")
            self.assertEqual(manifest["start_url"], "/")

            connection.request("GET", "/health/version")
            response = connection.getresponse()
            deployment = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(
                set(deployment),
                {
                    "service",
                    "status",
                    "version",
                    "repository_revision",
                    "deployed_time",
                },
            )
            self.assertEqual(deployment["service"], "blogger-collector")
            self.assertEqual(deployment["status"], "ok")

            connection.request("GET", "/service-worker.js")
            response = connection.getresponse()
            service_worker = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('url.pathname.includes("/api/collector/")', service_worker)

            connection.request("GET", "/app-icon.png")
            response = connection.getresponse()
            icon = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "image/png")
            self.assertTrue(icon.startswith(b"\x89PNG"))

            for icon_path in (
                "/apple-touch-icon.png",
                "/favicon-32.png",
                "/north-pole-collector-icon-1024.png",
                "/north-pole-collector-icon-192.png",
                "/north-pole-collector-icon-512.png",
            ):
                connection.request("GET", icon_path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200, icon_path)
                self.assertEqual(response.getheader("Content-Type"), "image/png")
                self.assertTrue(response.read().startswith(b"\x89PNG"), icon_path)

            connection.request("GET", "/api/collector/dashboard")
            response = connection.getresponse()
            dashboard = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(dashboard["status"]["collection_mode"], "manual_only")
            self.assertFalse(dashboard["status"]["automatic_collection"])
            self.assertEqual(dashboard["counts"]["comments"], 1)
            self.assertEqual(dashboard["transfer_counts"]["retry_wait"], 1)
            self.assertEqual(dashboard["creators"][0]["name"], "贵族之路")
            self.assertNotIn(str(self.root), json.dumps(dashboard, ensure_ascii=False))
            self.assertNotIn("private-profile", json.dumps(dashboard, ensure_ascii=False))

            connection.request(
                "GET",
                "/api/collector/creators/creator-one/settings",
            )
            response = connection.getresponse()
            settings = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(settings["collection_mode"], "manual_only")
            self.assertEqual(settings["history_limit"], 7)
            self.assertEqual(
                settings["profile_url"],
                "https://must-not-be-returned.invalid/private-profile",
            )

            settings_body = json.dumps(
                {
                    "name": "贵族之路作品",
                    "profile_url": "https://www.douyin.com/user/test-profile",
                    "history_limit": 12,
                    "comments_enabled": True,
                    "comment_limit": 6000,
                    "comment_tracking_hours": 36,
                }
            ).encode("utf-8")
            connection.request(
                "POST",
                "/api/collector/creators/creator-one/settings",
                body=settings_body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(settings_body)),
                },
            )
            response = connection.getresponse()
            updated = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(updated["name"], "贵族之路作品")
            self.assertEqual(updated["history_limit"], 12)
            self.assertEqual(_manager.run_requests, [])

            create_body = json.dumps(
                {
                    "name": "新博主",
                    "profile_url": "https://www.douyin.com/user/new-profile",
                }
            ).encode("utf-8")
            connection.request(
                "POST",
                "/api/collector/creators",
                body=create_body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(create_body)),
                },
            )
            response = connection.getresponse()
            created = json.loads(response.read())
            self.assertEqual(response.status, 201)
            self.assertEqual(created["name"], "新博主")
            self.assertEqual(created["collection_mode"], "manual_only")
            self.assertEqual(_manager.run_requests, [])

            connection.request("GET", "/api/collector/works?creator_id=creator-one")
            response = connection.getresponse()
            works = json.loads(response.read())["works"]
            self.assertEqual(works[0]["primary_asset"]["content_url"], "/api/collector/assets/9/content")

            connection.request("GET", "/api/collector/works/1")
            response = connection.getresponse()
            detail = json.loads(response.read())
            self.assertEqual(detail["comments"][0]["text"], "评论正文")
            self.assertTrue(detail["comments"][0]["author_liked"])
            self.assertNotIn("local_path", json.dumps(detail, ensure_ascii=False))
            self.assertNotIn(str(self.root), json.dumps(detail, ensure_ascii=False))

            connection.request(
                "GET",
                "/api/collector/assets/9/content",
                headers={"Range": "bytes=1-3"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.getheader("Content-Range"), "bytes 1-3/10")
            self.assertEqual(response.read(), b"ide")

            retry_body = b"{}"
            connection.request(
                "POST",
                "/api/collector/transfers/retry",
                body=retry_body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(retry_body)),
                },
            )
            response = connection.getresponse()
            retry = json.loads(response.read())
            self.assertEqual(response.status, 202)
            self.assertEqual(retry["expedited"], 1)
            self.assertEqual(outbox.expedited, 1)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            runtime.close()

    def test_hex_secret_has_priority_and_invalid_hex_is_not_fallbacked(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BLOGGER_AGENT_TRANSFER_SECRET_HEX": "ab" * 32,
                "BLOGGER_AGENT_TRANSFER_SECRET": "ignored-plain-text-secret",
            },
            clear=False,
        ):
            self.assertEqual(collector_server._transfer_secret(), bytes.fromhex("ab" * 32))
        with patch.dict(
            os.environ,
            {
                "BLOGGER_AGENT_TRANSFER_SECRET_HEX": "not-hex",
                "BLOGGER_AGENT_TRANSFER_SECRET": "x" * 40,
            },
            clear=False,
        ):
            self.assertEqual(collector_server._transfer_secret(), b"")


if __name__ == "__main__":
    unittest.main()
