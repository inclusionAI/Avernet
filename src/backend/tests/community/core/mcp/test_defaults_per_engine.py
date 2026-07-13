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
