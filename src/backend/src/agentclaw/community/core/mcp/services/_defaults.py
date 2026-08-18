"""Default MCP server configurations, keyed by engine type.

Moved from services/openclawserver/server/config.py. Each engine owns its own
default MCP list; callers pass an engine_type and get back the MCPs that should
be present in that engine's default skill set.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

from agentclaw.community.core.default_capabilities import (
    resolve_default_capabilities_engine_type,
)
from agentclaw.community.core.bot_management.engines.registry import (
    get_mcp_defaults_resolver_registry,
)

logger = logging.getLogger(__name__)


# The uctmcptools MCP server authenticates with an ``x-ling-auth`` header whose
# token is a per-deployment SECRET. It is never baked into source: community
# ships no token (the header is omitted), and a corp deployment supplies it via
# ``user_config.mcp.uct_auth_token`` in its config overlay. See
# ``_uct_auth_header``.
_UCT_SERVER_CODE = "mcp.ant.agentix.150490.uctmcptools"


# Per-engine default MCP server lists. Keep engine keys in sync with
# agentclaw.community.plugin_api.models.SUPPORTED_ENGINE_TYPES.
_DEFAULT_MCP_SERVERS_BY_ENGINE: Dict[str, List[dict]] = {
    "openclaw": [
        {"server_code": "mcp.ant.antprocessai.anttaskmcp"},
        {"server_code": "mcp.ant.arkai.dimamcpserver"},
        {"server_code": "mcp.ant.homistudio.meetmcp"},
        {"server_code": _UCT_SERVER_CODE},
        {"server_code": "mcp.ant.antdingopenapi.antdingeventmcpserver"},
        {"server_code": "mcp.ant.antdingopenapi.antdingtodomcpserver"},
        {"server_code": "mcp.ant.antdingopenapi.antdingmessagemcpserver"},
        {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"},
        {"server_code": "mcp.ant.antcodemcp.code.mcpserver"},
        {"server_code": "mcp.ant.brwithub.worksummaryserver"},
        {"server_code": "mcp.ant.agentclawscs.bcs_mcp"},
        {"server_code": "hitl"},
    ],
    "moltis": [],
    "teclaw": [
        {"server_code": "mcp.ant.lwawchat.cogmessagemcp", "name": "蚂蚁钉协作群消息相关-MCP服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdingreportmcpserver", "name": "蚂蚁钉日志服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdinggroupmcpserver", "name": "蚂蚁钉群服务"},
        {"server_code": "mcp.ant.lwawchat.cogdocumentmcp", "name": "蚂蚁钉协作群文档相关-MCP服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdingeventmcpserver", "name": "蚂蚁钉日程相关-MCP服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdingrobotmcpserver", "name": "蚂蚁钉机器人相关-MCP服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdingmessagemcpserver", "name": "蚂蚁钉消息相关-MCP服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdingtodomcpserver", "name": "蚂蚁钉待办服务"},
        {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver", "name": "语雀 MCP"},
        {"server_code": "mcp.ant.arkai.dimamcpserver", "name": "Dima-MCP服务"},
        {"server_code": "mcp.ant.homistudio.recordmcp", "name": "会中会话记录查询服务"},
        {"server_code": "mcp.ant.rpc.dcanttouch.adminservice", "name": "行政小宝MCP服务"},
        # Local/stdio; resolved through LocalMCPRegistry, not MCP Center. Kept
        # last to match the other engines' lists.
        {"server_code": "hitl", "name": "HITL"},
    ],
    "claude_code": [
        {"server_code": "mcp.ant.antprocessai.anttaskmcp", "name": "任务中心MCP", "description": "任务中心待办任务，已办任务等相关任务查询MCP"},
        {"server_code": "mcp.ant.arkai.dimamcpserver", "name": "Dima MCP", "description": "Dima MCP"},
        {"server_code": "mcp.ant.homistudio.meetmcp", "name": "会议信息服务", "description": "会议信息相关mcp，提供查询分享给我的会议列表、我创建的会议列表、单个会议的纪要、会议发言信息以及待办查询等功能"},
        {"server_code": _UCT_SERVER_CODE},
        {"server_code": "mcp.ant.antdingopenapi.antdingeventmcpserver", "name": "蚂蚁钉日程相关-MCP服务", "description": "蚂蚁钉日程相关-MCP服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdingtodomcpserver", "name": "蚂蚁钉待办服务", "description": "蚂蚁钉待办服务"},
        {"server_code": "mcp.ant.antdingopenapi.antdingmessagemcpserver", "name": "蚂蚁钉消息相关-MCP服务", "description": "蚂蚁钉消息相关-MCP服务"},
        {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver", "name": "语雀 MCP", "description": "语雀 MCP 服务，覆盖文档读写、知识库管理、目录操作、团队协作、互动分析全流程。"},
        {"server_code": "mcp.ant.antcodemcp.code.mcpserver", "name": "AntCodeMCP", "description": "AntCode提供的 MCP 服务"},
        {"server_code": "mcp.ant.archassistant-mcp.appmcp", "name": "应用信息服务", "description": "架构工作台提供的应用元信息查询服务"},
        {"server_code": "mcp.ant.brwithub.worksummaryserver", "name": "工作报告撰写", "description": "基于用户输入的结构化数据或非结构化文本，智能生成专业、规范的职场汇报文档（如周报、月报、项目总结等）的 MCP 服务。"},
        {"server_code": "mcp.ant.agentclawscs.bcs_mcp", "name": "BCN协作服务", "description": "用于BCN群聊中bot间协作"},
        {"server_code": "mcp.ant.zlatan.yuntumcpserver", "name": "云图 mcp 服务", "description": "云图官方mcp服务，支持链路环境自动检测，链路树状与数组形式详情查询"},
        {"server_code": "mcp.ant.alipaybase-antlogsmcp.mcp-server", "name": "antlogs mcp 服务", "description": "antlogs mcp 服务"},
        {"server_code": "mcp.ant.arkai.assistantmcpserver", "name": "Skybase - 知识问答", "description": "Skybase 是蚂蚁的研发 AI 知识库。当前 MCP 主要用于两个方面：1) 知识库的检索、2) 研发通用问答、前端问答、中间件问答。"},
        {"server_code": "mcp.ant.faas.aixjiter.AixCodingMemoryMCP", "name": "AixCodingMemoryMCP", "description": "用于aixcoding memoryOS知识库查询"},
        {"server_code": "mcp.ant.rgmcpserver.rgfastcheckmcpserver", "name": "星海MCP服务", "description": "星海MCP服务"},
        {"server_code": "hitl"},
        {"server_code": "clawmind"},
    ],
    "hermes": [
        {"server_code": "mcp.ant.antprocessai.anttaskmcp"},
        {"server_code": "mcp.ant.arkai.dimamcpserver"},
        {"server_code": "mcp.ant.homistudio.meetmcp"},
        {"server_code": _UCT_SERVER_CODE},
        {"server_code": "mcp.ant.antdingopenapi.antdingeventmcpserver"},
        {"server_code": "mcp.ant.antdingopenapi.antdingtodomcpserver"},
        {"server_code": "mcp.ant.antdingopenapi.antdingmessagemcpserver"},
        {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"},
        {"server_code": "mcp.ant.antcodemcp.code.mcpserver"},
        {"server_code": "mcp.ant.brwithub.worksummaryserver"},
        {"server_code": "mcp.ant.agentclawscs.bcs_mcp"},
        {"server_code": "hitl"},
    ],
    "aicoding": [
         {"server_code": "mcp.ant.antprocessai.anttaskmcp", "name": "任务中心MCP", "description": "任务中心待办任务，已办任务等相关任务查询MCP"},
                {"server_code": "mcp.ant.arkai.dimamcpserver", "name": "Dima MCP", "description": "Dima MCP"},
                {"server_code": "mcp.ant.homistudio.meetmcp", "name": "会议信息服务", "description": "会议信息相关mcp，提供查询分享给我的会议列表、我创建的会议列表、单个会议的纪要、会议发言信息以及待办查询等功能"},
                {"server_code": _UCT_SERVER_CODE},
                {"server_code": "mcp.ant.antdingopenapi.antdingeventmcpserver", "name": "蚂蚁钉日程相关-MCP服务", "description": "蚂蚁钉日程相关-MCP服务"},
                {"server_code": "mcp.ant.antdingopenapi.antdingtodomcpserver", "name": "蚂蚁钉待办服务", "description": "蚂蚁钉待办服务"},
                {"server_code": "mcp.ant.antdingopenapi.antdingmessagemcpserver", "name": "蚂蚁钉消息相关-MCP服务", "description": "蚂蚁钉消息相关-MCP服务"},
                {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver", "name": "语雀 MCP", "description": "语雀 MCP 服务，覆盖文档读写、知识库管理、目录操作、团队协作、互动分析全流程。"},
                {"server_code": "mcp.ant.antcodemcp.code.mcpserver", "name": "AntCodeMCP", "description": "AntCode提供的 MCP 服务"},
                {"server_code": "mcp.ant.archassistant-mcp.appmcp", "name": "应用信息服务", "description": "架构工作台提供的应用元信息查询服务"},
                {"server_code": "mcp.ant.brwithub.worksummaryserver", "name": "工作报告撰写", "description": "基于用户输入的结构化数据或非结构化文本，智能生成专业、规范的职场汇报文档（如周报、月报、项目总结等）的 MCP 服务。"},
                {"server_code": "mcp.ant.agentclawscs.bcs_mcp", "name": "BCN协作服务", "description": "用于BCN群聊中bot间协作"},
                {"server_code": "mcp.ant.zlatan.yuntumcpserver", "name": "云图 mcp 服务", "description": "云图官方mcp服务，支持链路环境自动检测，链路树状与数组形式详情查询"},
                {"server_code": "mcp.ant.alipaybase-antlogsmcp.mcp-server", "name": "antlogs mcp 服务", "description": "antlogs mcp 服务"},
                {"server_code": "mcp.ant.arkai.assistantmcpserver", "name": "Skybase - 知识问答", "description": "Skybase 是蚂蚁的研发 AI 知识库。当前 MCP 主要用于两个方面：1) 知识库的检索、2) 研发通用问答、前端问答、中间件问答。"},
                {"server_code": "mcp.ant.faas.aixjiter.AixCodingMemoryMCP", "name": "AixCodingMemoryMCP", "description": "用于aixcoding memoryOS知识库查询"},
                {"server_code": "mcp.ant.rgmcpserver.rgfastcheckmcpserver", "name": "星海MCP服务", "description": "星海MCP服务"},
                {"server_code": "hitl"},
                {"server_code": "clawmind"},
    ],
}


def _uct_auth_header() -> Dict[str, str]:
    """Per-deployment ``x-ling-auth`` header for the uctmcptools MCP server.

    Reads the token from ``user_config.mcp.uct_auth_token``. Community ships no
    token, so this returns ``{}`` (the header is omitted); a corp deployment sets
    the key in its config overlay and gets ``{"x-ling-auth": <token>}``.

    The config read is defensive: config may be unavailable in bare unit tests or
    early boot, so any failure falls back to ``{}`` (the safe, token-absent path).
    """
    try:
        from agentclaw.community.core.config.sofa import sofa_config

        mcp_block = (getattr(sofa_config, "user_config", None) or {}).get("mcp") or {}
        token = mcp_block.get("uct_auth_token")
    except Exception as exc:  # pragma: no cover — defensive; config may be absent
        logger.warning("uct_auth_token unavailable from config: %s", exc)
        return {}
    if isinstance(token, str) and token.strip():
        return {"x-ling-auth": token}
    return {}



class _EngineMcpDefaultsResolver:
    """Engine hook for deriving effective default MCP configs.

    The common MCP defaults flow resolves the default-capabilities engine bucket
    before selecting a resolver. The resolver only owns bucket-specific
    post-processing, such as merging template-provided MCP presets.
    """

    def resolve(
        self,
        default_servers: List[dict],
        ext_info: Optional[Mapping[str, Any]] = None,
    ) -> List[dict]:
        return [dict(cfg) for cfg in default_servers]


_DEFAULT_MCP_RESOLVER = _EngineMcpDefaultsResolver()


def _resolve_default_mcp_engine_bucket(
    engine_type: Optional[str],
    template_type: Any = None,
) -> str:
    return resolve_default_capabilities_engine_type(
        engine_type,
        template_type,
    )


def _mcp_defaults_resolver(engine_bucket: str):
    return (
        get_mcp_defaults_resolver_registry().resolve(engine_bucket)
        or _DEFAULT_MCP_RESOLVER
    )


def get_default_mcp_servers(
    engine_type: Optional[str] = None,
    template_type: Any = None,
    *,
    ext_info: Optional[Mapping[str, Any]] = None,
) -> List[dict]:
    """Return the default MCP server configs for the given engine.

    Unknown engines get an empty list (fail-closed, not a crash). The uctmcptools
    entry gets its secret ``x-ling-auth`` header injected from config when one is
    set (see :func:`_uct_auth_header`); otherwise it is returned header-free.
    """
    engine_bucket = _resolve_default_mcp_engine_bucket(
        engine_type,
        template_type,
    ) 
    resolver = _mcp_defaults_resolver(engine_bucket)
    servers = resolver.resolve(
        _DEFAULT_MCP_SERVERS_BY_ENGINE.get(engine_bucket, []),
        ext_info,
    )
    auth_header = _uct_auth_header()
    if auth_header:
        for cfg in servers:
            if cfg.get("server_code") == _UCT_SERVER_CODE:
                cfg["headers"] = {**cfg.get("headers", {}), **auth_header}
    return servers


def get_default_mcp_server_codes(
    engine_type: Optional[str] = None,
    template_type: Any = None,
    *,
    ext_info: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """Return the list of default MCP server_codes for the given engine."""
    return [
        cfg["server_code"]
        for cfg in get_default_mcp_servers(
            engine_type,
            template_type,
            ext_info=ext_info,
        )
    ]


def get_default_mcp_config(
    engine_type: Optional[str],
    server_code: str,
    template_type: Any = None,
    *,
    ext_info: Optional[Mapping[str, Any]] = None,
) -> Optional[dict]:
    """Return the default MCP config dict (with optional name/description/icon) for ``server_code``.

    Looks up the per-engine default list by both ``server_code`` and resolved engine.
    Returns ``None`` when the code is not a default MCP for that engine, so callers
    can fall back to the legacy mock-name path. Configs that only declare
    ``server_code`` (no ``name``) also return a dict — callers decide via
    ``cfg.get("name")`` whether a real name is available.
    """
    for cfg in get_default_mcp_servers(
        engine_type,
        template_type,
        ext_info=ext_info,
    ):
        if cfg.get("server_code") == server_code:
            return dict(cfg)
    return None


# ============ 默认 CLI 列表（按 engine 分桶）============
# 仿 _DEFAULT_MCP_SERVERS_BY_ENGINE：创建时读常量 → 传 apply_agent_passport(cli_items=)。
# CLI 无需 MCP Center 元信息拉取，其"执行内容"由 passport 授权侧据 cli_code 关联 Skill。
_DEFAULT_CLI_ITEMS_BY_ENGINE: Dict[str, List[dict]] = {
    "aicoding": [
        {"cli_code": "adev-cli",           "cli_name": "adev-cli",           "cli_desc": "Ant Adev 研发命令行工具"},
        {"cli_code": "acli",           "cli_name": "acli",           "cli_desc": "Ant Acli 命令行工具"},
        {"cli_code": "antcode-cli",        "cli_name": "antcode-cli",        "cli_desc": "AntCode 代码托管平台命令行工具"},
        {"cli_code": "linke-cli",          "cli_name": "linke-cli",          "cli_desc": "Linke CLI 命令行工具"},
        {"cli_code": "linkw-cli",          "cli_name": "linkw-cli",          "cli_desc": "Linkw CLI 命令行工具"},
        {"cli_code": "qmx-invoke-cli",     "cli_name": "qmx-invoke-cli",     "cli_desc": "QMX Invoke CLI 命令行工具"},
        {"cli_code": "serverless",         "cli_name": "serverless",         "cli_desc": "Serverless 命令行工具"},
        {"cli_code": "derisk-cli",         "cli_name": "derisk-cli",         "cli_desc": "Derisk 风控命令行工具"},
        {"cli_code": "yuque-cli",         "cli_name": "yuque-cli",         "cli_desc": "yuque cli"},
    ],
}


def get_default_cli_items(
    engine_type: Optional[str] = None,
    template_type: Optional[str] = None,
) -> List[dict]:
    """返回默认 CLI 列表（CliItem dict 形式）。

    默认能力分桶规则由对应引擎维护；CLI 这里只按分桶结果读取默认
    CLI 列表，未知桶返回空列表（fail-closed，避免误授权 CLI）。
    """
    from agentclaw.community.core.default_capabilities import (
        resolve_default_capabilities_engine_type,
    )

    key = resolve_default_capabilities_engine_type(
        engine_type,
        template_type,
    )
    return [dict(item) for item in _DEFAULT_CLI_ITEMS_BY_ENGINE.get(key, [])]
