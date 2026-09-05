from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .cloud_blogger import CloudBloggerReader, merge_cloud_search_result
from .conversation_memory import ConversationMemoryStore
from .knowledge import KnowledgeReader
from .knowledge_index import sync_dirty_knowledge
from .investment_thoughts import read_investment_thoughts
from .mainlines import (
    get_confirmed_mainline,
    read_confirmed_mainlines,
    search_confirmed_mainlines,
)
from .roles import ApplicationRole, require_role
from .storage import Storage
from .settings import load_settings


SERVER_INSTRUCTIONS = """这是“博主智能体”的统一知识与长期记忆接口，当前作品账号为模型先生。
搜索会合并本地历史知识库与新加坡即时 AI 中已接收的最新博主作品；云端记录编号为
cloud-video:<作品键>，只提供标题、来源、处理状态和正式视频原文（或明确标记的未确认识别文字）。
人工确认的“投资思路分类”是现有视频的分类索引，不复制标题、关键词或原文；
命中分类后直接读取其 video:<数字> 原记录中的唯一视频原文。
原始知识分为三类：视频原文、模型先生本人评论回复、用户解读感悟。
当用户要求以模型先生的知识、观点和投资认知进行讨论时，应先查询这里的数据，
优先使用视频原文和模型先生本人回复作为原始证据，再结合用户解读感悟进行理解。
当用户询问“最新视频”“今天观点”“最近发布”或指定日期的观点时，必须把原始时间表达
完整传给 search_model_knowledge，并优先读取返回结果中日期最新的记录。
用户解读感悟不得冒充模型先生原话；不得把 GPT 自己的推测冒充模型先生的原始观点。
回答重要结论时，应尽量注明记录编号、内容类型、日期和来源。
当用户明确给出行业、个股、估值、行情状态、策略、政策、指数资金、技术信号、
风险控制或投资心理等关键词时，可先调用 search_video_originals_by_keywords；
命中后必须用 get_video_original 读取所需记录的完整正式原文，再联系和解释观点。
分类关键词只是定位索引，不能单独当作模型先生的观点或投资结论。
当用户询问当前投资主线、科技行情路线、K线推演、预测演练或阶段节点时，
应调用 get_investment_mainlines 读取已确认的当前版本，并明确区分已经发生、
当前判断、观察条件和未来预测；不得把推演节点表述成确定结果。
当用户询问以前讨论过的内容时，应先搜索精炼对话记忆；只有命中后需要证据细节，
才读取完整长期记忆，避免每次载入大量历史文本。
只有用户明确要求“保存本次讨论”“写入长期记忆”或确认保存时，才调用保存工具；
保存前先按来源聊天标识查询上一次保存批次的结束时间。每次保存必须提交本次未保存聊天的
开始时间和结束时间，并严格分开模型先生原始观点、用户观点和 GPT 分析，不得混写。
聊天时间区间采用左闭右开规则 [开始, 结束)，相邻批次可以首尾相接，但不得重叠。
原始视频、评论和知识索引始终只读；投资思路分类关系通过主页人工维护，MCP仅提供只读调用。"""

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

WRITE_MEMORY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

require_role(ApplicationRole.ALL_DEV)
template_settings = load_settings()
ACCOUNT_NAME = template_settings.source_account_name or "当前博主"


def account_text(value: str) -> str:
    """Render copied tool metadata for the account configured in this template."""
    return value.replace("模型先生", ACCOUNT_NAME)


SERVER_INSTRUCTIONS = account_text(SERVER_INSTRUCTIONS)
reader = KnowledgeReader()
cloud_reader = CloudBloggerReader()
index_storage = Storage(reader.database_path)
memory_store = ConversationMemoryStore()
mcp = FastMCP(
    name="博主智能体",
    instructions=SERVER_INSTRUCTIONS,
    host="127.0.0.1",
    port=int(os.getenv("MX_AGENT_MCP_PORT", "8775")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool(
    name="search_model_knowledge",
    title="搜索博主知识",
    description=account_text(
        "接收用户问题或关键词，同时搜索本地历史库和新加坡即时 AI 最新博主作品，"
        "从原数据库的增量全文索引中快速搜索视频原文、"
        "模型先生本人评论回复、用户解读感悟和人工确认的投资思路分类视频。"
        "分类结果仍返回 video:<数字>，内容从视频库唯一原记录读取；返回相关记录、命中段落、内容类型、"
        "相关度、日期和出处。人工确认标题或封面识别标题具有最高检索权重。"
        "询问最新、今天、最近发布或指定日期时，会自动切换为按发布时间检索，"
        "不要求视频原文必须包含“最新观点”等字样。"
        "同时兼容搜索已确认的当前投资主线与K线推演，返回 mainline:<line_key>。"
        "视频原文及本人回复属于原始观点；用户解读感悟和投资主线属于二次理解。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def search_model_knowledge(question: str, limit: int = 10) -> dict[str, Any]:
    """Search the incrementally maintained read-only knowledge index."""
    safe_limit = max(1, min(int(limit), 30))
    with index_storage.connect() as conn:
        index_sync = sync_dirty_knowledge(conn)
    result = reader.search(question=question, limit=safe_limit)
    cloud = cloud_reader.search(question=question, limit=safe_limit)
    result["index_sync"] = index_sync
    # Time-scoped questions are asking what was published then. Confirmed
    # mainlines are separate, longer-lived syntheses and must not outrank the
    # day's source videos.
    mainline_items = (
        []
        if result.get("query_mode") == "latest"
        else search_confirmed_mainlines(
            reader.database_path,
            question=question,
            limit=safe_limit,
        )
    )
    if mainline_items:
        result["items"] = sorted(
            [*mainline_items, *result["items"]],
            key=lambda item: (
                float(item.get("relevance_score") or 0),
                str(item.get("published_at") or ""),
            ),
            reverse=True,
        )[:safe_limit]
        result["count"] = len(result["items"])
        result["retrieval"] = (
            "incremental_fts5_trigram+confirmed_investment_mainlines"
        )
        result["evidence_note"] += (
            " 投资主线与K线推演属于已确认的阶段性二次整理，"
            "必须结合节点证据和来源视频核验。"
        )
    return merge_cloud_search_result(result, cloud, safe_limit)


@mcp.tool(
    name="get_model_knowledge",
    title="读取博主完整知识记录",
    description=account_text(
        "根据 search_model_knowledge 返回的记录编号，只读返回该记录的三个内容板块："
        "video:<数字> 返回视频原文、投资思路分类、带用户问题上下文的模型先生评论回复和用户解读感悟；"
        "mainline:<line_key> 返回已确认的投资主线、K线推演节点、证据与来源；"
        "cloud-video:<作品键> 返回新加坡即时 AI 中该博主作品的正式视频原文。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_model_knowledge(record_id: str) -> dict[str, Any]:
    """Read one complete classified knowledge record without modifying it."""
    if str(record_id or "").strip().startswith("mainline:"):
        return get_confirmed_mainline(reader.database_path, record_id)
    if str(record_id or "").strip().startswith("cloud-video:"):
        return cloud_reader.get(record_id)
    return reader.get(record_id=record_id)


@mcp.tool(
    name="get_investment_thoughts",
    title="读取投资思路视频分类",
    description=account_text(
        "只读返回主页中由用户人工确认的投资思路分类及其关联视频。"
        "可按分类名称或关键词筛选；这里只返回 video:<数字> 索引，不保存第二份标题或视频原文。"
        "需要内容时继续用 get_model_knowledge 读取对应视频的唯一原记录。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_investment_thoughts(
    category: str = "",
    query: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Read manually maintained investment guidelines without modifying them."""
    return read_investment_thoughts(
        reader.database_path,
        category=category,
        query=query,
        limit=limit,
    )


@mcp.tool(
    name="search_video_originals_by_keywords",
    title="按分类关键词搜索正式视频原文",
    description=account_text(
        "只使用已经人工确认保存的10类关键词索引定位视频原文。"
        "可同时传多个关键词、限定分类和发布时间；match_all=true 时要求每个查询关键词都命中。"
        "返回命中关键词、原文片段、视频日期和 video:<数字>，不根据关键词概括观点。"
        "需要引用、比较或联系观点时，继续调用 get_video_original 读取完整正式原文。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def search_video_originals_by_keywords(
    keywords: list[str] | None = None,
    categories: list[str] | None = None,
    date_from: str = "",
    date_to: str = "",
    match_all: bool = False,
    author: str = ACCOUNT_NAME,
    limit: int = 10,
) -> dict[str, Any]:
    """Locate official originals through the confirmed ten-category keyword index."""
    safe_limit = max(1, min(int(limit), 30))
    with index_storage.connect() as conn:
        index_sync = sync_dirty_knowledge(conn)
    result = reader.search_video_originals_by_keywords(
        keywords=keywords,
        categories=categories,
        date_from=date_from,
        date_to=date_to,
        match_all=match_all,
        author=author,
        limit=safe_limit,
    )
    result["index_sync"] = index_sync
    return result


@mcp.tool(
    name="get_video_original",
    title="读取完整正式视频原文",
    description=account_text(
        "根据 search_video_originals_by_keywords 或 search_model_knowledge 返回的 video:<数字>"
        "或 cloud-video:<作品键>，"
        "只读返回人工确认的完整视频原文、真实发布时间、标题、媒体名称和10类关键词。"
        "关键词只用于定位，解释模型先生观点必须以返回的完整原文为依据。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_video_original(record_id: str) -> dict[str, Any]:
    """Read one manually confirmed official original without other derived summaries."""
    if str(record_id or "").strip().startswith("cloud-video:"):
        return cloud_reader.get(record_id)
    return reader.get_video_original(record_id=record_id)


@mcp.tool(
    name="get_investment_mainlines",
    title="读取当前投资主线与K线推演",
    description=account_text(
        "只读返回本地数据库中已经人工确认的当前投资主线、阶段判断、更新时间、"
        "K线推演节点、节点状态、原文证据、来源视频编号和当前版本。"
        "当用户询问科技投资路线、当前行情阶段、K线推演、预测演练或后续时间节点时调用。"
        "观察和预测节点不是事实保证；本工具不生成草稿、不调用AI、不确认或修改主线。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_investment_mainlines() -> dict[str, Any]:
    """Read confirmed investment mainlines without modifying the database."""
    return read_confirmed_mainlines(reader.database_path)


@mcp.tool(
    name="save_conversation_memory",
    title="保存本次讨论为长期记忆",
    description=account_text(
        "仅在用户明确要求保存时调用。保存前先调用 search_conversation_memory，按相同的"
        "source_chat_reference 和 chat_session_id 查询 latest_saved_batch，从上次结束时间之后开始。"
        "必须填写本批聊天的开始和结束时间；同一来源会话中完全相同或相互重叠的时间段不会重复保存。"
        "保存时会同时生成一条独立的精炼记忆，供以后优先检索。"
        "必须严格区分模型先生原始观点、用户判断和 GPT 分析；没有原始证据时，"
        "不得把 GPT 推论写入 model_mr_view。可关联 search_model_knowledge 返回的 video:<数字>。"
        "不修改视频、评论或知识索引。"
    ),
    annotations=WRITE_MEMORY_ANNOTATIONS,
    structured_output=True,
)
def save_conversation_memory(
    title: str,
    chat_started_at: str,
    chat_ended_at: str,
    source_chat_reference: str,
    chat_timezone: str = "Asia/Shanghai",
    chat_session_id: str = "",
    discussion_topic: str = "",
    core_conclusions: list[str] | None = None,
    related_record_ids: list[str] | None = None,
    securities: list[str] | None = None,
    industries: list[str] | None = None,
    keywords: list[str] | None = None,
    model_mr_view: str = "",
    user_view: str = "",
    gpt_analysis: str = "",
    unresolved_questions: list[str] | None = None,
    verification_items: list[str] | None = None,
    memory_key: str = "",
    batch_key: str = "",
) -> dict[str, Any]:
    """Save one confirmed, structured discussion memory."""
    return memory_store.save(
        title=title,
        chat_started_at=chat_started_at,
        chat_ended_at=chat_ended_at,
        source_chat_reference=source_chat_reference,
        chat_timezone=chat_timezone,
        chat_session_id=chat_session_id,
        discussion_topic=discussion_topic,
        core_conclusions=core_conclusions,
        related_record_ids=related_record_ids,
        securities=securities,
        industries=industries,
        keywords=keywords,
        model_mr_view=model_mr_view,
        user_view=user_view,
        gpt_analysis=gpt_analysis,
        unresolved_questions=unresolved_questions,
        verification_items=verification_items,
        memory_key=memory_key,
        batch_key=batch_key,
    )


@mcp.tool(
    name="search_conversation_memory",
    title="搜索历史对话长期记忆",
    description=account_text(
        "只搜索独立的精炼记忆库，按主题、个股、行业、关键词和历史结论返回压缩结果，"
        "不会把海量完整聊天全部载入上下文。保存前可传相同的 source_chat_reference 和"
        "chat_session_id，并把 query 留空、limit 设为 1，以读取 latest_saved_batch 的结束时间。"
        "当需要原始细节或版本历史时，再用 get_conversation_memory 读取完整记录。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def search_conversation_memory(
    query: str = "",
    limit: int = 10,
    source_chat_reference: str = "",
    chat_session_id: str = "",
) -> dict[str, Any]:
    """Search compact refined memories and expose the latest saved checkpoint."""
    return memory_store.search(
        query=query,
        limit=limit,
        source_chat_reference=source_chat_reference,
        chat_session_id=chat_session_id,
    )


@mcp.tool(
    name="get_conversation_memory",
    title="读取完整对话长期记忆",
    description=account_text(
        "根据 search_conversation_memory 返回的 memory:<数字> 或 memory_key，"
        "按需读取完整结构化记忆、聊天批次时间和版本历史。日常搜索不应批量调用本工具。"
        "返回内容继续保持模型先生观点、用户观点、GPT分析三者分离。"
    ),
    annotations=READ_ONLY_ANNOTATIONS,
    structured_output=True,
)
def get_conversation_memory(reference: str) -> dict[str, Any]:
    """Read one complete long-term memory and its revision history."""
    return memory_store.get(reference=reference)


@mcp.tool(
    name="update_conversation_memory",
    title="更新已有对话长期记忆",
    description=account_text(
        "仅在用户明确要求修正或补充某条长期记忆时调用。未提供的字段保持不变；"
        "每次有效修改都会新增版本记录，不直接抹掉旧版本，也不会修改原始视频和评论。"
        "更新后系统会自动同步重建对应的精炼记忆。聊天批次的起止时间不通过本工具修改。"
    ),
    annotations=WRITE_MEMORY_ANNOTATIONS,
    structured_output=True,
)
def update_conversation_memory(
    reference: str,
    title: str | None = None,
    discussion_topic: str | None = None,
    core_conclusions: list[str] | None = None,
    related_record_ids: list[str] | None = None,
    securities: list[str] | None = None,
    industries: list[str] | None = None,
    keywords: list[str] | None = None,
    model_mr_view: str | None = None,
    user_view: str | None = None,
    gpt_analysis: str | None = None,
    unresolved_questions: list[str] | None = None,
    verification_items: list[str] | None = None,
    source_chat_reference: str | None = None,
    status: str | None = None,
    change_note: str = "",
) -> dict[str, Any]:
    """Update a confirmed memory while preserving its previous versions."""
    return memory_store.update(
        reference=reference,
        title=title,
        discussion_topic=discussion_topic,
        core_conclusions=core_conclusions,
        related_record_ids=related_record_ids,
        securities=securities,
        industries=industries,
        keywords=keywords,
        model_mr_view=model_mr_view,
        user_view=user_view,
        gpt_analysis=gpt_analysis,
        unresolved_questions=unresolved_questions,
        verification_items=verification_items,
        source_chat_reference=source_chat_reference,
        status=status,
        change_note=change_note,
    )


def main() -> None:
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
