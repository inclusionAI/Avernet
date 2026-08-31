"""Unified public marketplace endpoints under ``/openapi/v1/bots/market``."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    USER_SCOPED_403,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.mcp.router import _to_server_detail
from agentclaw.community.adapters.http.openapi_v1.responses import (
    SkillCenterMarketplaceUnavailableError,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.skill_market_service import (
    SkillMarketSearchQuery,
    SkillMarketServiceProtocol,
)
from agentclaw.community.api.skill_center_gateway_service import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.api.skill_center_sync_service import (
    SkillCenterSyncServiceProtocol,
)
from agentclaw.community.core.mcp.config_flow import list_marketplace_servers
from agentclaw.community.core.mcp.presentation import ALLOWED_NETWORK_TYPES
from agentclaw.community.core.mcp.presentation import strip_ext_info
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterBelongTo,
    SkillCenterGatewayError,
    SkillCenterPublicSkillSearchRequest,
    SkillCenterSkill,
    SkillCenterSortOrder,
    SkillCenterTag as GatewaySkillCenterTag,
)

from .schemas import (
    McpMarketItem,
    McpMarketSearchRequest,
    SkillCenterMarketItem,
    SkillCenterMarketSearchRequest,
    SkillCenterTag as SkillCenterTagResponse,
    SkillCenterSyncFailure,
    SkillCenterSyncSummary,
    SkillMarketItem,
    SkillMarketSearchRequest,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(
    prefix="/openapi/v1/bots/market",
    tags=["market"],
    route_class=PublicAPIRoute,
)
_AUTH = [Depends(require_principal)]


def _to_skill_center_market_item(skill: SkillCenterSkill) -> SkillCenterMarketItem:
    optional_fields = {
        "creatorId": skill.creator_id,
        "latestVersionNumber": skill.latest_version_number,
        "officialVersionNumber": skill.official_version_number,
        "updatedAt": skill.updated_at,
        "iconUrl": skill.icon_url,
        "ownerName": skill.owner_name,
        "homepageUrl": skill.homepage_url,
        "officeDownloadUrl": skill.office_download_url,
        "intranetDownloadUrl": skill.intranet_download_url,
        "sha256": skill.sha256,
        "favoriteCount": skill.favorite_count,
        "downloadCount": skill.download_count,
        "isOfficial": skill.is_official,
        "isRecommended": skill.is_recommended,
        "isTest": skill.is_test,
        "antcodeUrl": skill.antcode_url,
    }
    payload: dict[str, object] = {
        "skillId": skill.skill_id,
        "skillCode": skill.skill_code,
        "skillName": skill.skill_name,
        "description": skill.description,
        "creatorName": skill.creator_name,
        "creatorWorkNo": skill.creator_work_no,
        "accessLevel": skill.access_level.value,
        "belongTo": skill.belong_to.value if skill.belong_to is not None else None,
        "tagList": list(skill.tags),
    }
    payload.update(
        {key: value for key, value in optional_fields.items() if value is not None}
    )
    if skill.network_types:
        payload["networkTypes"] = list(skill.network_types)
    return SkillCenterMarketItem.model_validate(payload)


def _to_skill_center_tag(tag: GatewaySkillCenterTag) -> SkillCenterTagResponse:
    return SkillCenterTagResponse.model_validate(
        {
            "id": int(tag.tag_id),
            "name": tag.name,
            "description": tag.description,
            "iconUrl": tag.icon_url,
            "parentId": int(tag.parent_id) if tag.parent_id is not None else None,
            "tagLevel": tag.level,
            "children": [_to_skill_center_tag(child) for child in tag.children],
        }
    )


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
    response_model_exclude_unset=True,
    dependencies=_AUTH,
)
@envelope_errors
async def search_mcp_servers(
    body: McpMarketSearchRequest,
    request: Request,
    service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> Envelope[Page[McpMarketItem]]:
    """Search the MCP marketplace with the legacy catalogue filters."""
    requested_network_types = body.network_types or list(ALLOWED_NETWORK_TYPES)
    effective_network_types = tuple(
        network_type
        for network_type in requested_network_types
        if network_type in ALLOWED_NETWORK_TYPES
    )
    if not effective_network_types:
        return page(0, [], request)

    result = list_marketplace_servers(
        page=body.page_num,
        page_size=body.page_size,
        keyword=body.keyword,
        network_types=effective_network_types,
        market_service=service,
        server_codes=body.server_codes,
        platform_server_codes=body.platform_server_codes,
        run_modes=body.run_modes,
        statuses=body.statuses,
        transport_protocols=body.transport_protocols,
        host_platforms=body.host_platforms,
        owners=body.owners,
        categories=body.categories,
        tenants=body.tenants,
        tags=body.tags,
    )
    items = [
        McpMarketItem.model_validate(
            _to_server_detail(strip_ext_info(item)).model_dump(exclude_unset=True)
        )
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
    service: SkillCenterGatewayServiceProtocol = Injected(
        SkillCenterGatewayServiceProtocol
    ),
) -> Envelope[Page[SkillCenterMarketItem]]:
    """Search public Skill Center Skills using server-managed credentials."""
    try:
        result = service.search_public_skills(
            SkillCenterPublicSkillSearchRequest(
                keyword=body.keyword,
                page_num=body.page_num,
                page_size=body.page_size,
                official_only=body.is_official,
                recommended_only=body.is_recommended,
                tags=tuple(body.tag_list),
                sort_by=SkillCenterSortOrder(body.sort_by),
                creator_name=body.creator_name,
                creator_work_no=body.creator_work_no,
                belong_to=(
                    SkillCenterBelongTo(body.belong_to)
                    if body.belong_to is not None
                    else None
                ),
            )
        )
    except SkillCenterGatewayError as exc:
        raise SkillCenterMarketplaceUnavailableError from exc
    items = [_to_skill_center_market_item(item) for item in result.items]
    return page(result.total, items, request)


@router.post(
    "/skill-center/sync",
    response_model=Envelope[SkillCenterSyncSummary],
    responses=USER_SCOPED_403,
    dependencies=_AUTH,
)
@envelope_errors
async def sync_materialized_skill_center_skills(
    request: Request,
    user_id: UserIdDep,
    service: SkillCenterSyncServiceProtocol = Injected(
        SkillCenterSyncServiceProtocol
    ),
) -> Envelope[SkillCenterSyncSummary]:
    """Synchronize only public Skill Center assets already materialized locally."""
    del user_id
    result = await asyncio.to_thread(service.sync)
    return envelope(
        SkillCenterSyncSummary(
            scanned=result.scanned,
            updated=result.updated,
            unchanged=result.unchanged,
            failed=result.failed,
            failures=[
                SkillCenterSyncFailure(
                    skill_id=item.skill_id,
                    skill_code=item.skill_code,
                    error_code=item.error_code,
                )
                for item in result.failures
            ],
        ),
        request,
    )


@router.get(
    "/skill-center/tags",
    response_model=Envelope[list[SkillCenterTagResponse]],
    dependencies=_AUTH,
)
@envelope_errors
async def list_skill_center_tags(
    request: Request,
    service: SkillCenterGatewayServiceProtocol = Injected(
        SkillCenterGatewayServiceProtocol
    ),
) -> Envelope[list[SkillCenterTagResponse]]:
    """List Skill Center tags for marketplace filter initialization."""
    try:
        result = service.list_public_tags()
    except SkillCenterGatewayError as exc:
        raise SkillCenterMarketplaceUnavailableError from exc
    items = [_to_skill_center_tag(item) for item in result]
    return envelope(items, request)
