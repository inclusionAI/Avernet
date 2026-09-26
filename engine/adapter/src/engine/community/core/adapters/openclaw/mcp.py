"""OpenClaw MCP ACL adapter.

Implements the core ``MCPService`` by delegating to an injected
``OpenClawMcpPort`` and translating the port's primitive dicts into core
DTOs.  All DTO construction (``MCPServerConfig``, ``MCPServer``,
``MCPServerStatus``, ``MCPToolCallResult``, ``MCPFilterResult``) lives here;
the port impl only deals in plain dicts.

Capability matrix:
- **Port-backed**: ``list_servers``, ``get_server``, ``create_server``,
  ``update_server``, ``delete_server``, ``get_server_status``,
  ``call_tool``, ``filter_servers``.
- **Constant False** (mcporter handles lifecycle out-of-band):
  ``start_server``, ``stop_server``, ``restart_server``.
- **Raises CapabilityNotSupportedError** (not on the port — decision 5):
  ``list_tools``, ``list_resources``, ``read_resource``,
  ``list_prompts``, ``get_prompt``.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.community.core.engine.capability import Capability
from engine.community.core.engine.context import AuthContext
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.mcp.models import (
    MCPFilterRequest,
    MCPFilterResult,
    MCPPrompt,
    MCPResource,
    MCPServer,
    MCPServerConfig,
    MCPServerStatus,
    MCPTool,
    MCPToolCallRequest,
    MCPToolCallResult,
    TransportType,
)
from engine.community.core.mcp.protocol import MCPService
from engine.community.plugin_api.openclaw.mcp import OpenClawMcpPort

log = logging.getLogger("openclaw-mcp-adapter")


# ── Dict → DTO helpers (relocated from engines/openclaw/mcp.py) ──────────────


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _to_str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _parse_transport(raw: Any) -> TransportType:
    val = str(raw).strip().lower() if raw is not None else ""
    if val in {"streamable_http", "http"}:
        return TransportType.HTTP
    if val == "sse":
        return TransportType.SSE
    if val == "stdio":
        return TransportType.STDIO
    return TransportType.SSE


def _normalize_timeout(raw: Any) -> int:
    if raw is None:
        return 30
    try:
        timeout = int(raw)
        return timeout if timeout >= 0 else 30
    except (TypeError, ValueError):
        return 30


def _config_from_raw(server_code: str, raw: dict[str, Any]) -> MCPServerConfig:
    """Build ``MCPServerConfig`` from a raw mcporter.json entry dict.

    Relocated from ``engines/openclaw/mcp.py:_config_from_raw``.  Tolerates
    legacy (``baseUrl`` / ``timeoutSeconds`` / ``type``) and canonical field
    names.
    """
    obj = raw if isinstance(raw, dict) else {}
    return MCPServerConfig(
        server_code=server_code,
        description=obj.get("description"),
        transport=_parse_transport(obj.get("transport", obj.get("type"))),
        url=obj.get("url", obj.get("baseUrl")),
        command=obj.get("command"),
        args=_to_str_list(obj.get("args")),
        env=_to_str_dict(obj.get("env")),
        headers=_to_str_dict(obj.get("headers")),
        timeout_seconds=_normalize_timeout(
            obj.get(
                "timeout_seconds",
                obj.get("timeoutSeconds", obj.get("timeout")),
            )
        ),
        enabled=bool(obj.get("enabled", True)),
    )


def _server_from_entry(entry: dict[str, Any]) -> MCPServer:
    """Build ``MCPServer`` from a raw port entry dict (includes ``server_code``).

    Relocated from ``engines/openclaw/mcp.py:_server_from_config`` but
    operates on the raw dict rather than an ``MCPServerConfig``.
    """
    server_code = entry.get("server_code", "")
    config = _config_from_raw(server_code, entry)
    status = (
        MCPServerStatus.RUNNING if config.enabled else MCPServerStatus.STOPPED
    )
    return MCPServer(config=config, status=status)


def _serialize_config(config: MCPServerConfig) -> dict[str, Any]:
    """Serialize ``MCPServerConfig`` to a plain dict for ``create_server``.

    The port impl handles legacy-key preservation on update; this method
    always emits canonical keys (used only for create where there is no
    existing raw entry to inspect).
    """
    return {
        "server_code": config.server_code,
        "description": config.description if config.description is not None else "",
        "transport": config.transport.value,
        "url": config.url,
        "command": config.command,
        "args": config.args,
        "env": config.env,
        "headers": config.headers,
        "timeout_seconds": config.timeout_seconds,
        "enabled": config.enabled,
    }


class OpenClawMcpAdapter(MCPService):
    """`MCPService` backed by the OpenClaw native MCP port."""

    def __init__(self, port: OpenClawMcpPort) -> None:
        self._port = port

    # ── Server CRUD ──────────────────────────────────────────────────────────

    async def list_servers(
        self, auth: AuthContext | None = None,
    ) -> list[MCPServer]:
        entries = await self._port.list_servers()
        return [_server_from_entry(e) for e in entries]

    async def get_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> MCPServer | None:
        entry = await self._port.get_server(server_code)
        if entry is None:
            return None
        return _server_from_entry(entry)

    async def create_server(
        self, config: MCPServerConfig, auth: AuthContext | None = None,
    ) -> MCPServer:
        entry = _serialize_config(config)
        stored = await self._port.create_server(entry)
        return _server_from_entry(stored)

    async def update_server(
        self,
        server_code: str,
        config: MCPServerConfig,
        auth: AuthContext | None = None,
    ) -> MCPServer:
        entry = _serialize_config(config)
        stored = await self._port.update_server(server_code, entry)
        return _server_from_entry(stored)

    async def delete_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        return await self._port.delete_server(server_code)

    # ── Server lifecycle (mcporter handles out-of-band) ──────────────────────

    async def start_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        return False

    async def stop_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        return False

    async def restart_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        return False

    # ── Status ───────────────────────────────────────────────────────────────

    async def get_server_status(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> MCPServerStatus:
        raw = await self._port.get_server_status(server_code)
        status_str = raw.get("status", "stopped")
        if status_str == "running":
            return MCPServerStatus.RUNNING
        return MCPServerStatus.STOPPED

    # ── Tools (not exposed by OpenClaw) ──────────────────────────────────────

    async def list_tools(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPTool]:
        raise CapabilityNotSupportedError("openclaw", Capability.MCP_TOOLS_LIST)

    async def call_tool(
        self, request: MCPToolCallRequest, auth: AuthContext | None = None,
    ) -> MCPToolCallResult:
        raw = await self._port.call_tool(
            request.tool_name, request.arguments or {}
        )
        return MCPToolCallResult(
            tool_name=raw["tool_name"],
            server_code=request.server_code or raw.get("server_code", ""),
            content=raw["content"],
            is_error=raw["is_error"],
        )

    # ── Resources (not exposed by OpenClaw) ──────────────────────────────────

    async def list_resources(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPResource]:
        raise CapabilityNotSupportedError("openclaw", Capability.MCP_RESOURCES_LIST)

    async def read_resource(
        self, server_code: str, uri: str, auth: AuthContext | None = None,
    ) -> str:
        raise CapabilityNotSupportedError("openclaw", Capability.MCP_RESOURCES_READ)

    # ── Prompts (not exposed by OpenClaw) ────────────────────────────────────

    async def list_prompts(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPPrompt]:
        raise CapabilityNotSupportedError("openclaw", Capability.MCP_PROMPTS_LIST)

    async def get_prompt(
        self,
        server_code: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        auth: AuthContext | None = None,
    ) -> str:
        raise CapabilityNotSupportedError("openclaw", Capability.MCP_PROMPTS_GET)

    # ── filter_servers ───────────────────────────────────────────────────────

    async def filter_servers(
        self, request: MCPFilterRequest, auth: AuthContext | None = None,
    ) -> MCPFilterResult:
        raw = await self._port.filter_servers(
            request.server_codes or [],
            timeout=_normalize_timeout(request.timeout_seconds) or 30,
        )
        return MCPFilterResult(
            server_codes=raw["server_codes"],
            command=raw["command"],
            return_code=raw["return_code"],
            stdout=raw["stdout"],
            stderr=raw["stderr"],
        )


__all__ = ["OpenClawMcpAdapter"]
