"""Resource management router — URL and Node resource types.

File operations have been migrated to file_router.py which uses
FileService directly without database dependency.

Legacy endpoints (GET/DELETE /{resource_id}, POST /file, POST /folder,
GET /{resource_id}/download, GET /{resource_id}/preview, POST /upload-by-channel)
are retained for backward compatibility during the frontend migration.
They can be removed once the old frontend is fully deployed.

Architecture constraints:
  OK: api/resources/schemas          (same layer)
  OK: api/resources/file_router      (same layer — new file ops)
  OK: core/resources/dependencies    (DI factory — sole gateway to legacy arch)
  OK: api/dependencies               (RequestContext / get_request_context)
  BAD: plugins.*                (forbidden)
  BAD: services/openclawserver.*     (forbidden)
"""
from __future__ import annotations

import logging
from typing import List as ListType, Optional
from urllib.parse import quote

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.resources.schemas import (
    BatchLinkCreateRequest,
    CheckNameData,
    CheckNameResponse,
    LinkItem,
    NodeCreateRequest,
    PreviewData,
    PreviewResponse,
    ResourceDeleteResponse,
    ResourceDetailResponse,
    ResourceListItem,
    ResourceListResponse,
    UpdateLinkRequest,
    URLCreateRequest,
)
from agentclaw.community.adapters.http.resources.file_router import router as file_router
from agentclaw.community.adapters.http.resources.file_search_download_router import router as file_search_download_router
from agentclaw.community.core.resources.dependencies.resource import (
    get_bot_data_dir,
    get_legacy_resource_service,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.core.resources.models import ResourceType
from agentclaw.community.core.repository.protocols.platform import ResourceRepositoryProtocol
from agentclaw.community.core.resources.yuque_resolve import YuqueResolveError, resolve_yuque_url
from agentclaw.community.core.resources.dependencies.service_dep import sync_yuque_permissions
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.devices.services import device_info as device_info_lookup
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

logger = logging.getLogger(__name__)

# MIME type mapping (shared with file_router)
_MIME_TYPES = {
    'pdf': 'application/pdf', 'txt': 'text/plain', 'md': 'text/markdown',
    'json': 'application/json', 'yaml': 'application/yaml', 'yml': 'application/yaml',
    'xml': 'application/xml', 'html': 'text/html', 'htm': 'text/html',
    'css': 'text/css', 'js': 'application/javascript', 'csv': 'text/csv',
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'svg': 'image/svg+xml', 'mp4': 'video/mp4',
    'mp3': 'audio/mpeg', 'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'zip': 'application/zip', 'tar': 'application/x-tar', 'gz': 'application/gzip',
}
_INLINE_TYPES = {'pdf', 'txt', 'md', 'json', 'xml', 'html', 'png', 'jpg', 'jpeg', 'gif', 'svg', 'mp4', 'mp3'}

router = APIRouter(prefix="/api/resources", tags=["resources"])

# Register file operations sub-routers (no DB, direct filesystem)
router.include_router(file_router)
router.include_router(file_search_download_router)

_DEFAULT_BOT_ID = "default"


# ---- Helpers ----

def _parse_resource_type(value: str) -> ResourceType:
    try:
        return ResourceType(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid resource type: {value}")


def _get_path_params(
    ctx: RequestContext,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> tuple:
    """Resolve effective path parameters for file operations.

    engine_type is resolved from the bot's active_engine when not overridden,
    so the frontend doesn't need to know which engine a bot runs on.
    """
    effective_bot_id = bot_id or ctx.bot_id or "default"
    owner_id_for_lookup = entity_id if entity_id else (ctx.user_id or "")
    return (
        entity_id or ctx.user_id or "default",
        effective_bot_id,
        resolve_engine_for_bot(
            bot_id=effective_bot_id,
            owner_id=owner_id_for_lookup,
            override=engine_type,
            bot_repo=bot_repo,
        ),
        entity_type or "staff",
    )


def _resource_to_list_item(resource, service) -> ResourceListItem:
    """Convert Resource model (from legacy service) to ResourceListItem."""
    from datetime import datetime
    gmt_created = resource.gmt_created
    if isinstance(gmt_created, datetime):
        gmt_created = gmt_created.isoformat()
    gmt_modified = resource.gmt_modified
    if isinstance(gmt_modified, datetime):
        gmt_modified = gmt_modified.isoformat()

    item = ResourceListItem(
        id=str(resource.id) if resource.id is not None else None,
        name=resource.name,
        resource_type=resource.resource_type.value if hasattr(resource.resource_type, 'value') else str(resource.resource_type),
        status=resource.status.value if hasattr(resource.status, 'value') else str(resource.status),
        user_id=resource.user_id,
        gmt_created=gmt_created or "",
        gmt_modified=gmt_modified or "",
    )
    if hasattr(resource, 'is_file') and resource.is_file:
        item.path = resource.path
        item.size = resource.size if not resource.is_directory else None
        item.is_directory = resource.is_directory
        item.extension = resource.extension if not resource.is_directory else None
        if resource.is_directory and service:
            item.child_count = service.count_children(resource.path)
    elif hasattr(resource, 'is_url') and resource.is_url:
        item.url = resource.url
    elif hasattr(resource, 'is_node') and resource.is_node:
        item.node_address = resource.node_address
        item.path = resource.node_address
    elif hasattr(resource, 'is_link') and resource.is_link:
        item.link_type = resource.link_type
        item.url = resource.url
        item.access_modes = (resource.attributes.get("access_modes") or None) if hasattr(resource, 'attributes') else None
    return item


# ---- URL / Node / Check-name Endpoints (still use DB) ----

@router.get("/check-name", response_model=CheckNameResponse)
async def check_name_availability(
    name: str = Query(..., description="Resource name to check"),
    resource_type: str = Query(..., description="Resource type: file, url, node"),
    parent_path: Optional[str] = Query(None, description="Parent folder path"),
    exclude_id: Optional[str] = Query(None, description="Resource ID to exclude"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    ctx: RequestContext = Depends(get_request_context),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> CheckNameResponse:
    """Check if a resource name is available."""
    resource_type_enum = _parse_resource_type(resource_type)
    effective_bot_id = bot_id or ctx.bot_id or _DEFAULT_BOT_ID
    service = factory.create(bot_id=effective_bot_id)

    exists = await service.check_name_exists(
        name=name,
        resource_type=resource_type_enum,
        parent_path=parent_path,
        user_id=ctx.user_id,
        exclude_id=exclude_id,
    )

    return CheckNameResponse(
        success=True,
        data=CheckNameData(
            available=not exists,
            message=f"Resource '{name}' already exists" if exists else "Name is available",
        ),
    )


@router.get("", response_model=ResourceListResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作，不记录日志
))
async def list_resources(
    search: Optional[str] = Query(None, description="Search keyword"),
    resource_type: Optional[str] = Query(None, description="Filter by type"),
    parent_path: Optional[str] = Query(None, description="Filter by parent path"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    limit: int = Query(100, description="Maximum results"),
    offset: int = Query(0, description="Offset for pagination"),
    ctx: RequestContext = Depends(get_request_context),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> ResourceListResponse:
    """List all URL/Node resources with optional filtering.

    NOTE: File resources are now managed via GET /api/resources/files.
    This endpoint remains for URL and Node resource types (and legacy clients).
    """
    type_filter = _parse_resource_type(resource_type) if resource_type else None
    effective_bot_id = bot_id or ctx.bot_id or _DEFAULT_BOT_ID
    service = factory.create(bot_id=effective_bot_id)

    resources = service.list_resources(
        resource_type=type_filter,
        parent_path=parent_path,
        user_id=owner_id or ctx.user_id,
        limit=limit,
        offset=offset,
    )

    if search:
        search_lower = search.lower()
        resources = [r for r in resources if search_lower in r.name.lower()]

    items = [_resource_to_list_item(r, service) for r in resources]
    return ResourceListResponse(success=True, data=items, total=len(items))


@router.post("/url", response_model=ResourceDetailResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    # Not a route parameter: force authoritative owner resolution from bot_id.
    owner_id="$owner_id",
))
async def create_url_resource(
    request: URLCreateRequest,
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    ctx: RequestContext = Depends(get_request_context),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> ResourceDetailResponse:
    """Create a new URL resource."""
    effective_bot_id = bot_id or ctx.bot_id or _DEFAULT_BOT_ID
    service = factory.create(bot_id=effective_bot_id)
    try:
        resource = await service.create_url_resource(
            name=request.name,
            url=request.url,
            method=request.method,
            headers=request.headers,
            parent_path=request.parent_path,
            user_id=ctx.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ResourceDetailResponse(
        success=True,
        data=_resource_to_list_item(resource, service).model_dump(),
    )


@router.post("/node", response_model=ResourceDetailResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    # Not a route parameter: force authoritative owner resolution from bot_id.
    owner_id="$owner_id",
))
async def create_node_resource(
    request: NodeCreateRequest,
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    ctx: RequestContext = Depends(get_request_context),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> ResourceDetailResponse:
    """Create a new Node resource."""
    effective_bot_id = bot_id or ctx.bot_id or _DEFAULT_BOT_ID
    service = factory.create(bot_id=effective_bot_id)
    try:
        resource = await service.create_node_resource(
            name=request.name,
            node_address=request.node_address,
            path_alias=request.path_alias,
            scan_recursive=request.scan_recursive,
            parent_path=request.parent_path,
            user_id=ctx.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ResourceDetailResponse(
        success=True,
        data=_resource_to_list_item(resource, service).model_dump(),
    )


VALID_LINK_TYPES = {"yuque", "dima", "antcode"}


@router.post("/links", response_model=ResourceListResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def batch_create_link_resources(
    request: BatchLinkCreateRequest,
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    ctx: RequestContext = Depends(get_request_context),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    passport: PassportPlugin = Injected(PassportPlugin),
) -> ResourceListResponse:
    """Batch create LINK resources for external knowledge sources.

    Request body is grouped by link_type:
        {"links": {"yuque": [{"url": "..."}], "dima": [{"url": "..."}]}}

    For yuque links, resolves URLs via MCP to get doc_id/book_id before storage,
    then syncs permissions via saveSubResources after all links are stored.
    """
    effective_user_id = owner_id or ctx.user_id

    # Validate all link_types
    invalid_types = set(request.links.keys()) - VALID_LINK_TYPES
    if invalid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid link_type(s): {sorted(invalid_types)}. Must be one of: {sorted(VALID_LINK_TYPES)}",
        )

    if not request.links:
        raise HTTPException(status_code=400, detail="No links provided")

    # Flatten all links with their link_type, resolve default name
    all_links: list[tuple[str, LinkItem]] = []
    for link_type, items in request.links.items():
        for link in items:
            if not link.name:
                link.name = link.url
            all_links.append((link_type, link))

    # Check for duplicate URLs across all types
    all_urls = [link.url for _, link in all_links]
    if len(all_urls) != len(set(all_urls)):
        raise HTTPException(status_code=400, detail="Duplicate link URLs in batch")

    effective_bot_id = bot_id or ctx.bot_id or _DEFAULT_BOT_ID
    service = factory.create(bot_id=effective_bot_id)

    # Resolve yuque URLs via MCP before storage
    yuque_resolved: dict[str, dict] = {}
    for link_type, link in all_links:
        if link_type != "yuque":
            continue
        try:
            resolved = resolve_yuque_url(
                link.url,
                bot_id=effective_bot_id,
                user_id=effective_user_id,
                resolver=resolver,
            )
            if not link.name or link.name == link.url:
                link.name = resolved.get("title") or link.url
            yuque_resolved[link.url] = resolved
        except YuqueResolveError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": exc.message,
                    "error_code": exc.error_code,
                    "url": exc.url or link.url,
                },
            )

    for _, link in all_links:
        if await service.check_link_url_exists(
            url=link.url,
            user_id=effective_user_id,
        ):
            raise HTTPException(
                status_code=409,
                detail=f"LINK resource with URL '{link.url}' already exists",
            )

    created_items = []
    for link_type, link in all_links:
        extra_attrs = None
        if link_type == "yuque" and link.url in yuque_resolved:
            r = yuque_resolved[link.url]
            extra_attrs = {
                "doc_id": r["doc_id"],
                "book_id": r["book_id"],
                "yuque_type": r["type"],
                "access_modes": link.access_modes,
            }
        resource = await service.create_link_resource(
            name=link.name,
            url=link.url,
            link_type=link_type,
            user_id=effective_user_id,
            created_by=ctx.user_id,
            extra_attrs=extra_attrs,
        )
        created_items.append(_link_resource_to_list_item(resource))

    # Sync yuque permissions via saveSubResources (non-blocking, full update)
    sync_yuque_permissions(effective_bot_id, effective_user_id, resource_repo, passport)

    return ResourceListResponse(
        success=True,
        data=created_items,
        total=len(created_items),
    )


def _link_resource_to_list_item(resource) -> ResourceListItem:
    """Convert a LINK Resource to ResourceListItem (independent of shared _resource_to_list_item)."""
    from datetime import datetime
    gmt_created = resource.gmt_created
    if isinstance(gmt_created, datetime):
        gmt_created = gmt_created.isoformat()
    gmt_modified = resource.gmt_modified
    if isinstance(gmt_modified, datetime):
        gmt_modified = gmt_modified.isoformat()

    return ResourceListItem(
        id=str(resource.id) if resource.id is not None else None,
        name=resource.name,
        resource_type=resource.resource_type.value if hasattr(resource.resource_type, 'value') else str(resource.resource_type),
        status=resource.status.value if hasattr(resource.status, 'value') else str(resource.status),
        user_id=resource.user_id,
        gmt_created=gmt_created or "",
        gmt_modified=gmt_modified or "",
        url=resource.attributes.get("url"),
        link_type=resource.attributes.get("link_type"),
        description=resource.attributes.get("description"),
        access_modes=(resource.attributes.get("access_modes") or None),
    )


@router.put("/links/{resource_id}", response_model=ResourceDetailResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def update_link_resource(
    resource_id: str,
    request: UpdateLinkRequest,
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    ctx: RequestContext = Depends(get_request_context),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    passport: PassportPlugin = Injected(PassportPlugin),
) -> ResourceDetailResponse:
    """Update a LINK resource type, URL, or name.

    For yuque links, re-syncs permissions after update.
    """
    effective_user_id = owner_id or ctx.user_id
    effective_bot_id = bot_id or ctx.bot_id or _DEFAULT_BOT_ID

    if request.link_type is not None and request.link_type not in VALID_LINK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid link_type: {request.link_type}. Must be one of: {sorted(VALID_LINK_TYPES)}",
        )

    service = factory.create(bot_id=effective_bot_id)

    # Resolve yuque URL if URL changed and target type is yuque
    extra_attrs: dict = {}
    target_link_type = request.link_type
    if target_link_type is None:
        stored_check = resource_repo.get_by_id(resource_id)
        if stored_check:
            target_link_type = (stored_check.get("attributes") or {}).get("link_type")

    if request.url is not None and target_link_type == "yuque":
        try:
            resolved = resolve_yuque_url(
                request.url,
                bot_id=effective_bot_id,
                user_id=effective_user_id,
                resolver=resolver,
            )
            if not request.name:
                request.name = resolved.get("title") or request.url
            extra_attrs = {
                "doc_id": resolved["doc_id"],
                "book_id": resolved["book_id"],
                "yuque_type": resolved["type"],
                "access_modes": request.access_modes or ["READ"],
            }
        except YuqueResolveError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": exc.message,
                    "error_code": exc.error_code,
                    "url": exc.url or request.url or "",
                },
            )

    try:
        resource = await service.update_link_resource(
            resource_id=resource_id,
            link_type=request.link_type,
            url=request.url,
            name=request.name,
        )
        if extra_attrs:
            resource.attributes.update(extra_attrs)
            resource_repo.update(resource_id, {"attributes": resource.attributes})
        elif request.access_modes is not None:
            # access_modes updated without URL change
            resource.attributes["access_modes"] = request.access_modes
            resource_repo.update(resource_id, {"attributes": resource.attributes})
    except ValueError as exc:
        err = str(exc)
        status_code = 404 if "not found" in err.lower() else 409
        raise HTTPException(status_code=status_code, detail=err)

    # Sync yuque permissions if relevant
    final_link_type = resource.attributes.get("link_type")
    if final_link_type == "yuque":
        sync_yuque_permissions(effective_bot_id, effective_user_id, resource_repo, passport)

    item = _link_resource_to_list_item(resource)
    return ResourceDetailResponse(success=True, data=item)


# ---- Legacy endpoints (backward compatibility — remove after frontend migration) ----


@router.get("/{resource_id}", response_model=ResourceDetailResponse)
async def get_resource(
    resource_id: str,
    entity_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    engine_type: Optional[str] = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
) -> ResourceDetailResponse:
    """Get resource details by ID. [LEGACY — use /files instead]"""
    eid, ebid, eeng, eetype = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)
    legacy_svc = get_legacy_resource_service(resource_repo, bot_repo, path_factory=path_factory, entity_id=eid, bot_id=ebid, engine_type=eeng, entity_type=eetype)
    resource = legacy_svc.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    return ResourceDetailResponse(success=True, data=_resource_to_list_item(resource, legacy_svc).model_dump())


@router.delete("/{resource_id}", response_model=ResourceDeleteResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$entity_id",
))
async def delete_resource(
    resource_id: str,
    entity_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    engine_type: Optional[str] = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
    passport: PassportPlugin = Injected(PassportPlugin),
) -> ResourceDeleteResponse:
    """Delete resource by ID. [LEGACY — use DELETE /files?path= instead]"""
    effective_user_id = entity_id or ctx.user_id
    logger.info(f"[resources.delete_resource] user_id={effective_user_id}, resource_id={resource_id}")

    eid, ebid, eeng, eetype = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)
    device_provider, sandbox_id = device_info_lookup.get_device_info(ebid, effective_user_id, bot_repo)
    ctx_dev = resolver.resolve_for_bot(ebid, effective_user_id)
    device_fs = device_fs_dispatcher.dispatch(ctx_dev)

    legacy_svc = get_legacy_resource_service(resource_repo, bot_repo, path_factory=path_factory, entity_id=eid, bot_id=ebid, engine_type=eeng, entity_type=eetype)
    resource = legacy_svc.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    is_yuque_link = (
        hasattr(resource, "is_link") and resource.is_link
        and hasattr(resource, "link_type") and resource.link_type == "yuque"
    )

    success = legacy_svc.delete_resource(resource_id, device_provider=device_provider, sandbox_id=sandbox_id, device_fs=device_fs)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete resource")

    if is_yuque_link:
        effective_bot_id = bot_id or ctx.bot_id or _DEFAULT_BOT_ID
        sync_yuque_permissions(effective_bot_id, effective_user_id, resource_repo, passport)

    return ResourceDeleteResponse(success=True, message="Resource deleted")


@router.post("/file", response_model=ResourceListResponse)
async def upload_files_legacy(
    files: ListType[UploadFile] = File(...),
    parent_path: Optional[str] = Query(""),
    entity_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    engine_type: Optional[str] = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> ResourceListResponse:
    """Upload file(s). [LEGACY — use POST /files/upload instead]"""
    logger.info(f"[resources.upload_files] user_id={ctx.user_id}, file_count={len(files)}")
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    eid, ebid, eeng, eetype = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)
    legacy_svc = get_legacy_resource_service(resource_repo, bot_repo, path_factory=path_factory, entity_id=eid, bot_id=ebid, engine_type=eeng, entity_type=eetype)
    ctx_dev = resolver.resolve_for_bot(ebid, ctx.user_id)
    device_fs = device_fs_dispatcher.dispatch(ctx_dev)

    results, errors = [], []
    for file in files:
        try:
            resource = await legacy_svc.upload_file(
                data=await file.read(),
                filename=file.filename or "unnamed",
                target_dir=parent_path or "",
                user_id=ctx.user_id,
                created_by=ctx.user_id,
                device_fs=device_fs,
            )
            results.append(resource)
        except ValueError as e:
            errors.append({"filename": file.filename, "error": str(e)})
        except Exception as e:
            logger.error(f"[resources.upload_files] Failed: {file.filename}, {e}")
            errors.append({"filename": file.filename, "error": str(e)})

    items = [_resource_to_list_item(r, legacy_svc) for r in results]
    return ResourceListResponse(success=len(errors) == 0, data=items, total=len(items),
                                errors=errors if errors else None)


@router.post("/upload-by-channel", response_model=ResourceListResponse)
async def upload_file_by_channel(
    file: UploadFile = File(...),
    channel_type: str = Query("dingding"),
    ip: str = Query(...),
    client_id: str = Query(...),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    channel_service: ChannelServiceProtocol = Injected(ChannelServiceProtocol),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> ResourceListResponse:
    """Upload file via channel config. user_id comes from auth context only."""
    user_id = ctx.user_id
    logger.info(f"[resources.upload_file_by_channel] channel={channel_type}, ip={ip}, user={user_id}")

    bot = await bot_service.get_bot_by_ip_and_user(ip, user_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot not found for ip={ip}, user_id={user_id}")

    bot_id = bot.get("bot_id")
    effective_engine = engine_type or bot.get("active_engine") or DEFAULT_ENGINE_TYPE
    channels = channel_service.list_channels(type=channel_type, identity_id=user_id, bind_bot_id=bot_id)
    matched = next(
        (
            c
            for c in channels
            if c.config.get("client_id") == client_id
        ),
        None,
    )
    if not matched:
        raise HTTPException(
            status_code=403,
            detail=f"No {channel_type} channel with client_id={client_id} for user_id={user_id}, bot_id={bot_id}",
        )

    entity_id = bot.get('entity_id', user_id)
    entity_type = bot.get('entity_type', 'staff')
    legacy_svc = get_legacy_resource_service(
        resource_repo, bot_repo, path_factory=path_factory, entity_id=entity_id, bot_id=bot_id, engine_type=effective_engine, entity_type=entity_type
    )

    dir_resource = legacy_svc.get_resource_by_name(
        name=channel_type, resource_type=ResourceType.FILE, parent_path=None, user_id=user_id,
    )
    if not (dir_resource and dir_resource.is_directory):
        try:
            await legacy_svc.create_directory(channel_type, user_id=user_id)
        except ValueError:
            pass

    try:
        resource = await legacy_svc.upload_file(
            data=await file.read(),
            filename=file.filename or "unnamed",
            target_dir=channel_type,
            user_id=user_id,
            created_by=user_id,
            device_fs=device_fs_dispatcher.dispatch(resolver.resolve_for_bot(bot_id, user_id)),
        )
        item = _resource_to_list_item(resource, legacy_svc)
        item.path = channel_type
        return ResourceListResponse(success=True, data=[item], total=1)
    except ValueError as e:
        err = str(e)
        raise HTTPException(status_code=409 if "already exists" in err else 400, detail=err)
    except Exception as e:
        logger.error(f"[resources.upload_file_by_channel] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/folder", response_model=ResourceDetailResponse)
async def create_folder(
    path: str = Form(...),
    entity_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    engine_type: Optional[str] = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
) -> ResourceDetailResponse:
    """Create a directory (folder) resource. [LEGACY — use POST /files/mkdir instead]"""
    logger.info(f"[resources.create_folder] user_id={ctx.user_id}, path={path}")

    eid, ebid, eeng, eetype = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)
    device_provider, _ = device_info_lookup.get_device_info(ebid, ctx.user_id, bot_repo)
    legacy_svc = get_legacy_resource_service(resource_repo, bot_repo, path_factory=path_factory, entity_id=eid, bot_id=ebid, engine_type=eeng, entity_type=eetype)

    try:
        resource = await legacy_svc.create_directory(
            path, user_id=ctx.user_id, created_by=ctx.user_id, device_provider=device_provider,
        )
        return ResourceDetailResponse(success=True, data=_resource_to_list_item(resource, legacy_svc).model_dump())
    except ValueError as e:
        err = str(e)
        raise HTTPException(status_code=409 if "already exists" in err else 400, detail=err)


@router.get("/{resource_id}/download")
async def download_resource(
    resource_id: str,
    entity_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    engine_type: Optional[str] = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> StreamingResponse:
    """Download resource content. [LEGACY — use GET /files/download instead]"""
    logger.info(f"[resources.download_resource] user_id={ctx.user_id}, resource_id={resource_id}")

    eid, ebid, eeng, eetype = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)
    legacy_svc = get_legacy_resource_service(resource_repo, bot_repo, path_factory=path_factory, entity_id=eid, bot_id=ebid, engine_type=eeng, entity_type=eetype)
    resource = legacy_svc.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not resource.is_file:
        raise HTTPException(status_code=400, detail="Only FILE type resources can be downloaded")
    if resource.is_directory:
        raise HTTPException(status_code=400, detail="Cannot download directories")

    filename = resource.name
    ext = filename.split('.')[-1].lower() if '.' in filename else ''
    file_path_str = resource.path
    device_provider, sandbox_id = device_info_lookup.get_device_info(ebid, ctx.user_id, bot_repo)
    data_dir = get_bot_data_dir(path_factory, eid, ebid, eeng, eetype)

    media_type = _MIME_TYPES.get(ext, 'application/octet-stream')
    disposition = 'inline' if ext in _INLINE_TYPES else 'attachment'
    content_disp = f"{disposition}; filename*=UTF-8''{quote(filename)}"

    if device_provider == 'arca' and sandbox_id:
        resource_path = file_path_str.lstrip('/') if file_path_str else ''
        arca_path = str(data_dir) + "/" + resource_path
        device_ctx = resolver.resolve_for_bot(ebid, ctx.user_id)
        device_fs = device_fs_dispatcher.dispatch(device_ctx)
        content_bytes = await device_fs.read_file(arca_path)
        if content_bytes is None:
            raise HTTPException(status_code=404, detail="File not found on Arca")

        async def arca_iter():
            yield content_bytes

        return StreamingResponse(
            arca_iter(), media_type=media_type,
            headers={'Content-Disposition': content_disp, 'Content-Length': str(len(content_bytes))},
        )

    from agentclaw.community.core.resources.dependencies.resource import get_file_service
    file_service = get_file_service(data_dir)
    file_path = await file_service.get_file_path(file_path_str)
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found on disk")

    async def file_iter():
        async with aiofiles.open(file_path, 'rb') as f:
            while chunk := await f.read(8192):
                yield chunk

    return StreamingResponse(
        file_iter(), media_type=media_type,
        headers={'Content-Disposition': content_disp},
    )


@router.get("/{resource_id}/preview", response_model=PreviewResponse)
async def preview_content(
    resource_id: str,
    max_size: int = Query(1024 * 1024),
    entity_id: Optional[str] = Query(None),
    entity_type: Optional[str] = Query(None),
    bot_id: Optional[str] = Query(None),
    engine_type: Optional[str] = Query(None),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    resource_repo: ResourceRepositoryProtocol = Injected(ResourceRepositoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
) -> PreviewResponse:
    """Preview resource content. [LEGACY — use GET /files/preview instead]"""
    logger.info(f"[resources.preview_content] user_id={ctx.user_id}, resource_id={resource_id}")

    eid, ebid, eeng, eetype = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)
    legacy_svc = get_legacy_resource_service(resource_repo, bot_repo, path_factory=path_factory, entity_id=eid, bot_id=ebid, engine_type=eeng, entity_type=eetype)
    resource = legacy_svc.get_resource(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not resource.is_file or resource.is_directory:
        raise HTTPException(status_code=400, detail="Only non-directory FILE resources can be previewed")

    file_path_str = resource.path
    data_dir = get_bot_data_dir(path_factory, eid, ebid, eeng, eetype)
    device_provider, sandbox_id = device_info_lookup.get_device_info(ebid, ctx.user_id, bot_repo)

    if device_provider == 'arca' and sandbox_id:
        resource_path = file_path_str.lstrip('/') if file_path_str else ''
        arca_path = str(data_dir) + "/" + resource_path
        device_ctx = resolver.resolve_for_bot(ebid, ctx.user_id)
        device_fs = device_fs_dispatcher.dispatch(device_ctx)
        content_bytes = await device_fs.read_file(arca_path)
        if content_bytes is None:
            raise HTTPException(status_code=404, detail="File not found on Arca")
        if len(content_bytes) > max_size:
            raise HTTPException(status_code=413, detail=f"File too large for preview (max {max_size} bytes)")
        content = content_bytes.decode('utf-8', errors='replace')
        return PreviewResponse(success=True, data=PreviewData(content=content, size=len(content_bytes)))

    from agentclaw.community.core.resources.dependencies.resource import get_file_service
    file_service = get_file_service(data_dir)
    file_path = await file_service.get_file_path(file_path_str)
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found on disk")
    file_size = file_path.stat().st_size
    if file_size > max_size:
        raise HTTPException(status_code=413, detail=f"File too large for preview (max {max_size} bytes)")
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            content = await f.read()
        return PreviewResponse(success=True, data=PreviewData(content=content, size=file_size))
    except Exception as e:
        logger.error(f"[resources.preview_content] Failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")
