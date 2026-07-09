"""
Identity router — thin HTTP delegation to :class:`IdentityService`.

Supports entity types: staff, proj, team. All business logic (validation, path
resolution, provider dispatch, file I/O, AGENTS.md sync) lives in
``core/services/identity.py``; this router only marshals HTTP params/responses and
maps ``ValueError`` (invalid entity/file type) to 400.

Endpoints:
- GET    /api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}
- PUT    /api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}
- GET    /api/identity/{entity_type}/{entity_id}/{file_type}
- PUT    /api/identity/{entity_type}/{entity_id}/{file_type}
- GET    /api/identity/{entity_type}/{entity_id}
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.params import Path as PathParam

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.core.bot_collaborator.interceptor.base import with_interceptors
from agentclaw.community.core.bot_collaborator.interceptor.collaborator import (
    CollaboratorPermissionInterceptor,
)
from agentclaw.community.core.services.identity import (
    IdentityFileContent,
    IdentityFileResponse,
    IdentityFileUpdateResponse,
    IdentityFileListResponse,
    BotIdentityFileResponse,
    BotIdentityFileUpdateResponse,
    IdentityService,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/identity", tags=["identity"])


# ==================== Bot Level APIs ====================
# NOTE: Must be defined BEFORE entity routes (more specific path first).

@router.get("/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}", response_model=BotIdentityFileResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$entity_id",
    persist_audit_log=False,
))
async def get_bot_identity_file(
    entity_type: str = PathParam(...),
    entity_id: str = PathParam(...),
    bot_id: str = PathParam(..., description="Bot ID"),
    file_type: str = PathParam(..., description="File type"),
    user_id: Optional[str] = Query(None, description="Operator user ID"),
    publish_id: Optional[str] = Query(None, description="Publish ID for reading from published bot device"),
    engine_type: Optional[str] = Query(None, description="Engine type override"),
    ctx: RequestContext = Depends(get_request_context),
    identity_service: IdentityService = Injected(IdentityService),
) -> BotIdentityFileResponse:
    """Get bot-level identity file content."""
    operator_id = user_id or ctx.user_id
    logger.info(
        "[identity.get_bot_identity_file] operator=%s, entity=%s/%s, bot=%s, file=%s, publish_id=%s",
        operator_id, entity_type, entity_id, bot_id, file_type, publish_id,
    )
    try:
        return await identity_service.get_bot_file(
            entity_type, entity_id, bot_id, file_type, operator_id,
            publish_id=publish_id, engine_type=engine_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}", response_model=BotIdentityFileUpdateResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$entity_id",
))
async def update_bot_identity_file(
    entity_type: str,
    entity_id: str,
    bot_id: str,
    file_type: str,
    request: IdentityFileContent,
    user_id: Optional[str] = Query(None, description="Operator user ID"),
    engine_type: Optional[str] = Query(None, description="Engine type override"),
    ctx: RequestContext = Depends(get_request_context),
    identity_service: IdentityService = Injected(IdentityService),
) -> BotIdentityFileUpdateResponse:
    """Update bot-level identity file content."""
    operator_id = user_id or ctx.user_id
    logger.info(
        "[identity.update_bot_identity_file] operator=%s, entity=%s/%s, bot=%s, file=%s",
        operator_id, entity_type, entity_id, bot_id, file_type,
    )
    try:
        return await identity_service.update_bot_file(
            entity_type, entity_id, bot_id, file_type, request.content, operator_id,
            engine_type=engine_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== Entity Level APIs ====================

@router.get("/{entity_type}/{entity_id}/{file_type}", response_model=IdentityFileResponse)
async def get_entity_identity_file(
    entity_type: str,
    entity_id: str,
    file_type: str,
    user_id: Optional[str] = Query(None, description="Operator user ID (optional, defaults to current user)"),
    ctx: RequestContext = Depends(get_request_context),
    identity_service: IdentityService = Injected(IdentityService),
) -> IdentityFileResponse:
    """Get entity-level identity file content."""
    operator_id = user_id or ctx.user_id
    logger.info(f"[identity.get_entity_identity_file] operator={operator_id}, entity={entity_type}/{entity_id}, file={file_type}")
    try:
        return await identity_service.get_entity_file(entity_type, entity_id, file_type, operator_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{entity_type}/{entity_id}/{file_type}", response_model=IdentityFileUpdateResponse)
async def update_entity_identity_file(
    entity_type: str,
    entity_id: str,
    file_type: str,
    request: IdentityFileContent,
    user_id: Optional[str] = Query(None, description="Operator user ID (optional, defaults to current user)"),
    ctx: RequestContext = Depends(get_request_context),
    identity_service: IdentityService = Injected(IdentityService),
) -> IdentityFileUpdateResponse:
    """Update entity-level identity file content."""
    operator_id = user_id or ctx.user_id
    logger.info(f"[identity.update_entity_identity_file] operator={operator_id}, entity={entity_type}/{entity_id}, file={file_type}")
    try:
        return await identity_service.update_entity_file(entity_type, entity_id, file_type, request.content, operator_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{entity_type}/{entity_id}", response_model=IdentityFileListResponse)
async def list_entity_identity_files(
    entity_type: str,
    entity_id: str,
    user_id: Optional[str] = Query(None, description="Operator user ID (optional, defaults to current user)"),
    ctx: RequestContext = Depends(get_request_context),
    identity_service: IdentityService = Injected(IdentityService),
) -> IdentityFileListResponse:
    """List all entity-level identity files."""
    operator_id = user_id or ctx.user_id
    logger.info(f"[identity.list_entity_identity_files] operator={operator_id}, entity={entity_type}/{entity_id}")
    try:
        return await identity_service.list_entity_files(entity_type, entity_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
