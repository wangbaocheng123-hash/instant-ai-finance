from __future__ import annotations

import http.client
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from sunmao_community.app import create_http_server
from sunmao_community.size_library import SizeLibraryStore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = PROJECT_ROOT / "web" / "size-resources.json"
ONE_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05"
    b"\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class SizeLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_seed_uses_the_verified_article_title(self) -> None:
        store = SizeLibraryStore(
            self.root / "community.sqlite3", self.root / "knowledge", SEED_PATH
        )
        resources = store.list_resources()
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["title"], "七代鲁班凳视频完整制作过程")
        self.assertEqual(resources[0]["type"], "external")
        with closing(sqlite3.connect(self.root / "community.sqlite3")) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            version = connection.execute(
                "SELECT version FROM knowledge_schema_versions"
            ).fetchone()
        self.assertTrue(
            {
                "knowledge_items",
                "knowledge_sources",
                "knowledge_measurements",
                "knowledge_assets",
                "knowledge_relations",
            }.issubset(tables)
        )
        self.assertEqual(version, (1,))

    def test_gallery_metadata_is_in_sqlite_and_original_is_outside_git(self) -> None:
        source_image = self.root / "approved-source.png"
        source_image.write_bytes(ONE_PIXEL_PNG)
        store = SizeLibraryStore(
            self.root / "community.sqlite3", self.root / "knowledge", SEED_PATH
        )
        gallery = store.import_gallery(
            {
                "id": "douyin-7674905792784469248",
                "sequence": 2,
                "title": "已核实的图文标题",
                "source": "抖音图文",
                "summary": "社区保存的原图资料。",
                "source_url": "https://www.douyin.com/note/7674905792784469248",
            },
            [source_image],
        )
        self.assertEqual(gallery["type"], "gallery")
        self.assertEqual(len(gallery["assets"]), 1)
        descriptor = store.get_asset(gallery["assets"][0]["id"])
        self.assertIsNotNone(descriptor)
        stored_path, content_type = descriptor or (Path(), "")
        self.assertEqual(content_type, "image/png")
        self.assertEqual(stored_path.read_bytes(), ONE_PIXEL_PNG)
        self.assertTrue((self.root / "knowledge") in stored_path.parents)
        self.assertIsNone(store.get_asset("../../community.sqlite3"))
        with closing(sqlite3.connect(self.root / "community.sqlite3")) as connection:
            connection.execute(
                "UPDATE knowledge_assets SET public_level = 'private' WHERE id = ?",
                (gallery["assets"][0]["id"],),
            )
            connection.commit()
        self.assertIsNone(store.get_asset(gallery["assets"][0]["id"]))

    def test_gallery_asset_is_served_only_by_opaque_same_origin_url(self) -> None:
        source_image = self.root / "approved-source.png"
        source_image.write_bytes(ONE_PIXEL_PNG)
        server = create_http_server(
            port=0,
            database_path=self.root / "community.sqlite3",
            knowledge_root=self.root / "knowledge",
        )
        gallery = server.size_library.import_gallery(
            {
                "id": "douyin-7674905792784469248",
                "sequence": 2,
                "title": "已核实的图文标题",
                "source": "抖音图文",
                "summary": "社区保存的原图资料。",
                "source_url": "https://www.douyin.com/note/7674905792784469248",
            },
            [source_image],
        )
        asset_url = gallery["assets"][0]["url"]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            connection.request("GET", asset_url)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "image/png")
            self.assertEqual(response.read(), ONE_PIXEL_PNG)
            connection.close()

            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            connection.request("GET", "/api/size-assets/../../community.sqlite3")
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
