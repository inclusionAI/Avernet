"""Unified public marketplace endpoints under ``/openapi/v1/bots/market``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.mcp.router import _to_server
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.skill_market_service import (
    SkillMarketSearchQuery,
    SkillMarketServiceProtocol,
)
from agentclaw.community.core.mcp.config_flow import list_marketplace_servers
from agentclaw.community.core.mcp.presentation import ALLOWED_NETWORK_TYPES
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
    SkillCenterMarketSearchRequest as SkillCenterPluginSearchRequest,
)

from .schemas import (
    McpMarketItem,
    McpMarketSearchRequest,
    SkillCenterMarketItem,
    SkillCenterMarketSearchRequest,
    SkillCenterTag,
    SkillMarketItem,
    SkillMarketSearchRequest,
)

router = APIRouter(prefix="/openapi/v1/bots/market", tags=["market"])
_AUTH = [Depends(require_principal)]


@router.post(
    "/skills",
    response_model=Envelope[Page[SkillMarketItem]],
    dependencies=_AUTH,
)
@envelope_errors
async def search_skills(
    body: SkillMarketSearchRequest,
    request: Request,
    service: SkillMarketServiceProtocol = Injected(SkillMarketServiceProtocol),
) -> Envelope[Page[SkillMarketItem]]:
    """Search the built-in Skill marketplace."""
    result = service.search(
        SkillMarketSearchQuery(
            keyword=body.keyword,
            page_num=body.page_num,
            page_size=body.page_size,
        )
    )
    items = [SkillMarketItem.model_validate(item) for item in result.items]
    return page(result.total, items, request)


@router.post(
    "/mcp-servers",
    response_model=Envelope[Page[McpMarketItem]],
    dependencies=_AUTH,
)
@envelope_errors
async def search_mcp_servers(
    body: McpMarketSearchRequest,
    request: Request,
    service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> Envelope[Page[McpMarketItem]]:
    """Search the MCP marketplace without changing the existing MCP API."""
    result = list_marketplace_servers(
        page=body.page_num,
        page_size=body.page_size,
        keyword=body.keyword,
        network_types=ALLOWED_NETWORK_TYPES,
        market_service=service,
    )
    items = [
        McpMarketItem.model_validate(_to_server(item).model_dump())
        for item in (result.get("data") or [])
        if isinstance(item, dict)
    ]
    return page(int(result.get("total", len(items))), items, request)


@router.post(
    "/skill-center/skills",
    response_model=Envelope[Page[SkillCenterMarketItem]],
    dependencies=_AUTH,
)
@envelope_errors
async def search_skill_center_skills(
    body: SkillCenterMarketSearchRequest,
    request: Request,
    client: SkillCenterClient = Injected(SkillCenterClient),
) -> Envelope[Page[SkillCenterMarketItem]]:
    """Search public Skill Center Skills using server-managed credentials."""
    result = client.search_market_skills(
        SkillCenterPluginSearchRequest(
            keyword=body.keyword,
            page_num=body.page_num,
            page_size=body.page_size,
            is_official=body.is_official,
            is_recommended=body.is_recommended,
            tag_list=tuple(body.tag_list),
            sort_by=body.sort_by,
            creator_name=body.creator_name,
            creator_work_no=body.creator_work_no,
            team_id=None,
            access_level="PUBLIC",
            belong_to=body.belong_to,
        )
    )
    items = [SkillCenterMarketItem.model_validate(item) for item in result.items]
    return page(result.total, items, request)


@router.get(
    "/skill-center/tags",
    response_model=Envelope[list[SkillCenterTag]],
    dependencies=_AUTH,
)
@envelope_errors
async def list_skill_center_tags(
    request: Request,
    client: SkillCenterClient = Injected(SkillCenterClient),
) -> Envelope[list[SkillCenterTag]]:
    """List Skill Center tags for marketplace filter initialization."""
    result = client.get_market_tags()
    items = [SkillCenterTag.model_validate(item) for item in result]
    return envelope(items, request)
