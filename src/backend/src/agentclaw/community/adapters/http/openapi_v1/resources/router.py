"""Resources group — ``/openapi/v1/bots/resources``.

A unified abstraction over files and links (a Yuque doc is a ``link`` resource);
the storage location is never exposed. All 9 handlers are wired to the slim
``core/resources/service.py`` ``ResourceService`` via ``ResourceServiceFactory``;
no legacy router private helper is imported (arch Rule 7 — thin adapter).

⚠️ STATUS: definition-only / NOT PUBLIC-READY. The handlers are wired to the
slim ``ResourceService`` and exercise the real service at the integration level,
but this surface is gated on the auth workstream before it is exposed to any
external tenant: ``require_principal`` is still a ``None`` stub, so the gateway's
signed-Principal seam is not in place yet. Do NOT expose to external callers
until that lands (see ``openapi_v1/dependencies.py`` and the cross-team tenant
isolation track in ``src/backend/docs/openapi-v1/README.zh-CN.md``).

Gates / follow-ups (block public-readiness, NOT a silent deployment):
- Owner/identity comes from ``caller_owner_id(principal)`` (fail-closed: a
  ``None`` principal raises ``MissingPrincipalError`` → 401), mirroring the
  bots router. The gateway's signed-Principal seam is the single replaceable
  point — when it lands, only ``principal.py``/``dependencies.py`` change.
- Cross-tenant isolation rides on the ac_bots guard (Phase 0); a deployed DDL
  for ``ac_resource.avernet_tenant`` MUST precede this code reaching prod (see
  Phase 0 plan) — code first / DDL later breaks bot reads with a missing column.
- device_fs resolution lives in the adapter (Rule 7 transport concern); a
  service owns the read/write via the opaque ``device_fs`` argument.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted as deleted_envelope,
    envelope,
    page as page_envelope,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
    DeviceFilesystemDispatcher,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.bot_repository import BotRepository

from .schemas import Preview, Resource, ResourceCreate, ResourceType, ResourceUpdate

# ── openapi_v1 envelope + field-mapping helpers (Phase 1) ───────────
from agentclaw.community.core.resources.models import Resource as _LegacyResource
from agentclaw.community.core.resources.models import ResourceType as _LegacyType

logger = get_logger()


# legacy ResourceType → openapi ResourceType. R1a: legacy URL 归并进 openapi LINK.
# NODE/DATABASE/API 不在 openapi 契约 —— 读路径若出现,退回 LINK(读 list 不该报错);
# create 路径本期只接 LINK,不会经过这里。(legacy 无 FOLDER 枚举值,故无 mapping。)
_TYPE_MAP: dict[_LegacyType, ResourceType] = {
    _LegacyType.FILE: ResourceType.FILE,
    _LegacyType.LINK: ResourceType.LINK,
    _LegacyType.URL: ResourceType.LINK,
}

# openapi ResourceType → legacy ResourceType. R1a: openapi LINK 归并 legacy URL +
# LINK —— filter 侧本期只匹配 LINK(URL fan-out is a follow-up, see
# `_legacy_type_for` callers). openapi FOLDER has no legacy equivalent (no row
# in the legacy enum), so it's intentionally absent: list filters to empty and
# create raises 501 — they never reach the service with FOLDER.
_OPENAPI_TO_LEGACY_TYPE: dict[ResourceType, _LegacyType] = {
    ResourceType.FILE: _LegacyType.FILE,
    ResourceType.LINK: _LegacyType.LINK,
}


def _legacy_type_for(openapi_type: ResourceType | None) -> _LegacyType | None:
    """Map an openapi ResourceType to the legacy ResourceType the slim service expects.

    Returns None when:
    - ``openapi_type`` is None (no filter — caller passes None through), or
    - ``openapi_type`` is FOLDER: there is no legacy equivalent (FOLDER is an
      openapi-only type with no backing enum value). Callers that must NOT
      fall through to "no filter" (list_resources) distinguish these two None
      cases via the ``type`` argument directly — see list_resources handler.
    """
    if openapi_type is None:
        return None
    return _OPENAPI_TO_LEGACY_TYPE.get(openapi_type)


def _to_openapi_resource(legacy: _LegacyResource) -> Resource:
    """Map a legacy domain Resource → public openapi Resource schema.

    Flattens type-specific attributes (url/size) to top-level fields and never
    exposes the storage location. Per arch Rule 7 (mapping = protocol concern).
    """
    return Resource(
        resource_id=str(legacy.id) if legacy.id is not None else "",
        name=legacy.name or "",
        type=_TYPE_MAP.get(legacy.resource_type, ResourceType.LINK),
        source=legacy.source,
        url=legacy.url,
        size=legacy.size if legacy.resource_type == _LegacyType.FILE else None,
        gmt_create=legacy.gmt_created.isoformat() if legacy.gmt_created else "",
        gmt_modified=legacy.gmt_modified.isoformat() if legacy.gmt_modified else "",
    )


router = APIRouter(prefix="/openapi/v1/bots/resources", tags=["resources"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get("", response_model=Envelope[Page[Resource]])
async def list_resources(
    page: PageParamsDep,
    principal: PrincipalDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    type: ResourceType | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Page[Resource]]:
    """List resources (filter + paginate)."""
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # Map openapi ResourceType → legacy ResourceType enum the slim service
    # expects (it does ``.value`` internally). ``None`` (no filter) means
    # "all types". BUT if the caller passes type=FOLDER (an openapi type with
    # no legacy equivalent), we MUST NOT fall through to None-=="all types" —
    # that would leak every resource under a FOLDER filter. Explicit FOLDER →
    # empty page (no legacy folders exist).
    legacy_type = _legacy_type_for(type)
    if type is not None and legacy_type is None:
        # type is an openapi type with no legacy mapping (FOLDER) → no rows
        openapi_items: list[Resource] = []
    else:
        items = service.list_resources(resource_type=legacy_type)
        openapi_items = [_to_openapi_resource(r) for r in items]
    start = (page.page - 1) * page.page_size
    end = start + page.page_size
    page_items = openapi_items[start:end]
    return page_envelope(len(openapi_items), page_items, request)


@router.get("/check-name", response_model=Envelope[NameCheck])
async def check_resource_name(
    name: str,
    principal: PrincipalDep,
    request: Request,
    type: ResourceType | None = None,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[NameCheck]:
    """Check whether a resource name is available.

    ``parent_path`` / ``user_id`` / ``exclude_id`` from the legacy signature
    are intentionally not exposed on the openapi contract yet — Direction A
    (principal → user_id) will wire ``user_id`` once it lands. For now the
    service treats them as None.
    """
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # Map openapi ResourceType → legacy ResourceType enum the slim service
    # expects. When the caller omits ``type``, default to FILE (matches the
    # legacy handler's most common case — the openapi check-name call shape
    # has no FOLDER equivalent).
    legacy_type = _legacy_type_for(type) or _LegacyType.FILE
    # owner_id from the verified principal (fail-closed via caller_owner_id);
    # parent_path stays None — the openapi check-name contract has no
    # parent_path concept (resources are bot-scoped only). The slim service
    # signature REQUIRES both keyword args (no defaults), so pass them
    # explicitly.
    owner_id = caller_owner_id(principal)
    exists = await service.check_name_exists(
        name=name,
        resource_type=legacy_type,
        parent_path=None,
        user_id=owner_id,
    )
    return envelope(NameCheck(name=name, exists=exists), request)


@router.post("", status_code=201, response_model=Envelope[Resource])
async def create_resource(
    body: ResourceCreate,
    principal: PrincipalDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Create a resource (file placeholder, link, or folder).

    Phase 1: only LINK supported. FILE → use POST /upload (Phase 3);
    FOLDER → create_directory (Phase 3, device_fs branch). Duplicate name
    surfaces as ValueError from the service → 409 Conflict (legacy parity).
    """
    if body.type == ResourceType.FILE:
        raise HTTPException(
            status_code=400,
            detail="Use POST /openapi/v1/bots/resources/upload for file resources",
        )
    if body.type == ResourceType.FOLDER:
        raise HTTPException(
            status_code=501,
            detail="Create folder not supported yet (Phase 3)",
        )
    if not body.url:
        raise HTTPException(
            status_code=400, detail="url is required for link resources"
        )

    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    try:
        r = await service.create_url_resource(
            name=body.name,
            url=body.url,
            # parent_path intentionally NOT forwarded: the openapi ResourceCreate
            # schema carries `parent_id` (a pending follow-up — its ID-vs-path
            # semantics aren't settled). Passing a half-defined value would risk
            # a wrong-attribute write; link scoping by bot_id is sufficient now.
            parent_path=None,
        )
    except ValueError as e:
        # service raises ValueError on duplicate name → 409 Conflict (legacy
        # parity: adapters/http/resources/router.py create_url_resource).
        raise HTTPException(status_code=409, detail=str(e)) from e
    return created(_to_openapi_resource(r), request)


@router.post("/upload", status_code=201, response_model=Envelope[Resource])
async def upload_resource(
    principal: PrincipalDep,
    name: str,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Envelope[Resource]:
    """Upload a file's raw bytes as a new resource.

    device_fs is resolved per-bot (Task 2 paradigm); upload_file is async and
    raises ValueError on duplicate name → 409 Conflict (legacy parity with
    create). device_fs operations are delivered through the
    dispatcher-resolved boundary directly. owner_id comes from the verified
    principal (``caller_owner_id``), fail-closed — mirroring the bots router.
    """
    effective_bot_id = bot_id
    owner_id = caller_owner_id(principal)
    ctx = resolver.resolve_for_bot(effective_bot_id, owner_id)
    device_fs = device_fs_dispatcher.dispatch(ctx)
    service = factory.create(bot_id=effective_bot_id)
    try:
        r = await service.upload_file(
            data=content,
            filename=name,
            user_id=owner_id,
            device_fs=device_fs,
        )
    except ValueError as e:
        # duplicate name → 409 Conflict (legacy + create parity).
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        # device_fs.write_file failure → 502 Bad Gateway. The slim service lets
        # the write exception bubble (no longer swallowed) so the handler is the
        # single translation point: file write failed, no DB record created.
        logger.exception("[upload_resource] device_fs write failed")
        raise HTTPException(
            status_code=502, detail=f"Upload storage failed: {e}"
        ) from e
    return created(_to_openapi_resource(r), request)


@router.get("/{resource_id}", response_model=Envelope[Resource])
async def get_resource(
    resource_id: str,
    principal: PrincipalDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Get a resource."""
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # NOTE: ``get_resource`` on the concrete service is SYNC (unlike
    # ``check_name_exists`` which is async) — do NOT `await` it.
    r = service.get_resource(resource_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return envelope(_to_openapi_resource(r), request)


@router.put("/{resource_id}", response_model=Envelope[Resource])
async def update_resource(
    resource_id: str,
    body: ResourceUpdate,
    principal: PrincipalDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Update a resource (link rename / url change).

    Phase 3a: link only; ``link_type`` is intentionally not exposed on the
    openapi contract. ValueError from the service (not found / url clash)
    → 409 Conflict, per legacy + create parity.
    """
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    try:
        r = await service.update_link_resource(
            resource_id=resource_id,
            name=body.name,
            url=body.url,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return envelope(_to_openapi_resource(r), request)


@router.delete("/{resource_id}", response_model=Envelope[Deleted])
async def delete_resource(
    resource_id: str,
    principal: PrincipalDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Envelope[Deleted]:
    """Delete a resource (file → device FS, link/folder → DB soft-delete).

    ``device_fs`` is resolved per-bot from ``ac_bots``; ``owner_id`` comes from
    the verified principal (``caller_owner_id``), fail-closed — mirroring the
    bots router. The four injected deps (factory, bot_repo, resolver,
    device_fs_dispatcher) stay out of the served OpenAPI schema — see
    ``tests/community/contracts/gateway/test_public_namespace.py``.
    """
    effective_bot_id = bot_id
    owner_id = caller_owner_id(principal)
    ctx = resolver.resolve_for_bot(effective_bot_id, owner_id)
    device_fs = device_fs_dispatcher.dispatch(ctx)
    service = factory.create(bot_id=effective_bot_id)
    # NOTE: ``delete_resource`` on the concrete service is ASYNC now (Phase 3
    # slim service awaits device_fs.delete_file for file resources) — must
    # be ``await``ed. Returns False when the record is missing → 404.
    ok = await service.delete_resource(resource_id, device_fs=device_fs)
    if not ok:
        raise HTTPException(status_code=404, detail="Resource not found")
    return deleted_envelope(request)


@router.get(
    "/{resource_id}/download",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_resource(
    resource_id: str,
    principal: PrincipalDep,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Response:
    """Download a resource's bytes (raw, not enveloped).

    device_fs is resolved per-bot from ``ac_bots`` (same chain as delete /
    upload); owner_id comes from the verified principal (``caller_owner_id``),
    fail-closed — mirroring the bots router. ``device_fs.read_file`` is
    delivered through the dispatcher-resolved boundary directly (parallel to
    upload_file). Service returns ``(bytes, mime)`` or ``None`` → 404
    (not-found / not-a-file / is-directory / read-failure all collapse to 404).
    """
    effective_bot_id = bot_id
    owner_id = caller_owner_id(principal)
    ctx = resolver.resolve_for_bot(effective_bot_id, owner_id)
    device_fs = device_fs_dispatcher.dispatch(ctx)
    service = factory.create(bot_id=effective_bot_id)
    result = await service.download_resource(resource_id, device_fs=device_fs)
    if result is None:
        raise HTTPException(
            status_code=404, detail="Resource not found or not downloadable"
        )
    content, content_type = result
    return Response(content=content, media_type=content_type)


@router.get("/{resource_id}/preview", response_model=Envelope[Preview])
async def preview_resource(
    resource_id: str,
    principal: PrincipalDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Envelope[Preview]:
    """Get a resource preview (text-ified content, enveloped).

    device_fs is resolved per-bot from ``ac_bots`` (same chain as delete /
    upload / download); owner_id comes from the verified principal
    (``caller_owner_id``), fail-closed — mirroring the bots router.
    ``device_fs.read_file`` is delivered through the dispatcher-resolved
    boundary directly. Service returns a dict ``{content, content_type,
    size}`` or ``None`` → 404 (not-found / not-a-file / is-directory /
    read-failure all collapse to 404 — service already filters non-file /
    directory / empty-bytes). ``ValueError`` from the service (content > 1
    MB cap) → 413 (legacy parity). Unlike download (raw ``Response``),
    preview returns an enveloped ``Preview`` schema so the caller gets a
    structured content_type + content pair.

    The four injected deps (factory, bot_repo, resolver, device_fs_dispatcher)
    stay out of the served OpenAPI schema — see
    ``tests/community/contracts/gateway/test_public_namespace.py``.
    """
    effective_bot_id = bot_id
    owner_id = caller_owner_id(principal)
    ctx = resolver.resolve_for_bot(effective_bot_id, owner_id)
    device_fs = device_fs_dispatcher.dispatch(ctx)
    service = factory.create(bot_id=effective_bot_id)
    try:
        result = await service.preview_resource(resource_id, device_fs=device_fs)
    except ValueError as e:
        # too large → 413 (legacy parity: "File too large for preview").
        raise HTTPException(status_code=413, detail=str(e)) from e
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Resource not found or not previewable",
        )
    return envelope(
        Preview(
            resource_id=resource_id,
            content_type=result["content_type"],
            content=result["content"],
        ),
        request,
    )
