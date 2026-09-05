from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mx_agent import collector_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "collector_web"


class NorthPoleBrandTests(unittest.TestCase):
    def test_icon_dimensions_and_true_colour_format(self) -> None:
        expected = {
            "north-pole-collector-icon-1024.png": (1024, 1024),
            "north-pole-collector-icon-512.png": (512, 512),
            "north-pole-collector-icon-192.png": (192, 192),
            "apple-touch-icon.png": (180, 180),
            "favicon-32.png": (32, 32),
        }
        for filename, dimensions in expected.items():
            data = (WEB_ROOT / filename).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", filename)
            width, height, bit_depth, colour_type = struct.unpack(
                ">IIBB", data[16:26]
            )
            self.assertEqual((width, height), dimensions, filename)
            self.assertEqual(bit_depth, 8, filename)
            self.assertEqual(colour_type, 2, filename)

    def test_manifest_and_html_publish_the_north_pole_identity(self) -> None:
        manifest = json.loads((WEB_ROOT / "manifest.webmanifest").read_text("utf-8"))
        self.assertEqual(manifest["name"], "北极采集器")
        self.assertEqual(manifest["short_name"], "北极采集")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["theme_color"], "#0b1d3a")
        self.assertEqual(
            {icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"}
        )

        for filename in ("index.html", "hub.html"):
            page = (WEB_ROOT / filename).read_text("utf-8")
            self.assertIn('name="apple-mobile-web-app-capable" content="yes"', page)
            self.assertIn('name="apple-mobile-web-app-title" content="北极采集器"', page)
            self.assertIn('name="theme-color" content="#0b1d3a"', page)
            self.assertIn('rel="apple-touch-icon"', page)
            self.assertIn('href="/collector/manifest.webmanifest"', page)

    def test_deployment_metadata_is_strictly_bounded(self) -> None:
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text("1.0.8\n", encoding="utf-8")
            (root / "DEPLOYMENT.json").write_text(
                json.dumps(
                    {
                        "repository_revision": revision,
                        "deployed_time": "2026-09-05T05:06:07Z",
                        "secret": "must-not-appear",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(collector_server, "ROOT_DIR", root):
                payload = collector_server._deployment_version()

        self.assertEqual(
            payload,
            {
                "service": "blogger-collector",
                "status": "ok",
                "version": "1.0.8",
                "repository_revision": revision,
                "deployed_time": "2026-09-05T05:06:07Z",
            },
        )


if __name__ == "__main__":
    unittest.main()
