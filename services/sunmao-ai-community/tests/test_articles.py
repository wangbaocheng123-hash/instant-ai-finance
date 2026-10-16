from __future__ import annotations

import copy
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.import_size_articles import bundle_images
from sunmao_community.app import create_http_server
from sunmao_community.articles import import_article, trusted_article_url, validate_blocks
from sunmao_community.size_library import SizeLibraryStore
from test_size_library import ONE_PIXEL_PNG, SEED_PATH


class ArticleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / "community.sqlite3"
        self.knowledge = self.root / "knowledge"
        self.store = SizeLibraryStore(self.database, self.knowledge, SEED_PATH)
        self.image = self.root / "source.png"
        self.image.write_bytes(ONE_PIXEL_PNG)
        self.metadata = {
            "id": "wechat-test-article", "sequence": 2, "title": "测试家具资料",
            "source": "阿木爷爷微信公众号", "summary": "仅供测试的合成资料",
            "authorization": "owner_authorized_copy",
            "source_url": "https://mp.weixin.qq.com/s/synthetic-test-article",
            "blocks": [
                {"type": "paragraph", "text": "<script>notExecuted()</script>"},
                {"type": "image", "position": 1, "alt": "测试图"},
                {"type": "heading", "text": "制作视频"},
                {"type": "video", "text": "测试视频", "url": "https://mp.weixin.qq.com/mp/readtemplate?t=pages/video_player_tmpl&action=mpvideo&auto=0&vid=wxv_1234567890123456789"},
            ],
        }

    def tearDown(self):
        self.temp.cleanup()

    def imported(self):
        return import_article(self.store, self.metadata, [self.image])

    def test_article_preserves_order_and_returns_only_public_image_ids(self):
        result = self.imported()
        self.assertEqual([x["type"] for x in result["blocks"]], ["paragraph", "image", "heading", "video"])
        self.assertEqual((result["type"], result["image_count"], result["video_count"]), ("article", 1, 1))
        self.assertEqual(result["blocks"][0]["text"], self.metadata["blocks"][0]["text"])
        self.assertNotIn("storage_key", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertRegex(result["blocks"][1]["url"], r"^/api/size-assets/[A-Za-z0-9_-]{24}$")
        self.assertEqual(self.store.list_resources()[0]["title"], "七代鲁班凳视频完整制作过程")

    def test_identical_retry_is_idempotent_and_preserves_files(self):
        first = self.imported()
        files = sorted(self.knowledge.rglob("original.*"))
        self.assertEqual(self.imported(), first)
        self.assertEqual(sorted(self.knowledge.rglob("original.*")), files)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT version FROM knowledge_items WHERE id=?", (self.metadata["id"],)).fetchone(), (1,))

    def test_existing_article_is_not_overwritten(self):
        first = self.imported()
        changed = dict(self.metadata, title="另一个标题")
        with self.assertRaisesRegex(ValueError, "different_content"):
            import_article(self.store, changed, [self.image])
        self.assertEqual(self.store.list_resources()[-1], first)

    def test_duplicate_source_and_sequence_conflicts_do_not_replace_items(self):
        self.imported()
        with self.assertRaisesRegex(ValueError, "source_already_imported"):
            import_article(self.store, dict(self.metadata, id="another-id", sequence=3), [self.image])
        with self.assertRaisesRegex(ValueError, "sequence_in_use"):
            import_article(self.store, dict(self.metadata, id="another-id", source_url="https://mp.weixin.qq.com/s/another-test-source"), [self.image])
        self.assertEqual(len(self.store.list_resources()), 2)

    def test_video_only_article_does_not_need_a_fake_image(self):
        metadata = dict(self.metadata, blocks=[self.metadata["blocks"][-1]])
        result = import_article(self.store, metadata, [])
        self.assertEqual((result["image_count"], result["video_count"]), (0, 1))

    def test_unapproved_content_or_bad_image_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "authorization_required"):
            import_article(self.store, dict(self.metadata, authorization="link_only"), [self.image])
        self.image.write_bytes(b"<html>blocked</html>")
        with self.assertRaisesRegex(ValueError, "unsupported_image_type"):
            self.imported()
        self.assertEqual(len(self.store.list_resources()), 1)
        self.assertFalse(list(self.knowledge.rglob("original.*")))

    def test_url_allowlist_rejects_scripts_untrusted_hosts_and_extra_query(self):
        good = self.metadata["blocks"][-1]["url"]
        self.assertTrue(trusted_article_url(good, video=True))
        self.assertFalse(trusted_article_url(good))
        for value in ["javascript:alert(1)", "https://mp.weixin.qq.com.evil.test/s/synthetic-test-article", good + "&vid=wxv_1234567890123456789", good + "#extra", good + "&token=secret", "https://user@mp.weixin.qq.com/s/synthetic-test-article", "https://mp.weixin.qq.com:443/s/synthetic-test-article"]:
            with self.subTest(value=value):
                self.assertFalse(trusted_article_url(value, video=True))
                with self.assertRaisesRegex(ValueError, "invalid_article_video"):
                    validate_blocks([{"type": "video", "text": "video", "url": value}], 0)

    def test_missing_image_references_and_unrecognized_blocks_are_rejected(self):
        for blocks in [[{"type": "html", "text": "unsafe"}], [{"type": "image", "position": True}], [{"type": "image", "position": 2}], [{"type": "paragraph", "text": "image omitted"}]]:
            with self.subTest(blocks=blocks), self.assertRaises(ValueError):
                validate_blocks(blocks, 1)

    def test_private_or_withdrawn_article_is_not_exposed(self):
        result = self.imported()
        asset_id = result["blocks"][1]["url"].rsplit("/", 1)[-1]
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE knowledge_assets SET public_level='private' WHERE id=?", (asset_id,))
        self.assertEqual(len(self.store.list_resources()), 1)
        self.assertIsNone(self.store.get_asset(asset_id))
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE knowledge_assets SET public_level='public' WHERE id=?", (asset_id,))
            connection.execute("UPDATE knowledge_items SET review_status='withdrawn' WHERE id=?", (self.metadata["id"],))
        self.assertEqual(len(self.store.list_resources()), 1)
        self.assertIsNone(self.store.get_asset(asset_id))

    def test_failed_file_write_rolls_back_new_item_and_files(self):
        with patch("sunmao_community.articles.secrets.token_urlsafe", side_effect=["a" * 24, "a" * 24]):
            metadata = copy.deepcopy(self.metadata)
            metadata["blocks"].append({"type": "image", "position": 2})
            with self.assertRaises(FileExistsError):
                import_article(self.store, metadata, [self.image, self.image])
        self.assertEqual(len(self.store.list_resources()), 1)
        self.assertEqual(list((self.knowledge / "originals" / "images").iterdir()), [])

    def test_manifest_verifies_sha_and_rejects_escape(self):
        manifest = self.root / "article.json"
        digest = hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
        self.assertEqual(bundle_images(manifest, {"images": [{"file": "source.png", "sha256": digest}]}), [self.image])
        with self.assertRaisesRegex(ValueError, "digest_mismatch"):
            bundle_images(manifest, {"images": [{"file": "source.png", "sha256": "0" * 64}]})
        folder = self.root / "package"
        folder.mkdir()
        with self.assertRaisesRegex(ValueError, "outside_bundle"):
            bundle_images(folder / "article.json", {"images": [{"file": "../source.png", "sha256": digest}]})

    def test_http_api_returns_article_and_serves_its_image(self):
        result = self.imported()
        server = create_http_server(port=0, database_path=self.database, knowledge_root=self.knowledge)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/api/size-resources")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["resources"][-1], result)
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", result["blocks"][1]["url"])
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "image/png")
            self.assertEqual(response.read(), ONE_PIXEL_PNG)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
