from __future__ import annotations

import unittest

from mx_agent.downloader_engine.douyin_core import (
    _find_aweme_with_images,
    _image_urls_from_aweme,
)


class DouyinImageTests(unittest.TestCase):
    def test_finds_nested_image_work_and_keeps_one_original_url_per_image(self):
        work_id = "7000000000000000003"
        payload = {
            "data": {
                "aweme_detail": {
                    "aweme_id": work_id,
                    "desc": "测试图文",
                    "images": [
                        {
                            "download_url_list": [
                                "https://p3.douyinpic.com/original-1.jpg",
                                "https://p11.douyinpic.com/mirror-1.jpg",
                            ]
                        },
                        {
                            "url_list": [
                                "https://p3.douyinpic.com/original-2.jpg",
                                "https://p11.douyinpic.com/mirror-2.jpg",
                            ]
                        },
                    ],
                }
            }
        }

        aweme = _find_aweme_with_images(payload, work_id)

        self.assertIsNotNone(aweme)
        self.assertEqual(
            _image_urls_from_aweme(aweme or {}),
            [
                "https://p3.douyinpic.com/original-1.jpg",
                "https://p3.douyinpic.com/original-2.jpg",
            ],
        )


if __name__ == "__main__":
    unittest.main()
