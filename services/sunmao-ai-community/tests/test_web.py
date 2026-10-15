from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMMUNITY_ROOT = PROJECT_ROOT / "web"


class SunmaoCommunityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(
            (COMMUNITY_ROOT / "videos.json").read_text(encoding="utf-8")
        )
        self.videos = self.payload["videos"]

    def test_collection_contains_exactly_34_contiguous_unique_episodes(self) -> None:
        episodes = [video["episode"] for video in self.videos]
        self.assertEqual(episodes, list(range(1, 35)))
        self.assertEqual(len({video["video_url"] for video in self.videos}), 34)
        self.assertEqual(self.payload["collection"]["id"], "7288595551828854799")
        self.assertEqual(self.payload["collection"]["author"], "阿木爷爷")

    def test_each_card_uses_a_canonical_douyin_video_and_cover(self) -> None:
        for video in self.videos:
            with self.subTest(episode=video["episode"]):
                source = urlsplit(video["video_url"])
                cover = urlsplit(video["cover_url"])
                self.assertEqual(source.scheme, "https")
                self.assertEqual(source.hostname, "www.douyin.com")
                self.assertRegex(source.path, r"^/video/[0-9]{15,22}$")
                self.assertEqual(source.query, "")
                self.assertEqual(cover.scheme, "https")
                self.assertTrue(
                    cover.hostname == "douyinpic.com"
                    or str(cover.hostname).endswith(".douyinpic.com")
                )
                self.assertTrue(
                    video["title"].startswith(f"第{video['episode']}集 |")
                )

    def test_video_cards_remain_title_cover_and_link_only(self) -> None:
        html = (COMMUNITY_ROOT / "index.html").read_text(encoding="utf-8")
        script = (COMMUNITY_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="videoGrid"', html)
        self.assertIn('className = "video-title"', script)
        self.assertIn('className = "cover-frame"', script)
        self.assertIn('link.target = "_blank"', script)
        self.assertIn('link.rel = "noopener noreferrer"', script)
        self.assertNotRegex(html + script, re.compile(r"<(?:video|audio|iframe)\b", re.I))

    def test_bottom_navigation_opens_discussion_sizes_and_joint_views(self) -> None:
        html = (COMMUNITY_ROOT / "index.html").read_text(encoding="utf-8")
        script = (COMMUNITY_ROOT / "app.js").read_text(encoding="utf-8")
        css = (COMMUNITY_ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertLess(
            html.index('data-view-target="sizes"'),
            html.index('data-view-target="exchange"'),
        )
        self.assertLess(
            html.index('data-view-target="exchange"'),
            html.index('data-view-target="joints"'),
        )
        self.assertIn('<span>尺寸型</span>', html)
        self.assertIn('<span>交流区</span>', html)
        self.assertIn('<span>榫卯108道</span>', html)
        self.assertIn('data-view="exchange"', html)
        self.assertIn('data-view="sizes"', html)
        self.assertIn('data-view="joints"', html)
        self.assertIn('position: fixed;', css)
        self.assertIn('bottom: 0;', css)
        self.assertIn('const VALID_VIEWS = new Set(["exchange", "sizes", "joints"])', script)

    def test_discussion_is_text_only_and_uses_same_origin_api(self) -> None:
        html = (COMMUNITY_ROOT / "index.html").read_text(encoding="utf-8")
        script = (COMMUNITY_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="messageForm"', html)
        self.assertIn('maxlength="20"', html)
        self.assertIn('maxlength="500"', html)
        self.assertIn("这是公开交流区", html)
        self.assertIn('fetch("./api/messages?limit=80"', script)
        self.assertIn('fetch("./api/messages"', script)
        self.assertIn("body.textContent = message.body", script)
        self.assertNotIn("innerHTML", script)

    def test_mobile_layout_and_accessible_loading_state_are_present(self) -> None:
        html = (COMMUNITY_ROOT / "index.html").read_text(encoding="utf-8")
        css = (COMMUNITY_ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn('name="viewport"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("@media (max-width: 700px)", css)
        self.assertIn("grid-template-columns: repeat(2", css)

    def test_first_party_analytics_and_private_admin_assets_are_wired(self) -> None:
        html = (COMMUNITY_ROOT / "index.html").read_text(encoding="utf-8")
        script = (COMMUNITY_ROOT / "app.js").read_text(encoding="utf-8")
        admin_html = (COMMUNITY_ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        admin_script = (COMMUNITY_ROOT / "admin" / "admin.js").read_text(encoding="utf-8")
        self.assertIn('/api/analytics/pageview', script)
        self.assertIn("不保存 IP 地址", html)
        self.assertIn('name="robots" content="noindex,nofollow"', admin_html)
        self.assertIn('/api/admin/analytics', admin_script)
        self.assertNotIn("innerHTML", admin_script)


if __name__ == "__main__":
    unittest.main()
