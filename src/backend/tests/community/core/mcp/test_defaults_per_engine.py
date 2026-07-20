from agentclaw.community.core.mcp.services._defaults import (
    _UCT_SERVER_CODE,
    _uct_auth_header,
    get_default_mcp_server_codes,
    get_default_mcp_servers,
)


BCS_MCP_SERVER_CODE = "mcp.ant.agentclawscs.bcs_mcp"


def test_openclaw_defaults_preserved():
    servers = get_default_mcp_servers("openclaw")
    codes = [s["server_code"] for s in servers]
    assert "mcp.ant.antprocessai.anttaskmcp" in codes
    assert "mcp.ant.antdingopenapi.antdingtodomcpserver" in codes
    assert BCS_MCP_SERVER_CODE in codes
    assert "hitl" in codes


def test_claude_code_has_its_own_list():
    servers = get_default_mcp_servers("claude-code")
    assert isinstance(servers, list)


def test_claude_code_merges_aicoding_research_mcps():
    servers = get_default_mcp_servers("claude_code")
    codes = [s["server_code"] for s in servers]
    # claude_code 原有 12 项全部保留（不丢能力）。
    assert "mcp.ant.antcodemcp.code.mcpserver" in codes
    assert "mcp.ant.brwithub.worksummaryserver" in codes
    assert "mcp.ant.homistudio.meetmcp" in codes
    # aicoding 独有的 6 个研发 MCP 已补入（不重复添加，不靠 template_type 判定）。
    aicoding_only = (
        "mcp.ant.zlatan.yuntumcpserver",
        "mcp.ant.alipaybase-antlogsmcp.mcp-server",
        "mcp.ant.arkai.assistantmcpserver",
        "mcp.ant.agentix.112858.aixAicoding",
        "mcp.ant.faas.aixjiter.AixCodingMemoryMCP",
        "mcp.ant.rgmcpserver.rgfastcheckmcpserver",
    )
    for code in aicoding_only:
        assert code in codes, f"missing aicoding-only MCP in claude_code: {code}"
    # 无重复。
    assert len(codes) == len(set(codes))
    # 12 原有 + 6 新增 + 1 clawmind = 19。
    assert len(codes) == 19


def test_aicoding_has_its_own_list():
    servers = get_default_mcp_servers("aicoding")
    codes = [s["server_code"] for s in servers]
    assert isinstance(servers, list)
    assert len(servers) == 10
    assert "mcp.ant.arkai.assistantmcpserver" in codes
    assert "mcp.ant.arkai.dimamcpserver" in codes
    assert "mcp.ant.faas.aixjiter.AixCodingMemoryMCP" in codes
    assert "mcp.ant.rgmcpserver.rgfastcheckmcpserver" in codes
    assert BCS_MCP_SERVER_CODE in codes
    assert "hitl" in codes
    # Trimmed servers must no longer be in the aicoding defaults.
    assert "mcp.ant.secaibase.secknowledgemcpserver" not in codes
    assert "mcp.ant.antcodemcp.code.mcpserver" not in codes
    # No duplicate entries (dimamcpserver was previously listed twice).
    assert len(codes) == len(set(codes))
    # Different list object from openclaw — no accidental sharing.
    assert servers is not get_default_mcp_servers("openclaw")


def test_hitl_is_default_for_mcp_enabled_engines():
    assert "hitl" in get_default_mcp_server_codes("openclaw")
    assert "hitl" in get_default_mcp_server_codes("claude_code")
    assert "hitl" in get_default_mcp_server_codes("hermes")
    assert "hitl" in get_default_mcp_server_codes("aicoding")
    assert "hitl" not in get_default_mcp_server_codes("moltis")


def test_clawmind_is_default_for_claude_code_only():
    assert "clawmind" in get_default_mcp_server_codes("claude_code")
    assert "clawmind" not in get_default_mcp_server_codes("openclaw")
    assert "clawmind" not in get_default_mcp_server_codes("hermes")
    assert "clawmind" not in get_default_mcp_server_codes("aicoding")


def test_bcs_mcp_is_default_for_mcp_enabled_engines():
    assert BCS_MCP_SERVER_CODE in get_default_mcp_server_codes("openclaw")
    assert BCS_MCP_SERVER_CODE in get_default_mcp_server_codes("claude_code")
    assert BCS_MCP_SERVER_CODE in get_default_mcp_server_codes("hermes")
    assert BCS_MCP_SERVER_CODE in get_default_mcp_server_codes("aicoding")
    assert BCS_MCP_SERVER_CODE not in get_default_mcp_server_codes("moltis")


def test_unknown_engine_returns_empty():
    assert get_default_mcp_servers("does-not-exist") == []


def test_codes_helper_uses_engine():
    openclaw_codes = get_default_mcp_server_codes("openclaw")
    claude_codes = get_default_mcp_server_codes("claude-code")
    assert openclaw_codes != claude_codes or openclaw_codes == [] == claude_codes
    aicoding_codes = get_default_mcp_server_codes("aicoding")
    assert openclaw_codes != aicoding_codes
    assert all(isinstance(c, str) for c in openclaw_codes)


def test_default_engine_fallback():
    # Calling without engine_type must not crash; it returns the DEFAULT_ENGINE_TYPE list.
    assert get_default_mcp_servers() == get_default_mcp_servers("openclaw")
    assert get_default_mcp_server_codes() == get_default_mcp_server_codes("openclaw")


# ── uctmcptools auth-token config injection ────────────────────────────────


class _FakeConfig:
    """Minimal stand-in for the lazy ``sofa_config`` proxy."""

    def __init__(self, user_config):
        self.user_config = user_config


def _uct_entry(servers):
    return next(s for s in servers if s["server_code"] == _UCT_SERVER_CODE)


def _patch_config(monkeypatch, user_config):
    # _uct_auth_header does ``from ...config.sofa import sofa_config`` at call
    # time, so patching the module attribute is picked up on the next call.
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        _FakeConfig(user_config),
    )


def test_uct_auth_header_absent_when_token_unset():
    # Community / test profile ships no token → header omitted, entry stays bare.
    assert _uct_auth_header() == {}
    assert "headers" not in _uct_entry(get_default_mcp_servers("openclaw"))


def test_uct_auth_header_injected_when_token_configured(monkeypatch):
    _patch_config(monkeypatch, {"mcp": {"uct_auth_token": "Bearer TESTTOKEN"}})
    assert _uct_auth_header() == {"x-ling-auth": "Bearer TESTTOKEN"}
    # Injected onto the uctmcptools entry for every engine that lists it.
    for engine in ("openclaw", "claude_code", "hermes"):
        assert _uct_entry(get_default_mcp_servers(engine))["headers"] == {
            "x-ling-auth": "Bearer TESTTOKEN"
        }
    # Other default entries stay header-free.
    others = [
        s
        for s in get_default_mcp_servers("openclaw")
        if s["server_code"] != _UCT_SERVER_CODE
    ]
    assert all("headers" not in s for s in others)


def test_uct_auth_header_ignores_blank_or_nonstring_token(monkeypatch):
    _patch_config(monkeypatch, {"mcp": {"uct_auth_token": "   "}})
    assert _uct_auth_header() == {}
    _patch_config(monkeypatch, {"mcp": {"uct_auth_token": 123}})
    assert _uct_auth_header() == {}
    _patch_config(monkeypatch, {})  # no mcp block at all
    assert _uct_auth_header() == {}



# ── 默认 CLI 列表（aicoding 链路：aicoding 引擎 / claude_code 研发模板）──

from agentclaw.community.core.mcp.services._defaults import (
    get_default_cli_items,
)

_EXPECTED_CLI_CODES = (
    "adev-cli", "acli", "antcode-cli", "linke-cli",
    "linkw-cli", "qmx-invoke-cli", "serverless", "derisk-cli",
    "yuque-cli",
)


def _assert_default_clis(items):
    codes = [it["cli_code"] for it in items]
    assert len(items) == len(_EXPECTED_CLI_CODES)
    for code in _EXPECTED_CLI_CODES:
        assert code in codes
    for it in items:
        assert "cli_code" in it
        assert "cli_name" in it
        assert "cli_desc" in it
    assert len(codes) == len(set(codes))


def test_aicoding_engine_has_default_cli_items():
    items = get_default_cli_items("aicoding")
    _assert_default_clis(items)


def test_claude_code_personal_coding_uses_aicoding_link():
    items = get_default_cli_items("claude_code", "personalCoding")
    _assert_default_clis(items)


def test_claude_code_application_coding_uses_aicoding_link():
    items = get_default_cli_items("claude_code", "applicationCoding")
    _assert_default_clis(items)


def test_claude_code_without_template_or_unknown_template_returns_empty():
    # claude_code 不带 template_type → 不走 aicoding 链路（返回空）
    assert get_default_cli_items("claude_code") == []
    # 非 personalCoding/applicationCoding 的 template_type → 空（fail-closed）
    assert get_default_cli_items("claude_code", "service") == []
    assert get_default_cli_items("claude_code", "other") == []
    # 非字符串且不可哈希的 template_type（来自用户 JSON）→ 不抛 TypeError，空（fail-closed）
    assert get_default_cli_items("claude_code", []) == []
    assert get_default_cli_items("claude_code", {}) == []
    # aicoding 引擎不依赖 template_type，始终返回默认 CLI 项
    assert len(get_default_cli_items("aicoding", [])) == len(_EXPECTED_CLI_CODES)


def test_non_aicoding_engines_return_empty_regardless_of_template():
    for engine in ("openclaw", "hermes", "moltis"):
        assert get_default_cli_items(engine) == []
        assert get_default_cli_items(engine, "personalCoding") == []
        assert get_default_cli_items(engine, "applicationCoding") == []


def test_default_cli_items_none_engine_returns_empty():
    assert get_default_cli_items(None) == []
    assert get_default_cli_items(None, "personalCoding") == []
    assert get_default_cli_items("") == []


def test_yuque_cli_is_default_entry():
    """yuque-cli is the newest default entry: full shape, last position, no dupes."""
    items = get_default_cli_items("aicoding")
    yuque = [it for it in items if it["cli_code"] == "yuque-cli"]
    assert len(yuque) == 1
    assert yuque[0] == {
        "cli_code": "yuque-cli",
        "cli_name": "yuque-cli",
        "cli_desc": "yuque cli",
    }
    codes = [it["cli_code"] for it in items]
    # appended last (stable ordering matters for downstream passport merge).
    assert codes[-1] == "yuque-cli"
    assert len(codes) == len(set(codes))
    # the claude_code coding templates share the same aicoding link, so the
    # new entry flows through that path too.
    codes_cc = [it["cli_code"] for it in get_default_cli_items("claude_code", "applicationCoding")]
    assert "yuque-cli" in codes_cc
    assert codes_cc[-1] == "yuque-cli"


def test_default_cli_items_returns_copy():
    items = get_default_cli_items("aicoding")
    items[0]["cli_code"] = "mutated"
    # 再次取不应被污染
    assert get_default_cli_items("aicoding")[0]["cli_code"] == "adev-cli"
    # claude_code 研发模板同样返还副本
    items2 = get_default_cli_items("claude_code", "personalCoding")
    items2[0]["cli_code"] = "mutated"
    assert get_default_cli_items("claude_code", "personalCoding")[0]["cli_code"] == "adev-cli"
