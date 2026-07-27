"""Resources group — ``/openapi/v1/resources`` (definition only).

A unified abstraction over files and links (a Yuque doc is a ``link`` resource);
the storage location is never exposed. Handlers are stubs; every route requires
an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response

from gateway.community.adapters.web import require_identities
from gateway.community.adapters.web.contracts import (
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
    requires_user_principal,
)
from gateway.community.spi.authn import Identities

from ._schemas import Preview, Resource, ResourceCreate, ResourceType, ResourceUpdate

router = APIRouter(prefix="/openapi/v1/resources", tags=["resources"])

_SEC = requires_user_principal()
IdentitiesDep = Annotated[Identities, Depends(require_identities)]


@router.get("", response_model=Envelope[Page[Resource]], openapi_extra=_SEC)
async def list_resources(
    page: PageParamsDep,
    identities: IdentitiesDep,
    bot_id: str | None = None,
    type: ResourceType | None = None,
) -> Envelope[Page[Resource]]:
    """List resources (filter + paginate)."""
    raise NotImplementedError


@router.get("/check-name", response_model=Envelope[NameCheck], openapi_extra=_SEC)
async def check_resource_name(
    name: str, identities: IdentitiesDep
) -> Envelope[NameCheck]:
    """Check whether a resource name is available."""
    raise NotImplementedError


@router.post("", status_code=201, response_model=Envelope[Resource], openapi_extra=_SEC)
async def create_resource(
    body: ResourceCreate, identities: IdentitiesDep
) -> Envelope[Resource]:
    """Create a resource (file placeholder, link, or folder)."""
    raise NotImplementedError


@router.post(
    "/upload", status_code=201, response_model=Envelope[Resource], openapi_extra=_SEC
)
async def upload_resource(
    identities: IdentitiesDep,
    name: str,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> Envelope[Resource]:
    """Upload a file's raw bytes as a new resource."""
    raise NotImplementedError


@router.get("/{resource_id}", response_model=Envelope[Resource], openapi_extra=_SEC)
async def get_resource(
    resource_id: str, identities: IdentitiesDep
) -> Envelope[Resource]:
    """Get a resource."""
    raise NotImplementedError


@router.put("/{resource_id}", response_model=Envelope[Resource], openapi_extra=_SEC)
async def update_resource(
    resource_id: str, body: ResourceUpdate, identities: IdentitiesDep
) -> Envelope[Resource]:
    """Update a resource."""
    raise NotImplementedError


@router.delete("/{resource_id}", response_model=Envelope[Deleted], openapi_extra=_SEC)
async def delete_resource(
    resource_id: str, identities: IdentitiesDep
) -> Envelope[Deleted]:
    """Delete a resource."""
    raise NotImplementedError


@router.get(
    "/{resource_id}/download",
    openapi_extra=_SEC,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_resource(resource_id: str, identities: IdentitiesDep) -> Response:
    """Download a resource's bytes (raw, not enveloped)."""
    raise NotImplementedError


@router.get(
    "/{resource_id}/preview", response_model=Envelope[Preview], openapi_extra=_SEC
)
async def preview_resource(
    resource_id: str, identities: IdentitiesDep
) -> Envelope[Preview]:
    """Get a resource preview."""
    raise NotImplementedError
