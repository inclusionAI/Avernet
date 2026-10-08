"""Unit tests for the OpenClaw MCP ACL adapter.

Drives ``OpenClawMcpAdapter`` against a fake ``OpenClawMcpPort`` that returns
canned primitive dicts.  Verifies:
- DTO construction for CRUD operations
- create/update serialisation (MCPServerConfig → entry dict)
- call_tool and filter_servers result building
- start/stop/restart return False directly (no port call)
- capability-gated methods raise CapabilityNotSupportedError
"""
from __future__ import annotations

from typing import Any

import pytest

from engine.community.core.adapters.openclaw.mcp import OpenClawMcpAdapter
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.mcp.models import (
    MCPFilterRequest,
    MCPServer,
    MCPServerConfig,
    MCPServerStatus,
    MCPToolCallRequest,
    MCPToolCallResult,
    MCPFilterResult,
    TransportType,
)


# ── Fake port ────────────────────────────────────────────────────────────────


class _FakeMcpPort:
    """Minimal fake returning canned results or raising on demand."""

    def __init__(self) -> None:
        self._list_result: list[dict] = []
        self._get_result: dict | None = None
        self._create_result: dict | None = None
        self._update_result: dict | None = None
        self._delete_result: bool = False
        self._status_result: dict | None = None
        self._call_tool_result: dict | None = None
        self._filter_result: dict | None = None
        self._raise: Exception | None = None
        # Track last call args for assertion
        self.last_create_entry: dict | None = None
        self.last_update_entry: dict | None = None
        self.last_update_code: str | None = None
        self.last_call_tool_args: dict | None = None
        self.last_filter_codes: list[str] | None = None

    # ── setup helpers ──

    def will_list(self, entries: list[dict]) -> None:
        self._list_result = entries

    def will_get(self, entry: dict | None) -> None:
        self._get_result = entry

    def will_create(self, entry: dict) -> None:
        self._create_result = entry

    def will_update(self, entry: dict) -> None:
        self._update_result = entry

    def will_delete(self, result: bool) -> None:
        self._delete_result = result

    def will_status(self, result: dict) -> None:
        self._status_result = result

    def will_call_tool(self, result: dict) -> None:
        self._call_tool_result = result

    def will_filter(self, result: dict) -> None:
        self._filter_result = result

    def will_raise(self, exc: Exception) -> None:
        self._raise = exc

    # ── port protocol ──

    async def list_servers(self) -> list[dict]:
        if self._raise:
            raise self._raise
        return self._list_result

    async def get_server(self, server_code: str) -> dict | None:
        if self._raise:
            raise self._raise
        return self._get_result

    async def create_server(self, entry: dict) -> dict:
        if self._raise:
            raise self._raise
        self.last_create_entry = entry
        return self._create_result  # type: ignore[return-value]

    async def update_server(self, server_code: str, entry: dict) -> dict:
        if self._raise:
            raise self._raise
        self.last_update_code = server_code
        self.last_update_entry = entry
        return self._update_result  # type: ignore[return-value]

    async def delete_server(self, server_code: str) -> bool:
        if self._raise:
            raise self._raise
        return self._delete_result

    async def get_server_status(self, server_code: str) -> dict:
        if self._raise:
            raise self._raise
        return self._status_result  # type: ignore[return-value]

    async def call_tool(self, tool: str, args: dict) -> dict:
        if self._raise:
            raise self._raise
        self.last_call_tool_args = args
        return self._call_tool_result  # type: ignore[return-value]

    async def filter_servers(self, codes: list[str], timeout: int = 30) -> dict:
        if self._raise:
            raise self._raise
        self.last_filter_codes = codes
        self.last_filter_timeout = timeout
        return self._filter_result  # type: ignore[return-value]


# ── helpers ──────────────────────────────────────────────────────────────────


def _raw_entry(
    server_code: str,
    *,
    transport: str = "sse",
    url: str = "http://localhost:8080",
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "server_code": server_code,
        "transport": transport,
        "url": url,
        "command": None,
        "args": [],
        "env": {},
        "headers": {},
        "timeout_seconds": 30,
        "enabled": enabled,
        "description": "",
    }


# ── list_servers ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_servers_builds_mcp_server_list():
    port = _FakeMcpPort()
    port.will_list([_raw_entry("alpha"), _raw_entry("beta")])
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.list_servers()

    assert len(result) == 2
    assert all(isinstance(s, MCPServer) for s in result)
    codes = [s.config.server_code for s in result]
    assert "alpha" in codes
    assert "beta" in codes


@pytest.mark.asyncio
async def test_list_servers_empty():
    port = _FakeMcpPort()
    port.will_list([])
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.list_servers()
    assert result == []


@pytest.mark.asyncio
async def test_list_servers_status_derived_from_enabled():
    port = _FakeMcpPort()
    port.will_list([
        _raw_entry("running_srv", enabled=True),
        _raw_entry("stopped_srv", enabled=False),
    ])
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.list_servers()
    by_code = {s.config.server_code: s for s in result}
    assert by_code["running_srv"].status == MCPServerStatus.RUNNING
    assert by_code["stopped_srv"].status == MCPServerStatus.STOPPED


# ── get_server ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_server_builds_mcp_server():
    port = _FakeMcpPort()
    port.will_get(_raw_entry("my_srv", transport="http", url="http://mcp:9000"))
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.get_server("my_srv")

    assert isinstance(result, MCPServer)
    assert result.config.server_code == "my_srv"
    assert result.config.transport == TransportType.HTTP
    assert result.config.url == "http://mcp:9000"


@pytest.mark.asyncio
async def test_get_server_none_when_not_found():
    port = _FakeMcpPort()
    port.will_get(None)
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.get_server("missing")
    assert result is None


# ── create_server ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_server_serializes_config_and_builds_mcp_server():
    port = _FakeMcpPort()
    stored = _raw_entry("new_srv", transport="stdio")
    port.will_create(stored)
    adapter = OpenClawMcpAdapter(port)

    config = MCPServerConfig(
        server_code="new_srv",
        transport=TransportType.STDIO,
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
        enabled=True,
    )
    result = await adapter.create_server(config)

    assert isinstance(result, MCPServer)
    # The adapter must have serialized the config to a dict for the port
    assert port.last_create_entry is not None
    assert port.last_create_entry["server_code"] == "new_srv"
    assert port.last_create_entry["transport"] == "stdio"
    assert port.last_create_entry["command"] == "npx"
    assert port.last_create_entry["args"] == ["-y", "@modelcontextprotocol/server-filesystem"]


@pytest.mark.asyncio
async def test_create_server_propagates_file_exists_error():
    port = _FakeMcpPort()
    port.will_raise(FileExistsError("MCP Server 已存在: dup_srv"))
    adapter = OpenClawMcpAdapter(port)

    config = MCPServerConfig(server_code="dup_srv")
    with pytest.raises(FileExistsError):
        await adapter.create_server(config)


@pytest.mark.asyncio
async def test_create_server_http_transport_serialized():
    port = _FakeMcpPort()
    stored = _raw_entry("http_srv", transport="http", url="http://mcp-host:8080")
    port.will_create(stored)
    adapter = OpenClawMcpAdapter(port)

    config = MCPServerConfig(
        server_code="http_srv",
        transport=TransportType.HTTP,
        url="http://mcp-host:8080",
    )
    await adapter.create_server(config)

    assert port.last_create_entry["transport"] == "http"
    assert port.last_create_entry["url"] == "http://mcp-host:8080"


# ── update_server ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_server_serializes_config_and_builds_mcp_server():
    port = _FakeMcpPort()
    stored = _raw_entry("existing", enabled=False)
    port.will_update(stored)
    adapter = OpenClawMcpAdapter(port)

    config = MCPServerConfig(
        server_code="existing",
        transport=TransportType.SSE,
        url="http://sse:9000",
        enabled=False,
    )
    result = await adapter.update_server("existing", config)

    assert isinstance(result, MCPServer)
    assert port.last_update_code == "existing"
    assert port.last_update_entry["enabled"] is False


@pytest.mark.asyncio
async def test_update_server_propagates_file_not_found():
    port = _FakeMcpPort()
    port.will_raise(FileNotFoundError("MCP Server 不存在: ghost"))
    adapter = OpenClawMcpAdapter(port)

    config = MCPServerConfig(server_code="ghost")
    with pytest.raises(FileNotFoundError):
        await adapter.update_server("ghost", config)


# ── delete_server ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_server_true_when_existed():
    port = _FakeMcpPort()
    port.will_delete(True)
    adapter = OpenClawMcpAdapter(port)

    assert await adapter.delete_server("srv") is True


@pytest.mark.asyncio
async def test_delete_server_false_when_not_found():
    port = _FakeMcpPort()
    port.will_delete(False)
    adapter = OpenClawMcpAdapter(port)

    assert await adapter.delete_server("missing") is False


# ── get_server_status ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_server_status_running():
    port = _FakeMcpPort()
    port.will_status({"server_code": "srv", "status": "running"})
    adapter = OpenClawMcpAdapter(port)

    status = await adapter.get_server_status("srv")
    assert status == MCPServerStatus.RUNNING


@pytest.mark.asyncio
async def test_get_server_status_stopped():
    port = _FakeMcpPort()
    port.will_status({"server_code": "srv", "status": "stopped"})
    adapter = OpenClawMcpAdapter(port)

    status = await adapter.get_server_status("srv")
    assert status == MCPServerStatus.STOPPED


# ── start/stop/restart (constant False) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_start_server_returns_false():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    assert await adapter.start_server("any") is False


@pytest.mark.asyncio
async def test_stop_server_returns_false():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    assert await adapter.stop_server("any") is False


@pytest.mark.asyncio
async def test_restart_server_returns_false():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    assert await adapter.restart_server("any") is False


# ── call_tool ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_tool_builds_result():
    port = _FakeMcpPort()
    port.will_call_tool({
        "tool_name": "my_tool",
        "server_code": "",
        "content": [{"type": "text", "text": "hello"}],
        "is_error": False,
    })
    adapter = OpenClawMcpAdapter(port)

    request = MCPToolCallRequest(
        tool_name="my_tool",
        arguments={"key": "value"},
        server_code="srv_a",
    )
    result = await adapter.call_tool(request)

    assert isinstance(result, MCPToolCallResult)
    assert result.tool_name == "my_tool"
    assert result.server_code == "srv_a"
    assert result.content == [{"type": "text", "text": "hello"}]
    assert result.is_error is False
    # Args passed through to port
    assert port.last_call_tool_args == {"key": "value"}


@pytest.mark.asyncio
async def test_call_tool_is_error_flag():
    port = _FakeMcpPort()
    port.will_call_tool({
        "tool_name": "bad_tool",
        "server_code": "",
        "content": [{"type": "text", "text": "error output"}],
        "is_error": True,
    })
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.call_tool(
        MCPToolCallRequest(tool_name="bad_tool", arguments={})
    )
    assert result.is_error is True


@pytest.mark.asyncio
async def test_call_tool_server_code_falls_back_to_empty():
    port = _FakeMcpPort()
    port.will_call_tool({
        "tool_name": "t",
        "server_code": "",
        "content": [],
        "is_error": False,
    })
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.call_tool(
        MCPToolCallRequest(tool_name="t", arguments={}, server_code=None)
    )
    assert result.server_code == ""


# ── filter_servers ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_filter_servers_builds_result():
    port = _FakeMcpPort()
    port.will_filter({
        "server_codes": ["alpha", "beta"],
        "command": ["mcporter", "filter-servers", "alpha,beta"],
        "return_code": 0,
        "stdout": "ok",
        "stderr": "",
    })
    adapter = OpenClawMcpAdapter(port)

    request = MCPFilterRequest(server_codes=["alpha", "beta"], timeout_seconds=90)
    result = await adapter.filter_servers(request)

    assert isinstance(result, MCPFilterResult)
    assert result.server_codes == ["alpha", "beta"]
    assert result.return_code == 0
    assert result.stdout == "ok"
    assert port.last_filter_codes == ["alpha", "beta"]
    # the caller's timeout_seconds must be forwarded to the port (not hardcoded 30)
    assert port.last_filter_timeout == 90


@pytest.mark.asyncio
async def test_filter_servers_empty_codes():
    port = _FakeMcpPort()
    port.will_filter({
        "server_codes": [],
        "command": ["mcporter", "filter-servers", "__EMPTY_FILTER_DISABLE_ALL__"],
        "return_code": 0,
        "stdout": "",
        "stderr": "",
    })
    adapter = OpenClawMcpAdapter(port)

    result = await adapter.filter_servers(MCPFilterRequest(server_codes=[]))
    assert result.server_codes == []
    assert port.last_filter_codes == []
    # default timeout_seconds=30 forwards as 30
    assert port.last_filter_timeout == 30


# ── capability-gated methods ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tools_raises_capability_not_supported():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    with pytest.raises(CapabilityNotSupportedError) as exc_info:
        await adapter.list_tools()
    assert exc_info.value.capability == Capability.MCP_TOOLS_LIST


@pytest.mark.asyncio
async def test_list_resources_raises_capability_not_supported():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    with pytest.raises(CapabilityNotSupportedError) as exc_info:
        await adapter.list_resources()
    assert exc_info.value.capability == Capability.MCP_RESOURCES_LIST


@pytest.mark.asyncio
async def test_read_resource_raises_capability_not_supported():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    with pytest.raises(CapabilityNotSupportedError) as exc_info:
        await adapter.read_resource("srv", "uri://foo")
    assert exc_info.value.capability == Capability.MCP_RESOURCES_READ


@pytest.mark.asyncio
async def test_list_prompts_raises_capability_not_supported():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    with pytest.raises(CapabilityNotSupportedError) as exc_info:
        await adapter.list_prompts()
    assert exc_info.value.capability == Capability.MCP_PROMPTS_LIST


@pytest.mark.asyncio
async def test_get_prompt_raises_capability_not_supported():
    adapter = OpenClawMcpAdapter(_FakeMcpPort())
    with pytest.raises(CapabilityNotSupportedError) as exc_info:
        await adapter.get_prompt("srv", "prompt_name")
    assert exc_info.value.capability == Capability.MCP_PROMPTS_GET
