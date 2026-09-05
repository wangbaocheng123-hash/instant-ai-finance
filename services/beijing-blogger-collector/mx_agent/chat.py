from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .conversation_memory import ConversationMemoryStore
from .knowledge import KnowledgeReader, _recency_intent
from .knowledge_index import sync_dirty_knowledge
from .settings import (
    Settings,
    doubao_api_is_configured,
    doubao_api_is_enabled,
    doubao_speech_is_configured,
    doubao_text_is_configured,
    doubao_vision_is_configured,
    openai_api_is_enabled,
    require_openai_api_enabled,
)
from .storage import Storage


CHAT_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "description": "日常讨论，兼顾质量、速度与成本",
    },
    {
        "id": "gpt-5.6-sol",
        "label": "GPT-5.6 Sol",
        "description": "复杂投资分析与深度推理",
    },
    {
        "id": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "description": "快速问答、分类和批量整理",
    },
    {
        "id": "gpt-5.5",
        "label": "GPT-5.5",
        "description": "上一代高能力模型",
    },
    {
        "id": "gpt-5.4",
        "label": "GPT-5.4",
        "description": "较低成本的复杂分析",
    },
    {
        "id": "gpt-5.4-mini",
        "label": "GPT-5.4 mini",
        "description": "低成本快速任务",
    },
)

DEFAULT_CHAT_MODEL = "gpt-5.6-terra"
ALLOWED_CHAT_MODELS = {item["id"] for item in CHAT_MODELS}


SYSTEM_INSTRUCTIONS = """你是“模型先生智能体”的本地对话助手。

你可以通过工具读取模型先生现有数据库和用户已经确认保存的长期记忆。

必须遵守：
1. 用户询问模型先生的知识、观点、投资认知或历史判断时，先调用 search_model_knowledge；人工确认的投资思路分类是视频索引，命中后按 video:<数字> 调用 get_model_knowledge 读取数据库中唯一的视频原文。
2. 用户询问“我们以前聊过什么”或个人历史判断时，先调用 search_conversation_memory；只有需要完整细节时才调用 get_conversation_memory。
3. 严格区分“模型先生原始观点”“用户自己的判断”和“GPT分析”。不得把GPT推测冒充模型先生原话。
4. 重要结论尽量注明记录编号、内容类型、日期和来源。没有命中资料时要明确说明，不得编造。
5. 当前工具均为读取工具。不要声称已经修改标题、关键词、视频文字、评论或数据库。
6. 投资讨论应提供证据、条件和风险，不把不确定推演表达成保证。

回答使用简体中文，先给结论，再给证据和必要说明。"""


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "search_model_knowledge",
        "description": "按用户问题、关键词或人工确认的投资思路分类搜索视频原文、本人评论回复和用户解读感悟。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户问题或检索关键词"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["question", "limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_model_knowledge",
        "description": "根据搜索返回的 video:<数字> 记录编号读取唯一的视频原文、关键词、分类和来源。",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "description": "例如 video:123"},
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "search_conversation_memory",
        "description": "搜索已经确认保存的长期讨论记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "主题、个股、行业或关键词"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_conversation_memory",
        "description": "根据 memory:<数字> 或 memory_key 读取完整长期记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "记忆编号或memory_key"},
            },
            "required": ["reference"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class ChatResult:
    answer: str
    model: str
    response_id: str | None
    tools_used: list[str]


class LocalChatService:
    def __init__(
        self,
        settings: Settings,
        knowledge_reader: KnowledgeReader | None = None,
        memory_store: ConversationMemoryStore | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.knowledge_reader = knowledge_reader or KnowledgeReader(settings.database_path)
        self.memory_store = memory_store or ConversationMemoryStore(settings.database_path)
        self._client_factory = client_factory
        database_path = getattr(self.knowledge_reader, "database_path", None)
        self.index_storage = Storage(database_path) if database_path else None

    def config(self) -> dict[str, Any]:
        configured = bool(self.settings.openai_api_key)
        enabled = openai_api_is_enabled(self.settings)
        return {
            "configured": configured,
            "enabled": enabled,
            "api_switch_enabled": bool(
                getattr(self.settings, "openai_api_enabled", True)
            ),
            "default_model": DEFAULT_CHAT_MODEL,
            "models": list(CHAT_MODELS),
            "tool_names": [tool["name"] for tool in TOOLS],
            "providers": {
                "domestic": {
                    "provider": "doubao",
                    "label": "国内 API",
                    "configured": doubao_api_is_configured(self.settings),
                    "enabled": doubao_api_is_enabled(self.settings),
                    "api_switch_enabled": bool(
                        getattr(self.settings, "doubao_api_enabled", False)
                    ),
                    "capabilities": {
                        "speech": doubao_speech_is_configured(self.settings),
                        "text": doubao_text_is_configured(self.settings),
                        "vision": doubao_vision_is_configured(self.settings),
                    },
                },
                "foreign": {
                    "provider": "openai",
                    "label": "国外 API",
                    "configured": configured,
                    "enabled": enabled,
                    "api_switch_enabled": bool(
                        getattr(self.settings, "openai_api_enabled", True)
                    ),
                },
            },
            "message": (
                "OpenAI API 已开启，可以开始对话。"
                if enabled
                else (
                    "OpenAI API 已配置，但总开关当前关闭。需要使用时请在右上角手动开启。"
                    if configured
                    else "尚未配置 OPENAI_API_KEY。请在页面的“设置”中填写后使用。"
                )
            ),
        }

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str = DEFAULT_CHAT_MODEL,
        account: str = "",
    ) -> ChatResult:
        require_openai_api_enabled(self.settings, "本地 AI 聊天")
        if model not in ALLOWED_CHAT_MODELS:
            raise ValueError("不支持的模型。")

        input_items = self._clean_messages(messages)
        if not input_items:
            raise ValueError("聊天内容不能为空。")

        creator = account or getattr(self.settings, "source_account_name", "") or "新博主"
        instructions = SYSTEM_INSTRUCTIONS.replace("模型先生", creator)
        instructions += f"\n\n当前作品账号：{creator}。"
        latest_user_question = next(
            (
                str(item.get("content") or "")
                for item in reversed(input_items)
                if item.get("role") == "user"
            ),
            "",
        )
        recency_intent = _recency_intent(latest_user_question)[0]
        forced_knowledge: dict[str, Any] | None = None
        if recency_intent:
            index_sync = self._sync_pending_index()
            forced_knowledge = self.knowledge_reader.search(
                question=latest_user_question,
                limit=5,
            )
            forced_knowledge["index_sync"] = index_sync
            instructions += (
                "\n\n本轮用户问题包含最新、今天、最近或指定日期。系统已经按用户原话"
                "执行时间检索，下面结果具有优先级，禁止把旧的相关记录表述为最新记录。"
                f"\n用户原始问题：{latest_user_question}\n"
                f"{self._bounded_json(forced_knowledge)}"
            )
        client = self._client()
        tools_used: list[str] = (
            ["search_model_knowledge"] if forced_knowledge is not None else []
        )
        response = client.responses.create(
            model=model,
            reasoning={"effort": "medium"},
            instructions=instructions,
            input=input_items,
            tools=TOOLS,
            store=False,
        )

        conversation_items: list[Any] = list(input_items)
        for _ in range(6):
            output_items = list(getattr(response, "output", []) or [])
            tool_calls = [item for item in output_items if self._item_value(item, "type") == "function_call"]
            if not tool_calls:
                break

            conversation_items.extend(self._serializable_item(item) for item in output_items)
            for call in tool_calls:
                name = str(self._item_value(call, "name") or "")
                call_id = str(self._item_value(call, "call_id") or "")
                arguments = self._parse_arguments(self._item_value(call, "arguments"))
                result = self._run_tool(
                    name,
                    arguments,
                    forced_knowledge=forced_knowledge,
                )
                if name not in tools_used:
                    tools_used.append(name)
                conversation_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": self._bounded_json(result),
                    }
                )

            response = client.responses.create(
                model=model,
                reasoning={"effort": "medium"},
                instructions=instructions,
                input=conversation_items,
                tools=TOOLS,
                store=False,
            )
        else:
            raise RuntimeError("本轮工具调用次数过多，已停止以避免循环。")

        answer = str(getattr(response, "output_text", "") or "").strip()
        if not answer:
            raise RuntimeError("模型没有返回可显示的文字结果。")
        return ChatResult(
            answer=answer,
            model=model,
            response_id=getattr(response, "id", None),
            tools_used=tools_used,
        )

    def _client(self) -> Any:
        if self._client_factory:
            return self._client_factory(str(self.settings.openai_api_key))
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("缺少 openai 依赖，请运行项目已有的依赖安装流程。") from exc
        return OpenAI(api_key=self.settings.openai_api_key)

    @staticmethod
    def _clean_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        if not isinstance(messages, list):
            raise ValueError("messages 必须是数组。")
        cleaned: list[dict[str, str]] = []
        for item in messages[-20:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            cleaned.append({"role": role, "content": content[:12000]})
        return cleaned

    def _run_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        forced_knowledge: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name == "search_model_knowledge":
            if forced_knowledge is not None:
                return forced_knowledge
            index_sync = self._sync_pending_index()
            result = self.knowledge_reader.search(
                question=str(arguments.get("question") or ""),
                limit=max(1, min(int(arguments.get("limit") or 10), 10)),
            )
            result["index_sync"] = index_sync
            return result
        if name == "get_model_knowledge":
            return self.knowledge_reader.get(record_id=str(arguments.get("record_id") or ""))
        if name == "search_conversation_memory":
            return self.memory_store.search(
                query=str(arguments.get("query") or ""),
                limit=max(1, min(int(arguments.get("limit") or 10), 10)),
            )
        if name == "get_conversation_memory":
            return self.memory_store.get(reference=str(arguments.get("reference") or ""))
        raise ValueError(f"未知工具：{name}")

    def _sync_pending_index(self) -> dict[str, int]:
        if self.index_storage is None:
            return {"checked": 0, "refreshed": 0}
        with self.index_storage.connect() as conn:
            return sync_dirty_knowledge(conn)

    @staticmethod
    def _parse_arguments(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("模型返回了无效的工具参数。") from exc
        if not isinstance(parsed, dict):
            raise ValueError("工具参数必须是对象。")
        return parsed

    @staticmethod
    def _item_value(item: Any, key: str) -> Any:
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key, None)

    @staticmethod
    def _serializable_item(item: Any) -> Any:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump(exclude_none=True)
        return item

    @staticmethod
    def _bounded_json(payload: dict[str, Any], limit: int = 36000) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= limit:
            return text
        return json.dumps(
            {
                "truncated": True,
                "notice": "工具结果过长，已截断。请缩小搜索范围或读取单条记录。",
                "partial_output": text[:limit],
            },
            ensure_ascii=False,
        )
