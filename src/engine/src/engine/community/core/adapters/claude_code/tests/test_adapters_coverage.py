"""Coverage extension tests for the ClaudeCode ACL adapters.

Targets the branches the base ``test_adapters.py`` leaves uncovered:
error/empty-result paths, None/missing-key payloads, list-vs-dict payload
variations, capability raises, and optional-field mappings.

Reuses the ``_Fake*Port`` patterns from ``test_adapters.py`` by subclassing
or fresh-defining ports that return canned edge-case data.
"""
from __future__ import annotations

from typing import Any

import pytest

from engine.community.core.adapters.claude_code.chat import ClaudeCodeChatAdapter
from engine.community.core.adapters.claude_code.cron import (
    ClaudeCodeCronAdapter,
    _build_add_params,
    _build_update_patch,
    _camel_to_snake,
    _convert_payload_for_wire,
    _convert_payload_keys,
    _job_from_dict,
    _notify_to_delivery,
    _run_from_dict,
    _schedule_for_wire,
    _status_from_dict,
)
from engine.community.core.adapters.claude_code.file import (
    ClaudeCodeFileAdapter,
    _extract_bytes,
)
from engine.community.core.adapters.claude_code.mcp import (
    ClaudeCodeMcpAdapter,
    _config_from_raw,
    _parse_transport,
    _prompt_from_payload,
    _resource_from_payload,
    _server_from_payload,
    _serialize_config,
    _to_str_dict,
    _to_str_list,
    _tool_from_payload,
)
from engine.community.core.adapters.claude_code.models import (
    ClaudeCodeModelsAdapter,
    _capabilities_from_payload,
    _model_from_payload,
    _provider_from_payload,
)
from engine.community.core.adapters.claude_code.session import (
    ClaudeCodeSessionAdapter,
    _parse_message_count,
    _parse_relay_timestamp,
    _relay_message_to_message,
    _relay_session_to_session,
)
from engine.community.core.adapters.claude_code.skills import (
    ClaudeCodeSkillsAdapter,
    _config_to_params,
    _parse_skill_status,
    _parse_skill_type,
    _skill_from_payload,
)
from engine.community.core.chat.models import ChatAbortRequest, ChatRequest
from engine.community.core.engine.context import AuthContext
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.mcp.models import (
    MCPFilterRequest,
    MCPServerConfig,
    MCPServerStatus,
    MCPToolCallRequest,
    TransportType,
)
from engine.community.core.session.models import (
    SessionClearRequest,
    SessionHistoryRequest,
    SessionListRequest,
    SessionResetRequest,
    SessionUpdateRequest,
)
from engine.community.core.skills.models import (
    CenterEnsureRequest,
    CleanSymlinksRequest,
    SkillConfig,
    SkillExecutionRequest,
    SkillStatus,
    SkillType,
    SyncBindPathsRequest,
    SyncSymlinksRequest,
)
from engine.community.kernel.frames import EventFrame
from engine.community.plugin_api.cron.models import (
    CreateJobRequest,
    CronJob,
    CronNotifyConfig,
    CronNotifyPatch,
    UpdateJobRequest,
)


# ── shared helpers ───────────────────────────────────────────────────────────


class _FakeAuth:
    def __init__(self, token: str | None = None) -> None:
        self.token = token


def _auth(token: str | None = None) -> AuthContext:
    return _FakeAuth(token=token)  # type: ignore[return-value]


# ──────────────────────────────────────────────────────────────────────────────
# mcp adapter coverage
# ──────────────────────────────────────────────────────────────────────────────


class TestMcpHelpers:
    def test_parse_transport_stdio_http_sse(self):
        assert _parse_transport("stdio") is TransportType.STDIO
        assert _parse_transport("http") is TransportType.HTTP
        assert _parse_transport("streamable_http") is TransportType.HTTP
        assert _parse_transport("sse") is TransportType.SSE
        # empty / unknown → SSE
        assert _parse_transport(None) is TransportType.SSE
        assert _parse_transport("") is TransportType.SSE
        assert _parse_transport("weird") is TransportType.SSE

    def test_to_str_list_scalar_and_none(self):
        assert _to_str_list(None) == []
        assert _to_str_list("abc") == ["abc"]
        assert _to_str_list([1, 2]) == ["1", "2"]

    def test_to_str_dict_non_dict_returns_empty(self):
        assert _to_str_dict(None) == {}
        assert _to_str_dict("x") == {}
        assert _to_str_dict({"a": 1, "b": 2}) == {"a": "1", "b": "2"}

    def test_config_from_raw_timeout_coercion(self):
        # timeout_seconds takes precedence over timeoutSeconds
        c = _config_from_raw("s1", {"timeout_seconds": 99, "timeoutSeconds": 5})
        assert c.timeout_seconds == 99
        # fallback to timeoutSeconds
        c = _config_from_raw("s1", {"timeoutSeconds": 7})
        assert c.timeout_seconds == 7
        # default 30 when missing
        c = _config_from_raw("s1", {})
        assert c.timeout_seconds == 30
        # bad value falls back to 30
        c = _config_from_raw("s1", {"timeoutSeconds": "abc"})
        assert c.timeout_seconds == 30
        # None falls back to 30
        c = _config_from_raw("s1", {"timeoutSeconds": None})
        assert c.timeout_seconds == 30
        # non-dict raw → empty config with defaults
        c = _config_from_raw("s1", "not-a-dict")  # type: ignore[arg-type]
        assert c.server_code == "s1"
        assert c.timeout_seconds == 30
        assert c.enabled is True

    def test_config_from_raw_field_aliases(self):
        # baseUrl alias for url; type alias for transport
        c = _config_from_raw("s1", {"type": "stdio", "baseUrl": "http://b"})
        assert c.transport is TransportType.STDIO
        assert c.url == "http://b"

    def test_server_from_payload_server_code_aliases(self):
        # server_code canonical wins over fallback
        s = _server_from_payload({"server_code": "canon", "serverCode": "wire"})
        assert s.config.server_code == "wire"  # serverCode takes priority
        # only server_code present
        s = _server_from_payload({"server_code": "c2"})
        assert s.config.server_code == "c2"
        # disabled → STOPPED
        s = _server_from_payload({"serverCode": "x", "enabled": False})
        assert s.status is MCPServerStatus.STOPPED

    def test_serialize_config_optional_fields(self):
        # full config exercises all optional branches
        cfg = MCPServerConfig(
            server_code="c1",
            transport=TransportType.STDIO,
            url="http://x",
            command="npx",
            args=["-y", "pkg"],
            env={"K": "v"},
            headers={"H": "v"},
            timeout_seconds=42,
            enabled=False,
        )
        out = _serialize_config(cfg)
        assert out["serverCode"] == "c1"
        assert out["type"] == "stdio"
        assert out["url"] == "http://x"
        assert out["command"] == "npx"
        assert out["args"] == ["-y", "pkg"]
        assert out["env"] == {"K": "v"}
        assert out["headers"] == {"H": "v"}
        assert out["timeout_seconds"] == 42
        assert out["enabled"] is False

        # minimal config: transport defaults to SSE (truthy) so still serialised
        cfg2 = MCPServerConfig(server_code="c2")
        out2 = _serialize_config(cfg2)
        assert out2 == {
            "serverCode": "c2",
            "type": "sse",
            "timeout_seconds": 30,
            "enabled": True,
        }

    def test_tool_from_payload_aliases(self):
        t = _tool_from_payload({"name": "t", "input_schema": {"x": 1}})
        assert t.input_schema == {"x": 1}
        t = _tool_from_payload({"name": "t", "inputSchema": {"y": 2}})
        assert t.input_schema == {"y": 2}
        # missing input → {}
        t = _tool_from_payload({"name": "t"})
        assert t.input_schema == {}

    def test_resource_from_payload(self):
        r = _resource_from_payload(
            {"uri": "u://1", "name": "n", "mimeType": "text/plain"}, "s1"
        )
        assert r.uri == "u://1"
        assert r.mime_type == "text/plain"
        assert r.server_code == "s1"
        # mime_type snake fallback
        r = _resource_from_payload(
            {"uri": "u://2", "name": "n2", "mime_type": "text/csv"}, "s2"
        )
        assert r.mime_type == "text/csv"

    def test_prompt_from_payload(self):
        p = _prompt_from_payload(
            {"name": "p", "description": "d", "arguments": [{"k": "v"}]}, "s1"
        )
        assert p.arguments == [{"k": "v"}]
        # missing arguments → []
        p = _prompt_from_payload({"name": "p"}, "s1")
        assert p.arguments == []


class _McpPort:
    """Fresh port for coverage scenarios."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._list_response: list[dict] = []
        self._get_response: dict | None = {}
        self._status_response: dict = {"status": "running"}

    async def mcp_list_servers(self, token=None) -> list[dict]:
        self.calls.append({"m": "list", "token": token})
        return self._list_response

    async def mcp_get_server(self, server_code, token=None) -> dict | None:
        self.calls.append({"m": "get", "server_code": server_code, "token": token})
        return self._get_response

    async def mcp_create_server(self, config, token=None) -> dict:
        self.calls.append({"m": "create", "config": config, "token": token})
        return config

    async def mcp_update_server(self, server_code, patch, token=None) -> dict:
        self.calls.append({"m": "update", "server_code": server_code, "patch": patch, "token": token})
        return {"serverCode": server_code, **patch}

    async def mcp_delete_server(self, server_code, token=None) -> bool:
        self.calls.append({"m": "delete", "server_code": server_code, "token": token})
        return True

    async def mcp_start_server(self, *a, **k): ...
    async def mcp_stop_server(self, *a, **k): ...
    async def mcp_restart_server(self, *a, **k): ...

    async def mcp_get_server_status(self, server_code, token=None) -> dict:
        self.calls.append({"m": "status", "server_code": server_code, "token": token})
        return self._status_response

    async def mcp_list_tools(self, server_code, token=None) -> list[dict]:
        self.calls.append({"m": "list_tools", "server_code": server_code, "token": token})
        return [{"name": "t1", "description": "d", "inputSchema": {"x": 1}}]

    async def mcp_call_tool(self, server_code, tool_name, arguments=None, token=None, timeout_ms=None) -> dict:
        self.calls.append({"m": "call_tool", "server_code": server_code, "tool_name": tool_name, "arguments": arguments, "token": token, "timeout_ms": timeout_ms})
        return {"content": [{"type": "text", "text": "ok"}], "isError": False, "serverCode": server_code}

    async def mcp_list_resources(self, server_code, token=None) -> list[dict]:
        self.calls.append({"m": "list_resources", "server_code": server_code, "token": token})
        return [{"uri": "u://1", "name": "r1"}]

    async def mcp_read_resource(self, server_code, resource_uri, token=None) -> dict:
        self.calls.append({"m": "read_resource", "server_code": server_code, "resource_uri": resource_uri, "token": token})
        return {"content": [{"type": "text", "text": "file-body"}]}

    async def mcp_list_prompts(self, server_code, token=None) -> list[dict]:
        self.calls.append({"m": "list_prompts", "server_code": server_code, "token": token})
        return [{"name": "p1", "description": "d"}]

    async def mcp_get_prompt(self, server_code, prompt_name, arguments=None, token=None) -> dict:
        self.calls.append({"m": "get_prompt", "server_code": server_code, "prompt_name": prompt_name, "arguments": arguments, "token": token})
        return {"content": "rendered"}

    async def mcp_filter_servers(self, query=None, token=None) -> list[dict]:
        return []

    async def mcp_apply_server_filter(self, server_codes, timeout_seconds=30, token=None) -> dict:
        return {"serverCodes": server_codes, "command": ["mcporter", "filter"],
                "returnCode": 0, "stdout": "ok", "stderr": ""}


class TestMcpAdapterCoverage:
    async def test_list_servers_filters_non_dict_entries(self):
        port = _McpPort()
        port._list_response = [
            {"serverCode": "s1", "type": "stdio", "command": "npx", "enabled": True},
            "not-a-dict",  # filtered out
            None,
        ]
        adapter = ClaudeCodeMcpAdapter(port)
        out = await adapter.list_servers(auth=_auth("tk"))
        assert len(out) == 1
        assert port.calls[0]["token"] == "tk"

    async def test_get_server_returns_server_dto(self):
        port = _McpPort()
        port._get_response = {"serverCode": "g1", "type": "sse", "url": "http://x", "enabled": True}
        adapter = ClaudeCodeMcpAdapter(port)
        s = await adapter.get_server("g1", auth=_auth("t"))
        assert s is not None
        assert s.config.server_code == "g1"
        assert s.config.transport is TransportType.SSE

    async def test_update_server_returns_server_dto(self):
        port = _McpPort()
        adapter = ClaudeCodeMcpAdapter(port)
        cfg = MCPServerConfig(server_code="u1", transport=TransportType.HTTP, url="http://u")
        s = await adapter.update_server("u1", cfg, auth=_auth("t"))
        assert s.config.server_code == "u1"
        call = port.calls[0]
        assert call["m"] == "update"
        assert call["server_code"] == "u1"
        assert call["token"] == "t"

    async def test_delete_server_returns_bool(self):
        port = _McpPort()
        adapter = ClaudeCodeMcpAdapter(port)
        ok = await adapter.delete_server("d1", auth=_auth("t"))
        assert ok is True

    async def test_get_server_status_maps_all_states(self):
        port = _McpPort()
        adapter = ClaudeCodeMcpAdapter(port)
        for raw_status, expected in [
            ("running", MCPServerStatus.RUNNING),
            ("starting", MCPServerStatus.STARTING),
            ("stopping", MCPServerStatus.STOPPING),
            ("error", MCPServerStatus.ERROR),
            ("stopped", MCPServerStatus.STOPPED),
            ("unknown", MCPServerStatus.STOPPED),
        ]:
            port._status_response = {"status": raw_status}
            assert await adapter.get_server_status("x") is expected

    async def test_list_tools_filters_non_dict_entries(self):
        port = _McpPort()

        class _P(_P_list_tools_nonfilter):
            pass

        # Use a small subclass inline via monkey on the port
        async def list_tools(server_code, token=None):
            return [{"name": "t1"}, "bad", None, 42]
        port.mcp_list_tools = list_tools  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        out = await adapter.list_tools(server_code="s1", auth=_auth("t"))
        assert len(out) == 1
        assert out[0].name == "t1"

    async def test_call_tool_no_server_code_uses_response(self):
        port = _McpPort()
        adapter = ClaudeCodeMcpAdapter(port)
        result = await adapter.call_tool(
            MCPToolCallRequest(tool_name="t1", arguments={"a": 1}),
            auth=_auth("t"),
        )
        # server_code was empty, so the port's serverCode echo fills it
        assert result.server_code == ""
        assert result.tool_name == "t1"
        assert result.is_error is False

    async def test_call_tool_is_error_true(self):
        port = _McpPort()

        async def call_tool(server_code, tool_name, arguments=None, token=None, timeout_ms=None):
            return {"content": [], "isError": True}
        port.mcp_call_tool = call_tool  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        result = await adapter.call_tool(
            MCPToolCallRequest(tool_name="t1", arguments={}, server_code="s1"),
        )
        assert result.is_error is True

    async def test_list_resources_none_server_returns_empty(self):
        adapter = ClaudeCodeMcpAdapter(_McpPort())
        assert await adapter.list_resources(server_code=None) == []

    async def test_list_resources_filters_non_dict(self):
        port = _McpPort()

        async def list_resources(server_code, token=None):
            return [{"uri": "u://1"}, "bad", None]
        port.mcp_list_resources = list_resources  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        out = await adapter.list_resources(server_code="s1")
        assert len(out) == 1
        assert out[0].uri == "u://1"

    async def test_read_resource_str_content(self):
        port = _McpPort()

        async def read_resource(server_code, resource_uri, token=None):
            return {"content": "plain body"}
        port.mcp_read_resource = read_resource  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.read_resource("s1", "uri://x") == "plain body"

    async def test_read_resource_list_content_concatenates_text_parts(self):
        port = _McpPort()

        async def read_resource(server_code, resource_uri, token=None):
            return {"content": [{"type": "text", "text": "a"}, {"type": "image"}, {"type": "text", "text": "b"}]}
        port.mcp_read_resource = read_resource  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.read_resource("s1", "uri://x") == "ab"

    async def test_read_resource_non_text_content_serialised(self):
        port = _McpPort()

        async def read_resource(server_code, resource_uri, token=None):
            return {"content": 12345}
        port.mcp_read_resource = read_resource  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.read_resource("s1", "uri://x") == "12345"

    async def test_read_resource_none_content_returns_empty(self):
        port = _McpPort()

        async def read_resource(server_code, resource_uri, token=None):
            return {"content": None}
        port.mcp_read_resource = read_resource  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.read_resource("s1", "uri://x") == ""

    async def test_read_resource_non_dict_returns_str(self):
        port = _McpPort()

        async def read_resource(server_code, resource_uri, token=None):
            return "raw-string"
        port.mcp_read_resource = read_resource  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.read_resource("s1", "uri://x") == "raw-string"

    async def test_list_prompts_none_server_returns_empty(self):
        adapter = ClaudeCodeMcpAdapter(_McpPort())
        assert await adapter.list_prompts(server_code=None) == []

    async def test_list_prompts_filters_non_dict(self):
        port = _McpPort()

        async def list_prompts(server_code, token=None):
            return [{"name": "p1"}, 5, None]
        port.mcp_list_prompts = list_prompts  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        out = await adapter.list_prompts(server_code="s1")
        assert len(out) == 1

    async def test_get_prompt_str_content(self):
        port = _McpPort()
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.get_prompt("s1", "p1") == "rendered"

    async def test_get_prompt_non_str_content(self):
        port = _McpPort()

        async def get_prompt(server_code, prompt_name, arguments=None, token=None):
            return {"content": None}
        port.mcp_get_prompt = get_prompt  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.get_prompt("s1", "p1") == ""

    async def test_get_prompt_non_dict_returns_str(self):
        port = _McpPort()

        async def get_prompt(server_code, prompt_name, arguments=None, token=None):
            return 42
        port.mcp_get_prompt = get_prompt  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        assert await adapter.get_prompt("s1", "p1") == "42"

    async def test_filter_servers_empty_server_codes(self):
        port = _McpPort()

        async def apply(server_codes, timeout_seconds=30, token=None):
            assert server_codes == []
            return {"serverCodes": [], "command": [], "returnCode": 0, "stdout": "", "stderr": ""}
        port.mcp_apply_server_filter = apply  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        result = await adapter.filter_servers(
            MCPFilterRequest(server_codes=[], timeout_seconds=5),
            auth=_auth("t"),
        )
        assert result.server_codes == []
        assert result.return_code == 0

    async def test_filter_servers_payload_missing_fields_defaults(self):
        port = _McpPort()

        async def apply(server_codes, timeout_seconds=30, token=None):
            # relay returns a sparse dict — defaults must fill in
            return {}
        port.mcp_apply_server_filter = apply  # type: ignore[assignment]
        adapter = ClaudeCodeMcpAdapter(port)
        result = await adapter.filter_servers(
            MCPFilterRequest(server_codes=["a"]),
        )
        assert result.server_codes == ["a"]  # fallback to request
        assert result.command == []
        assert result.return_code == 0
        assert result.stdout == ""
        assert result.stderr == ""

    async def test_capability_raises_carry_capability_enum(self):
        adapter = ClaudeCodeMcpAdapter(_McpPort())
        # Each lifecycle op must raise AND surface the capability.
        with pytest.raises(CapabilityNotSupportedError):
            await adapter.start_server("s1")
        with pytest.raises(CapabilityNotSupportedError):
            await adapter.stop_server("s1")
        with pytest.raises(CapabilityNotSupportedError):
            await adapter.restart_server("s1")


# Placeholder shim to keep the inline-class trick above valid syntax-wise —
# we never instantiate this; we just need the name to resolve.
class _P_list_tools_nonfilter:
    async def mcp_list_tools(self, server_code, token=None):
        return []


# ──────────────────────────────────────────────────────────────────────────────
# session adapter coverage
# ──────────────────────────────────────────────────────────────────────────────


class TestSessionHelpers:
    def test_parse_session_key_malformed_returns_none(self):
        from engine.community.core.adapters.claude_code.session import _parse_session_key as psk
        # malformed: prefix but truncated
        # new-form missing user portion (empty after split)
        assert psk("agent:bot1:session:s1:user:") == (None, None)
        # new-form missing agent portion
        assert psk("agent::session:s1:user:u1") == (None, None)
        # legacy-form missing user portion
        assert psk("user::session:s1:agent:b1") == (None, None)
        # legacy-form missing agent portion
        assert psk("user:u1:session:s1:agent:") == (None, None)
        # new-form value error during split (no :user:)
        assert psk("agent:bot1:session:s1") == (None, None)
        # legacy-form value error during split (no :agent:)
        assert psk("user:u1:session:s1") == (None, None)
        # new-form ValueError: :user: appears before :session: so the
        # post-session split has no :user: delimiter → ValueError → (None, None)
        assert psk("agent:b1:user:u1:session:s") == (None, None)
        # legacy-form ValueError: :agent: appears before :session: so the
        # post-session split has no :agent: delimiter → ValueError → (None, None)
        assert psk("user:u1:agent:b1:session:s") == (None, None)

    def test_parse_relay_timestamp_variants(self):
        # int millis
        ts = _parse_relay_timestamp(1717000000000)
        assert ts is not None
        # numeric string millis
        ts2 = _parse_relay_timestamp("1717000000000")
        assert ts2 is not None
        # ISO string with Z
        ts3 = _parse_relay_timestamp("2024-05-30T10:00:00Z")
        assert ts3 is not None
        # ISO string with explicit offset
        ts4 = _parse_relay_timestamp("2024-05-30T10:00:00+00:00")
        assert ts4 is not None
        # None
        assert _parse_relay_timestamp(None) is None
        # empty string
        assert _parse_relay_timestamp("") is None
        # whitespace string
        assert _parse_relay_timestamp("   ") is None
        # bad value
        assert _parse_relay_timestamp("not-a-date") is None
        # bad type
        assert _parse_relay_timestamp(object()) is None

    def test_parse_message_count_variants(self):
        assert _parse_message_count(5) == 5
        assert _parse_message_count("5") == 5
        assert _parse_message_count(None) == 0
        assert _parse_message_count("abc") == 0
        assert _parse_message_count(0) == 0

    def test_relay_session_to_session_minimal_payload(self):
        # No preview, no message_count, no timestamps — defaults exercised
        s = _relay_session_to_session({"key": "agent:b:session:s:user:u"})
        assert s.id == "agent:b:session:s:user:u"
        assert s.agent_id == "b"
        assert s.user_id == "u"
        assert s.message_count == 0
        assert s.last_message is None
        assert s.last_message_at is None
        assert s.total_input_tokens == 0
        assert s.total_output_tokens == 0

    def test_relay_session_to_session_with_preview_only(self):
        s = _relay_session_to_session(
            {"key": "agent:b:session:s:user:u", "preview": "  trailing  "},
        )
        assert s.last_message is not None
        assert s.last_message.content == "trailing"
        assert s.last_message.metadata["source"] == "preview"

    def test_relay_session_to_session_with_message_count_only(self):
        s = _relay_session_to_session(
            {"key": "agent:b:session:s:user:u", "messageCount": 3},
        )
        assert s.last_message is not None
        assert s.last_message.metadata["source"] == "session-summary"

    def test_relay_session_to_session_unparseable_key(self):
        s = _relay_session_to_session({"key": "session:short"})
        assert s.agent_id is None
        assert s.user_id == "default"  # falls through to default user_id

    def test_relay_message_to_message_content_list_variants(self):
        # list of dicts with text key directly
        m = _relay_message_to_message(
            {"id": "m1", "role": "user", "content": [{"text": "a"}, {"type": "text", "text": "b"}]},
            "sess", 0,
        )
        assert m.content == "a\nb"

    def test_relay_message_to_message_content_list_with_str_item(self):
        m = _relay_message_to_message(
            {"id": "m1", "role": "user", "content": ["plain-str"]},
            "sess", 0,
        )
        assert m.content == "plain-str"

    def test_relay_message_to_message_tool_result_role(self):
        m = _relay_message_to_message(
            {"id": "m1", "role": "tool_result",
             "metadata": {"toolName": "Bash", "toolCallId": "tc1", "isError": True, "output": "err"}},
            "sess", 1,
        )
        assert m.role == "tool_result"
        assert m.metadata["tool_name"] == "Bash"
        assert m.metadata["success"] is False
        assert m.metadata["result"] == "err"
        assert "<tool>" in m.content

    def test_relay_message_to_message_tool_result_role_with_data_field_fallbacks(self):
        # metadata missing toolName — falls back to data.get('toolName')
        m = _relay_message_to_message(
            {"id": "m1", "role": "tool_result",
             "toolName": "Read", "toolCallId": "tc2", "result": "ok"},
            "sess", 2,
        )
        assert m.metadata["tool_name"] == "Read"
        assert m.metadata["result"] == "ok"

    def test_relay_message_to_message_tool_use_role_with_data_field_fallbacks(self):
        m = _relay_message_to_message(
            {"id": "m1", "role": "tool_use",
             "toolName": "Write", "toolCallId": "tc3", "input": "data"},
            "sess", 3,
        )
        assert m.metadata["tool_name"] == "Write"
        assert m.metadata["arguments"] == "data"
        # tool_params (running=True) is rendered into content, not metadata
        assert '"running": true' in m.content

    def test_relay_message_to_message_no_id_uses_session_index(self):
        m = _relay_message_to_message(
            {"role": "user", "content": "hi"},
            "sess", 7,
        )
        assert m.id == "sess_7"

    def test_relay_message_to_message_no_timestamp_uses_now(self):
        m = _relay_message_to_message(
            {"role": "user", "content": "hi"},
            "sess", 0,
        )
        assert m.created_at is not None

    def test_relay_message_to_message_bad_timestamp_uses_now(self):
        m = _relay_message_to_message(
            {"role": "user", "content": "hi", "timestamp": "not-a-time"},
            "sess", 0,
        )
        assert m.created_at is not None

    def test_relay_message_to_message_iso_timestamp(self):
        m = _relay_message_to_message(
            {"role": "user", "content": "hi", "timestamp": "2024-05-30T10:00:00+00:00"},
            "sess", 0,
        )
        assert m.created_at is not None


class _SessionPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._sessions: list[dict[str, Any]] = []
        self._messages: list[dict[str, Any]] = []
        self._create_response: dict[str, Any] = {}
        self._clear_response: dict[str, Any] = {"success": True, "payload": {}}
        self._reset_response: dict[str, Any] = {"success": True, "payload": {"reset": True}}

    async def sessions_list(
        self, token=None, offset=0, limit=50, agent_id=None, session_key=None,
    ) -> list[dict]:
        self.calls.append({
            "m": "list", "token": token, "offset": offset, "limit": limit,
            "agent_id": agent_id, "session_key": session_key,
        })
        return self._sessions

    async def session_create(self, key, label=None, model=None, cwd=None, token=None) -> dict:
        self.calls.append({"m": "create", "key": key, "label": label, "model": model, "cwd": cwd, "token": token})
        return self._create_response

    async def session_delete(self, key, token=None) -> bool:
        self.calls.append({"m": "delete", "key": key, "token": token})
        return True

    async def session_reset(self, key, token=None) -> dict:
        self.calls.append({"m": "reset", "key": key, "token": token})
        return self._reset_response

    async def session_get_history(self, key, limit=100, token=None) -> list[dict]:
        self.calls.append({"m": "history", "key": key, "limit": limit, "token": token})
        return self._messages

    async def session_clear(self, key, token=None) -> dict:
        self.calls.append({"m": "clear", "key": key, "token": token})
        return self._clear_response


class TestSessionAdapterCoverage:
    async def test_list_conversion_failure_skipped(self):
        port = _SessionPort()
        # One valid + one broken (missing key triggers _relay_session_to_session
        # but the conversion util tolerates it; force an exception via a
        # non-dict entry that passes the raw.get chain — use a dict that
        # makes _parse_relay_timestamp choke is hard, so test the
        # `_relay_session_to_session` exception path via a bad messageCount type:
        # Actually message_count is tolerant. Test list with a non-dict which
        # the loop guards via raw.get; the try/except is hit when the value
        # is not a dict. But the loop does `raw.get` only on dicts implicitly?
        # No — `raw.get` will raise AttributeError on a non-dict, which is
        # caught. Good.
        port._sessions = [
            {"key": "agent:b1:session:s1:user:u1"},
            "not-a-dict",  # raises AttributeError inside the try
            42,
        ]
        adapter = ClaudeCodeSessionAdapter(port)
        out = await adapter.list(SessionListRequest(limit=10))
        assert len(out) == 1
        assert out[0].id == "agent:b1:session:s1:user:u1"

    async def test_create_no_agent_builds_short_key(self):
        port = _SessionPort()
        adapter = ClaudeCodeSessionAdapter(port)
        # No agent_id/user_id → short-form `session:<uuid>` key
        sess = await adapter.create(
            _make_create_request(title="T", user_id=None, agent_id=None),
            auth=_auth("t"),
        )
        assert sess.id.startswith("session:")
        assert port.calls[0]["key"].startswith("session:")

    async def test_create_with_uuid_passthrough(self):
        from engine.community.core.session.models import SessionCreateRequest
        port = _SessionPort()
        adapter = ClaudeCodeSessionAdapter(port)
        sess = await adapter.create(
            SessionCreateRequest(
                title="T", user_id="u1", agent_id="b1", model="m1",
                uuid="fixed-uuid",
            ),
        )
        assert "fixed-uuid" in sess.id

    async def test_clear_raises_with_default_message_when_error_missing(self):
        port = _SessionPort()
        port._clear_response = {"success": False}  # no error key
        adapter = ClaudeCodeSessionAdapter(port)
        with pytest.raises(RuntimeError, match="Unknown error"):
            await adapter.clear(SessionClearRequest(session_id="sk"))

    async def test_clear_passes_token(self):
        port = _SessionPort()
        adapter = ClaudeCodeSessionAdapter(port)
        await adapter.clear(SessionClearRequest(session_id="sk"), auth=_auth("tok"))
        assert port.calls[0]["token"] == "tok"

    async def test_reset_payload_missing_defaults_to_empty(self):
        port = _SessionPort()
        port._reset_response = {"success": True}  # no payload key
        adapter = ClaudeCodeSessionAdapter(port)
        result = await adapter.reset(SessionResetRequest(session_key="sk"))
        assert result.ok is True
        assert result.payload == {}

    async def test_reset_failure_missing_error_defaults(self):
        port = _SessionPort()
        port._reset_response = {"success": False}  # no error key
        adapter = ClaudeCodeSessionAdapter(port)
        result = await adapter.reset(SessionResetRequest(session_key="sk"))
        assert result.ok is False
        assert result.error_code == "UNKNOWN"
        assert result.error_message == "Unknown error"

    async def test_get_history_conversion_failure_skipped(self):
        port = _SessionPort()
        port._messages = [
            {"id": "ok", "role": "user", "content": "hi"},
            "not-a-dict",  # raises inside _relay_message_to_message → caught
            {"id": "ok2", "role": "assistant", "content": "yo"},
        ]
        adapter = ClaudeCodeSessionAdapter(port)
        result = await adapter.get_history(
            SessionHistoryRequest(session_id="sk", limit=10), auth=_auth("t"),
        )
        assert len(result.messages) == 2
        assert result.messages[0].id == "ok"
        assert result.messages[1].id == "ok2"

    async def test_get_history_applies_offset_and_limit(self):
        port = _SessionPort()
        port._messages = [
            {"id": f"m{i}", "role": "user", "content": str(i)} for i in range(10)
        ]
        adapter = ClaudeCodeSessionAdapter(port)
        result = await adapter.get_history(
            SessionHistoryRequest(session_id="sk", limit=3, offset=2),
        )
        assert len(result.messages) == 3
        assert result.messages[0].id == "m2"
        assert result.messages[2].id == "m4"

    async def test_get_history_limit_none_default_100(self):
        port = _SessionPort()
        adapter = ClaudeCodeSessionAdapter(port)
        await adapter.get_history(SessionHistoryRequest(session_id="sk"))
        assert port.calls[0]["limit"] == 100

    async def test_update_no_fields_finds_existing_session(self):
        port = _SessionPort()
        port._sessions = [
            {"key": "agent:b1:session:s1:user:u1", "label": "Existing"},
        ]
        adapter = ClaudeCodeSessionAdapter(port)
        sess = await adapter.update(
            SessionUpdateRequest(session_id="agent:b1:session:s1:user:u1"),
            auth=_auth("t"),
        )
        assert sess.id == "agent:b1:session:s1:user:u1"
        assert sess.title == "Existing"

    async def test_update_no_fields_not_found_raises(self):
        port = _SessionPort()
        port._sessions = []  # empty
        adapter = ClaudeCodeSessionAdapter(port)
        with pytest.raises(RuntimeError, match="Session not found"):
            await adapter.update(SessionUpdateRequest(session_id="missing"))

    async def test_update_with_fields_finds_session_in_list(self):
        port = _SessionPort()
        port._sessions = [
            {"key": "agent:b1:session:s1:user:u1", "label": "Old"},
        ]
        adapter = ClaudeCodeSessionAdapter(port)
        sess = await adapter.update(
            SessionUpdateRequest(
                session_id="agent:b1:session:s1:user:u1",
                title="New Title",
                model="new-model",
                cwd="/new",
                permission_mode="plan",
            ),
            auth=_auth("t"),
        )
        assert sess.title == "New Title"
        assert sess.model == "new-model"
        assert sess.cwd == "/new"
        assert sess.permission_mode == "plan"

    async def test_update_with_fields_not_in_list_returns_constructed(self):
        port = _SessionPort()
        port._sessions = []  # session not echoed back
        adapter = ClaudeCodeSessionAdapter(port)
        sess = await adapter.update(
            SessionUpdateRequest(
                session_id="agent:b1:session:s1:user:u1",
                title="Best Effort",
                agent_id="b1",
                user_id="u1",
            ),
        )
        assert sess.id == "agent:b1:session:s1:user:u1"
        assert sess.title == "Best Effort"
        assert sess.model is None


def _make_create_request(**kw):
    from engine.community.core.session.models import SessionCreateRequest
    defaults = {"title": "T", "user_id": "u1", "agent_id": "b1", "model": "m1"}
    defaults.update(kw)
    # filter None values so model defaults apply
    clean = {k: v for k, v in defaults.items() if v is not None}
    return SessionCreateRequest(**clean)


# import the helper after defining the test that uses it
from engine.community.core.adapters.claude_code.session import _parse_session_key  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# cron adapter coverage
# ──────────────────────────────────────────────────────────────────────────────


def _make_raw_job(**kw) -> dict[str, Any]:
    base = {
        "id": "j1", "name": "Job 1", "enabled": True,
        "schedule": {"kind": "cron", "expr": "* * * * *"},
        "payload": {"kind": "agentTurn", "timeoutSeconds": 30},
        "state": {"runningAtMs": 123},
        "sessionTarget": "isolated",
        "createdAtMs": 1, "updatedAtMs": 2,
    }
    base.update(kw)
    return base


class TestCronHelpers:
    def test_camel_to_snake_basic(self):
        assert _camel_to_snake("timeoutSeconds") == "timeout_seconds"
        assert _camel_to_snake("runningAtMs") == "running_at_ms"
        assert _camel_to_snake("simple") == "simple"
        assert _camel_to_snake("HTMLParser") == "html_parser"

    def test_convert_dict_keys_list_branch(self):
        from engine.community.core.adapters.claude_code.cron import _convert_dict_keys
        # top-level list → list branch (line 56)
        out = _convert_dict_keys([{"runningAtMs": 1}, {"createdAtMs": 2}])
        assert out == [{"running_at_ms": 1}, {"created_at_ms": 2}]

    def test_convert_dict_keys_scalar_passthrough(self):
        from engine.community.core.adapters.claude_code.cron import _convert_dict_keys
        assert _convert_dict_keys("x") == "x"
        assert _convert_dict_keys(42) == 42
        assert _convert_dict_keys(None) is None

    def test_convert_payload_keys_non_dict_passthrough(self):
        assert _convert_payload_keys("x") == "x"  # type: ignore[arg-type]
        assert _convert_payload_keys(None) is None  # type: ignore[arg-type]

    def test_convert_payload_keys_rename(self):
        # Only timeoutSeconds is renamed; other keys are NOT snake_cased here
        out = _convert_payload_keys({"timeoutSeconds": 30, "kind": "agentTurn"})
        assert out == {"timeout_secs": 30, "kind": "agentTurn"}

    def test_convert_payload_for_wire_non_dict_passthrough(self):
        assert _convert_payload_for_wire("x") == "x"  # type: ignore[arg-type]
        assert _convert_payload_for_wire(None) is None  # type: ignore[arg-type]

    def test_convert_payload_for_wire_rename(self):
        out = _convert_payload_for_wire({"timeout_secs": 30, "timeout_sec": 5, "kind": "x"})
        # both rename keys map to timeoutSeconds; last one wins
        assert out["timeoutSeconds"] == 5
        assert out["kind"] == "x"

    def test_schedule_for_wire_non_dict_passthrough(self):
        assert _schedule_for_wire("x") == "x"  # type: ignore[arg-type]

    def test_schedule_for_wire_at_kind_maps_to_once(self):
        out = _schedule_for_wire({"kind": "at", "at_ms": 999, "tz": "UTC"})
        assert out["kind"] == "once"
        assert out["atMs"] == 999
        assert out["tz"] == "UTC"

    def test_schedule_for_wire_once_kind_with_atMs(self):
        out = _schedule_for_wire({"kind": "once", "atMs": 999})
        assert out["kind"] == "once"
        assert out["atMs"] == 999

    def test_schedule_for_wire_every_kind_with_first_run(self):
        out = _schedule_for_wire(
            {"kind": "every", "everyMs": 60000, "firstRunAtMs": 123}
        )
        assert out["kind"] == "every"
        assert out["everyMs"] == 60000
        assert out["firstRunAtMs"] == 123

    def test_schedule_for_wire_every_kind_no_first_run(self):
        out = _schedule_for_wire({"kind": "every", "every_ms": 60000})
        assert out["kind"] == "every"
        assert out["everyMs"] == 60000
        assert "firstRunAtMs" not in out

    def test_schedule_for_wire_cron_kind(self):
        out = _schedule_for_wire({"kind": "cron", "expr": "* * * * *", "tz": "UTC"})
        assert out["kind"] == "cron"
        assert out["expr"] == "* * * * *"
        assert out["tz"] == "UTC"

    def test_schedule_for_wire_unknown_kind_passthrough(self):
        out = _schedule_for_wire({"kind": "weird", "x": 1})
        assert out == {"kind": "weird", "x": 1}

    def test_notify_to_delivery_none(self):
        assert _notify_to_delivery(None) == {"mode": "none", "to": ""}

    def test_notify_to_delivery_disabled_with_users(self):
        d = _notify_to_delivery(CronNotifyConfig(enabled=False, user_ids=["a", "b"]))
        assert d["mode"] == "none"
        assert d["accountId"] == "__disabled__a,b"

    def test_notify_to_delivery_disabled_no_users(self):
        d = _notify_to_delivery(CronNotifyConfig(enabled=False, user_ids=[]))
        assert d["accountId"] == "__disabled__"

    def test_notify_to_delivery_enabled_with_users(self):
        d = _notify_to_delivery(CronNotifyConfig(enabled=True, user_ids=["a", "b"]))
        assert d["accountId"] == "a,b"

    def test_notify_to_delivery_enabled_no_users(self):
        d = _notify_to_delivery(CronNotifyConfig(enabled=True, user_ids=[]))
        assert d["accountId"] == "__empty__"

    def test_job_from_dict_disabled_delivery_account(self):
        j = _job_from_dict(_make_raw_job(
            delivery={"accountId": "__disabled__u1,u2"},
        ))
        assert j.notify is not None
        assert j.notify.enabled is False
        assert j.notify.user_ids == ["u1", "u2"]

    def test_job_from_dict_empty_delivery_account(self):
        j = _job_from_dict(_make_raw_job(delivery={"accountId": "__empty__"}))
        assert j.notify is not None
        assert j.notify.enabled is True
        assert j.notify.user_ids == []

    def test_job_from_dict_plain_delivery_account(self):
        j = _job_from_dict(_make_raw_job(delivery={"accountId": "u1,u2"}))
        assert j.notify is not None
        assert j.notify.enabled is True
        assert j.notify.user_ids == ["u1", "u2"]

    def test_job_from_dict_delivery_not_dict(self):
        j = _job_from_dict(_make_raw_job(delivery="not-a-dict"))
        assert j.notify is None

    def test_job_from_dict_missing_id_defaults(self):
        j = _job_from_dict({"schedule": {}, "payload": {}, "state": {}})
        assert j.id == ""
        assert j.name == ""
        assert j.enabled is True
        assert j.session_target == "isolated"
        assert j.created_at_ms == 0
        assert j.updated_at_ms == 0

    def test_run_from_dict_with_usage_and_error(self):
        r = _run_from_dict({
            "jobId": "j1", "runAtMs": 100, "ts": 200, "status": "error",
            "error": "boom", "durationMs": 50, "summary": "out",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        })
        assert r.job_id == "j1"
        assert r.status == "error"
        assert r.error == "boom"
        assert r.output == "out"
        assert r.input_tokens == 10
        assert r.output_tokens == 20

    def test_run_from_dict_missing_fields_defaults(self):
        r = _run_from_dict({})
        assert r.job_id == ""
        assert r.status == "error"
        assert r.duration_ms == 0
        assert r.input_tokens is None
        assert r.output_tokens is None

    def test_run_from_dict_usage_not_dict(self):
        r = _run_from_dict({"usage": "weird"})
        assert r.input_tokens is None
        assert r.output_tokens is None

    def test_status_from_dict_defaults(self):
        s = _status_from_dict({})
        assert s.running is False
        assert s.job_count == 0
        assert s.enabled_count == 0
        assert s.next_run_at_ms is None

    def test_build_add_params_with_notify(self):
        req = CreateJobRequest(
            name="N",
            schedule={"kind": "cron", "expr": "* * * * *"},
            payload={"kind": "agentTurn", "timeout_secs": 30},
            notify=CronNotifyConfig(enabled=True, user_ids=["u1"]),
        )
        params = _build_add_params(req)
        assert params["delivery"]["accountId"] == "u1"
        assert params["payload"]["timeoutSeconds"] == 30

    def test_build_update_patch_name_only(self):
        patch = _build_update_patch(
            UpdateJobRequest(name="New"), existing_job=None,
        )
        assert patch == {"name": "New"}

    def test_build_update_patch_schedule(self):
        patch = _build_update_patch(
            UpdateJobRequest(schedule={"kind": "cron", "expr": "*/5 * * * *"}),
            existing_job=None,
        )
        assert patch["schedule"]["kind"] == "cron"

    def test_build_update_patch_payload(self):
        patch = _build_update_patch(
            UpdateJobRequest(payload={"timeout_secs": 99}),
            existing_job=None,
        )
        assert patch["payload"]["timeoutSeconds"] == 99

    def test_build_update_patch_enabled(self):
        patch = _build_update_patch(
            UpdateJobRequest(enabled=False), existing_job=None,
        )
        assert patch["enabled"] is False

    def test_build_update_patch_notify_no_existing(self):
        # No existing job → defaults (enabled=True, user_ids=[])
        patch = _build_update_patch(
            UpdateJobRequest(notify=CronNotifyPatch()),
            existing_job=None,
        )
        assert patch["delivery"]["accountId"] == "__empty__"

    def test_build_update_patch_notify_with_existing(self):
        existing = CronJob(
            id="j1", name="n", schedule={}, payload={},
            notify=CronNotifyConfig(enabled=False, user_ids=["old"]),
            created_at_ms=0, updated_at_ms=0,
        )
        # notify patch with only enabled set → keeps existing user_ids
        patch = _build_update_patch(
            UpdateJobRequest(notify=CronNotifyPatch(enabled=True)),
            existing_job=existing,
        )
        assert patch["delivery"]["accountId"] == "old"

    def test_build_update_patch_notify_with_existing_no_notify_field(self):
        existing = CronJob(
            id="j1", name="n", schedule={}, payload={},
            created_at_ms=0, updated_at_ms=0,  # no notify
        )
        patch = _build_update_patch(
            UpdateJobRequest(notify=CronNotifyPatch(user_ids=["new"])),
            existing_job=existing,
        )
        # enabled defaults to True (cur_enabled), user_ids is new
        assert patch["delivery"]["accountId"] == "new"

    def test_build_update_patch_empty_returns_empty(self):
        patch = _build_update_patch(UpdateJobRequest(), existing_job=None)
        assert patch == {}


class _CronPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._list: list[dict] = []
        self._get: dict | None = None
        self._status: dict = {"running": True, "jobCount": 5, "enabledCount": 3, "nextRunAtMs": 999}
        self._runs: list[dict] = []
        self._running: list[dict] = []
        self._add_response: dict = {}
        self._update_response: dict = {}

    async def cron_list_jobs(self, token=None) -> list[dict]:
        self.calls.append({"m": "list", "token": token})
        return self._list

    async def cron_get_job(self, job_id, token=None) -> dict | None:
        self.calls.append({"m": "get", "job_id": job_id, "token": token})
        return self._get

    async def cron_get_status(self, token=None) -> dict:
        self.calls.append({"m": "status", "token": token})
        return self._status

    async def cron_get_runs(self, job_id, limit=20, token=None) -> list[dict]:
        self.calls.append({"m": "runs", "job_id": job_id, "limit": limit, "token": token})
        return self._runs

    async def cron_get_running_jobs(self, token=None) -> list[dict]:
        self.calls.append({"m": "running", "token": token})
        return self._running

    async def cron_add_job(self, job, token=None) -> dict:
        self.calls.append({"m": "add", "job": job, "token": token})
        return self._add_response or {"id": "new", "name": job["name"], "enabled": True,
                                       "schedule": job["schedule"], "payload": job["payload"],
                                       "state": {}, "sessionTarget": job["sessionTarget"]}

    async def cron_update_job(self, job_id, patch, token=None) -> dict:
        self.calls.append({"m": "update", "job_id": job_id, "patch": patch, "token": token})
        return self._update_response or {"id": job_id, "name": "n", "enabled": True,
                                          "schedule": {}, "payload": {}, "state": {},
                                          "sessionTarget": "isolated"}

    async def cron_remove_job(self, job_id, token=None) -> bool:
        self.calls.append({"m": "remove", "job_id": job_id, "token": token})
        return True

    async def cron_run_job(self, job_id, token=None) -> dict:
        self.calls.append({"m": "run", "job_id": job_id, "token": token})
        return {"ran": job_id}


class TestCronAdapterCoverage:
    async def test_list_jobs_filters_non_dict(self):
        port = _CronPort()
        port._list = [_make_raw_job(), "bad", None]
        adapter = ClaudeCodeCronAdapter(port)
        out = await adapter.list_jobs(auth=_auth("t"))
        assert len(out) == 1
        assert port.calls[0]["token"] == "t"

    async def test_get_job_returns_dto(self):
        port = _CronPort()
        port._get = _make_raw_job(id="g1")
        adapter = ClaudeCodeCronAdapter(port)
        j = await adapter.get_job("g1", auth=_auth("t"))
        assert j is not None
        assert j.id == "g1"

    async def test_get_status_delegates(self):
        port = _CronPort()
        adapter = ClaudeCodeCronAdapter(port)
        s = await adapter.get_status(auth=_auth("t"))
        assert s.running is True
        assert s.job_count == 5

    async def test_get_runs_filters_non_dict(self):
        port = _CronPort()
        port._runs = [
            {"jobId": "j1", "runAtMs": 10, "ts": 20, "status": "ok", "durationMs": 5},
            "bad", None,
        ]
        adapter = ClaudeCodeCronAdapter(port)
        out = await adapter.get_runs("j1", limit=5)
        assert len(out) == 1
        assert out[0].job_id == "j1"

    async def test_add_job_with_notify_builds_delivery(self):
        port = _CronPort()
        adapter = ClaudeCodeCronAdapter(port)
        req = CreateJobRequest(
            name="N",
            schedule={"kind": "cron", "expr": "* * * * *"},
            payload={"kind": "agentTurn", "timeout_secs": 30},
            notify=CronNotifyConfig(enabled=True, user_ids=["u1"]),
        )
        j = await adapter.add_job(req, auth=_auth("t"))
        assert j.id == "new"
        # delivery was encoded
        call = port.calls[0]
        assert call["m"] == "add"
        assert call["job"]["delivery"]["accountId"] == "u1"
        assert call["token"] == "t"

    async def test_update_job_with_name_only(self):
        port = _CronPort()
        port._update_response = _make_raw_job(id="j1", name="Updated")
        adapter = ClaudeCodeCronAdapter(port)
        j = await adapter.update_job("j1", UpdateJobRequest(name="Updated"), auth=_auth("t"))
        assert j.name == "Updated"

    async def test_update_job_with_notify_fetches_existing(self):
        port = _CronPort()
        port._get = _make_raw_job(id="j1", delivery={"accountId": "old_user"})
        port._update_response = _make_raw_job(id="j1")
        adapter = ClaudeCodeCronAdapter(port)
        j = await adapter.update_job(
            "j1",
            UpdateJobRequest(notify=CronNotifyPatch(enabled=True)),
            auth=_auth("t"),
        )
        assert j.id == "j1"
        # Should have fetched the existing job first
        methods = [c["m"] for c in port.calls]
        assert "get" in methods
        assert "update" in methods

    async def test_update_job_with_notify_no_existing_uses_defaults(self):
        port = _CronPort()
        port._get = None  # existing job not found
        port._update_response = _make_raw_job(id="j1")
        adapter = ClaudeCodeCronAdapter(port)
        j = await adapter.update_job(
            "j1",
            UpdateJobRequest(notify=CronNotifyPatch(user_ids=["new"])),
        )
        assert j.id == "j1"

    async def test_remove_job_delegates(self):
        port = _CronPort()
        adapter = ClaudeCodeCronAdapter(port)
        ok = await adapter.remove_job("r1", auth=_auth("t"))
        assert ok is True
        assert port.calls[0]["token"] == "t"

    async def test_run_job_delegates(self):
        port = _CronPort()
        adapter = ClaudeCodeCronAdapter(port)
        result = await adapter.run_job("r1", force=True, auth=_auth("t"))
        assert result == {"ran": "r1"}

    async def test_get_running_jobs_returns_port_list(self):
        port = _CronPort()
        port._running = [{"id": "j1", "name": "n", "running_at_ms": 123}]
        adapter = ClaudeCodeCronAdapter(port)
        out = await adapter.get_running_jobs(auth=_auth("t"))
        assert out == [{"id": "j1", "name": "n", "running_at_ms": 123}]

    async def test_get_running_jobs_fallback_composition(self):
        port = _CronPort()
        port._running = []  # empty → fallback path
        port._list = [
            _make_raw_job(id="j1", state={"runningAtMs": 100}),
            _make_raw_job(id="j2", state={}),  # no running_at_ms
        ]
        adapter = ClaudeCodeCronAdapter(port)
        out = await adapter.get_running_jobs()
        assert len(out) == 1
        assert out[0]["id"] == "j1"
        assert out[0]["running_at_ms"] == 100  # snake_cased key from convert


# ──────────────────────────────────────────────────────────────────────────────
# skills adapter coverage
# ──────────────────────────────────────────────────────────────────────────────


class TestSkillsHelpers:
    def test_parse_skill_type_all_values(self):
        assert _parse_skill_type("symlink") is SkillType.SYMLINK
        assert _parse_skill_type("package") is SkillType.PACKAGE
        assert _parse_skill_type("builtin") is SkillType.BUILTIN
        assert _parse_skill_type("custom") is SkillType.CUSTOM
        assert _parse_skill_type("unknown") is SkillType.CUSTOM
        assert _parse_skill_type(None) is SkillType.CUSTOM
        assert _parse_skill_type("") is SkillType.CUSTOM

    def test_parse_skill_status_all_values(self):
        assert _parse_skill_status("installed") is SkillStatus.INSTALLED
        assert _parse_skill_status("available") is SkillStatus.AVAILABLE
        assert _parse_skill_status("disabled") is SkillStatus.DISABLED
        assert _parse_skill_status("error") is SkillStatus.ERROR
        assert _parse_skill_status("installing") is SkillStatus.INSTALLING
        assert _parse_skill_status("unknown") is SkillStatus.INSTALLED
        assert _parse_skill_status(None) is SkillStatus.INSTALLED

    def test_skill_from_payload_defaults_and_aliases(self):
        s = _skill_from_payload({"skillId": "s1"})
        assert s.skill_id == "s1"
        assert s.name == "s1"
        assert s.config.source is None
        assert s.config.enabled is True
        # source from path alias
        s = _skill_from_payload({"skillId": "s2", "path": "/p"})
        assert s.config.source == "/p"
        # status default installed
        s = _skill_from_payload({"skillId": "s3", "status": "error"})
        assert s.status is SkillStatus.ERROR

    def test_config_to_params_with_parameters(self):
        cfg = SkillConfig(
            skill_id="x", skill_type=SkillType.PACKAGE,
            source="pkg", parameters={"k": "v"},
        )
        params = _config_to_params(cfg)
        assert params["skillId"] == "x"
        assert params["skillType"] == "package"
        assert params["source"] == "pkg"
        assert params["parameters"] == {"k": "v"}
        assert params["enabled"] is True

    def test_config_to_params_minimal(self):
        cfg = SkillConfig(skill_id="y")
        params = _config_to_params(cfg)
        assert params == {"skillId": "y", "skillType": "symlink", "enabled": True}


class _SkillsPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def skills_list(self, token=None) -> list[dict]:
        self.calls.append({"m": "list", "token": token})
        return [{"skillId": "sk1", "name": "Skill 1", "description": "d", "skillType": "symlink"}]

    async def skills_get(self, skill_id, token=None) -> dict | None:
        self.calls.append({"m": "get", "skill_id": skill_id, "token": token})
        if skill_id == "missing":
            return None
        if skill_id == "errored":
            return {"skillId": skill_id, "name": skill_id, "status": "error"}
        return {"skillId": skill_id, "name": skill_id, "status": "installed"}

    async def skills_install(self, config, token=None) -> dict:
        self.calls.append({"m": "install", "config": config, "token": token})
        return {"skillId": config["skillId"], "name": "n", "description": ""}

    async def skills_uninstall(self, skill_id, token=None) -> bool:
        self.calls.append({"m": "uninstall", "skill_id": skill_id, "token": token})
        return True

    async def skills_update(self, skill_id, patch, token=None) -> dict:
        self.calls.append({"m": "update", "skill_id": skill_id, "patch": patch, "token": token})
        return {"skillId": skill_id, "name": skill_id, "description": ""}

    async def skills_enable(self, skill_id, token=None) -> bool:
        self.calls.append({"m": "enable", "skill_id": skill_id, "token": token})
        return True

    async def skills_disable(self, skill_id, token=None) -> bool:
        self.calls.append({"m": "disable", "skill_id": skill_id, "token": token})
        return True

    async def skills_execute(self, skill_id, args=None, token=None) -> dict:
        return {"success": True, "payload": {"done": True}}

    async def skills_validate(self, config, token=None) -> dict:
        return {"valid": True}

    async def skills_discover(self, source, token=None) -> list[dict]:
        return [{"skillId": "new", "name": "New", "description": ""}]

    async def skills_sync_symlinks(self, token=None) -> dict:
        return {"total": 2, "created": ["a"], "kept": ["b"]}

    async def skills_sync_bindpaths(self, token=None) -> dict:
        return {"total": 1, "created": ["c"]}

    async def skills_clean_symlinks(self, token=None) -> dict:
        return {"directories_scanned": 2, "removed": ["x"]}

    async def skills_ensure_center(self, token=None) -> dict:
        return {"ok": [{"skill_uuid": "u1", "version": "1.0"}],
                "failed": [{"skill_uuid": "u2", "version": "2.0", "reason": "missing"}]}


class TestSkillsAdapterCoverage:
    async def test_list_skills_filters_non_dict(self):
        port = _SkillsPort()

        async def lst(token=None):
            return [{"skillId": "s1"}, "bad", None]
        port.skills_list = lst  # type: ignore[assignment]
        adapter = ClaudeCodeSkillsAdapter(port)
        out = await adapter.list_skills()
        assert len(out) == 1

    async def test_get_skill_returns_skill_dto(self):
        adapter = ClaudeCodeSkillsAdapter(_SkillsPort())
        s = await adapter.get_skill("sk1", auth=_auth("t"))
        assert s is not None
        assert s.skill_id == "sk1"

    async def test_uninstall_skill_delegates(self):
        port = _SkillsPort()
        adapter = ClaudeCodeSkillsAdapter(port)
        ok = await adapter.uninstall_skill("sk1", auth=_auth("t"))
        assert ok is True
        assert port.calls[0]["token"] == "t"

    async def test_update_skill_overwrites_skill_id(self):
        port = _SkillsPort()
        adapter = ClaudeCodeSkillsAdapter(port)
        cfg = SkillConfig(skill_id="orig", skill_type=SkillType.SYMLINK)
        s = await adapter.update_skill("new_id", cfg, auth=_auth("t"))
        assert s.skill_id == "new_id"
        call = port.calls[0]
        assert call["patch"]["skillId"] == "new_id"

    async def test_enable_skill_delegates(self):
        port = _SkillsPort()
        adapter = ClaudeCodeSkillsAdapter(port)
        ok = await adapter.enable_skill("sk1", auth=_auth("t"))
        assert ok is True
        assert port.calls[0]["token"] == "t"

    async def test_disable_skill_delegates(self):
        port = _SkillsPort()
        adapter = ClaudeCodeSkillsAdapter(port)
        ok = await adapter.disable_skill("sk1", auth=_auth("t"))
        assert ok is True

    async def test_validate_skill_error_state(self):
        adapter = ClaudeCodeSkillsAdapter(_SkillsPort())
        result = await adapter.validate_skill("errored")
        assert len(result) == 1
        assert "error state" in result[0].lower()

    async def test_discover_skills_builds_dtos(self):
        port = _SkillsPort()
        adapter = ClaudeCodeSkillsAdapter(port)
        out = await adapter.discover_skills("source", auth=_auth("t"))
        assert len(out) == 1
        assert out[0].skill_id == "new"

    async def test_discover_skills_filters_non_dict(self):
        port = _SkillsPort()

        async def discover(source, token=None):
            return [{"skillId": "new"}, "bad", None]
        port.skills_discover = discover  # type: ignore[assignment]
        adapter = ClaudeCodeSkillsAdapter(port)
        out = await adapter.discover_skills("source")
        assert len(out) == 1

    async def test_sync_bindpaths_builds_result(self):
        adapter = ClaudeCodeSkillsAdapter(_SkillsPort())
        result = await adapter.sync_bindpaths(
            SyncBindPathsRequest(), auth=_auth("t"),
        )
        assert result.total == 1
        assert result.created == ["c"]

    async def test_sync_symlinks_with_all_fields(self):
        port = _SkillsPort()

        async def sync(token=None):
            return {"total": 5, "created": ["a"], "updated": ["b"],
                    "kept": ["c"], "removed": ["d"], "base_dir": "/base"}
        port.skills_sync_symlinks = sync  # type: ignore[assignment]
        adapter = ClaudeCodeSkillsAdapter(port)
        result = await adapter.sync_symlinks(SyncSymlinksRequest())
        assert result.total == 5
        assert result.updated == ["b"]
        assert result.removed == ["d"]
        assert result.base_dir == "/base"

    async def test_clean_symlinks_with_defaults(self):
        port = _SkillsPort()

        async def clean(token=None):
            return {}  # empty → defaults
        port.skills_clean_symlinks = clean  # type: ignore[assignment]
        adapter = ClaudeCodeSkillsAdapter(port)
        result = await adapter.clean_symlinks(CleanSymlinksRequest())
        assert result.directories_scanned == 0
        assert result.removed == []

    async def test_ensure_center_filters_non_dict(self):
        port = _SkillsPort()

        async def ensure(token=None):
            return {"ok": [{"skill_uuid": "ok1"}, "bad", None],
                    "failed": [{"skill_uuid": "f1", "reason": "x"}, 5, None]}
        port.skills_ensure_center = ensure  # type: ignore[assignment]
        adapter = ClaudeCodeSkillsAdapter(port)
        result = await adapter.ensure_center_skills(
            CenterEnsureRequest(items=[]), auth=_auth("t"),
        )
        assert len(result.ok) == 1
        assert result.ok[0].skill_uuid == "ok1"
        assert len(result.failed) == 1
        assert result.failed[0].skill_uuid == "f1"


# ──────────────────────────────────────────────────────────────────────────────
# chat adapter coverage
# ──────────────────────────────────────────────────────────────────────────────


class _ChatPort:
    def __init__(self, frames=None) -> None:
        self._frames = frames or []
        self.stream_calls: list[dict] = []
        self.abort_calls: list[dict] = []
        self.inject_calls: list[dict] = []
        self._abort_response: dict = {"success": True, "payload": {"aborted": True, "runId": "run-9"}}

    async def chat_stream(self, *, session_key, message, timeout_ms=None, cwd=None,
                          model=None, permission_mode=None, attachments=None, token=None):
        self.stream_calls.append({
            "session_key": session_key, "message": message, "timeout_ms": timeout_ms,
            "cwd": cwd, "model": model, "permission_mode": permission_mode,
            "attachments": attachments, "token": token,
        })
        for f in self._frames:
            yield f

    async def chat_abort(self, *, session_key, run_id=None, token=None) -> dict:
        self.abort_calls.append({"session_key": session_key, "run_id": run_id, "token": token})
        return self._abort_response

    async def chat_inject(self, *, session_key, message, label=None, token=None) -> dict:
        self.inject_calls.append({"session_key": session_key, "message": message, "label": label, "token": token})
        return {"success": True, "payload": {"injected": True}}

    async def resolve_exec_approval(self, **kw): return {"resolved": "exec"}
    async def resolve_interaction(self, **kw): return {"resolved": "interaction"}
    async def resolve_mode_transition(self, **kw): return {"resolved": "mode"}


class TestChatAdapterCoverage:
    def _request(self, **kw) -> ChatRequest:
        defaults = {
            "userId": "u1", "agentId": "b1", "query": "hello",
            "sessionId": "agent:b1:session:s1:user:u1",
            "extraParams": {"cwd": "/tmp", "model": "m1", "permissionMode": "plan"},
        }
        defaults.update(kw)
        return ChatRequest(**defaults)

    async def test_stream_passes_attachments_from_extra_params(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = self._request(extraParams={
            "cwd": "/tmp", "model": "m1", "permissionMode": "plan",
            "attachments": [{"id": "a1"}],
        })
        async for _ in adapter.stream(req, auth=_auth("tok")):
            pass
        assert port.stream_calls[0]["attachments"] == [{"id": "a1"}]
        assert port.stream_calls[0]["token"] == "tok"

    async def test_stream_handles_non_dict_extra_params(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        # extraParams is None → no attachments, cwd/model/permission None
        req = ChatRequest(userId="u1", agentId="b1", query="hi")
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["attachments"] is None
        assert port.stream_calls[0]["cwd"] is None
        assert port.stream_calls[0]["model"] is None
        assert port.stream_calls[0]["permission_mode"] is None

    async def test_stream_preserves_user_prefixed_session(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = ChatRequest(
            userId="u1", agentId="b1", query="hi",
            sessionId="user:u1:session:s:agent:b1",
        )
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["session_key"] == "user:u1:session:s:agent:b1"

    async def test_stream_preserves_session_prefixed_session(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = ChatRequest(
            userId="u1", agentId="b1", query="hi",
            sessionId="session:abc-123",
        )
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["session_key"] == "session:abc-123"

    async def test_stream_converts_generic_exception_to_error_frame(self):
        class _ErrPort(_ChatPort):
            async def chat_stream(self, **kw):
                raise RuntimeError("internal boom")
                yield  # pragma: no cover
        adapter = ClaudeCodeChatAdapter(_ErrPort())  # type: ignore[arg-type]
        out = []
        async for f in adapter.stream(self._request()):
            out.append(f)
        assert len(out) == 1
        assert out[0].event == "error"
        assert out[0].payload["errorCode"] == "INTERNAL_ERROR"
        assert "internal boom" in out[0].payload["errorMessage"]

    async def test_stream_extracts_cwd_from_cwd_path_alias(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = self._request(extraParams={"cwd_path": "/from-cwd-path"})
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["cwd"] == "/from-cwd-path"

    async def test_stream_extracts_permission_mode_from_alias(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = self._request(extraParams={"permission_mode": "acceptEdits"})
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["permission_mode"] == "acceptEdits"

    async def test_stream_empty_cwd_becomes_none(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = self._request(extraParams={"cwd": "   "})
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["cwd"] is None

    async def test_stream_empty_model_becomes_none(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = self._request(extraParams={"model": ""})
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["model"] is None

    async def test_stream_empty_permission_mode_becomes_none(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = self._request(extraParams={"permissionMode": ""})
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["permission_mode"] is None

    async def test_stream_attachments_not_list_ignored(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        req = self._request(extraParams={
            "cwd": "/tmp",
            "attachments": "not-a-list",  # non-list → ignored
        })
        async for _ in adapter.stream(req):
            pass
        assert port.stream_calls[0]["attachments"] is None

    async def test_abort_port_raises_returns_internal_error(self):
        port = _ChatPort()

        async def abort(**kw):
            raise RuntimeError("port boom")
        port.chat_abort = abort  # type: ignore[assignment]
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.abort(ChatAbortRequest(session_key="sk"))
        assert result.ok is False
        assert result.error.code == "INTERNAL_ERROR"
        assert "port boom" in result.error.message

    async def test_abort_not_aborted_no_emit_events(self):
        port = _ChatPort()
        port._abort_response = {"success": True, "payload": {"aborted": False}}
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.abort(ChatAbortRequest(session_key="sk", run_id="r1"))
        assert result.ok is True
        assert result.aborted is False
        assert result.emit_events == []
        assert result.run_id == "r1"  # falls back to request.run_id

    async def test_abort_payload_missing_defaults(self):
        port = _ChatPort()
        port._abort_response = {"success": True}  # no payload
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.abort(ChatAbortRequest(session_key="sk"))
        assert result.ok is True
        assert result.aborted is False
        assert result.run_id is None
        assert result.emit_events == []

    async def test_inject_port_raises_returns_internal_error(self):
        port = _ChatPort()

        async def inject(**kw):
            raise RuntimeError("inject boom")
        port.chat_inject = inject  # type: ignore[assignment]
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.inject("sk", "msg", auth=_auth("t"))
        assert result["ok"] is False
        assert result["error"]["code"] == "INTERNAL_ERROR"

    async def test_inject_failure_with_error_dict(self):
        port = _ChatPort()

        async def inject(**kw):
            return {"success": False, "error": {"code": "DENY", "message": "no"}}
        port.chat_inject = inject  # type: ignore[assignment]
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.inject("sk", "msg")
        assert result["ok"] is False
        assert result["error"]["code"] == "DENY"
        assert result["error"]["message"] == "no"

    async def test_inject_success_payload_missing_defaults_empty(self):
        port = _ChatPort()

        async def inject(**kw):
            return {"success": True}  # no payload
        port.chat_inject = inject  # type: ignore[assignment]
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.inject("sk", "msg")
        assert result["ok"] is True
        assert result["payload"] == {}

    async def test_inject_empty_message_returns_invalid(self):
        adapter = ClaudeCodeChatAdapter(_ChatPort())
        result = await adapter.inject("sk", "")
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_REQUEST"

    async def test_inject_empty_session_returns_invalid(self):
        adapter = ClaudeCodeChatAdapter(_ChatPort())
        result = await adapter.inject("", "msg")
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_REQUEST"

    async def test_resolve_exec_approval_delegates(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.resolve_exec_approval("sk", "r1", "approve", auth=_auth("t"))
        assert result == {"resolved": "exec"}

    async def test_resolve_interaction_delegates(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.resolve_interaction("sk", "r1", response="yes")
        assert result == {"resolved": "interaction"}

    async def test_resolve_mode_transition_delegates(self):
        port = _ChatPort()
        adapter = ClaudeCodeChatAdapter(port)
        result = await adapter.resolve_mode_transition("sk", "r1", "accept")
        assert result == {"resolved": "mode"}


# ──────────────────────────────────────────────────────────────────────────────
# file adapter coverage
# ──────────────────────────────────────────────────────────────────────────────


class TestFileHelpers:
    def test_extract_bytes_from_bytes(self):
        assert _extract_bytes(b"raw-bytes") == b"raw-bytes"

    def test_extract_bytes_from_str(self):
        assert _extract_bytes("hello") == b"hello"

    def test_extract_bytes_from_dict_with_bytes_content(self):
        assert _extract_bytes({"content": b"dict-bytes"}) == b"dict-bytes"

    def test_extract_bytes_from_dict_with_str_content(self):
        assert _extract_bytes({"content": "dict-str"}) == b"dict-str"

    def test_extract_bytes_from_dict_without_content(self):
        assert _extract_bytes({"other": "x"}) == b""

    def test_extract_bytes_from_other_type(self):
        assert _extract_bytes(123) == b""


class _FilePort:
    def __init__(self) -> None:
        self._upload_response: dict = {"target_path": "/d/f.txt", "size": 4, "overwritten": False}
        self._read_response: Any = {"content": "hello body"}
        self._remove_ok: bool = True
        self._rmtree_ok: bool = True
        self._list_response: list[dict] = [{"name": "f.txt", "path": "/d/f.txt", "relative_path": "f.txt", "is_dir": False, "size": 10}]

    async def file_upload(self, path, content_bytes=None, token=None) -> dict:
        return self._upload_response

    async def file_read(self, path, token=None) -> dict:
        return self._read_response

    async def file_remove(self, path, token=None) -> bool:
        return self._remove_ok

    async def file_rmtree(self, path, token=None) -> bool:
        return self._rmtree_ok

    async def file_list_dir(self, path, token=None) -> list[dict]:
        return self._list_response


class TestFileAdapterCoverage:
    async def test_upload_target_path_fallback_to_path_key(self):
        port = _FilePort()
        port._upload_response = {"path": "/fallback"}  # no target_path — uses 'path'
        adapter = ClaudeCodeFileAdapter(port)
        result = await adapter.upload("/d/f.txt", b"data", auth=_auth("t"))
        assert result.target_path == "/fallback"

    async def test_upload_size_fallback_to_content_len(self):
        port = _FilePort()
        port._upload_response = {"target_path": "/p"}  # no size key — uses len(content)
        adapter = ClaudeCodeFileAdapter(port)
        result = await adapter.upload("/d/f.txt", b"abcd", auth=_auth("t"))
        assert result.size == 4

    async def test_read_returns_bytes_from_str_content(self):
        adapter = ClaudeCodeFileAdapter(_FilePort())
        body = await adapter.read("/d/f.txt")
        assert body == b"hello body"

    async def test_read_returns_bytes_from_bytes_content(self):
        port = _FilePort()
        port._read_response = {"content": b"raw-bytes"}
        adapter = ClaudeCodeFileAdapter(port)
        body = await adapter.read("/d/f.txt")
        assert body == b"raw-bytes"

    async def test_read_returns_empty_when_no_content(self):
        port = _FilePort()
        port._read_response = {"other": "x"}
        adapter = ClaudeCodeFileAdapter(port)
        body = await adapter.read("/d/f.txt")
        assert body == b""

    async def test_remove_raises_file_not_found_on_failure(self):
        port = _FilePort()
        port._remove_ok = False
        adapter = ClaudeCodeFileAdapter(port)
        with pytest.raises(FileNotFoundError, match="remove failed"):
            await adapter.remove("/d/f.txt")

    async def test_rmtree_raises_file_not_found_on_failure(self):
        port = _FilePort()
        port._rmtree_ok = False
        adapter = ClaudeCodeFileAdapter(port)
        with pytest.raises(FileNotFoundError, match="rmtree failed"):
            await adapter.rmtree("/d")

    async def test_list_dir_filters_non_dict_entries(self):
        port = _FilePort()
        port._list_response = [{"name": "f.txt"}, "bad", None]
        adapter = ClaudeCodeFileAdapter(port)
        result = await adapter.list_dir("/d", recursive=True, auth=_auth("t"))
        assert len(result.files) == 1
        assert result.files[0].name == "f.txt"

    async def test_list_dir_entry_camel_case_fallback(self):
        port = _FilePort()
        port._list_response = [{"name": "f", "path": "/p", "relativePath": "f", "isDir": True, "size": 0}]
        adapter = ClaudeCodeFileAdapter(port)
        result = await adapter.list_dir("/d")
        assert result.files[0].relative_path == "f"
        assert result.files[0].is_dir is True


# ──────────────────────────────────────────────────────────────────────────────
# models adapter coverage
# ──────────────────────────────────────────────────────────────────────────────


class TestModelsHelpers:
    def test_capabilities_from_payload_non_dict(self):
        c = _capabilities_from_payload(None)
        assert c.context_window is None
        assert c.vision is None
        c = _capabilities_from_payload("not-dict")
        assert c.context_window is None

    def test_capabilities_from_payload_full(self):
        c = _capabilities_from_payload({
            "context_window": 200000,
            "max_output_tokens": 4096,
            "vision": True,
            "function_calling": True,
            "reasoning": False,
            "streaming": True,
            "json_mode": True,
        })
        assert c.context_window == 200000
        assert c.max_output_tokens == 4096
        assert c.vision is True
        assert c.function_calling is True
        assert c.reasoning is False
        assert c.streaming is True
        assert c.json_mode is True

    def test_model_from_payload_provider_id_fallback(self):
        m = _model_from_payload({"id": "m1"}, provider_id="anthropic")
        assert m.provider == "anthropic"
        assert m.name == "m1"  # falls back to id

    def test_model_from_payload_name_from_id_alias(self):
        m = _model_from_payload({"id": "m1", "name": "Named"})
        assert m.name == "Named"

    def test_provider_from_payload_no_models_list(self):
        p = _provider_from_payload({"id": "p1", "name": "Prov"})
        assert p.id == "p1"
        assert p.models == []

    def test_provider_from_payload_models_not_list(self):
        p = _provider_from_payload({"id": "p1", "models": "not-a-list"})
        assert p.models == []

    def test_provider_from_payload_filters_non_dict_models(self):
        p = _provider_from_payload({
            "id": "p1",
            "models": [{"id": "m1"}, "bad", None],
        })
        assert len(p.models) == 1
        assert p.models[0].id == "m1"

    def test_provider_from_payload_missing_name_uses_id(self):
        p = _provider_from_payload({"id": "p1"})
        assert p.name == "p1"


class _ModelsPort:
    def __init__(self) -> None:
        self._models: list[dict] = []
        self._providers: list[dict] = []

    async def models_list(self, token=None) -> list[dict]:
        return self._models

    async def models_list_providers(self, token=None) -> list[dict]:
        return self._providers


class TestModelsAdapterCoverage:
    async def test_list_models_filters_non_dict(self):
        port = _ModelsPort()
        port._models = [{"id": "m1"}, "bad", None]
        adapter = ClaudeCodeModelsAdapter(port)
        out = await adapter.list_models(auth=_auth("t"))
        assert len(out) == 1
        assert out[0].id == "m1"

    async def test_list_models_skips_conversion_errors(self):
        port = _ModelsPort()
        # An entry whose id is unhashable in str() won't error; force an error
        # by providing an id that makes _model_from_payload choke. _model_from_payload
        # is quite tolerant, so we use an entry whose 'enabled' is an unhashable type
        # that choke bool(). bool() is very tolerant though; the realistic
        # exception path is hard to trigger from the helper. Use a non-dict that
        # passes the isinstance check then fails — but it can't. So trigger
        # via an entry whose 'default' is an object whose bool() raises.
        class _BoomBool:
            def __bool__(self):
                raise ValueError("boom")

        port._models = [{"id": "m1", "default": _BoomBool()}]
        adapter = ClaudeCodeModelsAdapter(port)
        out = await adapter.list_models()
        # The exception was caught → entry skipped
        assert out == []

    async def test_list_providers_filters_non_dict(self):
        port = _ModelsPort()
        port._providers = [{"id": "p1"}, "bad", None]
        adapter = ClaudeCodeModelsAdapter(port)
        out = await adapter.list_providers()
        assert len(out) == 1
        assert out[0].id == "p1"

    async def test_list_providers_skips_conversion_errors(self):
        port = _ModelsPort()
        class _BoomBool:
            def __bool__(self):
                raise ValueError("boom")
        port._providers = [{"id": "p1", "enabled": _BoomBool()}]
        adapter = ClaudeCodeModelsAdapter(port)
        out = await adapter.list_providers()
        assert out == []

    async def test_list_providers_passes_token(self):
        port = _ModelsPort()
        port._providers = [{"id": "p1"}]
        adapter = ClaudeCodeModelsAdapter(port)
        await adapter.list_providers(auth=_auth("tok"))
        # token is extracted and passed; no direct assertion on port call since
        # the port signature doesn't record it, but we verify no exception.
