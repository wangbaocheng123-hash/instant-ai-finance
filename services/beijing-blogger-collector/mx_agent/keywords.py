from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Callable

from .keyword_taxonomy import (
    KEYWORD_CATEGORIES,
    KEYWORD_MAX_PER_CATEGORY,
    KEYWORD_SCHEMA_VERSION,
    flatten_keyword_categories,
    normalize_keyword_categories,
)
from .settings import Settings, require_openai_api_enabled
from .storage import Storage, from_json

if TYPE_CHECKING:
    from .doubao import DoubaoFoundationService


DEFAULT_KEYWORD_MODEL = "gpt-5.6-luna"
ALLOWED_KEYWORD_MODELS = {"gpt-5.6-luna", "gpt-5.6-terra"}
KEYWORD_NOTE_TYPE = "ai_keywords"

KEYWORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        category: {
            "type": "array",
            "minItems": 0,
            "maxItems": KEYWORD_MAX_PER_CATEGORY,
            "items": {"type": "string"},
        }
        for category in KEYWORD_CATEGORIES
    },
    "required": list(KEYWORD_CATEGORIES),
    "additionalProperties": False,
}

KEYWORD_INSTRUCTIONS = """你是“模型先生智能体”的视频原文关键词归类器。

你只有一个任务：从输入的 video_original 中提取短关键词，并放入下面固定的10个分类。
不得概括原文观点，不得总结内容，不得输出核心要点、投资结论、解释、证据或置信度。

固定分类：
1. 行业与板块
2. 企业、个股与产业链
3. 基本面与估值
4. 时间周期与走势状态
5. 投资战略、战术与选股方法
6. 宏观、政策与事件
7. 市场、指数与资金
8. 技术面与交易信号
9. 交易管理与风险控制
10. 投资心理、学习与适用人群

严格规则：
- 只依据 video_original；title_reference 只帮助识别作品，不得提取只在标题出现、原文没有的词。
- 原文没有谈到的分类必须返回空数组。
- 每项只能是便于搜索的短关键词或短词组，不得写成完整句子，不得改写成观点。
- 保留原文中的公司、个股、行业、产业链、估值指标、时间、行情状态、方法、信号和风险词。
- 合并完全同义或重复的词；同一个关键词只放在最合适的一个分类中。
- 不联网，不使用外部知识，不补充原文没有出现或不能由原文直接截取的概念。
- 不得为了填满分类或凑数量而造词；每类最多8个，全部分类合计最多40个。
- 只返回符合结构的JSON对象，不得返回JSON以外的任何文字。
"""


class KeywordExtractionService:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        client_factory: Callable[[str], Any] | None = None,
        *,
        doubao_service: DoubaoFoundationService | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self._client_factory = client_factory
        self.doubao_service = doubao_service

    def status(self, video_id: int) -> dict[str, Any]:
        material = self._material(video_id)
        source_hash = self._source_hash(material)
        saved = self._saved(video_id)
        categories = saved.get("categories") or normalize_keyword_categories({})
        is_saved = bool(saved.get("confirmed_at") or saved.get("schema_version"))
        return {
            "categories": categories,
            "keywords": flatten_keyword_categories(categories),
            "model": saved.get("model", ""),
            "schema_version": saved.get("schema_version", ""),
            "source_hash": source_hash,
            "saved_source_hash": saved.get("source_hash", ""),
            "stale": is_saved and (
                saved.get("source_hash") != source_hash
                or saved.get("schema_version") != KEYWORD_SCHEMA_VERSION
            ),
            "confirmed_at": saved.get("confirmed_at"),
            "can_extract": bool(material["video_original"]),
        }

    def preview(
        self,
        video_id: int,
        *,
        model: str = DEFAULT_KEYWORD_MODEL,
        force: bool = False,
    ) -> dict[str, Any]:
        use_doubao = bool(self.doubao_service and self.doubao_service.text_enabled())
        if not use_doubao and model not in ALLOWED_KEYWORD_MODELS:
            raise ValueError("不支持的关键词提炼模型。")
        material = self._material(video_id)
        if not material["video_original"]:
            raise ValueError("这条作品还没有正式视频原文，暂不能提炼关键词。")

        source_hash = self._source_hash(material)
        model_material = {
            "title_reference": material["title_reference"],
            "video_original": material["video_original"][:60000],
        }
        saved = self._saved(video_id)
        saved_is_current = bool(saved.get("confirmed_at")) and (
            saved.get("source_hash") == source_hash
            and saved.get("schema_version") == KEYWORD_SCHEMA_VERSION
        )
        if not force and saved_is_current:
            categories = saved["categories"]
            return {
                "categories": categories,
                "keywords": flatten_keyword_categories(categories),
                "model": saved.get("model", model),
                "schema_version": KEYWORD_SCHEMA_VERSION,
                "source_hash": source_hash,
                "cached": True,
            }

        if use_doubao:
            domestic = self.doubao_service.extract_keywords(model_material)
            parsed = domestic["payload"]
            used_model = str(domestic["model"])
            response_id = domestic.get("response_id")
        else:
            require_openai_api_enabled(self.settings, "AI 提炼关键词")
            response = self._client().responses.create(
                model=model,
                reasoning={"effort": "low"},
                instructions=KEYWORD_INSTRUCTIONS,
                input=json.dumps(model_material, ensure_ascii=False),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "video_original_keywords",
                        "description": "视频原文的固定10类短关键词；没有涉及的分类为空数组",
                        "schema": KEYWORD_SCHEMA,
                        "strict": True,
                    },
                    "verbosity": "low",
                },
                max_output_tokens=1800,
                store=False,
            )
            raw = str(getattr(response, "output_text", "") or "").strip()
            if not raw:
                raise RuntimeError("模型没有返回关键词结果。")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("模型返回的关键词格式无法解析，请重试。") from exc
            used_model = model
            response_id = getattr(response, "id", None)

        categories = normalize_keyword_categories(parsed)
        return {
            "categories": categories,
            "keywords": flatten_keyword_categories(categories),
            "model": used_model,
            "schema_version": KEYWORD_SCHEMA_VERSION,
            "source_hash": source_hash,
            "cached": False,
            "response_id": response_id,
        }

    def save(
        self,
        video_id: int,
        *,
        categories: dict[str, Any] | None = None,
        source_hash: str,
        model: str = DEFAULT_KEYWORD_MODEL,
        keywords: list[Any] | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        material = self._material(video_id)
        current_hash = self._source_hash(material)
        if source_hash != current_hash:
            raise ValueError("视频原文已经变化，请重新提炼后再保存。")

        raw_payload: dict[str, Any]
        if isinstance(categories, dict):
            raw_payload = {"categories": categories}
        else:
            raw_payload = {"keywords": keywords or [], "items": items or []}
        cleaned_categories = normalize_keyword_categories(raw_payload)
        return self.storage.save_content_keywords(
            video_id,
            categories=cleaned_categories,
            source_hash=current_hash,
            schema_version=KEYWORD_SCHEMA_VERSION,
            model=str(model or DEFAULT_KEYWORD_MODEL).strip()[:120],
        )

    def _material(self, video_id: int) -> dict[str, str]:
        video = self.storage.get_video(video_id)
        if not video:
            raise ValueError("video not found")
        title_info = self.storage.get_video_title(video_id) or {}
        title = str(title_info.get("active_title") or video.get("title") or "").strip()[:240]
        original = self.storage.get_content_original(video_id)
        if original and str(original.get("original_text") or "").strip():
            video_text = str(original["original_text"])
        else:
            legacy_note = self.storage.get_note(video_id, "video_text") or {}
            video_text = str(legacy_note.get("text") or "")
        return {
            "title_reference": title,
            "video_original": video_text,
        }

    @staticmethod
    def _source_hash(material: dict[str, str]) -> str:
        return hashlib.sha256(
            str(material.get("video_original") or "").encode("utf-8")
        ).hexdigest()

    def _saved(self, video_id: int) -> dict[str, Any]:
        saved_set = self.storage.get_content_keyword_set(video_id)
        if saved_set:
            return saved_set
        note = self.storage.get_note(video_id, KEYWORD_NOTE_TYPE)
        if not note:
            return {}
        data = from_json(note.get("text"), {})
        if not isinstance(data, dict):
            return {}
        return {
            "categories": normalize_keyword_categories(data),
            "model": data.get("model", ""),
            "source_hash": data.get("source_hash", ""),
            "schema_version": data.get("schema_version", "legacy-v2"),
            "confirmed_at": data.get("confirmed_at") or note.get("updated_at"),
        }

    def _client(self) -> Any:
        if self._client_factory:
            return self._client_factory(str(self.settings.openai_api_key))
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("缺少 openai 依赖，请运行项目已有的依赖安装流程。") from exc
        return OpenAI(api_key=self.settings.openai_api_key)
