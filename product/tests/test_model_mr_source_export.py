from __future__ import annotations

import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instant_ai.model_mr import ModelMrClient


LIST_URL = "https://www.douyin.com/video/7000000000000000001"
DETAIL_URL = "https://www.douyin.com/video/7000000000000000002"
WORK_ID = 340  # Deliberately unrelated to either public source work ID.


class ModelMrSourceExportTests(unittest.TestCase):
    def export_owner(self, list_source: dict, detail_source: dict) -> tuple[dict, dict]:
        listed = {"id": WORK_ID, "title": "Synthetic source work", **list_source}
        detail = {
            "video": {"id": WORK_ID, "title": "Synthetic detail work", **detail_source},
            "notes": {"video_text": {"text": "Preserve original text", "official": True},
                      "interpretation": {"text": "Preserve interpretation"}},
            "comments": [{"id": 22, "text": "Preserve comment"}],
        }
        originals = copy.deepcopy((listed, detail))

        def source_response(path: str, **_kwargs) -> dict:
            if path.startswith("/api/videos?"):
                return {"items": [copy.deepcopy(listed)]}
            if path == f"/api/videos/{WORK_ID}":
                return copy.deepcopy(detail)
            if path == f"/api/videos/{WORK_ID}/stock-mentions?limit=20":
                return {}
            if path.startswith("/api/investment-thoughts?"):
                return {"categories": [], "links_by_video": {}}
            raise AssertionError(f"Unexpected source endpoint: {path}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "synthetic-media"
            media.mkdir()
            manifest = root / "media-manifest.json"
            manifest.write_text(json.dumps({"backup_root": str(media), "items": []}), encoding="utf-8")
            output = root / "export" / "public-snapshot.json"
            client = ModelMrClient("http://127.0.0.1:9", output, media)
            with patch.object(ModelMrClient, "_json", side_effect=source_response):
                result = client.write_owner_library(output, media_manifest=manifest)
            snapshot = json.loads(output.read_text(encoding="utf-8"))
            exported = json.loads((output.parent / "details" / f"{WORK_ID}.json").read_text(encoding="utf-8"))
        self.assertEqual(result["works"], 1)
        self.assertEqual((listed, detail), originals, "Export must not mutate source responses")
        self.assertEqual(exported["video_text"]["text"], "Preserve original text")
        self.assertEqual(exported["interpretation"]["text"], "Preserve interpretation")
        self.assertEqual(exported["comments"][0]["text"], "Preserve comment")
        return snapshot["works"][0], exported["work"]

    def test_valid_list_url_survives_empty_or_invalid_detail_url(self) -> None:
        for detail_url in ("", "javascript:alert(1)"):
            with self.subTest(detail_url=detail_url):
                summary, detail = self.export_owner({"url": LIST_URL}, {"url": detail_url})
                self.assertEqual(summary["url"], LIST_URL)
                self.assertEqual(detail["url"], LIST_URL)

    def test_valid_detail_url_remains_preferred_over_list_url(self) -> None:
        summary, detail = self.export_owner({"url": LIST_URL}, {"url": DETAIL_URL})
        self.assertEqual(summary["url"], DETAIL_URL)
        self.assertEqual(detail["url"], DETAIL_URL)

    def test_missing_source_urls_stay_empty_without_guessing_from_internal_id(self) -> None:
        summary, detail = self.export_owner({}, {})
        self.assertEqual(summary["url"], "")
        self.assertEqual(detail["url"], "")
        self.assertEqual(summary["id"], WORK_ID)

    def test_legacy_source_url_is_exported_from_list_or_detail(self) -> None:
        for listed, detailed, expected in (
            ({"source_url": LIST_URL}, {}, LIST_URL),
            ({}, {"source_url": DETAIL_URL}, DETAIL_URL),
        ):
            with self.subTest(listed=listed, detailed=detailed):
                summary, detail = self.export_owner(listed, detailed)
                self.assertEqual(summary["url"], expected)
                self.assertEqual(detail["url"], expected)


class ModelMrSavedMetadataSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        script = Path(__file__).resolve().parents[2] / "scripts" / "model-mr-metadata.py"
        spec = importlib.util.spec_from_file_location("model_mr_metadata_export_test", script)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def export_metadata(self, source: dict) -> dict:
        videos = [{"id": WORK_ID, "title": "Synthetic metadata work", **source}]
        original = copy.deepcopy(videos)

        def source_response(url: str, **_kwargs):
            if url == "http://127.0.0.1:8787/api/videos?account=%E6%A8%A1%E5%9E%8B%E5%85%88%E7%94%9F&limit=500":
                payload = {"items": videos}
            elif url == "http://127.0.0.1:8787/api/investment-thoughts?limit=500":
                payload = {"categories": [], "links_by_video": {}}
            else:
                raise AssertionError(f"Unexpected source endpoint: {url}")
            return io.BytesIO(json.dumps(payload).encode("utf-8"))

        with patch.object(self.module, "urlopen", side_effect=source_response) as request:
            result = self.module.saved_metadata()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(videos, original)
        return result["works"][0]

    def test_metadata_supports_legacy_source_url_and_strips_tracking_fields(self) -> None:
        for primary in ("", "javascript:alert(1)"):
            with self.subTest(primary=primary):
                result = self.export_metadata({"url": primary, "source_url": LIST_URL + "?tracking=synthetic#part"})
                self.assertEqual(result["url"], LIST_URL)

    def test_metadata_keeps_valid_primary_url_before_legacy_fallback(self) -> None:
        result = self.export_metadata({"url": LIST_URL, "source_url": DETAIL_URL})
        self.assertEqual(result["url"], LIST_URL)

    def test_metadata_without_valid_source_never_derives_url_from_library_id(self) -> None:
        for source in ({}, {"url": "javascript:alert(1)", "source_url": "https://example.test/video/340"}):
            with self.subTest(source=source):
                result = self.export_metadata(source)
                self.assertEqual(result["url"], "")
                self.assertEqual(result["id"], WORK_ID)


if __name__ == "__main__":
    unittest.main()
