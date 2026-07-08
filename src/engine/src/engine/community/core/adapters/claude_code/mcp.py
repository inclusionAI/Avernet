"""ClaudeCode MCP ACL adapter.

Implements the core ``MCPService`` by delegating to an injected
``ClaudeCodeMcpPort`` and translating the port's primitive dicts into core
DTOs.  All DTO construction (``MCPServerConfig``, ``MCPServer``,
``MCPServerStatus``, ``MCPTool``, ``MCPResource``, ``MCPPrompt``,
``MCPToolCallResult``) lives here; the port impl only deals in plain dicts.

Capability matrix:
- **Port-backed**: ``list_servers``, ``get_server``, ``create_server``,
  ``update_server``, ``delete_server``, ``get_server_status``,
  ``list_tools``, ``call_tool``, ``list_resources``, ``read_resource``,
  ``list_prompts``, ``get_prompt``.
- **Raises CapabilityNotSupportedError** (the relay has no lifecycle RPCs
  in the v3 surface — the corp impl raised the same): ``start_server``,
  ``stop_server``, ``restart_server``.
- **Allow-list apply**: ``filter_servers`` delegates to the port's
  ``mcp_apply_server_filter`` (wire RPC ``mcp.filter_servers``, mirroring the
  corp impl + the vendored relay's ``handleFilterServers``). Distinct from the
  port's ``mcp_filter_servers`` substring-search helper (no RPC).
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
from engine.community.plugin_api.claude_code.mcp import ClaudeCodeMcpPort

log = logging.getLogger("claude-code-mcp-adapter")


# ── Dict → DTO helpers (relocated from engines/claude_code/mcp.py) ────────────


def _parse_transport(raw: Any) -> TransportType:
    val = str(raw).strip().lower() if raw else ""
    if val == "stdio":
        return TransportType.STDIO
    if val in ("http", "streamable_http"):
        return TransportType.HTTP
    return TransportType.SSE


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


def _config_from_raw(server_code: str, raw: dict[str, Any]) -> MCPServerConfig:
    """Build ``MCPServerConfig`` from a raw relay server dict.

    Tolerates both canonical (``transport``) and relay-native (``type``,
    ``baseUrl``, ``timeoutSeconds``) field names.
    """
    obj = raw if isinstance(raw, dict) else {}
    timeout_raw = obj.get("timeout_seconds", obj.get("timeoutSeconds", 30))
    try:
        timeout = int(timeout_raw) if timeout_raw is not None else 30
    except (TypeError, ValueError):
        timeout = 30
    return MCPServerConfig(
        server_code=server_code,
        description=obj.get("description"),
        transport=_parse_transport(obj.get("transport", obj.get("type"))),
        url=obj.get("url", obj.get("baseUrl")),
        command=obj.get("command"),
        args=_to_str_list(obj.get("args")),
        env=_to_str_dict(obj.get("env")),
        headers=_to_str_dict(obj.get("headers")),
        timeout_seconds=timeout,
        enabled=bool(obj.get("enabled", True)),
    )


def _server_from_payload(data: dict[str, Any]) -> MCPServer:
    """Build ``MCPServer`` from a raw port server dict.

    Relocated from ``engines/claude_code/mcp.py:_server_from_payload``.
    Accepts both ``serverCode`` (relay wire) and ``server_code`` (canonical).
    """
    server_code = data.get("serverCode") or data.get("server_code", "")
    config = _config_from_raw(server_code, data)
    status = MCPServerStatus.RUNNING if config.enabled else MCPServerStatus.STOPPED
    return MCPServer(config=config, status=status)


def _serialize_config(config: MCPServerConfig) -> dict[str, Any]:
    """Serialize ``MCPServerConfig`` to the relay's camelCase wire dict."""
    out: dict[str, Any] = {"serverCode": config.server_code}
    if config.transport:
        out["type"] = config.transport.value
    if config.url:
        out["url"] = config.url
    if config.command:
        out["command"] = config.command
    if config.args:
        out["args"] = config.args
    if config.env:
        out["env"] = config.env
    if config.headers:
        out["headers"] = config.headers
    out["timeout_seconds"] = config.timeout_seconds
    out["enabled"] = config.enabled
    return out


def _tool_from_payload(data: dict[str, Any]) -> MCPTool:
    return MCPTool(
        name=data.get("name", ""),
        description=data.get("description", ""),
        input_schema=data.get("inputSchema") or data.get("input_schema") or {},
        server_code=data.get("serverCode") or data.get("server_code", ""),
    )


def _resource_from_payload(data: dict[str, Any], server_code: str) -> MCPResource:
    return MCPResource(
        uri=data.get("uri", ""),
        name=data.get("name", ""),
        description=data.get("description"),
        mime_type=data.get("mimeType") or data.get("mime_type"),
        server_code=server_code,
    )


def _prompt_from_payload(data: dict[str, Any], server_code: str) -> MCPPrompt:
    return MCPPrompt(
        name=data.get("name", ""),
        description=data.get("description", ""),
        arguments=data.get("arguments") or [],
        server_code=server_code,
    )


class ClaudeCodeMcpAdapter(MCPService):
    """`MCPService` backed by the claude_code native MCP port."""

    def __init__(self, port: ClaudeCodeMcpPort) -> None:
        self._port = port

    # ── Server CRUD ──────────────────────────────────────────────────────────

    async def list_servers(
        self, auth: AuthContext | None = None,
    ) -> list[MCPServer]:
        token = auth.token if auth is not None else None
        entries = await self._port.mcp_list_servers(token=token)
        return [_server_from_payload(e) for e in entries if isinstance(e, dict)]

    async def get_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> MCPServer | None:
        token = auth.token if auth is not None else None
        entry = await self._port.mcp_get_server(server_code=server_code, token=token)
        if entry is None:
            return None
        return _server_from_payload(entry)

    async def create_server(
        self, config: MCPServerConfig, auth: AuthContext | None = None,
    ) -> MCPServer:
        token = auth.token if auth is not None else None
        params = _serialize_config(config)
        stored = await self._port.mcp_create_server(config=params, token=token)
        return _server_from_payload(stored)

    async def update_server(
        self,
        server_code: str,
        config: MCPServerConfig,
        auth: AuthContext | None = None,
    ) -> MCPServer:
        token = auth.token if auth is not None else None
        params = _serialize_config(config)
        stored = await self._port.mcp_update_server(
            server_code=server_code, patch=params, token=token
        )
        return _server_from_payload(stored)

    async def delete_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        token = auth.token if auth is not None else None
        return await self._port.mcp_delete_server(server_code=server_code, token=token)

    # ── Server lifecycle (no relay RPCs) ──────────────────────────────────────

    async def start_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        raise CapabilityNotSupportedError("claude_code", Capability.MCP_START)

    async def stop_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        raise CapabilityNotSupportedError("claude_code", Capability.MCP_STOP)

    async def restart_server(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> bool:
        raise CapabilityNotSupportedError("claude_code", Capability.MCP_STOP)

    # ── Status ───────────────────────────────────────────────────────────────

    async def get_server_status(
        self, server_code: str, auth: AuthContext | None = None,
    ) -> MCPServerStatus:
        token = auth.token if auth is not None else None
        raw = await self._port.mcp_get_server_status(
            server_code=server_code, token=token
        )
        status_str = str(raw.get("status", "stopped")).lower()
        mapping = {
            "running": MCPServerStatus.RUNNING,
            "starting": MCPServerStatus.STARTING,
            "stopping": MCPServerStatus.STOPPING,
            "error": MCPServerStatus.ERROR,
        }
        return mapping.get(status_str, MCPServerStatus.STOPPED)

    # ── Tools ─────────────────────────────────────────────────────────────────

    async def list_tools(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPTool]:
        token = auth.token if auth is not None else None
        if server_code is None:
            # No server filter: corp impl returned [] for a missing context.
            # The port requires a server_code; surface [] when unspecified.
            return []
        raw = await self._port.mcp_list_tools(server_code=server_code, token=token)
        return [_tool_from_payload(t) for t in raw if isinstance(t, dict)]

    async def call_tool(
        self, request: MCPToolCallRequest, auth: AuthContext | None = None,
    ) -> MCPToolCallResult:
        token = auth.token if auth is not None else None
        raw = await self._port.mcp_call_tool(
            server_code=request.server_code or "",
            tool_name=request.tool_name,
            arguments=request.arguments,
            token=token,
        )
        return MCPToolCallResult(
            tool_name=request.tool_name,
            server_code=request.server_code or raw.get("serverCode", "") or "",
            content=raw.get("content", []),
            is_error=bool(raw.get("isError", False)),
        )

    # ── Resources ─────────────────────────────────────────────────────────────

    async def list_resources(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPResource]:
        if server_code is None:
            return []
        token = auth.token if auth is not None else None
        raw = await self._port.mcp_list_resources(server_code=server_code, token=token)
        return [_resource_from_payload(r, server_code) for r in raw if isinstance(r, dict)]

    async def read_resource(
        self, server_code: str, uri: str, auth: AuthContext | None = None,
    ) -> str:
        token = auth.token if auth is not None else None
        raw = await self._port.mcp_read_resource(
            server_code=server_code, resource_uri=uri, token=token
        )
        # The corp impl raised here; the port exposes the data, so return the
        # content field as a string (fallback to serialising the payload).
        if isinstance(raw, dict):
            content = raw.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # MCP content list → concatenate text parts.
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                return "".join(parts)
            return str(content) if content is not None else ""
        return str(raw)

    # ── Prompts ───────────────────────────────────────────────────────────────

    async def list_prompts(
        self,
        server_code: str | None = None,
        auth: AuthContext | None = None,
    ) -> list[MCPPrompt]:
        if server_code is None:
            return []
        token = auth.token if auth is not None else None
        raw = await self._port.mcp_list_prompts(server_code=server_code, token=token)
        return [_prompt_from_payload(p, server_code) for p in raw if isinstance(p, dict)]

    async def get_prompt(
        self,
        server_code: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        auth: AuthContext | None = None,
    ) -> str:
        token = auth.token if auth is not None else None
        raw = await self._port.mcp_get_prompt(
            server_code=server_code,
            prompt_name=name,
            arguments=arguments,
            token=token,
        )
        if isinstance(raw, dict):
            content = raw.get("content")
            if isinstance(content, str):
                return content
            return str(content) if content is not None else ""
        return str(raw)

    # ── filter_servers (allow-list apply via relay mcp.filter_servers RPC) ─────

    async def filter_servers(
        self, request: MCPFilterRequest, auth: AuthContext | None = None,
    ) -> MCPFilterResult:
        token = auth.token if auth is not None else None
        payload = await self._port.mcp_apply_server_filter(
            server_codes=request.server_codes or [],
            timeout_seconds=request.timeout_seconds,
            token=token,
        )
        return MCPFilterResult(
            server_codes=payload.get("serverCodes", request.server_codes or []),
            command=payload.get("command", []),
            return_code=payload.get("returnCode", 0),
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
        )


__all__ = ["ClaudeCodeMcpAdapter"]
