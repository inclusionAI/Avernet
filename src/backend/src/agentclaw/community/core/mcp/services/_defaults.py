"""Default MCP server configurations, keyed by engine type.

Moved from services/openclawserver/server/config.py. Each engine owns its own
default MCP list; callers pass an engine_type and get back the MCPs that should
be present in that engine's default skill set.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

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
    "claude_code": [
        {"server_code": "mcp.ant.antprocessai.anttaskmcp", "name": "任务中心MCP", "description": "任务中心待办任务，已办任务等相关任务查询MCP", "icon": "https://mdn.alipayobjects.com/huamei_hipplu/afts/img/7sWyTrRA6awAAAAAQCAAAAgADspJAQFr/original"},
        {"server_code": "mcp.ant.arkai.dimamcpserver", "name": "Dima MCP", "description": "Dima MCP", "icon": "https://mass.alipay.com/pa_csrobotmng/afts/file/A*-R2BRJMTE-sAAAAAAAAAAAAAend4AQ"},
        {"server_code": "mcp.ant.homistudio.meetmcp", "name": "会议信息服务", "description": "会议信息相关mcp，提供查询分享给我的会议列表、我创建的会议列表、单个会议的纪要、会议发言信息以及待办查询等功能", "icon": "https://mass.alipay.com/antlx/afts/file/VschRLkf5RgAAAAATSAAAAgAn_rLAQBr"},
        {"server_code": _UCT_SERVER_CODE},
        {"server_code": "mcp.ant.antdingopenapi.antdingeventmcpserver", "name": "蚂蚁钉日程相关-MCP服务", "description": "蚂蚁钉日程相关-MCP服务", "icon": "https://mass.alipay.com/pa_csrobotmng/afts/file/A*bowcRJzX2hkAAAAATnAAAAgAend4AQ"},
        {"server_code": "mcp.ant.antdingopenapi.antdingtodomcpserver", "name": "蚂蚁钉待办服务", "description": "蚂蚁钉待办服务", "icon": "https://mass.alipay.com/pa_csrobotmng/afts/file/A*c14QQpiCb3cAAAAAboAAAAgAend4AQ"},
        {"server_code": "mcp.ant.antdingopenapi.antdingmessagemcpserver", "name": "蚂蚁钉消息相关-MCP服务", "description": "蚂蚁钉消息相关-MCP服务", "icon": "https://mass.alipay.com/pa_csrobotmng/afts/file/A*YKl9TpTR77MAAAAATnAAAAgAend4AQ"},
        {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver", "name": "语雀 MCP", "description": "语雀 MCP 服务，覆盖文档读写、知识库管理、目录操作、团队协作、互动分析全流程。", "icon": "https://mdn.alipayobjects.com/member_xuexiao/afts/img/y_mYRqICWf0AAAAAQSAAAAgAel3JAQBr/original"},
        {"server_code": "mcp.ant.antcodemcp.code.mcpserver", "name": "AntCodeMCP", "description": "AntCode提供的 MCP 服务", "icon": "https://mass.alipay.com/pa_csrobotmng/afts/file/A*7uPnTZJBxJ0AAAAAQKAAAAgAend4AQ"},
        {"server_code": "mcp.ant.archassistant-mcp.appmcp", "name": "应用信息服务", "description": "架构工作台提供的应用元信息查询服务", "icon": "https://mass.alipay.com/antlx/afts/file/6_xrRZPIsoEAAAAAQ0AAAAgAn_rLAQBr?t=W_pWcnzlTiEUcWXMvI3mE4oCplZZYlIuN8OGDJvi7bQDAAAAZAAAy_poguVo"},
        {"server_code": "mcp.ant.brwithub.worksummaryserver", "name": "工作报告撰写", "description": "基于用户输入的结构化数据或非结构化文本，智能生成专业、规范的职场汇报文档（如周报、月报、项目总结等）的 MCP 服务。", "icon": "https://mass.alipay.com/antlx/afts/file/CaptSLTbO9oAAAAASrAAAAgAn_rLAQBr"},
        {"server_code": "mcp.ant.agentclawscs.bcs_mcp", "name": "BCN协作服务", "description": "用于BCN群聊中bot间协作", "icon": "https://mass.alipay.com/antlx/afts/file/HVkVS5xeugEAAAAAUKAAAAgAn_rLAQBr"},
        {"server_code": "mcp.ant.zlatan.yuntumcpserver", "name": "云图 mcp 服务", "description": "云图官方mcp服务，支持链路环境自动检测，链路树状与数组形式详情查询", "icon": "https://mass.alipay.com/pa_csrobotmng/afts/file/A*0MzWTIgbMx8AAAAAXRAAAAgAend4AQ"},
        {"server_code": "mcp.ant.alipaybase-antlogsmcp.mcp-server", "name": "antlogs mcp 服务", "description": "antlogs mcp 服务", "icon": "https://mass.alipay.com/pa_csrobotmng/afts/file/A*D9gjQYxQYQUAAAAARQAAAAgAend4AQ"},
        {"server_code": "mcp.ant.arkai.assistantmcpserver", "name": "Skybase - 知识问答", "description": "Skybase 是蚂蚁的研发 AI 知识库。当前 MCP 主要用于两个方面：1) 知识库的检索、2) 研发通用问答、前端问答、中间件问答。", "icon": "https://mdn.alipayobjects.com/huamei_hipplu/afts/img/VAz9RqYB9BQAAAAAAAAAAAAADspJAQFr/original"},
        {"server_code": "mcp.ant.faas.aixjiter.AixCodingMemoryMCP", "name": "AixCodingMemoryMCP", "description": "用于aixcoding memoryOS知识库查询", "icon": "https://mdn.alipayobjects.com/huamei_hipplu/afts/img/7sWyTrRA6awAAAAAQCAAAAgADspJAQFr/original"},
        {"server_code": "mcp.ant.rgmcpserver.rgfastcheckmcpserver", "name": "星海MCP服务", "description": "星海MCP服务", "icon": "https://mass.alipay.com/antlx/afts/file/va69QrsWQawAAAAATyAAAAgAn_rLAQBr"},
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
        {"server_code": "mcp.ant.zlatan.yuntumcpserver"},
        {"server_code": "mcp.ant.alipaybase-antlogsmcp.mcp-server"},
        {"server_code": "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver"},
        {"server_code": "mcp.ant.arkai.assistantmcpserver"},
        {"server_code": "mcp.ant.arkai.dimamcpserver"},
        {"server_code": "mcp.ant.agentix.112858.aixAicoding"},
        {"server_code": "mcp.ant.agentclawscs.bcs_mcp"},
        {"server_code": "mcp.ant.faas.aixjiter.AixCodingMemoryMCP"},
        {"server_code": "mcp.ant.rgmcpserver.rgfastcheckmcpserver"},
        {"server_code": "hitl"},
    ],
}


def _resolve(engine_type: Optional[str]) -> str:
    return engine_type or DEFAULT_ENGINE_TYPE


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


def get_default_mcp_servers(engine_type: Optional[str] = None) -> List[dict]:
    """Return the default MCP server configs for the given engine.

    Unknown engines get an empty list (fail-closed, not a crash). The uctmcptools
    entry gets its secret ``x-ling-auth`` header injected from config when one is
    set (see :func:`_uct_auth_header`); otherwise it is returned header-free.
    """
    servers = [
        dict(cfg) for cfg in _DEFAULT_MCP_SERVERS_BY_ENGINE.get(_resolve(engine_type), [])
    ]
    auth_header = _uct_auth_header()
    if auth_header:
        for cfg in servers:
            if cfg.get("server_code") == _UCT_SERVER_CODE:
                cfg["headers"] = {**cfg.get("headers", {}), **auth_header}
    return servers


def get_default_mcp_server_codes(engine_type: Optional[str] = None) -> List[str]:
    """Return the list of default MCP server_codes for the given engine."""
    return [cfg["server_code"] for cfg in get_default_mcp_servers(engine_type)]


def get_default_mcp_config(
    engine_type: Optional[str],
    server_code: str,
) -> Optional[dict]:
    """Return the default MCP config dict (with optional name/description/icon) for ``server_code``.

    Looks up the per-engine default list by both ``server_code`` and resolved engine.
    Returns ``None`` when the code is not a default MCP for that engine, so callers
    can fall back to the legacy mock-name path. Configs that only declare
    ``server_code`` (no ``name``) also return a dict — callers decide via
    ``cfg.get("name")`` whether a real name is available.
    """
    for cfg in _DEFAULT_MCP_SERVERS_BY_ENGINE.get(_resolve(engine_type), []):
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


# template_type 白名单：claude_code 引擎下，仅 personalCoding / applicationCoding
# 走 aicoding 默认 CLI 链路（研发类 bot 才需要这些 CLI）。
_CLAUDE_CODE_CLI_TEMPLATE_TYPES = frozenset({"personalCoding", "applicationCoding"})


def get_default_cli_items(
    engine_type: Optional[str] = None,
    template_type: Optional[str] = None,
) -> List[dict]:
    """返回默认 CLI 列表（CliItem dict 形式）。

    走 aicoding 链路（返回默认 CLI）的判定：
      1. engine_type == "aicoding"；或
      2. engine_type == "claude_code" 且 template_type in
         {"personalCoding", "applicationCoding"}。
    其余一律返回空列表（fail-closed，避免给非研发类 bot 误授权 CLI）。

    注意：与 get_default_mcp_servers 不同，CLI 不做 DEFAULT_ENGINE_TYPE 兜底，
    None 直接返回空列表。
    """
    if not engine_type:
        return []
    if engine_type == "aicoding":
        key = "aicoding"
    elif (
        engine_type == "claude_code"
        and isinstance(template_type, str)
        and template_type in _CLAUDE_CODE_CLI_TEMPLATE_TYPES
    ):
        key = "aicoding"
    else:
        return []
    return [dict(item) for item in _DEFAULT_CLI_ITEMS_BY_ENGINE.get(key, [])]
