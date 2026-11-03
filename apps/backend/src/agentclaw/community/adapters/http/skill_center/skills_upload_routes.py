"""Legacy skills upload routes."""

import json
from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.legacy_bot_address import legacy_bot
from agentclaw.community.adapters.http.skill_center.schemas import (
    CreateSkillRequest,
    PaginatedSkillListResponse,
    SkillDetailResponse,
    SkillMetadataResponse,
    UploadSkillErrorResponse,
    UploadSkillResponse,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillInvalidPackageError,
    LocalSkillLayoutRollbackError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.skill_center.upload_error_codes import (
    SkillUploadErrorCode,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from fastapi import (
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from .skills_common import (
    _build_upload_error_response,
    _get_path_params,
    logger,
    parse_tags,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.post(
    "/upload",
    response_model=UploadSkillResponse,
    responses={
        409: {
            "model": UploadSkillErrorResponse,
            "description": "A Local Skill edit or layout rollback is in progress.",
        },
        503: {
            "model": UploadSkillErrorResponse,
            "description": "The edit-lock backend is temporarily unavailable.",
        },
    },
)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
    )
)
async def upload_skill(
    files: list[UploadFile] = File(...),
    file_paths: str | None = Form(
        None, description="JSON array of relative paths for each file"
    ),
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    user_id: str | None = Query(None, description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    upload_service: LocalSkillUploadServiceProtocol = Injected(
        LocalSkillUploadServiceProtocol
    ),
) -> UploadSkillResponse:
    """Upload a new skill from files.

    Supports single file or multiple files (for skills with subdirectories).
    Also supports ZIP files which will be extracted automatically.
    """
    logger.info(
        f"[skills.upload_skill] Request started: user_id={user_id}, ctx.user_id={ctx.user_id}, bot_id={ctx.bot_id}, file_count={len(files)}, entity_id={entity_id}"
    )

    # Use user_id as entity_id if provided, otherwise use entity_id
    effective_entity_id_input = user_id or entity_id

    # Get effective path parameters
    (
        _effective_entity_id,
        effective_bot_id,
        _effective_engine,
        _runtime_engine,
        _effective_entity_type,
        _is_desktop,
    ) = _get_path_params(
        ctx,
        effective_entity_id_input,
        entity_type,
        bot_id,
        engine_type,
        bot_repo=bot_repo,
    )

    owner_id_for_lookup = user_id or entity_id or ctx.user_id or ""
    bot = legacy_bot(bot_repo, effective_bot_id, owner_id_for_lookup)
    if not bot:
        return UploadSkillResponse(
            success=False,
            error_code=SkillUploadErrorCode.BOT_NOT_FOUND,
            message="Bot not found.",
        )
    bot_owner_id = str(bot.get("owner_id") or "")
    if not bot_owner_id:
        return UploadSkillResponse(
            success=False,
            error_code=SkillUploadErrorCode.BOT_OWNER_MISSING,
            message="Bot ownership metadata is incomplete.",
        )

    # 状态闸门:桌面 bot 的 DB 状态(ac_bots.status)有延迟 —— BaaS 上 VM 已
    # ACTIVE 但 ac_bots 还停旧值会误拒上传。桌面 bot 先用 BaaS 实时状态覆盖;
    # 云端 bot(personal/service)直接信 DB。resolve_desktop_live_status 对非
    # 桌面 / 过程态 / BaaS 失败均返回 None → 回落 DB 状态,行为不劣于现状。
    bot_status = bot.get("status")
    if bot.get("bot_type") == "desktop":
        live_status = await run_in_threadpool(
            bot_service.resolve_desktop_live_status, bot
        )
        if live_status:
            bot_status = live_status

    if bot_status != "ACTIVE":
        return UploadSkillResponse(
            success=False,
            error_code=SkillUploadErrorCode.BOT_NOT_READY,
            message=f"Bot is not ready. Current status: {bot_status or 'UNKNOWN'}, expected ACTIVE.",
        )

    logger.info(f"[skills.upload_skill] Processing {len(files)} files")

    # Parse file_paths JSON
    parsed_paths = []
    if file_paths:
        try:
            parsed_paths = json.loads(file_paths)
            logger.info(f"[skills.upload_skill] Parsed file_paths: {parsed_paths}")
        except json.JSONDecodeError as e:
            logger.error(f"[skills.upload_skill] Failed to parse file_paths JSON: {e}")
            return UploadSkillResponse(
                success=False,
                error_code=SkillUploadErrorCode.FILE_PATHS_INVALID,
                message="file_paths must be a JSON array.",
            )
        if not isinstance(parsed_paths, list):
            return UploadSkillResponse(
                success=False,
                error_code=SkillUploadErrorCode.FILE_PATHS_INVALID,
                message="file_paths must be a JSON array.",
            )
        if len(parsed_paths) != len(files):
            return UploadSkillResponse(
                success=False,
                error_code=SkillUploadErrorCode.FILE_PATHS_COUNT_MISMATCH,
                message="file_paths length must match files length.",
            )

    try:
        uploaded_files: list[tuple[str, bytes]] = []
        for i, file in enumerate(files):
            content = await file.read()
            # Use provided relative_path or fallback to filename
            relative_path = parsed_paths[i] if i < len(parsed_paths) else file.filename
            logger.info(
                f"[skills.upload_skill] Processing file {i}: filename={file.filename}, relative_path={relative_path}"
            )
            uploaded_files.append((str(relative_path), content))

        logger.info(
            "[skills.upload_skill] Calling LocalSkillUploadService with "
            f"{len(uploaded_files)} files"
        )

        # ``ctx.user_id`` is the authenticated actor used by the collaborator
        # interceptor and audit trail.  Local Skill metadata follows the
        # historical product contract and belongs to the Bot owner, resolved
        # from the persisted Bot rather than a caller-controlled parameter.

        result = await upload_service.upload_local_skill_files(
            bot_id=effective_bot_id,
            owner_id=bot_owner_id,
            actor_id=str(ctx.user_id or bot_owner_id),
            files=uploaded_files,
        )
        skill = result["skill"]

        logger.info(
            f"[skills.upload_skill] Success: skill_id={skill.get('id')}, name={skill.get('name')}"
        )

        return UploadSkillResponse(
            success=True,
            data=SkillMetadataResponse(
                id=str(skill.get("id")) if skill.get("id") is not None else "",
                name=skill.get("name"),
                description=skill.get("description"),
                git_path=skill.get("git_path"),
                link_name=skill.get("link_name"),
                category=skill.get("category"),
                tags=parse_tags(skill.get("tags")),
                risk_tags=skill.get("risk_tags") or [],
                mcp_dependencies=skill.get("mcp_dependencies") or [],
                input_schema=skill.get("input_schema"),
                output_schema=skill.get("output_schema"),
                is_public=skill.get("is_public"),
                is_builtin=skill.get("is_builtin"),
                user_id=str(skill.get("user_id"))
                if skill.get("user_id") is not None
                else None,
                bot_id=(
                    str(skill["bolt_id"])
                    if skill.get("bolt_id") is not None
                    else "default"
                ),
                gmt_created=skill.get("gmt_created")
                if skill.get("gmt_created")
                else "",
                gmt_modified=skill.get("gmt_modified")
                if skill.get("gmt_modified")
                else "",
            ),
            message="Skill uploaded successfully",
        )
    except (
        SkillsPoolEditLockUnavailableError,
        LocalSkillEditLockUnavailableError,
    ) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except (
        SkillsPoolEditPausedError,
        LocalSkillEditBusyError,
        LocalSkillEditPausedError,
        LocalSkillLayoutRollbackError,
    ) as e:
        # A held edit/rollback lock is an ordinary request conflict, not a
        # successful response carrying a failed business envelope.  Clients
        # must be able to distinguish it from a completed upload.
        raise HTTPException(status_code=409, detail=str(e)) from e
    except HTTPException:
        raise
    except (ValueError, LocalSkillInvalidPackageError, LocalSkillTooLargeError) as e:
        logger.error(f"[skills.upload_skill] Validation error: {e}")
        return _build_upload_error_response(e)
    except Exception as e:
        logger.error(f"[skills.upload_skill] Unexpected error: {e}", exc_info=True)
        return _build_upload_error_response(e, include_upload_prefix=True)


@router.get("", response_model=PaginatedSkillListResponse)
async def list_skills(
    category: str | None = Query(None, description="Filter by category"),
    tags: str | None = Query(None, description="Filter by tags (comma-separated)"),
    is_public: bool | None = Query(None, description="Filter by public status"),
    search: str | None = Query(None, description="Search query"),
    sort_by: str = Query(
        "gmt_created", description="Sort field: gmt_created, gmt_modified, name"
    ),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    user_id: Optional[str] = Query(None, description="User ID for filtering"),
    bot_id: Optional[str] = Query(None, description="Bot ID for filtering"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> PaginatedSkillListResponse:
    """List skills with filtering and pagination (from database)."""
    try:
        service = skill_service_factory.create()

        # Parse tags
        tag_list = None
        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        if search:
            # Search mode
            skills = service.search_skills_db(
                search, user_id=user_id, limit=limit, bolt_id=bot_id
            )
            total = len(skills)
        else:
            # Filter mode
            skills = service.list_skills(
                category=category,
                tags=tag_list,
                is_public=is_public,
                user_id=user_id,
                bolt_id=bot_id,
            )
            total = len(skills)
            # Manual pagination
            skills = skills[offset : offset + limit]

        return PaginatedSkillListResponse(
            success=True,
            data=[
                SkillMetadataResponse(
                    id=str(s.get("id")) if s.get("id") is not None else "",
                    name=s.get("name"),
                    description=s.get("description"),
                    git_path=s.get("git_path"),
                    link_name=s.get("link_name"),
                    category=s.get("category"),
                    tags=parse_tags(s.get("tags")),
                    risk_tags=s.get("risk_tags") or [],
                    mcp_dependencies=s.get("mcp_dependencies") or [],
                    input_schema=s.get("input_schema"),
                    output_schema=s.get("output_schema"),
                    is_public=s.get("is_public"),
                    is_builtin=s.get("is_builtin"),
                    user_id=str(s.get("user_id"))
                    if s.get("user_id") is not None
                    else None,
                    bot_id=s.get("bolt_id") if s.get("bolt_id") else "default",
                    gmt_created=s.get("gmt_created") if s.get("gmt_created") else "",
                    gmt_modified=s.get("gmt_modified") if s.get("gmt_modified") else "",
                )
                for s in skills
            ],
            total=total,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"[list_skills] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=SkillDetailResponse)
async def create_skill(
    request: CreateSkillRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> SkillDetailResponse:
    """Create a new skill metadata entry."""
    # Scope the service to the caller's bot so repo_dir resolves to the bot's
    # skills-repo (local: unified ~/.openclaw/workspace/skills/skills-repo).
    # Without scoping it falls back to ~/.moltis/skills-repo and git skill
    # validation can't find the host source.
    entity_id, bot_id, engine_type, runtime_engine, entity_type, is_desktop = (
        _get_path_params(
            ctx, request.user_id, None, request.bot_id, None, bot_repo=bot_repo
        )
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        entity_id, bot_id, runtime_engine, entity_type, is_desktop=is_desktop
    )
    service = skill_service_factory.create(
        repo_dir=repo_dir,
        entity_id=entity_id,
        bot_id=bot_id,
        engine_type=engine_type,
    )
    try:
        skill = service.create_skill(
            name=request.name,
            description=request.description,
            skill_path=request.git_path,
            category=request.category,
            tags=request.tags,
            input_schema=request.input_schema,
            output_schema=request.output_schema,
            is_public=request.is_public,
            user_id=request.user_id,
            bolt_id=request.bot_id,
        )
        return SkillDetailResponse(
            success=True,
            data=SkillMetadataResponse(
                id=str(skill.get("id")) if skill.get("id") is not None else "",
                name=skill.get("name"),
                description=skill.get("description"),
                git_path=skill.get("git_path"),
                link_name=skill.get("link_name"),
                category=skill.get("category"),
                tags=parse_tags(skill.get("tags")),
                risk_tags=skill.get("risk_tags") or [],
                mcp_dependencies=skill.get("mcp_dependencies") or [],
                input_schema=skill.get("input_schema"),
                output_schema=skill.get("output_schema"),
                is_public=skill.get("is_public"),
                is_builtin=skill.get("is_builtin"),
                user_id=str(skill.get("user_id"))
                if skill.get("user_id") is not None
                else None,
                bot_id=skill.get("bolt_id") if skill.get("bolt_id") else "default",
                gmt_created=skill.get("gmt_created")
                if skill.get("gmt_created")
                else "",
                gmt_modified=skill.get("gmt_modified")
                if skill.get("gmt_modified")
                else "",
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
