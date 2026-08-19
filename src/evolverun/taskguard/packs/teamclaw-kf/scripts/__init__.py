"""
TeamClaw Support Scripts

提供 Langfuse 查询、Dima 缺陷创建、GRT 向量知识库检索和用户问题处理工具。

Usage:
    from scripts import query_conversations, get_langfuse_client

    # 查询对话记录
    sessions, machine_names = query_conversations(user_id="103892", days=0.02)

    # 获取 Langfuse 客户端
    client = get_langfuse_client()

    # 创建 Dima 缺陷（需求请使用 MCP createIssue）
    from scripts.dima_create_bug import DimaClient, create_teamclaw_bug, load_dima_config
    config = load_dima_config()
    client = DimaClient(config)
    result = create_teamclaw_bug(client, staff_id="012345", ...)

    # GRT 向量知识库检索
    from scripts.grt_search import grt_search, GRTSearchResult
    result = grt_search("Bot权限怎么配置", user_name="楚生", user_id="103892")

    # MCP Center 搜索与详情
    from scripts.mcp_center import mcp_search, mcp_detail, MCPSearchResult, MCPDetailResult
    result = mcp_search("知识库")
    detail = mcp_detail("mcp-dima-service")
"""

from .langfuse_query import (
    query_conversations,
    get_langfuse_client,
    ConversationSession,
    ConversationTurn,
)

from .dima_create_bug import (
    DimaClient,
    DimaConfig,
    create_teamclaw_bug,
    create_teamclaw_issue,
    create_teamclaw_task,
    format_work_item_url,
    _load_config as load_dima_config,
)

from .grt_search import (
    grt_search,
    GRTSearchResult,
    GRTSearchItem,
)

from .mcp_center import (
    mcp_search,
    mcp_detail,
    MCPSearchResult,
    MCPDetailResult,
    MCPSearchItem,
    MCPToolInfo,
    MCPEndpointInfo,
)

__version__ = "1.3.0"
__all__ = [
    "query_conversations",
    "get_langfuse_client",
    "ConversationSession",
    "ConversationTurn",
    "DimaClient",
    "DimaConfig",
    "create_teamclaw_bug",
    "create_teamclaw_issue",
    "create_teamclaw_task",
    "format_work_item_url",
    "load_dima_config",
    "grt_search",
    "GRTSearchResult",
    "GRTSearchItem",
    "mcp_search",
    "mcp_detail",
    "MCPSearchResult",
    "MCPDetailResult",
    "MCPSearchItem",
    "MCPToolInfo",
    "MCPEndpointInfo",
]