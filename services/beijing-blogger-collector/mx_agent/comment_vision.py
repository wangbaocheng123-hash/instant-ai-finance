from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance

from .settings import openai_api_is_enabled
from .comment_ocr import (
    insert_visual_author_markers,
    is_time_line,
    line_bounds,
    ocr_image_payload,
    parse_ocr_comments,
    parse_positioned_comments,
    positioned_line_text,
)


DEFAULT_COMMENT_OCR_MODEL = "gpt-5.4-mini"
DEFAULT_COMMENT_OCR_FALLBACK_MODEL = "gpt-5.4"
MIN_AI_CONFIDENCE = 0.90

COMMENT_OCR_INSTRUCTIONS = """你是抖音评论截图的逐字识别器。
输入图片已经裁剪为一条评论或一条回复。禁止总结、改写、润色、同义替换或补写。

识别规则：
1. 只返回当前这一条评论；用户名位于正文上方较小的文字或表情位置。
2. 保留图片中真实出现的中文、数字、英文和表情。无法辨认的用户名留空，不得把正文开头当用户名。
3. 金融缩写必须准确保留：图片中的 etf 输出为 ETF；a浪、b浪、c浪输出为 A浪、B浪、C浪。
4. 只有红色“作者”徽标与当前用户名在同一行并紧挨用户名右侧时，author_badge_visible 才为 true。
5. 只有用户名准确等于“{author_name}”且上述徽标存在时，is_model_author 才为 true。
6. 正文中出现“先生”“模型先生”或表达支持、感动，绝不代表发言者是作者。
7. published_at 只读取时间和地区，不要把“回复、分享”等按钮写入正文。
"""

COMMENT_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "author": {"type": "string"},
        "text": {"type": "string"},
        "published_at": {"type": "string"},
        "is_model_author": {"type": "boolean"},
        "author_badge_visible": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": [
        "author",
        "text",
        "published_at",
        "is_model_author",
        "author_badge_visible",
        "confidence",
    ],
    "additionalProperties": False,
}


class CommentRecognitionService:
    """Local OCR first, then per-comment vision verification.

    The image is split at locally detected timestamp rows before it is sent to
    the model.  A badge on the following reply can therefore never be used to
    classify the preceding fan comment as an author reply.
    """

    def __init__(self, settings: Any, client_factory: Any | None = None) -> None:
        self.settings = settings
        self._client_factory = client_factory

    def recognize(
        self,
        image_path: Path,
        author_name: str,
        mode: str = "hybrid",
    ) -> dict[str, Any]:
        payload = self._local_ocr_with_retry(image_path)
        lines = insert_visual_author_markers(image_path, payload, author_name=author_name)
        local_comments = (
            parse_positioned_comments(image_path, payload, author_name=author_name)
            or parse_ocr_comments(lines, author_name=author_name)
        )
        local_items = [self._prepare_local(item, author_name) for item in local_comments]
        regions = self._comment_regions(image_path, payload)

        if mode == "local":
            return {
                "engine": "windows-ocr",
                "model": "",
                "comments": local_items,
                "ocr_lines": lines,
                "warnings": ["本地识别结果尚未经过AI复核，请确认后再导入。"],
            }

        api_key = str(getattr(self.settings, "openai_api_key", "") or "").strip()
        if not openai_api_is_enabled(self.settings):
            return {
                "engine": "windows-ocr",
                "model": "",
                "comments": local_items,
                "ocr_lines": lines,
                "warnings": [
                    (
                        "OpenAI API 总开关已关闭，已返回本地识别预览。"
                        if api_key
                        else "未配置OPENAI_API_KEY，已返回本地识别预览。"
                    )
                ],
            }

        model = os.getenv("MX_AGENT_COMMENT_OCR_MODEL", DEFAULT_COMMENT_OCR_MODEL)
        fallback_model = os.getenv(
            "MX_AGENT_COMMENT_OCR_FALLBACK_MODEL",
            DEFAULT_COMMENT_OCR_FALLBACK_MODEL,
        )
        warnings: list[str] = []
        merged: list[dict[str, Any]] = []
        response_ids: list[str] = []

        for index, local_item in enumerate(local_items):
            if index >= len(regions):
                fallback = dict(local_item)
                fallback["needs_review"] = True
                fallback["review_reason"] = "未能定位这一条评论的独立图片区域"
                merged.append(fallback)
                continue

            try:
                ai_item, response_id = self._recognize_crop(
                    regions[index],
                    author_name,
                    model,
                )
                if response_id:
                    response_ids.append(response_id)
                used_model = model
                if self._needs_fallback(local_item, ai_item, author_name) and fallback_model:
                    ai_item, response_id = self._recognize_crop(
                        regions[index],
                        author_name,
                        fallback_model,
                    )
                    used_model = fallback_model
                    if response_id:
                        response_ids.append(response_id)
                merged.append(
                    self._merge_comment(
                        local_item,
                        ai_item,
                        author_name=author_name,
                        model=used_model,
                    )
                )
            except Exception as exc:
                fallback = dict(local_item)
                fallback["needs_review"] = True
                fallback["review_reason"] = "AI复核失败，当前显示本地OCR结果"
                merged.append(fallback)
                warnings.append(f"第{index + 1}条AI复核失败：{exc}")

        return {
            "engine": "hybrid-openai-vision",
            "model": model,
            "fallback_model": fallback_model,
            "comments": merged,
            "ocr_lines": lines,
            "warnings": warnings,
            "response_ids": response_ids,
        }

    def _local_ocr_with_retry(self, image_path: Path) -> dict[str, Any]:
        """Retry transient Windows Runtime OCR failures before giving up."""

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return ocr_image_payload(image_path)
            except RuntimeError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.25 * (attempt + 1))
        assert last_error is not None
        raise RuntimeError("本地OCR连续三次启动失败，请稍后重试") from last_error

    def _comment_regions(
        self,
        image_path: Path,
        payload: dict[str, Any],
    ) -> list[Image.Image]:
        time_bounds: list[tuple[int, int, int, int]] = []
        for source in payload.get("lines", []):
            bounds = line_bounds(source)
            text = positioned_line_text(source)
            if bounds and is_time_line(text):
                time_bounds.append(bounds)
        time_bounds.sort(key=lambda item: (item[1], item[0]))

        image = Image.open(image_path).convert("RGB")
        regions: list[Image.Image] = []
        previous_bottom = 0
        for bounds in time_bounds:
            bottom = min(image.height, bounds[3] + 2)
            if bottom <= previous_bottom:
                continue
            crop = image.crop((0, max(0, previous_bottom), image.width, bottom))
            previous_bottom = bounds[3]
            regions.append(self._enhance_crop(crop))
        return regions

    def _enhance_crop(self, crop: Image.Image) -> Image.Image:
        target_width = min(1800, max(crop.width, 1600))
        scale = target_width / max(1, crop.width)
        if scale > 1.05:
            crop = crop.resize(
                (round(crop.width * scale), round(crop.height * scale)),
                Image.Resampling.LANCZOS,
            )
        crop = ImageEnhance.Sharpness(crop).enhance(1.5)
        return crop

    def _recognize_crop(
        self,
        crop: Image.Image,
        author_name: str,
        model: str,
    ) -> tuple[dict[str, Any], str]:
        image_buffer = io.BytesIO()
        crop.save(image_buffer, format="PNG")
        image_data = base64.b64encode(image_buffer.getvalue()).decode("ascii")
        response = self._client().responses.create(
            model=model,
            reasoning={"effort": "none"},
            instructions=COMMENT_OCR_INSTRUCTIONS.format(author_name=author_name),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "请逐字识别这一条评论，并按结构返回。",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{image_data}",
                            "detail": "high",
                        },
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "douyin_comment_ocr",
                    "description": "单条抖音评论或回复的逐字识别结果",
                    "schema": COMMENT_OCR_SCHEMA,
                    "strict": True,
                },
                "verbosity": "low",
            },
            max_output_tokens=600,
            store=False,
        )
        raw = str(getattr(response, "output_text", "") or "").strip()
        if not raw:
            raise RuntimeError("模型没有返回评论识别结果")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("模型返回的评论识别格式无法解析") from exc
        return parsed, str(getattr(response, "id", "") or "")

    def _prepare_local(
        self,
        item: dict[str, Any],
        author_name: str,
    ) -> dict[str, Any]:
        result = dict(item)
        result["text"] = normalize_financial_text(str(result.get("text") or ""))
        result["author"] = str(result.get("author") or "识图用户").strip() or "识图用户"
        result["author_badge_visible"] = (
            result.get("kind") == "author_reply"
            and result.get("author") == author_name
        )
        result["confidence"] = 0.65
        result["needs_review"] = True
        result["review_reason"] = "仅经过本地OCR"
        result["recognition_model"] = "windows-ocr"
        result["local_text"] = result["text"]
        result["ai_text"] = ""
        return result

    def _needs_fallback(
        self,
        local_item: dict[str, Any],
        ai_item: dict[str, Any],
        author_name: str,
    ) -> bool:
        try:
            confidence = float(ai_item.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        ai_text = str(ai_item.get("text") or "")
        ai_author = str(ai_item.get("author") or "")
        local_is_author = (
            local_item.get("kind") == "author_reply"
            and local_item.get("author") == author_name
        )
        ai_is_author = bool(ai_item.get("author_badge_visible")) and ai_author == author_name
        return (
            confidence < MIN_AI_CONFIDENCE
            or local_is_author != ai_is_author
            or bool(re.search(r"(?i)\b(?:ett|e廿|e甘)\b", ai_text))
            or ("模型" in ai_author and ai_author != author_name)
        )

    def _merge_comment(
        self,
        local_item: dict[str, Any],
        ai_item: dict[str, Any],
        author_name: str,
        model: str,
    ) -> dict[str, Any]:
        local_text = normalize_financial_text(str(local_item.get("text") or ""))
        ai_text = normalize_financial_text(str(ai_item.get("text") or ""))
        ai_author = str(ai_item.get("author") or "").strip()
        local_is_author = (
            local_item.get("kind") == "author_reply"
            and local_item.get("author") == author_name
            and bool(local_item.get("author_badge_visible"))
        )
        ai_is_author = (
            bool(ai_item.get("author_badge_visible"))
            and ai_author == author_name
        )
        is_author = local_is_author and ai_is_author
        reply_depth = int(local_item.get("reply_depth") or 0)
        kind = "author_reply" if is_author else ("user_reply" if reply_depth else "user_comment")

        similarity = SequenceMatcher(
            None,
            comparable_text(local_text),
            comparable_text(ai_text),
        ).ratio()
        try:
            confidence = max(0.0, min(1.0, float(ai_item.get("confidence") or 0)))
        except (TypeError, ValueError):
            confidence = 0.0
        needs_review = (
            not ai_text
            or confidence < MIN_AI_CONFIDENCE
            or local_is_author != ai_is_author
            or similarity < 0.72
        )
        reasons = []
        if local_is_author != ai_is_author:
            reasons.append("本地与AI对作者身份判断不一致")
        if similarity < 0.72:
            reasons.append("本地与AI文字差异较大")
        if confidence < MIN_AI_CONFIDENCE:
            reasons.append("AI置信度较低")
        if not ai_text:
            reasons.append("AI没有识别出正文")

        selected_text = (
            ai_text
            if has_ocr_risk(local_text) or similarity < 0.90
            else local_text
        )
        result = dict(local_item)
        result.update(
            {
                "author": author_name if is_author else (ai_author or local_item.get("author") or "识图用户"),
                "text": selected_text or ai_text or local_text,
                "kind": kind,
                "author_badge_visible": is_author,
                "confidence": confidence,
                "needs_review": needs_review,
                "review_reason": "；".join(reasons),
                "recognition_model": model,
                "local_text": local_text,
                "ai_text": ai_text,
                "text_similarity": round(similarity, 4),
            }
        )
        # Local OCR is more reliable for timestamp/location and like counters,
        # because these fields are already associated with the correct row.
        result["published_at"] = str(local_item.get("published_at") or ai_item.get("published_at") or "")
        result["like_count"] = int(local_item.get("like_count") or 0)
        return result

    def _client(self) -> Any:
        api_key = str(getattr(self.settings, "openai_api_key", "") or "")
        if self._client_factory:
            return self._client_factory(api_key)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("缺少openai依赖，无法执行AI评论复核") from exc
        return OpenAI(api_key=api_key)


def normalize_financial_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"(?i)(?<![A-Za-z])etf(?![A-Za-z])", "ETF", text)
    text = re.sub(
        r"(?i)(?<![A-Za-z])([abc])\s*浪",
        lambda match: f"{match.group(1).upper()}浪",
        text,
    )
    text = text.replace(",", "，").replace("?", "？").replace("!", "！")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([，。！？、])\s*", r"\1", text)
    return text


def has_ocr_risk(value: str) -> bool:
    return bool(
        re.search(r"(?i)(?:e廿|e甘|ett|乙肝|[0-9][oO][0-9])", value)
    )


def comparable_text(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?·:：;；“”\"'‘’（）()]+", "", value).lower()
