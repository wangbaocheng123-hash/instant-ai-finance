from __future__ import annotations

from typing import Any

from .settings import Settings
from .storage import Storage


class OptimizationLoop:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage

    def feedback_summary(self) -> dict[str, Any]:
        feedback = self.storage.latest_feedback_summary()
        avg = feedback["avg_rating"]
        if feedback["count"] == 0:
            status = "waiting_for_feedback"
            recommendation = "先积累 5-10 条人工反馈，再调整提示词和模型路由。"
        elif avg >= 4:
            status = "healthy"
            recommendation = "当前分析质量较好，可优先优化速度、成本和自动化抓取。"
        elif avg >= 3:
            status = "needs_prompt_tuning"
            recommendation = "建议抽取低分案例，更新提示词中的成功标准和输出结构。"
        else:
            status = "needs_model_or_context_upgrade"
            recommendation = "建议补充转写稿/关键帧，并用更强模型重新跑评估。"

        return {
            "status": status,
            "feedback": feedback,
            "recommendation": recommendation,
            "active_analysis_version": self.settings.analysis_version,
            "active_prompt_version": self.settings.prompt_version,
        }

