"""Expert discovery HTTP translation."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
    resolve_avernet_tenant,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.expert_catalog_service import ExpertCatalogServiceProtocol
from agentclaw.community.di import Injected
from .schemas import ExpertDetail, ExpertSummary

router = APIRouter(
    prefix="/openapi/v1/experts",
    tags=["experts"],
    route_class=PublicAPIRoute,
    dependencies=[Depends(require_principal)],
)
Tenant = Annotated[str, Depends(resolve_avernet_tenant)]


@router.get("", response_model=Envelope[Page[ExpertSummary]])
@envelope_errors
async def list_experts(
    request: Request,
    tenant: Tenant,
    keyword: Annotated[
        str,
        Query(
            max_length=200,
            description="Literal name, description or tag substring; whitespace is trimmed.",
        ),
    ] = "",
    category: Annotated[
        str | None,
        Query(
            max_length=40,
            description="Single category combined with the keyword filter.",
        ),
    ] = None,
    sort: Annotated[
        Literal["hot", "newest"],
        Query(
            description="hot orders historical creation counts; newest orders first registration."
        ),
    ] = "hot",
    page_num: Annotated[int, Query(ge=1, description="One-based page number.")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Maximum number of items per page.")
    ] = 20,
    service: ExpertCatalogServiceProtocol = Injected(ExpertCatalogServiceProtocol),
) -> Envelope[Page[ExpertSummary]]:
    """Search the current tenant's enabled expert templates."""
    total, items = service.search(
        tenant=tenant,
        keyword=keyword,
        category=category,
        sort=sort,
        page_num=page_num,
        page_size=page_size,
    )
    return page(total, [ExpertSummary.model_validate(item) for item in items], request)


@router.get("/categories", response_model=Envelope[list[str]])
@envelope_errors
async def expert_categories(
    request: Request,
    tenant: Tenant,
    service: ExpertCatalogServiceProtocol = Injected(ExpertCatalogServiceProtocol),
) -> Envelope[list[str]]:
    """List all categories in the current visible catalogue."""
    return envelope(service.categories(tenant=tenant), request)


@router.get("/{expert_id}", response_model=Envelope[ExpertDetail])
@envelope_errors
async def expert_detail(
    request: Request,
    tenant: Tenant,
    expert_id: Annotated[
        str,
        Path(
            max_length=64,
            description="Stable identifier in the configured expert source.",
        ),
    ],
    service: ExpertCatalogServiceProtocol = Injected(ExpertCatalogServiceProtocol),
) -> Envelope[ExpertDetail]:
    """Read an enabled expert template's display content."""
    return envelope(
        ExpertDetail.model_validate(service.get(tenant=tenant, expert_id=expert_id)),
        request,
    )
