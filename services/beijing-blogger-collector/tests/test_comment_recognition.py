from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from mx_agent.comment_ocr import parse_positioned_comments
from mx_agent.comment_vision import CommentRecognitionService


def payload_line(text: str, x: int, y: int, width: int, height: int) -> dict:
    return {
        "text": text,
        "words": [
            {
                "text": text,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
        ],
    }


def sample_payload() -> dict:
    return {
        "lines": [
            payload_line("临门一脚", 80, 22, 76, 18),
            payload_line("大哥，卫星etf套住了30个点，可以等b浪反弹再走吗", 80, 56, 430, 22),
            payload_line("8分钟前·安徽回复", 80, 96, 170, 18),
            payload_line("模型先生", 120, 150, 76, 18),
            payload_line("etf问题不大", 120, 184, 150, 22),
            payload_line("4分钟前·安徽回复", 120, 224, 170, 18),
        ]
    }


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.outputs = [
            {
                "author": "临门一脚",
                "text": "大哥，卫星etf套住了30个点，可以等b浪反弹再走吗",
                "published_at": "8分钟前·安徽",
                "is_model_author": False,
                "author_badge_visible": False,
                "confidence": 0.98,
            },
            {
                "author": "模型先生",
                "text": "etf问题不大",
                "published_at": "4分钟前·安徽",
                "is_model_author": True,
                "author_badge_visible": True,
                "confidence": 0.99,
            },
        ]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs[len(self.calls) - 1]
        return SimpleNamespace(
            id=f"resp-comment-{len(self.calls)}",
            output_text=json.dumps(output, ensure_ascii=False),
        )


class CommentRecognitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.image_path = Path(self.temp_dir.name) / "comments.png"
        image = Image.new("RGB", (560, 280), "white")
        # Only the second nickname row has the red Douyin author badge.
        ImageDraw.Draw(image).rectangle((202, 149, 241, 169), fill=(255, 45, 85))
        image.save(self.image_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_author_badge_is_bound_to_its_own_nickname_row(self) -> None:
        with patch("mx_agent.comment_ocr.recognize_nickname", return_value=""):
            comments = parse_positioned_comments(
                self.image_path,
                sample_payload(),
                author_name="模型先生",
            )

        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]["author"], "临门一脚")
        self.assertEqual(comments[0]["kind"], "user_comment")
        self.assertEqual(comments[1]["author"], "模型先生")
        self.assertEqual(comments[1]["kind"], "author_reply")

    def test_hybrid_recognition_normalizes_financial_tokens_and_keeps_roles(self) -> None:
        responses = FakeResponses()
        client = SimpleNamespace(responses=responses)
        settings = SimpleNamespace(openai_api_key="test-key")
        service = CommentRecognitionService(
            settings,
            client_factory=lambda _key: client,
        )

        with (
            patch("mx_agent.comment_vision.ocr_image_payload", return_value=sample_payload()),
            patch("mx_agent.comment_ocr.recognize_nickname", return_value=""),
        ):
            result = service.recognize(
                self.image_path,
                author_name="模型先生",
                mode="hybrid",
            )

        self.assertEqual(result["engine"], "hybrid-openai-vision")
        self.assertEqual(len(responses.calls), 2)
        self.assertTrue(all(call["model"] == "gpt-5.4-mini" for call in responses.calls))
        self.assertTrue(all(call["store"] is False for call in responses.calls))
        self.assertTrue(all(call["reasoning"] == {"effort": "none"} for call in responses.calls))
        self.assertIn("卫星ETF", result["comments"][0]["text"])
        self.assertIn("B浪", result["comments"][0]["text"])
        self.assertEqual(result["comments"][0]["kind"], "user_comment")
        self.assertEqual(result["comments"][1]["text"], "ETF问题不大")
        self.assertEqual(result["comments"][1]["kind"], "author_reply")
        self.assertFalse(result["comments"][0]["needs_review"])
        self.assertFalse(result["comments"][1]["needs_review"])

    def test_local_mode_never_calls_api_and_always_requires_confirmation(self) -> None:
        responses = FakeResponses()
        service = CommentRecognitionService(
            SimpleNamespace(openai_api_key="test-key"),
            client_factory=lambda _key: SimpleNamespace(responses=responses),
        )
        with (
            patch("mx_agent.comment_vision.ocr_image_payload", return_value=sample_payload()),
            patch("mx_agent.comment_ocr.recognize_nickname", return_value=""),
        ):
            result = service.recognize(
                self.image_path,
                author_name="模型先生",
                mode="local",
            )

        self.assertEqual(responses.calls, [])
        self.assertEqual(result["engine"], "windows-ocr")
        self.assertTrue(all(item["needs_review"] for item in result["comments"]))


if __name__ == "__main__":
    unittest.main()
