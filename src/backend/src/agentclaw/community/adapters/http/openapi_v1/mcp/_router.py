"""MCP group — ``/openapi/v1/mcp`` (definition only).

MCP marketplace (list / detail / permission), tenants, and the caller's unified
per-server config. Handlers are stubs; every route requires an authenticated
user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.openapi_v1._deps import require_principal
from agentclaw.community.adapters.http.openapi_v1._contracts import (
    Envelope,
    Page,
    PageParamsDep,
    requires_user_principal,
)
from agentclaw.community.adapters.http.openapi_v1._deps import Principal

from ._schemas import (
    McpConfig,
    McpConfigWrite,
    McpPermission,
    McpServer,
    McpServerDetail,
    McpTenant,
)

router = APIRouter(prefix="/openapi/v1/bots/mcp", tags=["mcp"])

_SEC = requires_user_principal()
PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get("/servers", response_model=Envelope[Page[McpServer]], openapi_extra=_SEC)
async def list_mcp_servers(
    page: PageParamsDep, principal: PrincipalDep, keyword: str | None = None
) -> Envelope[Page[McpServer]]:
    """List marketplace MCP servers (filter + paginate)."""
    raise NotImplementedError


@router.get("/tenants", response_model=Envelope[list[McpTenant]], openapi_extra=_SEC)
async def list_mcp_tenants(principal: PrincipalDep) -> Envelope[list[McpTenant]]:
    """List MCP tenants."""
    raise NotImplementedError


@router.get(
    "/servers/{server_code}",
    response_model=Envelope[McpServerDetail],
    openapi_extra=_SEC,
)
async def get_mcp_server(
    server_code: str, principal: PrincipalDep
) -> Envelope[McpServerDetail]:
    """Get an MCP server's detail."""
    raise NotImplementedError


@router.get(
    "/servers/{server_code}/permissions",
    response_model=Envelope[McpPermission],
    openapi_extra=_SEC,
)
async def check_mcp_permission(
    server_code: str, principal: PrincipalDep
) -> Envelope[McpPermission]:
    """Check the caller's permission for an MCP server."""
    raise NotImplementedError


@router.get(
    "/servers/{server_code}/config",
    response_model=Envelope[McpConfig],
    openapi_extra=_SEC,
)
async def get_mcp_config(
    server_code: str, principal: PrincipalDep
) -> Envelope[McpConfig]:
    """Read the caller's unified config for an MCP server."""
    raise NotImplementedError


@router.put(
    "/servers/{server_code}/config",
    response_model=Envelope[McpConfig],
    openapi_extra=_SEC,
)
async def update_mcp_config(
    server_code: str, body: McpConfigWrite, principal: PrincipalDep
) -> Envelope[McpConfig]:
    """Write the caller's unified config for an MCP server (pushed to devices)."""
    raise NotImplementedError
