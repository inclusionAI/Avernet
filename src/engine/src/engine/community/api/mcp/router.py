"""MCP router — dispatches every endpoint through ``EngineManager.mcp``.

Engine-specific behaviour (mcporter file edits, ``mcporter`` CLI shell-out)
lives in ``engines/openclaw/mcp.OpenClawMCPService``; AiCoding ships its own
plugin proxying to teamclaw-aicoding-relay. The router only marshals
HTTP↔Plugin types and applies capability guards.
"""
from __future__ import annotations

import logging
import subprocess

from fastapi import APIRouter, HTTPException

from engine.community.api.caps import check_capability
from engine.community.api.mcp.schemas import (
    MCPCallToolRequest,
    MCPFilterServersRequest,
    MCPServerCreateRequest,
    MCPServerOperationRequest,
    MCPServerUpdateRequest,
)
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.mcp.models import (
    MCPFilterRequest,
    MCPServer,
    MCPServerConfig,
    MCPToolCallRequest,
    TransportType,
)

log = logging.getLogger("api-mcp")

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


# ── Helpers ──


def _mcp_plugin():
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().mcp


def _parse_transport(raw: str | None) -> TransportType:
    val = (raw or "").strip().lower()
    if val in {"streamable_http", "http"}:
        return TransportType.HTTP
    if val == "stdio":
        return TransportType.STDIO
    return TransportType.SSE


def _config_from_create(req: MCPServerCreateRequest) -> MCPServerConfig:
    return MCPServerConfig(
        server_code=req.server_code,
        transport=_parse_transport(req.transport),
        description=req.description,
        url=req.url,
        command=req.command,
        args=list(req.args or []),
        env=dict(req.env or {}),
        headers=dict(req.headers or {}),
        timeout_seconds=int(req.timeout_seconds or 30),
        enabled=bool(req.enabled),
    )


def _patch_config(
    base: MCPServerConfig, req: MCPServerUpdateRequest,
) -> MCPServerConfig:
    """Apply a partial update on top of an existing config dataclass."""
    patch = req.model_dump(exclude_unset=True)
    if "transport" in patch:
        patch["transport"] = _parse_transport(patch["transport"])
    for key in ("args", "env", "headers"):
        if key in patch and patch[key] is None:
            patch[key] = [] if key == "args" else {}
    return MCPServerConfig(
        server_code=base.server_code,
        transport=patch.get("transport", base.transport),
        description=patch.get("description", base.description),
        url=patch.get("url", base.url),
        command=patch.get("command", base.command),
        args=list(patch.get("args", base.args) or []),
        env=dict(patch.get("env", base.env) or {}),
        headers=dict(patch.get("headers", base.headers) or {}),
        timeout_seconds=int(patch.get("timeout_seconds", base.timeout_seconds) or 30),
        enabled=bool(patch.get("enabled", base.enabled)),
    )


def _server_to_dict(server: MCPServer) -> dict:
    cfg = server.config
    return {
        "server_code": cfg.server_code,
        "description": cfg.description,
        "transport": cfg.transport.value,
        "url": cfg.url,
        "command": cfg.command,
        "args": list(cfg.args or []),
        "env": dict(cfg.env or {}),
        "headers": dict(cfg.headers or {}),
        "timeout_seconds": cfg.timeout_seconds,
        "enabled": cfg.enabled,
    }


# ── Endpoints ──


@router.get("", response_model=ApiResponse)
async def list_mcp_servers() -> ApiResponse:
    warning = check_capability(Capability.MCP_LIST)
    try:
        plugin = _mcp_plugin()
        servers = await plugin.list_servers()
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e

    items = [_server_to_dict(s) for s in servers]
    return ApiResponse(
        success=True,
        data={"servers": items, "total": len(items)},
        warning=warning,
    )


@router.get("/{server_code}", response_model=ApiResponse)
async def get_mcp_server(server_code: str) -> ApiResponse:
    warning = check_capability(Capability.MCP_LIST)
    try:
        plugin = _mcp_plugin()
        server = await plugin.get_server(server_code)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e

    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP Server 不存在: {server_code}")
    return ApiResponse(success=True, data=_server_to_dict(server), warning=warning)


@router.post("", response_model=ApiResponse)
async def create_mcp_server(request: MCPServerCreateRequest) -> ApiResponse:
    warning = check_capability(Capability.MCP_CREATE)
    config = _config_from_create(request)
    try:
        plugin = _mcp_plugin()
        server = await plugin.create_server(config)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"创建失败: {e}") from e

    return ApiResponse(
        success=True,
        data=_server_to_dict(server),
        message="创建成功",
        warning=warning,
    )


@router.put("/{server_code}", response_model=ApiResponse)
async def update_mcp_server(
    server_code: str, request: MCPServerUpdateRequest,
) -> ApiResponse:
    warning = check_capability(Capability.MCP_UPDATE)
    try:
        plugin = _mcp_plugin()
        existing = await plugin.get_server(server_code)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"MCP Server 不存在: {server_code}")
        merged = _patch_config(existing.config, request)
        server = await plugin.update_server(server_code, merged)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"更新失败: {e}") from e

    return ApiResponse(
        success=True,
        data=_server_to_dict(server),
        message="更新成功",
        warning=warning,
    )


@router.delete("/{server_code}", response_model=ApiResponse)
async def delete_mcp_server(server_code: str) -> ApiResponse:
    warning = check_capability(Capability.MCP_DELETE)
    try:
        plugin = _mcp_plugin()
        deleted = await plugin.delete_server(server_code)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e

    if not deleted:
        raise HTTPException(status_code=404, detail=f"MCP Server 不存在: {server_code}")
    return ApiResponse(success=True, message="删除成功", warning=warning)


@router.post("/{server_code}/start", response_model=ApiResponse)
async def start_mcp_server(
    server_code: str, request: MCPServerOperationRequest | None = None,
) -> ApiResponse:
    warning = check_capability(Capability.MCP_START)
    try:
        plugin = _mcp_plugin()
        ok = await plugin.start_server(server_code)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e

    if not ok:
        raise HTTPException(status_code=501, detail="Not Implemented")
    return ApiResponse(success=True, message="启动成功", warning=warning)


@router.post("/{server_code}/stop", response_model=ApiResponse)
async def stop_mcp_server(
    server_code: str, request: MCPServerOperationRequest | None = None,
) -> ApiResponse:
    warning = check_capability(Capability.MCP_STOP)
    try:
        plugin = _mcp_plugin()
        ok = await plugin.stop_server(server_code)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e

    if not ok:
        raise HTTPException(status_code=501, detail="Not Implemented")
    return ApiResponse(success=True, message="停止成功", warning=warning)


@router.post("/{server_code}/restart", response_model=ApiResponse)
async def restart_mcp_server(
    server_code: str, request: MCPServerOperationRequest | None = None,
) -> ApiResponse:
    warning = check_capability(Capability.MCP_STOP)
    try:
        plugin = _mcp_plugin()
        ok = await plugin.restart_server(server_code)
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e

    if not ok:
        raise HTTPException(status_code=501, detail="Not Implemented")
    return ApiResponse(success=True, message="重启成功", warning=warning)


@router.post("/call-tool", response_model=ApiResponse)
async def call_mcp_tool(request: MCPCallToolRequest) -> ApiResponse:
    warning = check_capability(Capability.MCP_TOOLS_CALL)
    log.info("[mcp.call-tool] tool=%s args=%s", request.tool, request.args)
    try:
        plugin = _mcp_plugin()
        arguments: dict[str, str] = {}
        for arg in request.args:
            if "=" in arg:
                k, v = arg.split("=", 1)
                arguments[k] = v
        result = await plugin.call_tool(
            MCPToolCallRequest(
                tool_name=request.tool,
                arguments=arguments,
                server_code=None,
            )
        )
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail=f"mcporter 执行超时: {e}") from e
    except RuntimeError as e:
        log.error("[mcp.call-tool] RuntimeError: tool=%s error=%s", request.tool, e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        log.error("[mcp.call-tool] Unexpected error: tool=%s error=%s", request.tool, e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    content_text = ""
    is_error = result.is_error
    for item in result.content:
        if isinstance(item, dict) and item.get("type") == "text":
            content_text += item.get("text", "")

    log.info(
        "[mcp.call-tool] result: tool=%s is_error=%s content_len=%d",
        result.tool_name, is_error, len(content_text),
    )

    return ApiResponse(
        success=not is_error,
        data={
            "tool_name": result.tool_name,
            "server_code": result.server_code,
            "content": content_text,
            "is_error": is_error,
        },
        warning=warning,
    )


@router.post("/filter-servers", response_model=ApiResponse)
async def filter_mcp_servers(request: MCPFilterServersRequest) -> ApiResponse:
    warning = check_capability(Capability.MCP_FILTER_SERVERS)
    try:
        plugin = _mcp_plugin()
        result = await plugin.filter_servers(
            MCPFilterRequest(
                server_codes=list(request.server_codes or []),
                timeout_seconds=int(request.timeout_seconds or 30),
            )
        )
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail=f"命令执行超时: {e}") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data={
            "command": list(result.command),
            "server_codes": list(result.server_codes),
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
        message="执行成功",
        warning=warning,
    )
