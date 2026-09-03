from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .blogger_library import BloggerLibrary, BloggerLibraryUnavailable
from .blogger_mcp_oauth import MCP_SCOPE
from .model_mr_mcp import ModelMrMcpLibrary, ModelMrMcpUnavailable


SERVER_NAME = "instant-ai-blogger-cloud"
# Keep the protocol name stable for existing ChatGPT connections. The title is
# owner-facing and now reflects the combined Blogger + Model Mr read surface.
SERVER_TITLE = "即时 AI 资料智能体（云端）"
SUPPORTED_PROTOCOLS = {"2025-03-26", "2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL = "2025-06-18"


def tool_definitions() -> list[dict[str, Any]]:
    security = [{"type": "oauth2", "scopes": [MCP_SCOPE]}]
    common_annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    return [
        {
            "name": "search_blogger_videos",
            "title": "查询云端博主视频文字",
            "description": (
                "只读搜索即时 AI 新加坡博主智能体中的当前作品、博主名称、标题和视频文字。"
                "适合查询某位博主最新视频或按主题检索；不会采集、转写、修改或返回评论和媒体文件。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                        "description": "自然语言查询，例如：查询李爱琳rene最新一条视频文字。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 10,
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
            "outputSchema": {"type": "object", "additionalProperties": True},
            "annotations": {"title": "查询云端博主视频文字", **common_annotations},
            "securitySchemes": security,
            "_meta": {"securitySchemes": security},
        },
        {
            "name": "get_blogger_video_text",
            "title": "读取一条云端视频完整原文",
            "description": (
                "根据 search_blogger_videos 返回的 cloud-video: 编号读取完整正式原文；"
                "若只有尚未确认的识别文字，会明确标记 transcript_unconfirmed。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "pattern": "^cloud-video:[0-9a-f]{64}$",
                        "description": "搜索结果中的 cloud-video: 编号。",
                    }
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
            "outputSchema": {"type": "object", "additionalProperties": True},
            "annotations": {"title": "读取一条云端视频完整原文", **common_annotations},
            "securitySchemes": security,
            "_meta": {"securitySchemes": security},
        },
        {
            "name": "search_model_mr_works",
            "title": "查询模型先生作品",
            "description": (
                "只读搜索即时 AI 模型先生资料库中的作品标题、关键词、正式视频原文、"
                "未确认转写和已有投资解读。适合查询最新作品或按主题找内容；"
                "不会读取评论和媒体，也不会触发识别、AI 或写入。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                        "description": "自然语言查询，例如：查询模型先生最新一条作品原文。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 10,
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
            "outputSchema": {"type": "object", "additionalProperties": True},
            "annotations": {"title": "查询模型先生作品", **common_annotations},
            "securitySchemes": security,
            "_meta": {"securitySchemes": security},
        },
        {
            "name": "get_model_mr_work_text",
            "title": "读取模型先生作品完整文字",
            "description": (
                "根据 search_model_mr_works 返回的 model-mr-work: 编号，"
                "只读返回完整正式原文或明确标记的未确认文字，并同时返回已有投资解读。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "record_id": {
                        "type": "string",
                        "pattern": "^model-mr-work:[1-9][0-9]{0,11}$",
                        "description": "搜索结果中的 model-mr-work: 编号。",
                    }
                },
                "required": ["record_id"],
                "additionalProperties": False,
            },
            "outputSchema": {"type": "object", "additionalProperties": True},
            "annotations": {"title": "读取模型先生作品完整文字", **common_annotations},
            "securitySchemes": security,
            "_meta": {"securitySchemes": security},
        },
        {
            "name": "list_model_mr_investment_thoughts",
            "title": "查询模型先生投资思路",
            "description": (
                "只读列出或按主题筛选模型先生已保存的投资思路分类和说明。"
                "这是资料索引，不调用 AI，也不生成即时买卖建议。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "maxLength": 2000,
                        "default": "",
                        "description": "可选主题；留空时列出全部思路分类。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "default": 100,
                    },
                },
                "additionalProperties": False,
            },
            "outputSchema": {"type": "object", "additionalProperties": True},
            "annotations": {"title": "查询模型先生投资思路", **common_annotations},
            "securitySchemes": security,
            "_meta": {"securitySchemes": security},
        },
    ]


def handle_message(
    message: Mapping[str, Any],
    *,
    library: BloggerLibrary,
    model_mr_library: ModelMrMcpLibrary,
    version: str,
    authenticated: bool,
) -> dict[str, Any] | None:
    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(request_id, -32600, "Invalid Request")
    method = str(message["method"])
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        return _error(request_id, -32602, "Invalid params")

    if request_id is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        return _result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": version},
                "instructions": (
                    "这是即时 AI 新加坡端的单主人只读资料库，包含博主智能体和模型先生。"
                    "先搜索，再用 cloud-video: 或 model-mr-work: 编号读取完整文字；"
                    "不得把未确认转写冒充正式原文。"
                ),
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tool_definitions()})
    if method != "tools/call":
        return _error(request_id, -32601, "Method not found")
    if not authenticated:
        return _error(
            request_id,
            -32001,
            "Owner authorization required",
            data={"oauth_required": True},
        )

    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, Mapping):
        return _error(request_id, -32602, "Invalid tool arguments")
    try:
        if name == "search_blogger_videos":
            question = str(arguments.get("question") or "").strip()
            if not question or len(question) > 2000:
                raise ValueError("question_required")
            try:
                limit = int(arguments.get("limit", 10))
            except (TypeError, ValueError) as error:
                raise ValueError("limit_invalid") from error
            if limit < 1 or limit > 30:
                raise ValueError("limit_invalid")
            result = library.search_for_mcp(question, limit)
        elif name == "get_blogger_video_text":
            record_id = str(arguments.get("record_id") or "")
            if not record_id.startswith("cloud-video:"):
                raise ValueError("record_id_invalid")
            result = library.get_for_mcp(record_id)
        elif name == "search_model_mr_works":
            question = str(arguments.get("question") or "").strip()
            if not question or len(question) > 2000:
                raise ValueError("question_required")
            try:
                limit = int(arguments.get("limit", 10))
            except (TypeError, ValueError) as error:
                raise ValueError("limit_invalid") from error
            if limit < 1 or limit > 30:
                raise ValueError("limit_invalid")
            result = model_mr_library.search_works_for_mcp(question, limit)
        elif name == "get_model_mr_work_text":
            record_id = str(arguments.get("record_id") or "")
            if re.fullmatch(r"model-mr-work:[1-9][0-9]{0,11}", record_id) is None:
                raise ValueError("record_id_invalid")
            result = model_mr_library.get_work_for_mcp(record_id)
        elif name == "list_model_mr_investment_thoughts":
            query = str(arguments.get("query") or "").strip()
            if len(query) > 2000:
                raise ValueError("query_invalid")
            try:
                limit = int(arguments.get("limit", 100))
            except (TypeError, ValueError) as error:
                raise ValueError("limit_invalid") from error
            if limit < 1 or limit > 300:
                raise ValueError("limit_invalid")
            result = model_mr_library.list_thoughts_for_mcp(query, limit)
        else:
            return _tool_error(request_id, "tool_not_found")
    except ValueError as error:
        return _tool_error(request_id, str(error))
    except BloggerLibraryUnavailable:
        return _tool_error(request_id, "blogger_library_unavailable")
    except ModelMrMcpUnavailable:
        return _tool_error(request_id, "model_mr_library_unavailable")

    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": False,
        },
    )


def attach_oauth_challenge(response: dict[str, Any], challenge: str) -> dict[str, Any]:
    error = response.get("error")
    if not isinstance(error, dict) or int(error.get("code", 0)) != -32001:
        return response
    data = error.setdefault("data", {})
    if isinstance(data, dict):
        data.setdefault("_meta", {})["mcp/www_authenticate"] = [challenge]
    return response


def _result(request_id: Any, result: object) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = dict(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _tool_error(request_id: Any, code: str) -> dict[str, Any]:
    payload = {"error": code}
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": True,
        },
    )


__all__ = ["attach_oauth_challenge", "handle_message", "tool_definitions"]
