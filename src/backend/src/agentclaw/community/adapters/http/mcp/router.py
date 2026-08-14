"""MCP API router — facade layer over core/mcp/services."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.mcp.schemas import (
    MCPApplyPermissionRequest,
    MCPApplyPermissionResponse,
    MCPDetailResponse,
    MCPListResponse,
    MCPPermissionResponse,
    MCPSyncResult,
    MCPUnifiedConfigData,
    MCPUnifiedConfigRequest,
    MCPUnifiedConfigResponse,
    TenantListResponse,
)
from agentclaw.community.api.mcp_auth_service import MCPAuthServiceProtocol
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.mcp.config_flow import (
    read_unified_config,
    write_unified_config,
)
from agentclaw.community.core.mcp.errors import (
    McpConfigValueError,
    McpHeadersInvalidError,
    McpServerNotFoundError,
    McpSyncFailedError,
)
from agentclaw.community.core.mcp.presentation import (
    ALLOWED_NETWORK_TYPES,
    is_network_type_visible,
    mask_api_key,
    strip_ext_info,
    strip_ext_info_from_list,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


router = APIRouter(prefix="/api/mcp", tags=["mcp"])
logger = get_logger()


# ==================== Helper Functions ====================

def _get_path_params(
    user: AuthenticatedUser,
    default_bot_id: str,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    bot_id: Optional[str] = None,
) -> tuple:
    """Get effective path parameters.

    Priority:
    1. If entity_id provided: use it directly (pure ID, no prefix needed)
    2. Otherwise: use user.id
    3. If entity_type provided: use it directly
    4. Otherwise: default to "staff"
    """
    effective_entity_id = entity_id if entity_id else (user.staffId if user.staffId else "default")
    effective_entity_type = entity_type if entity_type else "staff"
    effective_bot_id = bot_id if bot_id else (default_bot_id if default_bot_id else "default")
    return effective_entity_id, effective_bot_id, effective_entity_type


# ==================== MCP Market APIs ====================

@router.get("/market/list", response_model=MCPListResponse)
async def list_mcp_servers(
    page_num: int = Query(1, ge=1, description="当前页码"),
    page_size: int = Query(10, ge=1, le=100, description="分页大小"),
    search_key: Optional[str] = Query(None, description="搜索条件，支持名称、serverCode模糊搜索"),
    server_codes: Optional[list[str]] = Query(None, description="serverCode列表"),
    platform_server_codes: Optional[list[str]] = Query(None, description="平台serverCode列表"),
    run_modes: Optional[list[str]] = Query(None, description="运行方式列表"),
    statuses: Optional[list[str]] = Query(None, description="状态列表"),
    transport_protocols: Optional[list[str]] = Query(None, description="传输协议列表"),
    host_platforms: Optional[list[str]] = Query(None, description="平台列表"),
    owners: Optional[list[str]] = Query(None, description="负责人工号列表"),
    network_types: Optional[list[str]] = Query(None, description="网络类型列表，默认只允许 INTERNET/OFFICE"),
    categories: Optional[list[str]] = Query(None, description="类目列表"),
    tenants: Optional[list[str]] = Query(None, description="租户列表"),
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> MCPListResponse:
    """Get MCP list from market."""
    allowed_network_types = list(ALLOWED_NETWORK_TYPES)
    if network_types:
        filtered = [t for t in network_types if t in allowed_network_types]
        if not filtered:
            return MCPListResponse(success=True, data=[], total=0, page_num=page_num, page_size=page_size)
        effective_network_types = filtered
    else:
        effective_network_types = allowed_network_types

    result = market_service.get_mcp_list(
        page_num=page_num,
        page_size=page_size,
        search_key=search_key,
        server_codes=server_codes,
        platform_server_codes=platform_server_codes,
        run_modes=run_modes,
        statuses=statuses,
        transport_protocols=transport_protocols,
        host_platforms=host_platforms,
        owners=owners,
        network_types=effective_network_types,
        categories=categories,
        tenants=tenants,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "Failed to fetch MCP list"))
    return MCPListResponse(**strip_ext_info_from_list(result))


@router.get("/market/detail", response_model=MCPDetailResponse)
async def get_mcp_detail(
    request: Request,
    server_code: str = Query(..., description="MCP server code"),
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
    auth_service: MCPAuthServiceProtocol = Injected(MCPAuthServiceProtocol),
) -> MCPDetailResponse:
    """Get single MCP detail.

    If the caller provides an IAM token (via ``IAM_TOKEN`` cookie),
    backend will attempt to fetch fresh ``tools/list`` directly from the
    MCP server endpoint.  If the live fetch fails for any reason (auth,
    network, no endpoint), the response falls back to MCP Center data
    unchanged.
    """
    iam_token = request.cookies.get("IAM_TOKEN") or request.cookies.get("iam_token")

    access_token = None
    if iam_token:
        access_token = auth_service.exchange_iam_token(iam_token)

    detail = market_service.get_mcp_detail(server_code, access_token=access_token)
    if not detail:
        raise HTTPException(status_code=404, detail="MCP server not found")

    if not is_network_type_visible(detail):
        raise HTTPException(status_code=404, detail="MCP server not found")

    return MCPDetailResponse(success=True, data=strip_ext_info(detail))


@router.get("/market/permission", response_model=MCPPermissionResponse)
async def check_mcp_permission(
    server_code: str = Query(..., description="MCP server code"),
    user_id: str = Query(..., description="User ID"),
    auth_service: MCPAuthServiceProtocol = Injected(MCPAuthServiceProtocol),
) -> MCPPermissionResponse:
    """Check user permission for MCP server and tools."""
    result = auth_service.check_mcp_permission_detail(user_id, server_code)
    return MCPPermissionResponse(
        success=True,
        has_permission=result["has_permission"],
        access_level=result.get("access_level"),
        tool_permissions=result.get("tool_permissions"),
    )


@router.post("/market/permission/apply", response_model=MCPApplyPermissionResponse)
async def apply_mcp_permission(
    request: MCPApplyPermissionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    auth_service: MCPAuthServiceProtocol = Injected(MCPAuthServiceProtocol),
) -> MCPApplyPermissionResponse:
    """申请 MCP Server 或 Tool 的权限。"""
    try:
        result = auth_service.apply_permission(
            staff_no=user.staffId,
            service_code=request.server_code,
            tool_list=request.tool_list,
            is_public=request.is_public,
            reason=request.reason,
        )
        return MCPApplyPermissionResponse(
            success=result.get("success", False),
            server_code=request.server_code,
            process_url=result.get("process_url"),
            error=result.get("error"),
        )
    except Exception as e:
        logger.exception("[mcp] apply_mcp_permission failed")
        return MCPApplyPermissionResponse(
            success=False,
            server_code=request.server_code,
            process_url=None,
            error=str(e),
        )


# ==================== Tenant APIs ====================

@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(
    tenant_code: Optional[str] = Query(None, description="租户编码"),
    arch_domain_code: Optional[str] = Query(None, description="架构域编码"),
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> TenantListResponse:
    """获取租户列表。"""
    result = market_service.get_tenant_list(
        tenant_code=tenant_code,
        arch_domain_code=arch_domain_code,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "Failed to fetch tenant list"))
    return TenantListResponse(success=True, data=result.get("data", []))


# ==================== Unified MCP Config APIs ====================

@router.post("/user/config", response_model=MCPUnifiedConfigResponse)
async def update_mcp_unified_config(
    request: MCPUnifiedConfigRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    default_bot_id: Optional[str] = Query("default", description="Default Bot ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    config_service: MCPConfigServiceProtocol = Injected(MCPConfigServiceProtocol),
    sync_service: MCPSyncServiceProtocol = Injected(MCPSyncServiceProtocol),
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> MCPUnifiedConfigResponse:
    """更新MCP统一配置并同步到设备。

    Validation, the write→push→rollback sequence, and masking now live in
    ``core/mcp/config_flow.write_unified_config`` so this surface and the public
    ``/openapi/v1`` surface share one implementation. This handler is the thin
    adapter: it maps the flow's typed errors back onto the exact
    ``HTTPException`` bodies this route has always returned, and shapes the
    response identically (headers not echoed on the write path; ``sync_results``
    mapped through :class:`MCPSyncResult`).
    """
    try:
        effective_entity_id, _effective_bot_id, effective_entity_type = _get_path_params(
            user, default_bot_id, entity_id, entity_type, bot_id
        )

        result = await write_unified_config(
            user_id=user.staffId,
            server_code=request.server_code,
            entity_id=effective_entity_id,
            entity_type=effective_entity_type,
            api_key=request.api_key,
            headers=request.headers,
            endpoint_env=request.endpoint_env,
            transport_protocol=request.transport_protocol,
            config_service=config_service,
            market_service=market_service,
            sync_service=sync_service,
        )

        sync_results = None
        if result.sync_results is not None:
            sync_results = [
                MCPSyncResult(
                    conn_info=r.get("conn_info"),
                    bot_id=r.get("bot_id"),
                    synced=r.get("synced", False),
                    reason=r.get("reason"),
                    error=r.get("error"),
                )
                for r in result.sync_results
            ]

        return MCPUnifiedConfigResponse(
            success=True,
            message="MCP config updated and synced to all devices",
            data=MCPUnifiedConfigData(
                server_code=result.server_code,
                api_key=result.api_key,
                endpoint_env=result.endpoint_env,
                transport_protocol=result.transport_protocol,
                headers=result.headers,
                has_config=result.has_config,
                sync_results=sync_results,
            ),
        )
    except HTTPException:
        raise
    except (McpConfigValueError, McpHeadersInvalidError) as e:
        # Both were 400s with the flow's own message before extraction.
        raise HTTPException(status_code=400, detail=str(e))
    except McpServerNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"MCP server {request.server_code} not found"
        )
    except McpSyncFailedError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[update_mcp_unified_config] Error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/config", response_model=MCPUnifiedConfigResponse)
async def get_mcp_unified_config(
    server_code: str = Query(..., description="MCP Server Code"),
    user: AuthenticatedUser = Depends(get_current_user),
    config_service: MCPConfigServiceProtocol = Injected(MCPConfigServiceProtocol),
) -> MCPUnifiedConfigResponse:
    """获取MCP统一配置。

    Delegates to ``read_unified_config`` for the read + masking; the message
    keys off whether a stored row exists (``exists``), not ``has_config`` — a row
    can exist while carrying no api_key/headers/transport, and it read
    "Config retrieved" before extraction.
    """
    config = read_unified_config(
        user_id=user.staffId, server_code=server_code, config_service=config_service
    )
    return MCPUnifiedConfigResponse(
        success=True,
        message="Config retrieved" if config.exists else "No config found",
        data=MCPUnifiedConfigData(
            server_code=config.server_code,
            api_key=config.api_key,
            headers=config.headers,
            endpoint_env=config.endpoint_env,
            transport_protocol=config.transport_protocol,
            has_config=config.has_config,
        ),
    )
