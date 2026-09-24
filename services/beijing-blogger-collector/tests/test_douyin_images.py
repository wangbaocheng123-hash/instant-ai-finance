from __future__ import annotations

import json
import unittest

from mx_agent.downloader_engine.douyin_core import (
    _find_aweme_with_images,
    _image_description_from_aweme,
    _image_title_from_aweme,
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

    def test_text_article_keeps_markdown_and_public_origin_cover(self):
        work_id = "7000000000000000004"
        payload = {
            "aweme_detail": {
                "aweme_id": work_id,
                "desc": "",
                "article_info": {
                    "article_title": "长图文标题",
                    "article_content": json.dumps(
                        {"long_article_abstract": "", "markdown": "第一段正文\n\n第二段正文"},
                        ensure_ascii=False,
                    ),
                    "fe_data": json.dumps({"image_length": 0, "image_list": []}),
                },
                "video": {
                    "origin_cover": {
                        "url_list": ["https://p3.douyinpic.com/article-cover.jpg"]
                    }
                },
            }
        }
        aweme = _find_aweme_with_images(payload, work_id)
        self.assertIsNotNone(aweme)
        self.assertEqual(_image_description_from_aweme(aweme or {}), "第一段正文\n\n第二段正文")
        self.assertEqual(_image_title_from_aweme(aweme or {}), "长图文标题")
        self.assertEqual(
            _image_urls_from_aweme(aweme or {}),
            ["https://p3.douyinpic.com/article-cover.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
