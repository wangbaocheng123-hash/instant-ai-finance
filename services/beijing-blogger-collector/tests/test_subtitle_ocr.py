from __future__ import annotations

import unittest

from mx_agent.subtitle_ocr import (
    clean_subtitle_text,
    collapse_subtitle_samples,
    merge_adjacent_ocr_variants,
    merge_visual_and_audio_segments,
    parse_srt,
    stitch_segment_text,
    subtitle_text_from_payload,
)


class SubtitleOcrTests(unittest.TestCase):
    def test_largest_caption_line_excludes_douyin_interface_text(self) -> None:
        payload = {
            "lines": [
                {
                    "text": "现 在 市 场 最 好 的 走 势 啊",
                    "words": [{"y": 72, "height": 60}],
                },
                {
                    "text": "@模 型 先 生 · 39 分 钟 前",
                    "words": [{"y": 154, "height": 40}],
                },
                {
                    "text": "理 财 有 风 险 ， 投 资 需 谨 慎",
                    "words": [{"y": 236, "height": 29}],
                },
                {
                    "text": "相 关 搜 索 · 模 组 三 剑 客",
                    "words": [{"y": 322, "height": 35}],
                },
            ]
        }
        self.assertEqual("现在市场最好的走势啊", subtitle_text_from_payload(payload))

    def test_interface_text_is_excluded_even_when_its_font_is_large(self) -> None:
        payload = {
            "lines": [
                {
                    "text": "因 为 你 进 出 的 逻 辑 要 是 一",
                    "words": [{"x": 120, "y": 40, "height": 58}],
                },
                {
                    "text": "致 啊",
                    "words": [{"x": 410, "y": 104, "height": 56}],
                },
                {
                    "text": "点 击 推 荐",
                    "words": [{"x": 60, "y": 110, "height": 55}],
                },
                {
                    "text": "理 财 有 风 险 ， 投 资 需 谨 慎",
                    "words": [{"x": 30, "y": 330, "height": 52}],
                },
            ]
        }
        self.assertEqual("因为你进出的逻辑要是一致啊", subtitle_text_from_payload(payload))

    def test_small_control_words_are_removed_from_the_same_ocr_line(self) -> None:
        payload = {
            "lines": [
                {
                    "text": "点 击 了 那 你 扛 就 有 问 题 了",
                    "words": [
                        {"text": "点", "x": 70, "y": 105, "height": 29},
                        {"text": "击", "x": 105, "y": 105, "height": 29},
                        {"text": "了", "x": 250, "y": 70, "height": 56},
                        {"text": "那", "x": 310, "y": 70, "height": 59},
                        {"text": "你", "x": 370, "y": 70, "height": 60},
                        {"text": "扛", "x": 430, "y": 70, "height": 59},
                        {"text": "就", "x": 490, "y": 70, "height": 59},
                        {"text": "有", "x": 550, "y": 70, "height": 60},
                        {"text": "问", "x": 610, "y": 70, "height": 60},
                        {"text": "题", "x": 670, "y": 70, "height": 57},
                        {"text": "了", "x": 730, "y": 70, "height": 55},
                    ],
                },
                {
                    "text": "里 有 风 险 ， 投 资 需 谨 慎",
                    "words": [
                        {"text": "里", "x": 0, "y": 332, "height": 48},
                        {"text": "有", "x": 114, "y": 332, "height": 27},
                        {"text": "风", "x": 143, "y": 332, "height": 27},
                        {"text": "险", "x": 172, "y": 332, "height": 28},
                    ],
                },
                {
                    "text": "首 页",
                    "words": [{"text": "首页", "x": 36, "y": 425, "height": 38}],
                },
            ]
        }
        self.assertEqual("了那你扛就有问题了", subtitle_text_from_payload(payload))

    def test_centered_caption_is_kept_when_it_is_low_in_the_crop(self) -> None:
        payload = {
            "lines": [
                {
                    "text": "孙 子 兵 法 里 说 智 者 之 虑",
                    "words": [
                        {
                            "text": char,
                            "x": 345 + index * 104,
                            "y": 365,
                            "width": 101,
                            "height": 100,
                        }
                        for index, char in enumerate("孙子兵法里说智者之虑")
                    ],
                }
            ]
        }
        self.assertEqual("孙子兵法里说智者之虑", subtitle_text_from_payload(payload))

    def test_repeated_frames_collapse_to_one_segment(self) -> None:
        segments = collapse_subtitle_samples(
            [
                {"timestamp": 0.0, "text": "回避高位科技股"},
                {"timestamp": 0.667, "text": "回避高位科技股啊"},
                {"timestamp": 1.334, "text": "回避高位科技股啊"},
            ],
            frame_interval=0.667,
        )
        self.assertEqual(1, len(segments))
        self.assertEqual("回避高位科技股啊", segments[0]["text"])
        self.assertEqual(3, segments[0]["sample_count"])

    def test_parse_srt_and_audio_only_fills_visual_gap(self) -> None:
        audio = parse_srt(
            """1
00:00:00,000 --> 00:00:02,000
音频错误内容

2
00:00:04,000 --> 00:00:06,000
这里没有画面字幕
"""
        )
        visual = [
            {"start": 0.0, "end": 2.2, "text": "画面正确字幕", "source": "video-subtitle-ocr"}
        ]
        merged = merge_visual_and_audio_segments(visual, audio)
        self.assertEqual(["画面正确字幕", "这里没有画面字幕"], [item["text"] for item in merged])
        self.assertEqual("whisper.cpp-gap-fill", merged[1]["source"])

    def test_audio_repairs_incomplete_visual_caption(self) -> None:
        visual = [
            {
                "start": 0.0,
                "end": 2.2,
                "text": "因为你进出的逻辑要是一",
                "source": "video-subtitle-ocr",
            }
        ]
        audio = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "因为你进出的逻辑要一致啊",
                "source": "whisper.cpp",
            }
        ]
        merged = merge_visual_and_audio_segments(visual, audio)
        self.assertEqual("因为你进出的逻辑要一致啊", merged[0]["text"])
        self.assertEqual("whisper.cpp-visual-repair", merged[0]["source"])

    def test_audio_replaces_visual_interface_noise(self) -> None:
        visual = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "科技股是否见底理财有风险投资需谨慎",
                "source": "video-subtitle-ocr",
            }
        ]
        audio = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "科技股是否见底重点观察这四只股票",
                "source": "whisper.cpp",
            }
        ]
        merged = merge_visual_and_audio_segments(visual, audio)
        self.assertEqual("科技股是否见底重点观察这四只股票", merged[0]["text"])

    def test_visual_finance_terms_win_over_similar_audio_misrecognition(self) -> None:
        visual = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "重点观察中际旭创寒武纪",
                "source": "video-subtitle-ocr",
            }
        ]
        audio = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "重点观察终极蓄创寒午季",
                "source": "whisper.cpp",
            }
        ]
        merged = merge_visual_and_audio_segments(visual, audio)
        self.assertEqual("重点观察中际旭创寒武纪", merged[0]["text"])
        self.assertEqual("video-subtitle-ocr-fused", merged[0]["source"])

    def test_stitch_removes_overlapping_subtitle_fragments(self) -> None:
        text = stitch_segment_text(
            [
                {"text": "最后一波行情的话"},
                {"text": "行情的话其实我们明确指出"},
                {"text": "明确指出30号是高点"},
            ]
        )
        self.assertEqual("最后一波行情的话其实我们明确指出30号是高点", text)

    def test_stitch_does_not_force_punctuation_between_contiguous_fragments(self) -> None:
        text = stitch_segment_text(
            [
                {"start": 0.0, "end": 1.0, "text": "因为你进出的"},
                {"start": 1.05, "end": 2.0, "text": "逻辑要是一致啊"},
            ]
        )
        self.assertEqual("因为你进出的逻辑要是一致啊", text)

    def test_transition_noise_is_merged_and_common_ocr_errors_are_cleaned(self) -> None:
        segments = merge_adjacent_ocr_variants(
            [
                {"start": 19.0, "end": 21.0, "text": "其实我们这个明确指出30号是高点", "sample_count": 3},
                {"start": 21.0, "end": 22.0, "text": "其实我们这个明旨出3", "sample_count": 1},
            ],
            frame_interval=1.0,
        )
        self.assertEqual(1, len(segments))
        self.assertEqual("其实我们这个明确指出30号是高点", segments[0]["text"])
        self.assertEqual("我最近在看多商业航天", clean_subtitle_text("为，我最在看多商业航天"))


if __name__ == "__main__":
    unittest.main()
