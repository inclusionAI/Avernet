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
        {"server_code": "mcp.ant.faas.aixjiter.AixCodingMemoryMCP"},
        {"server_code": "mcp.ant.rgmcpserver.rgfastcheckmcpserver"},
        {"server_code": "hitl"},
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



# ============ 默认 CLI 列表（按 engine 分桶）============
# 仿 _DEFAULT_MCP_SERVERS_BY_ENGINE：创建时读常量 → 传 apply_agent_passport(cli_items=)。
# CLI 无需 MCP Center 元信息拉取，其"执行内容"由 passport 授权侧据 cli_code 关联 Skill。
_DEFAULT_CLI_ITEMS_BY_ENGINE: Dict[str, List[dict]] = {
    "aicoding": [
        {"cli_code": "adev",           "cli_name": "adev",           "cli_desc": "Ant Adev 研发命令行工具"},
        {"cli_code": "acli",           "cli_name": "acli",           "cli_desc": "Ant Acli 命令行工具"},
        {"cli_code": "antcode",        "cli_name": "antcode",        "cli_desc": "AntCode 代码托管平台命令行工具"},
        {"cli_code": "linke",          "cli_name": "linke",          "cli_desc": "Linke 命令行工具"},
        {"cli_code": "linke-cli",      "cli_name": "linke-cli",      "cli_desc": "Linke CLI 命令行工具"},
        {"cli_code": "linkw-cli",      "cli_name": "linkw-cli",      "cli_desc": "Linkw CLI 命令行工具"},
        {"cli_code": "qmx-invoke-cli", "cli_name": "qmx-invoke-cli", "cli_desc": "Qmx Invoke 命令行工具"},
        {"cli_code": "serverless",     "cli_name": "serverless",     "cli_desc": "Serverless 命令行工具"},
        {"cli_code": "derisk",         "cli_name": "derisk",         "cli_desc": "Derisk 风控命令行工具"},
    ],
}


# template_type 白名单：claude_code 引擎下，仅 personalCoding / applicationCoding
# 走 aicoding 默认 CLI 链路（研发类 bot 才需要这 9 个 CLI）。
_CLAUDE_CODE_CLI_TEMPLATE_TYPES = frozenset({"personalCoding", "applicationCoding"})


def get_default_cli_items(
    engine_type: Optional[str] = None,
    template_type: Optional[str] = None,
) -> List[dict]:
    """返回默认 CLI 列表（CliItem dict 形式）。

    走 aicoding 链路（返回 9 个默认 CLI）的判定：
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
