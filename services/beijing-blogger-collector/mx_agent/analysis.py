from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from .settings import PROMPTS_DIR, Settings, openai_api_is_enabled
from .storage import Storage


ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "core_thesis": {"type": "string"},
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["title", "detail", "evidence"],
            },
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risk_flags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "score": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "novelty": {"type": "integer", "minimum": 1, "maximum": 5},
                "business_value": {"type": "integer", "minimum": 1, "maximum": 5},
                "actionability": {"type": "integer", "minimum": 1, "maximum": 5},
                "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["novelty", "business_value", "actionability", "confidence"],
        },
    },
    "required": [
        "summary",
        "core_thesis",
        "insights",
        "recommendations",
        "risk_flags",
        "score",
    ],
}


class VideoIntelligenceAgent:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self.prompt = (PROMPTS_DIR / "analysis_v1.md").read_text(encoding="utf-8")

    def analyze_video(self, video_id: int) -> dict[str, Any]:
        run_id = self.storage.start_run("analysis", {"video_id": video_id})
        try:
            video = self.storage.get_video(video_id)
            if not video:
                raise ValueError(f"Video {video_id} not found")
            transcript = self.storage.latest_transcript(video_id)
            context = self._build_context(video, transcript)
            if openai_api_is_enabled(self.settings):
                result = self._analyze_with_openai(context)
            else:
                result = self._analyze_locally(video, transcript)

            analysis = {
                "video_id": video_id,
                "version": self.settings.analysis_version,
                "prompt_version": self.settings.prompt_version,
                "model": result.get("model", "local-rules"),
                "summary": result["summary"],
                "insights": result.get("insights", []),
                "recommendations": result.get("recommendations", []),
                "risk_flags": result.get("risk_flags", []),
                "score": result.get("score", {}),
                "raw_output": result,
                "trace_id": result.get("trace_id"),
            }
            analysis_id = self.storage.save_analysis(analysis)
            self.storage.finish_run(
                run_id,
                "succeeded",
                {"analysis_id": analysis_id, "model": analysis["model"]},
            )
            return {"analysis_id": analysis_id, **analysis}
        except Exception as exc:
            self.storage.finish_run(run_id, "failed", error=str(exc))
            raise

    def _build_context(
        self,
        video: dict[str, Any],
        transcript: dict[str, Any] | None,
    ) -> str:
        text = transcript["text"] if transcript else ""
        return json.dumps(
            {
                "video": {
                    "title": video.get("title", ""),
                    "description": video.get("description", ""),
                    "url": video.get("url", ""),
                    "published_at": video.get("published_at"),
                    "author": video.get("author", ""),
                },
                "transcript": text,
                "prior_context": "No long-term memory has been indexed yet in this MVP.",
            },
            ensure_ascii=False,
            indent=2,
        )

    def _analyze_with_openai(self, context: str) -> dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OPENAI_API_KEY is configured, but the openai package is not installed. "
                "Run: pip install -r requirements.txt"
            ) from exc

        client = OpenAI(api_key=self.settings.openai_api_key)
        response = client.responses.create(
            model=self.settings.deep_model,
            reasoning={"effort": self.settings.reasoning_effort},
            text={
                "format": {
                    "type": "json_schema",
                    "name": "video_analysis",
                    "schema": ANALYSIS_SCHEMA,
                    "strict": True,
                }
            },
            input=[
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": context},
            ],
        )
        raw_text = getattr(response, "output_text", "") or "{}"
        parsed = json.loads(raw_text)
        parsed["model"] = self.settings.deep_model
        parsed["trace_id"] = getattr(response, "id", None)
        return parsed

    def _analyze_locally(
        self,
        video: dict[str, Any],
        transcript: dict[str, Any] | None,
    ) -> dict[str, Any]:
        text = " ".join(
            [
                video.get("title", ""),
                video.get("description", ""),
                transcript["text"] if transcript else "",
            ]
        ).strip()
        compact_text = re.sub(r"\s+", " ", text)
        keywords = self._keywords(compact_text)
        summary = self._summary(video, compact_text, keywords)
        has_transcript = bool(transcript and transcript.get("text", "").strip())
        risk_flags = []
        if not has_transcript:
            risk_flags.append("当前只有标题/描述，缺少完整转写稿，分析置信度偏低。")
        if len(compact_text) < 80:
            risk_flags.append("输入内容较短，建议补充视频口播文本或关键帧信息。")

        return {
            "summary": summary,
            "core_thesis": summary,
            "insights": [
                {
                    "title": "核心主题",
                    "detail": "这条内容最值得优先关注的关键词是：" + "、".join(keywords[:5]),
                    "evidence": video.get("title", ""),
                },
                {
                    "title": "可行动方向",
                    "detail": "建议把该视频纳入后续选题库，并在补充转写稿后进行二次深度分析。",
                    "evidence": "本地规则分析结果",
                },
            ],
            "recommendations": [
                "补充视频转写稿，让智能体从观点、论据、案例、行动建议四层重新分析。",
                "把高价值视频加入评估样本，用人工反馈训练下一版提示词。",
                "接入官方 Webhook 或授权视频列表，把发现延迟压到分钟级以内。",
            ],
            "risk_flags": risk_flags,
            "score": {
                "novelty": 3,
                "business_value": 3,
                "actionability": 2 if risk_flags else 4,
                "confidence": 2 if risk_flags else 4,
            },
            "model": "local-rules",
        }

    def _keywords(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", text)
        stopwords = {
            "这个",
            "一个",
            "我们",
            "他们",
            "就是",
            "如果",
            "因为",
            "所以",
            "视频",
            "内容",
            "今天",
        }
        filtered = [token for token in tokens if token not in stopwords and len(token) > 1]
        common = Counter(filtered).most_common(8)
        return [word for word, _ in common] or ["待补充"]

    def _summary(self, video: dict[str, Any], text: str, keywords: list[str]) -> str:
        title = video.get("title", "").strip() or "未命名视频"
        if len(text) > 120:
            return f"《{title}》围绕{keywords[0]}展开，当前本地分析认为重点在于观点提炼、案例归档和后续行动判断。"
        return f"《{title}》已入库。当前信息较少，建议补充转写稿后进行深度分析。"
