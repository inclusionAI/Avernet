"""Resources group — ``/openapi/v1/resources`` (definition only).

A unified abstraction over files and links (a Yuque doc is a ``link`` resource);
the storage location is never exposed. Handlers are stubs; every route requires
an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal

from .schemas import Preview, Resource, ResourceCreate, ResourceType, ResourceUpdate

router = APIRouter(prefix="/openapi/v1/bots/resources", tags=["resources"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get("", response_model=Envelope[Page[Resource]])
async def list_resources(
    page: PageParamsDep,
    principal: PrincipalDep,
    bot_id: str | None = None,
    type: ResourceType | None = None,
) -> Envelope[Page[Resource]]:
    """List resources (filter + paginate)."""
    raise NotImplementedError


@router.get("/check-name", response_model=Envelope[NameCheck])
async def check_resource_name(
    name: str, principal: PrincipalDep
) -> Envelope[NameCheck]:
    """Check whether a resource name is available."""
    raise NotImplementedError


@router.post("", status_code=201, response_model=Envelope[Resource])
async def create_resource(
    body: ResourceCreate, principal: PrincipalDep
) -> Envelope[Resource]:
    """Create a resource (file placeholder, link, or folder)."""
    raise NotImplementedError


@router.post("/upload", status_code=201, response_model=Envelope[Resource])
async def upload_resource(
    principal: PrincipalDep,
    name: str,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> Envelope[Resource]:
    """Upload a file's raw bytes as a new resource."""
    raise NotImplementedError


@router.get("/{resource_id}", response_model=Envelope[Resource])
async def get_resource(resource_id: str, principal: PrincipalDep) -> Envelope[Resource]:
    """Get a resource."""
    raise NotImplementedError


@router.put("/{resource_id}", response_model=Envelope[Resource])
async def update_resource(
    resource_id: str, body: ResourceUpdate, principal: PrincipalDep
) -> Envelope[Resource]:
    """Update a resource."""
    raise NotImplementedError


@router.delete("/{resource_id}", response_model=Envelope[Deleted])
async def delete_resource(
    resource_id: str, principal: PrincipalDep
) -> Envelope[Deleted]:
    """Delete a resource."""
    raise NotImplementedError


@router.get(
    "/{resource_id}/download",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_resource(resource_id: str, principal: PrincipalDep) -> Response:
    """Download a resource's bytes (raw, not enveloped)."""
    raise NotImplementedError


@router.get("/{resource_id}/preview", response_model=Envelope[Preview])
async def preview_resource(
    resource_id: str, principal: PrincipalDep
) -> Envelope[Preview]:
    """Get a resource preview."""
    raise NotImplementedError
