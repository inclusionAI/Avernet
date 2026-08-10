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
- Owner/identity comes from ``UserIdDep`` — the request's own ``user_id``
  query parameter, refused unless it names the verified caller — mirroring the
  bots router. That dependency is the single replaceable point: when an App may
  act for a user, only ``principal.py`` changes.
- Cross-tenant isolation rides on the ac_bots guard (Phase 0); a deployed DDL
  for ``ac_resource.avernet_tenant`` MUST precede this code reaching prod (see
  Phase 0 plan) — code first / DDL later breaks bot reads with a missing column.
- device_fs resolution lives in the adapter (Rule 7 transport concern); a
  service owns the read/write via the opaque ``device_fs`` argument.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response

from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
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


@router.get("", response_model=Envelope[Page[Resource]])
@envelope_errors
async def list_resources(
    page: PageParamsDep,
    user_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    type: ResourceType | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Page[Resource]]:
    """List resources (filter + paginate)."""
    # Declared but not used — and that is a gap, not a design. These four
    # handlers scope on the caller-supplied ``bot_id`` and the tenant guard, and
    # never check that the caller owns that bot; see
    # ``specs/2026-08-02-public-api-user-only-principal/``. They take the
    # parameter because they are user-scoped in principle, so closing the gap
    # later is a change to this function and not to the public contract. This is
    # NOT the ``check_bot_name`` / MCP-catalogue case, which has no user
    # dimension at all and therefore takes no ``user_id``.
    del user_id
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    if type is not None and _legacy_type_for(type) is None:
        # type is an openapi type with no legacy mapping (FOLDER) → no rows
        return page_envelope(0, [], request)
    legacy_type = _legacy_type_for(type)
    start = (page.page - 1) * page.page_size
    # Push the page bound down to the service so we only materialise Resource /
    # openapi models for the requested page, not the full set (the service still
    # reads the full repo row-set today — a repo-level LIMIT/COUNT is the deeper
    # follow-up). total uses the repo count, not the page length.
    items = service.list_resources(
        resource_type=legacy_type, limit=page.page_size, offset=start
    )
    page_items = [_to_openapi_resource(r) for r in items]
    total = service.count_resources(resource_type=legacy_type)
    return page_envelope(total, page_items, request)


@router.get("/check-name", response_model=Envelope[NameCheck])
@envelope_errors
async def check_resource_name(
    name: str,
    owner_id: UserIdDep,
    request: Request,
    type: ResourceType | None = None,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[NameCheck]:
    """Check whether a resource name is in use within the bot.

    Pass the `type` you are asking about; omitting it asks about a file name.

    **Not a reliable preflight for creating a link.** A `link` here is checked
    against a different set of records than the one link creation writes to, so
    a name this endpoint reports as free can still be refused by create with
    409. Treat create's answer as the authoritative one and handle the 409;
    this endpoint is dependable for files.
    """
    # The mismatch is real, not a caveat invented for the docstring: this maps
    # openapi LINK to legacy ResourceType.LINK, while `create_url_resource`
    # checks and writes legacy ResourceType.URL — which is what every link
    # created through this surface is. So the check scans a set that holds none
    # of them. Fixing it means deciding whether LINK fans out to both legacy
    # types here (the "URL fan-out" follow-up noted on _OPENAPI_TO_LEGACY_TYPE),
    # which is a behaviour change to the resources contract and not this
    # change's to make. Until then the endpoint says what it actually does.
    #
    # parent_path / exclude_id from the service signature stay off this contract
    # and are passed as None: resources are bot-scoped, so there is no parent
    # path to qualify the name with.
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # Map openapi ResourceType → legacy ResourceType enum the slim service
    # expects. When the caller omits ``type``, default to FILE (matches the
    # legacy handler's most common case — the openapi check-name call shape
    # has no FOLDER equivalent).
    legacy_type = _legacy_type_for(type) or _LegacyType.FILE
    # owner_id is the request's own ``user_id`` — fail-closed, since neither a
    # missing parameter nor one naming another user reaches this line. The slim
    # service signature REQUIRES both keyword args (no defaults), so parent_path
    # is passed explicitly as None rather than omitted.
    exists = await service.check_name_exists(
        name=name,
        resource_type=legacy_type,
        parent_path=None,
        user_id=owner_id,
    )
    return envelope(NameCheck(name=name, exists=exists), request)


@router.post("", status_code=201, response_model=Envelope[Resource])
@envelope_errors
async def create_resource(
    body: ResourceCreate,
    user_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Create a link resource.

    Only `link` resources are created here, and `url` is required for them. Send
    a file's bytes to the upload endpoint instead (400); folders are not
    creatable yet (501). A name already used within the bot is refused (409).
    """
    del user_id  # not-yet-enforced ownership — see list_resources
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
    # DuplicateResourceError propagates to @envelope_errors → 409 fixed message
    # (no str(exc) leakage, unlike the prior hand-translation).
    r = await service.create_url_resource(
        name=body.name,
        url=body.url,
        # parent_path intentionally NOT forwarded: the openapi ResourceCreate
        # schema carries `parent_id` (a pending follow-up — its ID-vs-path
        # semantics aren't settled). Passing a half-defined value would risk
        # a wrong-attribute write; link scoping by bot_id is sufficient now.
        parent_path=None,
    )
    return created(_to_openapi_resource(r), request)


@router.post("/upload", status_code=201, response_model=Envelope[Resource])
@envelope_errors
async def upload_resource(
    owner_id: UserIdDep,
    name: str,
    content: Annotated[
        bytes,
        Body(
            media_type="application/octet-stream",
            description="The file's raw bytes, sent as the whole request body. "
            "This is not a multipart form upload — send the bytes unwrapped, "
            "with Content-Type: application/octet-stream.",
        ),
    ],
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Envelope[Resource]:
    """Upload a file's raw bytes as a new resource.

    The body is the file itself, not a form: send the bytes with
    `Content-Type: application/octet-stream` and name the resource with the
    `name` query parameter. A name already used within the bot is refused (409).
    """
    effective_bot_id = bot_id
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
    except ValueError:
        # DuplicateResourceError propagates to @envelope_errors → 409.
        raise
    except Exception:
        # device_fs.write_file failure → 502 Bad Gateway. The slim service lets
        # the write exception bubble (no longer swallowed) so the handler is the
        # single translation point: file write failed, no DB record created.
        # Fixed message (never str(exc)) so internal details don't leak.
        logger.exception("[upload_resource] device_fs write failed")
        raise HTTPException(status_code=502, detail="Upload storage failed")
    return created(_to_openapi_resource(r), request)


@router.get("/{resource_id}", response_model=Envelope[Resource])
@envelope_errors
async def get_resource(
    resource_id: str,
    user_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Get a resource."""
    del user_id  # not-yet-enforced ownership — see list_resources
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # NOTE: ``get_resource`` on the concrete service is SYNC (unlike
    # ``check_name_exists`` which is async) — do NOT `await` it.
    r = service.get_resource(resource_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return envelope(_to_openapi_resource(r), request)


@router.put("/{resource_id}", response_model=Envelope[Resource])
@envelope_errors
async def update_resource(
    resource_id: str,
    body: ResourceUpdate,
    user_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    """Rename a link resource or change its target URL.

    Link resources only. A name or URL already used within the bot is refused
    (409).
    """
    del user_id  # not-yet-enforced ownership — see list_resources
    effective_bot_id = bot_id
    service = factory.create(bot_id=effective_bot_id)
    # ResourceNotFoundError (404) / DuplicateResourceError (409) propagate to
    # @envelope_errors with fixed messages — no str(exc) leakage. The prior
    # hand-translation also wrongly mapped not-found → 409; this fixes that.
    r = await service.update_link_resource(
        resource_id=resource_id,
        name=body.name,
        url=body.url,
    )
    return envelope(_to_openapi_resource(r), request)


@router.delete("/{resource_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_resource(
    resource_id: str,
    owner_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Envelope[Deleted]:
    """Delete a resource.

    Deleting a file removes its bytes from the bot's device; deleting a link or
    folder removes the record. The bot must have a device bound either way (409
    otherwise).
    """
    effective_bot_id = bot_id
    # Follow-up (#4): device_fs resolution is unconditional — a link/folder
    # soft-delete never touches device_fs but the handler can't tell the type
    # until the service reads the row, so an unbound bot raises
    # DeviceNotBoundError (→409) even for a link delete. The clean fix moves
    # the lazy device_fs dispatch into the service (it knows is_file); left as
    # a service-layer follow-up with totalfrank's comment, not a router
    # get_resource work-around (2× reads + breaks the stub-service test shape).
    ctx = resolver.resolve_for_bot(effective_bot_id, owner_id)
    device_fs = device_fs_dispatcher.dispatch(ctx)
    service = factory.create(bot_id=effective_bot_id)
    # delete_resource is ASYNC. Returns False when the record is missing → 404.
    ok = await service.delete_resource(resource_id, device_fs=device_fs)
    if not ok:
        raise HTTPException(status_code=404, detail="Resource not found")
    return deleted_envelope(request)


@router.get(
    "/{resource_id}/download",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
@envelope_errors
async def download_resource(
    resource_id: str,
    owner_id: UserIdDep,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Response:
    """Download a file resource's bytes.

    The response is the raw bytes with the resource's own media type — this is
    the one endpoint on this surface that does not wrap its answer in the
    standard envelope. A resource with no bytes to serve answers 404.
    """
    effective_bot_id = bot_id
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
@envelope_errors
async def preview_resource(
    resource_id: str,
    owner_id: UserIdDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID this resource belongs to."),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(
        DeviceFilesystemDispatcher
    ),
) -> Envelope[Preview]:
    """Get a readable preview of a resource's content.

    Content over 1 MB is refused (413) rather than truncated. A resource that
    cannot be previewed answers 404.
    """
    effective_bot_id = bot_id
    ctx = resolver.resolve_for_bot(effective_bot_id, owner_id)
    device_fs = device_fs_dispatcher.dispatch(ctx)
    service = factory.create(bot_id=effective_bot_id)
    result = await service.preview_resource(resource_id, device_fs=device_fs)
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
