"""

Migrated from: services/openclawserver/server/skills
Unified Skills router for skill management, activation, and metadata.
Combines functionality from skills, skills_metadata, and skillset_switch routers.
"""
import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.dependencies import (
    get_request_context,
    RequestContext,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import skill_admin
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.skill_center.schemas import (
    ActivateRequest,
    ActivateResponse,
    ActivateSkillFailedItem,
    ActivateSkillSetRequest,
    ActivateSkillSetResponse,
    ActivateSkillsRequest,
    ActivateSkillsResponse,
    ActivateSkillsResults,
    ActivateSkillSuccessItem,
    ActiveSkillSetsResponse,
    ActiveSkillsResponse,
    AddSkillMemberRequest,
    BatchAddMembersResponse,
    BatchAddMembersResult,
    BatchAddSkillMembersRequest,
    CreateSkillRequest,
    CurrentSkillSetResponse,
    DeactivateResponse,
    DeactivateSkillSetRequest,
    DeactivateSkillSetResponse,
    DownloadUrlResponse,
    FileContentResponse,
    FileStructureResponse,
    MarketListResponse,
    MarketSearchResponse,
    MarketTagsResponse,
    MarketTreeResponse,
    MessageResponse,
    PaginatedSkillListResponse,
    PublishSkillRequest,
    PublishStatusResponse,
    SaveSkillParametersRequest,
    SearchRequest,
    SearchResponse,
    SkillDetailResponse,
    SkillListResponse,
    SkillMemberListResponse,
    SkillMemberOperationResponse,
    SkillMemberResponse,
    SkillMetadataResponse,
    SkillParametersResponse,
    SkillReadmeResponse,
    SwitchSkillSetRequest,
    SwitchSkillSetResponse,
    SyncSkillSetRequest,
    SyncSkillsRequest,
    SyncSkillsResponse,
    SyncSkillsResult,
    SyncStatusResponse,
    UpdateMCPDependenciesRequest,
    UpdateRiskTagsRequest,
    UpdateSkillMemberRoleRequest,
    UpdateSkillRequest,
    UploadSkillResponse,
    VersionListResponse,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.skill_center.constants import LOCK_HELD_ERRORS
from agentclaw.community.core.skill_center.errors import (
    SkillDeleteConsistencyError,
    SkillReferencedBySkillSetError,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditGuard,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from agentclaw.community.api.skill_member_service import SkillMemberServiceProtocol
from agentclaw.community.api.skill_parameter_service_factory import SkillParameterServiceFactoryProtocol
from agentclaw.community.api.skill_publish_service import SkillPublishServiceProtocol
from agentclaw.community.api.runtime_layout_probe_service import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeServiceProtocol,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_activator_factory import SkillSetActivatorFactoryProtocol
from agentclaw.community.api.skill_set_service_factory import SkillSetServiceFactoryProtocol
from agentclaw.community.api.skill_set_switcher_factory import SkillSetSwitcherFactoryProtocol
from agentclaw.community.core.config_compose.teclaw_paths import to_local_skill_engine_path
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
)
from agentclaw.community.core.devices.services.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    InterceptedResponse,
    InterceptorContext,
    with_interceptors,
)
from agentclaw.community.core.bot_collaborator.interceptor.extractors import PermissionParams

DEFAULT_ENGINE_TYPE = "openclaw"
_UPLOAD_SIGN_URL_EXPIRES = 7200


logger = get_logger()

router = APIRouter(prefix="/api/skills", tags=["skills"])

BOT_RUNTIME_UNAVAILABLE_MESSAGE = "当前 Bot 的运行环境暂不可用，请重新启动 Bot 后重试。"
_BOT_RUNTIME_UNAVAILABLE_MARKERS = (
    "404 not found",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "timeout",
    "connection refused",
    "connection reset",
    "connecterror",
    "readtimeout",
    "requesterror",
    "proxypass",
    "agentclawproxy",
    "sandbox id is required",
)
_FILESYSTEM_LAYOUT_ENGINES = frozenset(
    {"openclaw", "claude_code", "aicoding", "hermes"}
)


def _desktop_active_root_from_probe(
    probe: RuntimeLayoutProbeResult,
    *,
    legacy_fallback: Path,
) -> Path:
    """Use the Engine-owned active root, with one old-image fallback."""
    if probe.status is RuntimeLayoutProbeStatus.READY:
        resolved_layout = probe.evidence.get("resolved_layout")
        active_root = (
            resolved_layout.get("active_root")
            if isinstance(resolved_layout, dict)
            else None
        )
        if (
            not isinstance(active_root, str)
            or not active_root
            or not Path(active_root).is_absolute()
            or ".." in Path(active_root).parts
        ):
            raise RuntimeError("runtime layout probe omitted a valid active root")
        return Path(active_root)

    if (
        probe.status is RuntimeLayoutProbeStatus.NOT_CAPABLE
        and probe.evidence.get("reason") == "runtime_layout_probe_endpoint_absent"
    ):
        return legacy_fallback

    raise RuntimeError(
        "runtime layout probe did not resolve an authoritative active root: "
        f"status={probe.status.value} reason={probe.evidence.get('reason')}"
    )


def _normalize_upload_error_message(error_msg: str) -> str:
    lowered = error_msg.lower()
    if any(marker in lowered for marker in _BOT_RUNTIME_UNAVAILABLE_MARKERS):
        return BOT_RUNTIME_UNAVAILABLE_MESSAGE
    return error_msg


def _build_uploaded_zip(uploaded_files: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    paths = [f.get("relative_path") or f.get("filename") for f in uploaded_files]
    common = os.path.commonpath(paths) if len(paths) > 1 else ""
    if common and not any(p == common for p in paths):
        prefix_len = len(common.rstrip("/")) + 1
    else:
        prefix_len = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_info in uploaded_files:
            relative_path = file_info.get("relative_path") or file_info.get("filename")
            arcname = relative_path[prefix_len:] if prefix_len else relative_path
            zf.writestr(arcname, file_info.get("content", b""))
    return buf.getvalue()


def _build_upload_oss_path(skill_name: str, version: str = "1.0.0", timestamp: str = None) -> str:
    from agentclaw.community.core.config import sofa
    ts = timestamp or str(int(time.time()))
    # 从配置读取 OSS 上传路径前缀，没配置则用默认值
    skill_config = sofa.sofa_config.user_config.get("skill", {}).get("oss", {})
    prefix = skill_config.get("upload_prefix", "aidesktop/aidesktop_pre/bolt_shared/skills-upload")
    return f"{prefix}/{skill_name}/{ts}/{version}.zip"


# ==================== Helper Functions ====================

def parse_tags(skill_tags) -> list[str]:
    """Parse tags from database (JSON string) to list."""
    if not skill_tags:
        return []
    if isinstance(skill_tags, list):
        return skill_tags
    try:
        return json.loads(skill_tags)
    except (json.JSONDecodeError, TypeError):
        return []


def _get_path_params(
    ctx: RequestContext,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> tuple:
    """Resolve effective path parameters for skill operations.

    Priority for engine_type:
      1. Caller-supplied override (engine_type query/body param).
      2. Bot record's active_engine (looked up by bot_id + ctx.user_id).
      3. DEFAULT_ENGINE_TYPE.

    Also looks up ``ac_bots.bot_type`` to decide ``is_desktop``: desktop bots
    route to the agentbox engine-view path; personal/service bots stay on the
    cloud OSS-view path (engine ``_convert_path`` rewrites their prefix).

    NOTE: do NOT use ``device_provider == "baas"`` for this decision — service
    bots' device_provider is also baas but their bot_type is ``"service"``, not
    ``"desktop"``, and they MUST stay on the cloud branch.

    owner_id resolution for bot_type lookup:
      entity_id (if provided) takes precedence over ctx.user_id.
      Reason: for public bots the caller may supply entity_id = bot owner's ID
      while ctx.user_id is the current viewer; bot_type belongs to the owner.

    Returns:
        Tuple of (effective_entity_id, effective_bot_id,
                  effective_engine_type, effective_entity_type, is_desktop)

    Raises:
        HTTPException 403: if entity_type is staff and entity_id does not match ctx.user_id (IDOR prevention).
    """
    effective_entity_type = entity_type if entity_type else "staff"

    # IDOR prevention: staff-type entity_id must match the authenticated user
    # if effective_entity_type == "staff":
    #     if entity_id and entity_id != ctx.user_id:
    #         logger.warning(
    #             "[_get_path_params] IDOR blocked: user=%s attempted to access entity_id=%s (staff)",
    #             ctx.user_id, entity_id,
    #         )
    #         raise HTTPException(status_code=403, detail="无权操作其他用户的资源")

    effective_entity_id = entity_id if entity_id else (ctx.user_id if ctx.user_id else "default")
    effective_bot_id = bot_id if bot_id else (ctx.bot_id if ctx.bot_id else "default")

    # Use entity_id as owner when supplied (entity_id = bot owner; ctx.user_id = current viewer).
    owner_id_for_lookup = entity_id if entity_id else (ctx.user_id or "")

    effective_engine = resolve_engine_for_bot(
        bot_id=effective_bot_id,
        owner_id=owner_id_for_lookup,
        override=engine_type,
        bot_repo=bot_repo,
    )
    is_desktop = False
    try:
        bot = bot_repo.get_by_id_and_owner(effective_bot_id, owner_id_for_lookup)
        if bot and bot.get("bot_type") == "desktop":
            is_desktop = True
    except Exception as e:
        logger.warning(
            "[_get_path_params] bot_type lookup failed for "
            "bot_id=%s owner=%s: %s — defaulting is_desktop=False",
            effective_bot_id, owner_id_for_lookup, e,
        )

    return effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop


def _resolve_teclaw_local_skill(resolver, bot_id: str, owner_id: str):
    """``(is_teclaw, local_skill_path_adapter)`` for a bot's local-skill device-fs.

    teclaw owns its local-skill files (engine-owned model): the DB records the
    minimal ``local://skills-local/<name>`` and the device-fs paths are expanded to
    the workspace namespace. Resolving the provider is best-effort — any failure
    (no binding yet, resolver error) falls back to the unchanged non-teclaw
    behavior ``(False, None)`` so arca/baas/local paths are untouched.
    """
    try:
        if resolver.resolve_for_bot(bot_id, owner_id).provider == "teclaw":
            return True, to_local_skill_engine_path
    except Exception as e:
        logger.info(f"[skills] provider resolve skipped ({e}); treating as non-teclaw")
    return False, None


def _get_skill_by_id_or_link_name(service, skill_id: str, *bolt_ids: str | None):
    """Look up a skill by numeric ID or link_name across likely bot scopes.

    README requests may arrive without the real bot id in the request context.
    Numeric IDs are globally unique, while link_name lookup is bot-scoped; try
    the effective/request bot candidates first and then a global lookup.
    """
    if skill_id.isdigit():
        skill = service.get_skill(skill_id)
        if skill:
            return skill

    seen: set[str | None] = set()
    for bolt_id in (*bolt_ids, None):
        if bolt_id in seen:
            continue
        seen.add(bolt_id)
        skill = service.get_skill_by_link_name(skill_id, bolt_id=bolt_id)
        if skill:
            return skill
    return None


def _require_pool_runtime_sync_success(
    service,
    sync_result,
    *,
    detail: str,
) -> None:
    """Fail closed when a Pool-owned CRUD could not reach runtime state.

    Legacy bots retain their historical best-effort behavior. Pool-owned I/O
    cannot do that safely: its DB locator, canonical files and active mapping
    must describe one committed layout.
    """

    if getattr(service, "runtime_uses_pool_paths", False) is not True:
        return
    if not isinstance(sync_result, dict) or sync_result.get("success") is not True:
        raise HTTPException(status_code=502, detail=detail)


# ==================== Core CRUD APIs ====================

@router.post("/upload", response_model=UploadSkillResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$user_id",
))
async def upload_skill(
    files: list[UploadFile] = File(...),
    file_paths: str | None = Form(None, description="JSON array of relative paths for each file"),
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    user_id: str | None = Query(None, description="User ID"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    edit_guard: SkillsPoolEditGuard = Injected(SkillsPoolEditGuard),
) -> UploadSkillResponse:
    """Upload a new skill from files.

    Supports single file or multiple files (for skills with subdirectories).
    Also supports ZIP files which will be extracted automatically.
    """
    logger.info(f"[skills.upload_skill] Request started: user_id={user_id}, ctx.user_id={ctx.user_id}, bot_id={ctx.bot_id}, file_count={len(files)}, entity_id={entity_id}")

    # Use user_id as entity_id if provided, otherwise use entity_id
    effective_entity_id_input = user_id or entity_id

    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, effective_entity_id_input, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    owner_id_for_lookup = user_id or entity_id or ctx.user_id or ""
    bot = bot_repo.get_by_id_and_owner(effective_bot_id, owner_id_for_lookup)
    if not bot:
        return UploadSkillResponse(success=False, message="Bot not found.")
    bot_owner_id = str(bot.get("owner_id") or "")
    if not bot_owner_id:
        return UploadSkillResponse(
            success=False,
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
            message=f"Bot is not ready. Current status: {bot_status or 'UNKNOWN'}, expected ACTIVE."
        )

    # teclaw owns its local-skill files (engine-owned model): record the minimal
    # logical path (local://skills-local/<name>) and forward writes per-file to the
    # draft container, expanding the path to the workspace namespace at the
    # device-fs seam.
    is_teclaw, local_skill_adapter = _resolve_teclaw_local_skill(resolver, effective_bot_id, effective_entity_id)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop, is_teclaw=is_teclaw)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    logger.info(f"[skills.upload_skill] Paths: skills_dir={skills_dir}, local_dir={local_dir}, repo_dir={repo_dir}, is_teclaw={is_teclaw}")

    # Create per-request service instance with user-specific paths. teclaw gets the
    # path adapter that expands skills-local/... → workspace/skills-local/...
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        local_skill_path_adapter=local_skill_adapter,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
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
                message="file_paths must be a JSON array."
            )
        if not isinstance(parsed_paths, list):
            return UploadSkillResponse(
                success=False,
                message="file_paths must be a JSON array."
            )
        if len(parsed_paths) != len(files):
            return UploadSkillResponse(
                success=False,
                message="file_paths length must match files length."
            )

    try:
        # Convert UploadFile list to the format expected by upload_skill
        uploaded_files = []
        for i, file in enumerate(files):
            content = await file.read()
            # Use provided relative_path or fallback to filename
            relative_path = parsed_paths[i] if i < len(parsed_paths) else file.filename
            logger.info(f"[skills.upload_skill] Processing file {i}: filename={file.filename}, relative_path={relative_path}")
            uploaded_files.append({
                "filename": file.filename,
                "relative_path": relative_path,
                "content": content
            })

        logger.info(f"[skills.upload_skill] Calling service.upload_skill with {len(uploaded_files)} files")

        # ``ctx.user_id`` is the authenticated actor used by the collaborator
        # interceptor and audit trail.  Local Skill metadata follows the
        # historical product contract and belongs to the Bot owner, resolved
        # from the persisted Bot rather than a caller-controlled parameter.

        # Call the service method (async)
        edit_lease = edit_guard.acquire_for_edit(
            scope=BotSkillLayoutScope(
                env=str(bot["env"]),
                entity_id=str(bot["entity_id"]),
                bot_id=effective_bot_id,
            )
        )
        try:
            skill = await service.upload_skill(
                uploaded_files,
                user_id=bot_owner_id,
                bolt_id=effective_bot_id,
            )
        finally:
            edit_guard.release(edit_lease)

        logger.info(f"[skills.upload_skill] Success: skill_id={skill.get('id')}, name={skill.get('name')}")

        return UploadSkillResponse(
            success=True,
            data=SkillMetadataResponse(
                id=str(skill.get('id')) if skill.get('id') is not None else "",
                name=skill.get('name'),
                description=skill.get('description'),
                git_path=skill.get('git_path'),
                link_name=skill.get('link_name'),
                category=skill.get('category'),
                tags=parse_tags(skill.get('tags')),
                risk_tags=skill.get('risk_tags') or [],
                mcp_dependencies=skill.get('mcp_dependencies') or [],
                input_schema=skill.get('input_schema'),
                output_schema=skill.get('output_schema'),
                is_public=skill.get('is_public'),
                is_builtin=skill.get('is_builtin'),
                user_id=str(skill.get('user_id')) if skill.get('user_id') is not None else None,
                bot_id=(
                    str(skill["bolt_id"])
                    if skill.get("bolt_id") is not None
                    else "default"
                ),
                gmt_created=skill.get('gmt_created') if skill.get('gmt_created') else "",
                gmt_modified=skill.get('gmt_modified') if skill.get('gmt_modified') else ""
            ),
            message="Skill uploaded successfully"
        )
    except SkillsPoolEditPausedError as e:
        return UploadSkillResponse(success=False, message=str(e))
    except ValueError as e:
        error_msg = str(e)
        logger.error(f"[skills.upload_skill] Validation error: {error_msg}")
        return UploadSkillResponse(
            success=False,
            message=_normalize_upload_error_message(error_msg)
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[skills.upload_skill] Unexpected error: {error_msg}", exc_info=True)
        return UploadSkillResponse(
            success=False,
            message=_normalize_upload_error_message(f"Upload failed: {error_msg}")
        )


@router.get("", response_model=PaginatedSkillListResponse)
async def list_skills(
    category: str | None = Query(None, description="Filter by category"),
    tags: str | None = Query(None, description="Filter by tags (comma-separated)"),
    is_public: bool | None = Query(None, description="Filter by public status"),
    search: str | None = Query(None, description="Search query"),
    sort_by: str = Query("gmt_created", description="Sort field: gmt_created, gmt_modified, name"),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    user_id: Optional[str] = Query(None, description="User ID for filtering"),
    bot_id: Optional[str] = Query(None, description="Bot ID for filtering"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
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
            skills = service.search_skills_db(search, user_id=user_id, limit=limit, bolt_id=bot_id)
            total = len(skills)
        else:
            # Filter mode
            skills = service.list_skills(
                category=category,
                tags=tag_list,
                is_public=is_public,
                user_id=user_id,
                bolt_id=bot_id
            )
            total = len(skills)
            # Manual pagination
            skills = skills[offset:offset + limit]

        return PaginatedSkillListResponse(
            success=True,
            data=[SkillMetadataResponse(
                id=str(s.get('id')) if s.get('id') is not None else "",
                name=s.get('name'),
                description=s.get('description'),
                git_path=s.get('git_path'),
                link_name=s.get('link_name'),
                category=s.get('category'),
                tags=parse_tags(s.get('tags')),
                risk_tags=s.get('risk_tags') or [],
                mcp_dependencies=s.get('mcp_dependencies') or [],
                input_schema=s.get('input_schema'),
                output_schema=s.get('output_schema'),
                is_public=s.get('is_public'),
                is_builtin=s.get('is_builtin'),
                user_id=str(s.get('user_id')) if s.get('user_id') is not None else None,
                bot_id=s.get('bolt_id') if s.get('bolt_id') else "default",
                gmt_created=s.get('gmt_created') if s.get('gmt_created') else "",
                gmt_modified=s.get('gmt_modified') if s.get('gmt_modified') else ""
            ) for s in skills],
            total=total,
            limit=limit,
            offset=offset
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
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SkillDetailResponse:
    """Create a new skill metadata entry."""
    # Scope the service to the caller's bot so repo_dir resolves to the bot's
    # skills-repo (local: unified ~/.openclaw/workspace/skills/skills-repo).
    # Without scoping it falls back to ~/.moltis/skills-repo and git skill
    # validation can't find the host source.
    entity_id, bot_id, engine_type, entity_type, is_desktop = _get_path_params(
        ctx, request.user_id, None, request.bot_id, None, bot_repo=bot_repo
    )
    repo_dir = path_factory.get_bot_skills_repo_dir(
        entity_id, bot_id, engine_type, entity_type, is_desktop=is_desktop
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
            bolt_id=request.bot_id
        )
        return SkillDetailResponse(success=True, data=SkillMetadataResponse(
            id=str(skill.get('id')) if skill.get('id') is not None else "",
            name=skill.get('name'),
            description=skill.get('description'),
            git_path=skill.get('git_path'),
            link_name=skill.get('link_name'),
            category=skill.get('category'),
            tags=parse_tags(skill.get('tags')),
            risk_tags=skill.get('risk_tags') or [],
            mcp_dependencies=skill.get('mcp_dependencies') or [],
            input_schema=skill.get('input_schema'),
            output_schema=skill.get('output_schema'),
            is_public=skill.get('is_public'),
            is_builtin=skill.get('is_builtin'),
            user_id=str(skill.get('user_id')) if skill.get('user_id') is not None else None,
            bot_id=skill.get('bolt_id') if skill.get('bolt_id') else "default",
            gmt_created=skill.get('gmt_created') if skill.get('gmt_created') else "",
            gmt_modified=skill.get('gmt_modified') if skill.get('gmt_modified') else ""
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== Activation APIs ====================

@router.get("/active/list", response_model=ActiveSkillsResponse)
async def get_active_skills(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    runtime_layout_probe: RuntimeLayoutProbeServiceProtocol = Injected(RuntimeLayoutProbeServiceProtocol),
) -> ActiveSkillsResponse:
    """Get list of currently active skills (symlinked in skills directory)."""
    logger.info(f"[skills.get_active_skills] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, entity_id={entity_id}")

    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    runtime_skills_dir = skills_dir
    if is_desktop and effective_engine in _FILESYSTEM_LAYOUT_ENGINES:
        probe = await runtime_layout_probe.probe_bot(
            bot_id=effective_bot_id,
            user_id=effective_entity_id,
            engine=effective_engine,
        )
        runtime_skills_dir = _desktop_active_root_from_probe(
            probe,
            legacy_fallback=skills_dir,
        )
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    if is_desktop:
        active_skills = await service.get_active_skills_from_device(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            active_dir=runtime_skills_dir,
        )
    else:
        active_skills = service.get_active_skills()
    logger.info(f"[skills.get_active_skills] Found {len(active_skills)} active skills")
    return ActiveSkillsResponse(
        success=True,
        data=[s.to_dict() for s in active_skills],
        count=len(active_skills)
    )


# ==================== SkillSet APIs (固定路径必须在动态路径之前) ====================

@router.get("/skillset/current", response_model=CurrentSkillSetResponse, deprecated=True)
async def get_current_skill_set(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    switcher_factory: SkillSetSwitcherFactoryProtocol = Injected(SkillSetSwitcherFactoryProtocol),
) -> CurrentSkillSetResponse:
    """[DEPRECATED] Get the currently active skill set.

    请使用 GET /skillset/active 获取所有激活的能力集列表。
    此接口将在未来版本中移除。
    """
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    switcher = switcher_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )
    current = switcher.get_current_skill_set()

    return CurrentSkillSetResponse(success=True, data=current)


@router.post("/skillset/switch", response_model=SwitchSkillSetResponse, deprecated=True)
async def switch_skill_set(
    request: SwitchSkillSetRequest,
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    switcher_factory: SkillSetSwitcherFactoryProtocol = Injected(SkillSetSwitcherFactoryProtocol),
) -> SwitchSkillSetResponse:
    """[DEPRECATED] Switch to a new skill set (batch deactivate current, activate new).

    请使用 POST /skillset/activate 和 POST /skillset/deactivate 替代。
    此接口将在未来版本中移除。

    Args:
        proxy_token: agentclawproxy token for syncing symlinks to remote device.
    """
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    switcher = switcher_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )
    result = await switcher.switch_to_skill_set(
        skill_set_id=request.skill_set_id,
        user_id=ctx.user_id,
        proxy_token=request.proxy_token
    )

    return SwitchSkillSetResponse(
        success=result.success,
        message=result.message,
        data={
            "activated": result.activated,
            "deactivated": result.deactivated,
            "failed": result.failed
        }
    )


@router.post("/skillset/sync", response_model=SwitchSkillSetResponse)
async def sync_skill_set(
    request: SyncSkillSetRequest,
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    switcher_factory: SkillSetSwitcherFactoryProtocol = Injected(SkillSetSwitcherFactoryProtocol),
) -> SwitchSkillSetResponse:
    """Sync a skill set to active skills without deactivating others."""
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    switcher = switcher_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )
    result = await switcher.sync_skill_set_to_active(
        skill_set_id=request.skill_set_id,
        user_id=ctx.user_id
    )
    return SwitchSkillSetResponse(
        success=result.success,
        message=result.message,
        data={
            "activated": result.activated,
            "deactivated": result.deactivated,
            "failed": result.failed
        }
    )


# ==================== Multi-SkillSet Activation APIs ====================

@router.post("/skillset/activate", response_model=ActivateSkillSetResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$request.bot_id",
    owner_id="$request.entity_id",
))
async def activate_skill_set(
    request: ActivateSkillSetRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    activator_factory: SkillSetActivatorFactoryProtocol = Injected(SkillSetActivatorFactoryProtocol),
) -> ActivateSkillSetResponse:
    """激活单个能力集（增量激活，不清除其他已激活的能力集）

    支持多个能力集同时激活。默认能力集始终处于激活状态，无需调用此接口。

    Args:
        skill_set_id: 要激活的能力集 ID
        proxy_token: agentclawproxy token，用于同步软链到远程设备
    """
    # Get effective path parameters from request body
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(
        ctx, request.entity_id, request.entity_type, request.bot_id, request.engine_type, bot_repo=bot_repo
    )

    activator = activator_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )
    result = await activator.activate_skill_set(
        skill_set_id=request.skill_set_id,
        user_id=request.entity_id,
        proxy_token=request.proxy_token
    )

    return ActivateSkillSetResponse(
        success=result.success,
        message=result.message,
        data={
            "activated": result.activated,
            "failed": result.failed
        }
    )


@router.post("/skillset/deactivate", response_model=DeactivateSkillSetResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$request.bot_id",
    owner_id="$request.entity_id",
))
async def deactivate_skill_set(
    request: DeactivateSkillSetRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    activator_factory: SkillSetActivatorFactoryProtocol = Injected(SkillSetActivatorFactoryProtocol),
) -> DeactivateSkillSetResponse:
    """取消激活单个能力集

    注意：默认能力集不允许取消激活

    Args:
        skill_set_id: 要取消激活的能力集 ID
        proxy_token: agentclawproxy token，用于同步软链到远程设备
    """
    # Get effective path parameters from request body
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(
        ctx, request.entity_id, request.entity_type, request.bot_id, request.engine_type, bot_repo=bot_repo
    )

    activator = activator_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )
    result = await activator.deactivate_skill_set(
        skill_set_id=request.skill_set_id,
        user_id=request.entity_id,
        proxy_token=request.proxy_token
    )

    return DeactivateSkillSetResponse(
        success=result.success,
        message=result.message,
        data={
            "deactivated": result.deactivated,
            "failed": result.failed
        }
    )


@router.get("/skillset/active", response_model=ActiveSkillSetsResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$entity_id",
    persist_audit_log=False,  # 只读操作，不记录日志
))
async def get_active_skill_sets(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    repo: SkillSetRepository = Injected(SkillSetRepository),
) -> ActiveSkillSetsResponse:
    """获取当前 bot 的所有激活能力集列表

    返回用户激活的能力集 + 默认能力集（始终在列表中）
    """
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # 使用 repository 直接获取所有激活的能力集
    # 注意：使用 effective_entity_id 而不是 ctx.user_id，以保持一致性
    # ctx.user_id 可能带有前缀（如 staff_xxx），而数据库存储的是纯 ID
    active_sets = repo.get_all_active_skill_sets(
        user_id=effective_entity_id,
        bolt_id=effective_bot_id
    )

    # 处理返回数据，移除 skills 字段
    for s in active_sets:
        s.pop('skills', None)
        s['bot_id'] = s.pop('bolt_id', 'default')

    return ActiveSkillSetsResponse(
        success=True,
        data=active_sets,
        count=len(active_sets)
    )


# ==================== Skill Activation APIs (动态路径) ====================

@router.post("/{skill_id}/activate", response_model=ActivateResponse)
async def activate_skill(
    skill_id: str,
    request: ActivateRequest,
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(SkillSetServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_sync_dispatcher: DeviceSyncDispatcher = Injected(DeviceSyncDispatcher),
) -> ActivateResponse:
    """Activate a skill by creating symlink."""
    logger.info(f"[skills.activate_skill] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, skill_id={skill_id}, entity_id={entity_id}")

    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    actual_skill_id = request.relative_path if request.relative_path else skill_id
    # activate_skill is async — direct await; previously the call was
    # wrapped in run_in_threadpool which silently dropped the coroutine
    # (returning a truthy coroutine object, so `if not success` never
    # triggered). Plus the second positional arg `request.source_path`
    # was being misaligned with the `user_id` parameter — switch to
    # kwargs to make the contract explicit.
    success = await service.activate_skill(
        actual_skill_id,
        user_id=ctx.user_id,
        bolt_id=effective_bot_id,
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to activate skill")
    link_name = service.get_link_name(actual_skill_id)

    # 关键修复：同步软链到设备（支持 Arca 模式）
    try:
        ctx_dev = resolver.resolve_for_bot(effective_bot_id, effective_entity_id)
        device_sync = device_sync_dispatcher.dispatch(ctx_dev)
        skill_set_service = skill_set_service_factory.create(
            user_id=ctx.user_id,
            entity_id=effective_entity_id,
            bot_id=effective_bot_id,
            engine_type=effective_engine,
            entity_type=effective_entity_type,
        )
        mapping_kwargs: dict[str, Any] = {
            "user_id": ctx.user_id,
            "bolt_id": effective_bot_id,
        }
        if service.runtime_uses_pool_paths:
            mapping_kwargs["additional_skill_paths"] = [actual_skill_id]
        symlinks = skill_set_service.get_symlink_mappings(**mapping_kwargs)
        symlinks_dict = [sm.to_dict() for sm in symlinks]
        sync_result = device_sync.sync_symlinks(symlinks_dict)
        logger.info(f"[skills.activate_skill] Device sync result: {sync_result}")
    except Exception as e:
        logger.warning(f"[skills.activate_skill] Device sync skipped or failed: {e}")
        _require_pool_runtime_sync_success(
            service,
            None,
            detail="Failed to synchronize activated skills to runtime",
        )
    else:
        _require_pool_runtime_sync_success(
            service,
            sync_result,
            detail="Failed to synchronize activated skills to runtime",
        )

    logger.info(f"[skills.activate_skill] Success: skill_id={actual_skill_id}, link_name={link_name}")
    return ActivateResponse(success=True, message="Skill activated successfully", link_name=link_name)


@router.post("/{skill_id}/deactivate", response_model=DeactivateResponse)
async def deactivate_skill(
    skill_id: str,
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(SkillSetServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_sync_dispatcher: DeviceSyncDispatcher = Injected(DeviceSyncDispatcher),
) -> DeactivateResponse:
    """Deactivate a skill by removing symlink."""
    logger.info(f"[skills.deactivate_skill] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, skill_id={skill_id}, entity_id={entity_id}")

    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    # deactivate_skill is async — must await; previously a direct sync call
    # returned an un-awaited coroutine object that the truthy `if not success`
    # check ignored, so the endpoint silently no-op'd.
    success = await service.deactivate_skill(
        skill_id,
        user_id=ctx.user_id,
        bolt_id=effective_bot_id,
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to deactivate skill")

    # 关键修复：同步软链到设备（支持 Arca 模式，即使为空也要同步以清理设备端软链）
    try:
        ctx_dev = resolver.resolve_for_bot(effective_bot_id, effective_entity_id)
        device_sync = device_sync_dispatcher.dispatch(ctx_dev)
        skill_set_service = skill_set_service_factory.create(
            user_id=ctx.user_id,
            entity_id=effective_entity_id,
            bot_id=effective_bot_id,
            engine_type=effective_engine,
            entity_type=effective_entity_type,
        )
        symlinks = skill_set_service.get_symlink_mappings(
            user_id=ctx.user_id,
            bolt_id=effective_bot_id,
        )
        symlinks_dict = [sm.to_dict() for sm in symlinks]
        sync_result = device_sync.sync_symlinks(symlinks_dict)
        logger.info(f"[skills.deactivate_skill] Device sync result: {sync_result}")
    except Exception as e:
        logger.warning(f"[skills.deactivate_skill] Device sync skipped or failed: {e}")
        _require_pool_runtime_sync_success(
            service,
            None,
            detail="Failed to synchronize deactivated skills to runtime",
        )
    else:
        _require_pool_runtime_sync_success(
            service,
            sync_result,
            detail="Failed to synchronize deactivated skills to runtime",
        )

    logger.info(f"[skills.deactivate_skill] Success: skill_id={skill_id}")
    return DeactivateResponse(success=True, message="Skill deactivated successfully")


@router.post("/deactivate-all", response_model=SwitchSkillSetResponse)
async def deactivate_all_skills(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    switcher_factory: SkillSetSwitcherFactoryProtocol = Injected(SkillSetSwitcherFactoryProtocol),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
) -> SwitchSkillSetResponse:
    """Deactivate all currently active skills."""
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    switcher = switcher_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )
    result = await switcher.deactivate_all_skills()
    return SwitchSkillSetResponse(
        success=result.success,
        message=result.message,
        data={
            "activated": result.activated,
            "deactivated": result.deactivated,
            "failed": result.failed
        }
    )


# ==================== Skill README API ====================

@router.get("/{skill_id}/readme", response_model=SkillReadmeResponse)
async def get_skill_readme(
    skill_id: str,
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    skill_center_client: SkillCenterClient = Injected(SkillCenterClient),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
) -> SkillReadmeResponse:
    """Get skill README content.

    Looks up the skill in DB by id/link_name first. If git_path is center://,
    fetch from SkillCenter file-content API; otherwise delegate to
    SkillService.get_skill_readme (handles local://, git://, etc.).
    """
    logger.info(
        "[skills.get_skill_readme] Request: user_id=%s, ctx_bot_id=%s, "
        "skill_id=%s, entity_id=%s, bot_id=%s",
        ctx.user_id, ctx.bot_id, skill_id, entity_id, bot_id,
    )

    # Start with the caller/request context only to locate the DB record.
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    initial_skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type
    )
    initial_local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        effective_entity_type,
        is_desktop=is_desktop,
        is_teclaw=False,
    )
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    initial_repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop
    )

    service = skill_service_factory.create(
        active_dir=initial_skills_dir,
        repo_dir=initial_repo_dir,
        local_dir=initial_local_dir,
        local_skill_path_adapter=None,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    skill = _get_skill_by_id_or_link_name(service, skill_id, effective_bot_id, ctx.bot_id)

    # SkillCenter branch: route center:// skills to file-content API.
    if skill and (skill.get('git_path') or '').startswith('center://'):
        try:
            result = skill_center_client.get_file_content(skill.get('name'), "SKILL.md")
            if result.get("success") and result.get("data", {}).get("content"):
                content = result["data"]["content"]
                # SkillCenter file-content 可能返回 base64 编码
                if isinstance(content, str) and not content.startswith("#"):
                    import base64
                    try:
                        content = base64.b64decode(content).decode("utf-8")
                    except Exception:
                        pass
                logger.info(f"[skills.get_skill_readme] Found in SkillCenter: skill_id={skill_id}")
                return SkillReadmeResponse(success=True, data={"content": content})
        except Exception as e:
            logger.warning(f"[skills.get_skill_readme] SkillCenter file-content failed: {e}")
        raise HTTPException(status_code=404, detail="Skill or README not found")

    # A Skill may be read while the caller is operating another Bot.  Its DB
    # ``bolt_id`` is the authoritative target; derive owner, entity type and
    # engine from that Bot instead of trusting the caller's route parameters.
    # ``skill.user_id`` is metadata ownership, but historical rows may contain
    # a collaborator ID; device ownership must still come from the Bot row.
    read_bot_id = (skill or {}).get("bolt_id") or effective_bot_id
    target_bot = None
    # ``default`` is shared by many owners.  Resolve an explicitly supplied
    # owner against the server-side environment so a same-named default Bot
    # can never select an arbitrary user's workspace.
    owner_hint = entity_id or (ctx.user_id if read_bot_id == "default" else None)
    try:
        if owner_hint:
            matches = bot_repo.get_live_by_id_owner_and_env(
                bot_id=read_bot_id,
                owner_id=owner_hint,
                env=get_current_env(),
            )
            if len(matches) > 1:
                raise HTTPException(status_code=409, detail="Skill's owning bot is ambiguous")
            if matches:
                target_bot = matches[0]

        if not target_bot and read_bot_id != "default":
            # Service Bot IDs are normally globally unique.  Fail closed if
            # they are not, rather than using repository ``.first()``.
            target_bot = bot_repo.get_unique_by_id(read_bot_id)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.warning(
            "[skills.get_skill_readme] target bot lookup failed for bot_id=%s: %s",
            read_bot_id,
            e,
        )

    # A persisted local Skill must always be read from the Bot that owns it.
    # Do not fall back to the caller's workspace when that Bot has disappeared:
    # doing so can turn a stale record into a read against an unrelated device.
    if skill and skill.get("bolt_id") and not target_bot:
        raise HTTPException(status_code=404, detail="Skill's owning bot was not found")

    read_owner_id = (target_bot or {}).get("owner_id") or effective_entity_id
    read_entity_type = (target_bot or {}).get("entity_type") or effective_entity_type
    # Do not honour a caller-provided engine override for a Skill owned by a
    # different Bot: it points at a different on-device workspace.
    read_engine = resolve_engine_for_bot(
        bot_id=read_bot_id,
        owner_id=read_owner_id,
        bot_repo=bot_repo,
    )
    if target_bot and (
        (entity_id and entity_id != read_owner_id)
        or (bot_id and bot_id != read_bot_id)
        or (engine_type and engine_type != read_engine)
    ):
        logger.warning(
            "[skills.get_skill_readme] Ignoring mismatched caller context: "
            "skill_id=%s target_bot_id=%s target_owner_id=%s target_engine=%s",
            skill_id,
            read_bot_id,
            read_owner_id,
            read_engine,
        )
    read_is_desktop = False
    if target_bot:
        read_is_desktop = target_bot.get("bot_type") == "desktop"

    read_is_teclaw, read_local_skill_adapter = _resolve_teclaw_local_skill(
        resolver, read_bot_id, read_owner_id
    )

    read_skills_dir = path_factory.get_bot_skills_dir(
        read_owner_id, read_bot_id, read_engine, read_entity_type
    )
    read_local_dir = path_factory.get_bot_skills_local_dir(
        read_owner_id,
        read_bot_id,
        read_engine,
        read_entity_type,
        is_desktop=read_is_desktop,
        is_teclaw=read_is_teclaw,
    )
    path_factory.get_bot_engine_dir(read_owner_id, read_bot_id, read_engine, read_entity_type)
    read_repo_dir = path_factory.get_bot_skills_repo_dir(
        read_owner_id, read_bot_id, read_engine, read_entity_type, is_desktop=read_is_desktop
    )

    logger.info(
        "[skills.get_skill_readme] Creating read service: skills_dir=%s, "
        "repo_dir=%s, read_bot_id=%s, read_owner_id=%s, is_teclaw=%s",
        read_skills_dir, read_repo_dir, read_bot_id, read_owner_id, read_is_teclaw,
    )

    read_service = skill_service_factory.create(
        active_dir=read_skills_dir,
        repo_dir=read_repo_dir,
        local_dir=read_local_dir,
        local_skill_path_adapter=read_local_skill_adapter,
        entity_id=read_owner_id,
        bot_id=read_bot_id,
        engine_type=read_engine,
    )

    # Default: local://, git://, or repo lookup via service.
    logger.info(
        "[skills.get_skill_readme] Calling service.get_skill_readme: "
        "skill_id=%s, user_id=%s, bot_id=%s",
        skill_id, read_owner_id, read_bot_id,
    )
    readme = await read_service.get_skill_readme(
        skill_id,
        ctx.user_id,
        read_bot_id,
        device_owner_id=read_owner_id,
    )
    if readme is None:
        raise HTTPException(status_code=404, detail="Skill or README not found")
    logger.info(f"[skills.get_skill_readme] Success: skill_id={skill_id}")
    return SkillReadmeResponse(success=True, data={"content": readme})


# ==================== Git Sync APIs ====================

@router.post("/sync-from-git", response_model=SyncSkillsResponse)
async def sync_skills_from_git(
    request: SyncSkillsRequest,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SyncSkillsResponse:
    """Sync skills from Git repository to database."""
    service = skill_service_factory.create()
    # `service.sync_skills_from_git` is the sync method on SkillService;
    # the contract scanner false-positives because the API endpoint above
    # shares the name as an async def. allow-run-in-threadpool suppresses
    # only this contract check, not other lint rules.
    results = await run_in_threadpool(service.sync_skills_from_git, user_id=request.user_id)  # allow-run-in-threadpool
    return SyncSkillsResponse(
        success=True,
        data=SyncSkillsResult(**results),
        message=f"Synced: {results['created']} created, {results['updated']} updated, {results['deleted']} deleted, {results['failed']} failed"
    )


# ==================== Market APIs ====================

_EXTRA_FIELDS = [
    "is_public", "git_path", "is_builtin", "gmt_created", "gmt_modified",
    "risk_tags", "mcp_dependencies", "user_id", "env", "bolt_id", "category",
    "skill_uuid",
]


def _merge_skill_fields(sc_skills: list[dict], name_skill_map: dict[str, dict]) -> None:
    """将 ac_skill 的额外字段 merge 进 SC 返回的技能数据。"""
    for skill in sc_skills:
        if not isinstance(skill, dict):
            continue
        local = name_skill_map.get(skill.get("name", ""))
        if not local:
            continue
        skill["id"] = local.get("id")
        for field in _EXTRA_FIELDS:
            if field in local:
                skill[field] = local[field]


@router.get("/market/local", response_model=MarketListResponse)
async def list_local_market_skills(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type override; defaults to bot's active_engine"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> MarketListResponse:
    """Get all local skills (alias for /market/list with empty path)."""
    logger.info(f"[skills.list_local_market_skills] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, entity_id={entity_id}")

    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    skills = service.get_skills_in_path("")
    logger.info(f"[skills.list_local_market_skills] Found {len(skills)} local skills")
    return MarketListResponse(success=True, data=skills, count=len(skills))


@router.get("/market/tree", response_model=MarketTreeResponse)
async def get_market_tree(
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine type override; defaults to bot's active_engine"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> MarketTreeResponse:
    """Get skill market tree structure from git repo."""
    logger.info(f"[skills.get_market_tree] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, entity_id={entity_id}")

    # Get user-specific paths
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    tree = service.get_market_tree()
    logger.info(f"[skills.get_market_tree] Found {len(tree)} top-level items")
    return MarketTreeResponse(success=True, data=tree)


@router.get("/market/list", response_model=MarketListResponse)
async def list_market_skills(
    path: str = "",
    orderby: str | None = Query(None, description="排序方式: 'latest'(最新), 'hotest'(最热)，默认按创建时间倒序"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine type override; defaults to bot's active_engine"),
) -> MarketListResponse:
    """Get all skills in marketplace (only git:// skills from database).

    排序参数：
    - orderby=latest: 按创建时间倒序
    - orderby=hotest: 按被添加到能力集的次数倒序
    """
    logger.info(f"[skills.list_market_skills] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, path={path}, orderby={orderby}, entity_id={entity_id}")

    # 验证排序参数
    if orderby and orderby not in ('latest', 'hotest'):
        raise HTTPException(status_code=400, detail="orderby must be 'latest' or 'hotest'")

    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    # 从数据库查询 git_path 以 git:// 开头的技能 (marketplace 不区分 bolt_id)
    skills = service.list_git_skills(path=path or None, bolt_id=None, orderby=orderby)

    logger.info(f"[skills.list_market_skills] Found {len(skills)} git skills from database")
    return MarketListResponse(success=True, data=skills, count=len(skills))


@router.post("/market/activate-batch", response_model=ActivateSkillsResponse)
async def activate_skills_batch(
    request: ActivateSkillsRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine type override; defaults to bot's active_engine"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(SkillSetServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_sync_dispatcher: DeviceSyncDispatcher = Injected(DeviceSyncDispatcher),
) -> ActivateSkillsResponse:
    """Batch activate skills from market."""
    logger.info(f"[skills.activate_skills_batch] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, paths={request.skill_paths}, entity_id={entity_id}")

    # Get user-specific paths
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    # activate_skills_batch is async — direct await. run_in_threadpool over
    # an async function silently discards the inner coroutine (same bug as
    # activate_skill above).
    results = await service.activate_skills_batch(
        request.skill_paths,
        user_id=ctx.user_id,
        bolt_id=effective_bot_id,
    )

    # 关键修复：批量激活后同步软链到设备（支持 Arca 模式）
    try:
        ctx_dev = resolver.resolve_for_bot(effective_bot_id, effective_entity_id)
        device_sync = device_sync_dispatcher.dispatch(ctx_dev)
        skill_set_service = skill_set_service_factory.create(
            user_id=ctx.user_id,
            entity_id=effective_entity_id,
            bot_id=effective_bot_id,
            engine_type=effective_engine,
            entity_type=effective_entity_type,
        )
        mapping_kwargs: dict[str, Any] = {
            "user_id": ctx.user_id,
            "bolt_id": effective_bot_id,
        }
        if service.runtime_uses_pool_paths:
            mapping_kwargs["additional_skill_paths"] = [
                item["path"] for item in results["success"]
            ]
        symlinks = skill_set_service.get_symlink_mappings(**mapping_kwargs)
        symlinks_dict = [sm.to_dict() for sm in symlinks]
        sync_result = device_sync.sync_symlinks(symlinks_dict)
        logger.info(f"[skills.activate_skills_batch] Device sync result: {sync_result}")
    except Exception as e:
        logger.warning(f"[skills.activate_skills_batch] Device sync skipped or failed: {e}")
        _require_pool_runtime_sync_success(
            service,
            None,
            detail="Failed to synchronize activated skills to runtime",
        )
    else:
        _require_pool_runtime_sync_success(
            service,
            sync_result,
            detail="Failed to synchronize activated skills to runtime",
        )

    logger.info(f"[skills.activate_skills_batch] Success: {len(results['success'])} activated, {len(results['failed'])} failed")
    return ActivateSkillsResponse(
        success=True,
        data=ActivateSkillsResults(
            success=[ActivateSkillSuccessItem(**item) for item in results["success"]],
            failed=[ActivateSkillFailedItem(**item) for item in results["failed"]]
        ),
        message=f"Activated {len(results['success'])} skills, {len(results['failed'])} failed"
    )


@router.post("/market/search", response_model=SearchResponse)
async def search_market_skills(
    request: SearchRequest,
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine type override; defaults to bot's active_engine"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SearchResponse:
    """Search skills in marketplace."""
    logger.info(f"[skills.search_market_skills] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, query={request.query}, entity_id={entity_id}")

    # Get user-specific paths
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    # 使用缓存搜索市场技能（复用 list_git_skills 缓存，limit=100）
    results = service.search_market_skills(request.query, limit=100)

    logger.info(f"[skills.search_market_skills] Found {len(results)} matching skills")
    return SearchResponse(success=True, data=results, count=len(results))


@router.post("/market/sync", response_model=SyncStatusResponse)
async def sync_market(
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(None, description="Engine type (default: moltis)"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SyncStatusResponse:
    """Sync skills from git repository with rate limiting.

    This endpoint performs two operations:
    1. Git pull/clone to update local skills repository
    2. Scan repository and sync skill metadata to database (only if step 1 actually ran)

    - 5分钟内重复请求不会实际同步
    - 返回详细的同步状态和下次可同步时间
    """
    logger.info(f"[skills.sync_market] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}")

    # Get user-specific paths
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    # Step 1: Sync repository (git pull/clone)
    result = await run_in_threadpool(service.sync_repo_with_lock, min_interval=300)

    # 锁被占用时（两把锁任一：进程内 GlobalSyncLock / 跨机分布式锁），
    # 说明已有同步在进行，返回成功 + 提示（而非 500 错误）。
    if result.get("error") in LOCK_HELD_ERRORS:
        return SyncStatusResponse(
            success=True,
            data={
                "synced": False,
                "message": "同步进行中，请稍后再试"
            }
        )

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("message", "同步技能仓库失败")
        )

    # Step 2: Sync skills from git to database (only if git sync actually ran)
    db_sync_result = None
    if result.get("synced"):
        logger.info("[skills.sync_market] Repository synced, syncing skills to database...")
        try:
            # See sync_skills_from_git above — same false-positive pattern.
            db_sync_result = await run_in_threadpool(service.sync_skills_from_git, user_id=ctx.user_id)  # allow-run-in-threadpool
            logger.info(f"[skills.sync_market] DB sync completed: created={db_sync_result.get('created', 0)}, updated={db_sync_result.get('updated', 0)}, deleted={db_sync_result.get('deleted', 0)}, failed={db_sync_result.get('failed', 0)}")
        except Exception as e:
            logger.error(f"[skills.sync_market] DB sync failed: {e}")
            db_sync_result = {"error": str(e)}

    logger.info(f"[skills.sync_market] Success: synced={result.get('synced', False)}")
    return SyncStatusResponse(
        success=True,
        data={
            "synced": result.get("synced", False),
            "last_sync": result.get("last_sync"),
            "next_sync_in": result.get("next_sync_in", 0),
            "db_sync": db_sync_result,
        },
        message=result.get("message", "Sync completed")
    )


@router.get("/market/sync-status", response_model=SyncStatusResponse)
async def get_sync_status(
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine type override; defaults to bot's active_engine"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SyncStatusResponse:
    """Get current sync status."""
    logger.info(f"[skills.get_sync_status] Request: user_id={ctx.user_id}, bot_id={ctx.bot_id}, entity_id={entity_id}")

    # Get user-specific paths
    # Get effective path parameters
    effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop = _get_path_params(ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo)

    # Get user-specific paths using new directory structure
    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)
    path_factory.get_bot_engine_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    # Create per-request service instance with user-specific paths
    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )

    status = service.get_sync_status()
    logger.info(f"[skills.get_sync_status] Status: last_sync={status.get('last_sync')}")
    return SyncStatusResponse(
        success=True,
        data=status,
        message="获取同步状态成功"
    )


# ==================== User Skills APIs ====================

@router.get("/user/my-skills", response_model=SkillListResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$user_id",
    persist_audit_log=False,  # 只读操作，不记录日志
))
async def list_user_skills(
    user_id: Optional[str] = Query(None, description="User ID"),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    ctx: RequestContext = Depends(get_request_context),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SkillListResponse:
    """List skills uploaded by user."""
    service = skill_service_factory.create()
    effective_bot_id = bot_id or ctx.bot_id or "default"
    skills = service.list_user_skills(user_id=user_id, bolt_id=effective_bot_id)
    return SkillListResponse(
        success=True,
        data=[SkillMetadataResponse(
            id=str(s.get('id')) if s.get('id') is not None else "",
            name=s.get('name'),
            description=s.get('description'),
            git_path=s.get('git_path'),
            link_name=s.get('link_name'),
            category=s.get('category'),
            tags=parse_tags(s.get('tags')),
            risk_tags=s.get('risk_tags') or [],
            mcp_dependencies=s.get('mcp_dependencies') or [],
            input_schema=s.get('input_schema'),
            output_schema=s.get('output_schema'),
            is_public=s.get('is_public'),
            is_builtin=s.get('is_builtin'),
            user_id=str(s.get('user_id')) if s.get('user_id') is not None else None,
            bot_id=s.get('bolt_id') if s.get('bolt_id') else "default",
            gmt_created=s.get('gmt_created') if s.get('gmt_created') else "",
            gmt_modified=s.get('gmt_modified') if s.get('gmt_modified') else ""
        ) for s in skills],
        count=len(skills)
    )


# ==================== Individual Skill CRUD APIs (MUST be after all specific routes) ====================
# These routes use path parameters and should be registered LAST to avoid catching
# specific paths like "market", "active", "user", etc.

@router.get("/{skill_id}", response_model=SkillDetailResponse)
async def get_skill(
    skill_id: str,
    user_id: Optional[str] = Query(None, description="User ID for permission check"),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SkillDetailResponse:
    """Get a skill by ID or link_name."""
    service = skill_service_factory.create()

    # Try to get by ID first (if it's a digit)
    skill = None
    if skill_id.isdigit():
        skill = service.get_skill(skill_id, user_id=user_id)

    # If not found by ID, try by link_name
    if not skill:
        skill = service.get_skill_by_link_name(skill_id)

    if not skill:
        raise HTTPException(
            status_code=500,
            detail=f"Skill lookup failed: the identifier '{skill_id}' does not match any existing skill ID or link_name"
        )
    return SkillDetailResponse(success=True, data=SkillMetadataResponse(
        id=str(skill.get('id')) if skill.get('id') is not None else "",
        name=skill.get('name'),
        description=skill.get('description'),
        git_path=skill.get('git_path'),
        link_name=skill.get('link_name'),
        category=skill.get('category'),
        tags=parse_tags(skill.get('tags')),
        risk_tags=skill.get('risk_tags') or [],
        mcp_dependencies=skill.get('mcp_dependencies') or [],
        input_schema=skill.get('input_schema'),
        output_schema=skill.get('output_schema'),
        is_public=skill.get('is_public'),
        is_builtin=skill.get('is_builtin'),
        user_id=str(skill.get('user_id')) if skill.get('user_id') is not None else None,
        gmt_created=skill.get('gmt_created') if skill.get('gmt_created') else "",
        gmt_modified=skill.get('gmt_modified') if skill.get('gmt_modified') else ""
    ))


@router.put("/{skill_id}", response_model=SkillDetailResponse)
async def update_skill(
    skill_id: str,
    request: UpdateSkillRequest,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SkillDetailResponse:
    """Update a skill."""
    service = skill_service_factory.create()
    try:
        update_data = request.model_dump(exclude_unset=True, exclude={"user_id"})
        skill = service.update_skill(skill_id, user_id=request.user_id, **update_data)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillDetailResponse(success=True, data=SkillMetadataResponse(
            id=str(skill.get('id')) if skill.get('id') is not None else "",
            name=skill.get('name'),
            description=skill.get('description'),
            git_path=skill.get('git_path'),
            link_name=skill.get('link_name'),
            category=skill.get('category'),
            tags=parse_tags(skill.get('tags')),
            risk_tags=skill.get('risk_tags') or [],
            mcp_dependencies=skill.get('mcp_dependencies') or [],
            input_schema=skill.get('input_schema'),
            output_schema=skill.get('output_schema'),
            is_public=skill.get('is_public'),
            is_builtin=skill.get('is_builtin'),
            user_id=str(skill.get('user_id')) if skill.get('user_id') is not None else None,
            bot_id=skill.get('bolt_id') if skill.get('bolt_id') else "default",
            gmt_created=skill.get('gmt_created') if skill.get('gmt_created') else "",
            gmt_modified=skill.get('gmt_modified') if skill.get('gmt_modified') else ""
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{skill_id}/risk-tags", response_model=SkillDetailResponse)
async def update_skill_risk_tags(
    skill_id: str,
    request: UpdateRiskTagsRequest,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SkillDetailResponse:
    """Update skill risk tags by ID."""
    service = skill_service_factory.create()
    try:
        skill = service.update_skill(skill_id, user_id=request.user_id, risk_tags=request.risk_tags)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillDetailResponse(success=True, data=SkillMetadataResponse(
            id=str(skill.get('id')) if skill.get('id') is not None else "",
            name=skill.get('name'),
            description=skill.get('description'),
            git_path=skill.get('git_path'),
            link_name=skill.get('link_name'),
            category=skill.get('category'),
            tags=parse_tags(skill.get('tags')),
            risk_tags=skill.get('risk_tags') or [],
            mcp_dependencies=skill.get('mcp_dependencies') or [],
            input_schema=skill.get('input_schema'),
            output_schema=skill.get('output_schema'),
            is_public=skill.get('is_public'),
            is_builtin=skill.get('is_builtin'),
            user_id=str(skill.get('user_id')) if skill.get('user_id') is not None else None,
            bot_id=skill.get('bolt_id') if skill.get('bolt_id') else "default",
            gmt_created=skill.get('gmt_created') if skill.get('gmt_created') else "",
            gmt_modified=skill.get('gmt_modified') if skill.get('gmt_modified') else ""
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _extract_skill_mutation_permission(
    skill_id: str,
    ctx: InterceptorContext,
) -> PermissionParams:
    """Resolve the persisted Bot identity used for local Skill mutations."""
    if not skill_id or ctx.injector is None:
        return PermissionParams()
    try:
        service = ctx.injector.get(SkillServiceFactoryProtocol).create()
        skill = service.get_skill(skill_id)
    except Exception:
        return PermissionParams()
    if not skill:
        return PermissionParams()
    return PermissionParams(
        bot_id=skill.get("bolt_id"),
        owner_id=skill.get("user_id"),
    )


class _FailClosedSkillMutationPermissionInterceptor(
    CollaboratorPermissionInterceptor,
):
    """Never turn a collaborator permission lookup failure into mutation access."""

    def __init__(self, *args, authorization_state_key: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._authorization_state_key = authorization_state_key

    async def before(
        self,
        ctx: InterceptorContext,
    ) -> InterceptorContext | None:
        actor_id = ctx.user.staffId if ctx.user else None
        # Preserve the pre-existing global Skill admin capability; it is not a
        # Bot-collaborator grant and therefore must not depend on that service.
        if actor_id and actor_id in skill_admin():
            ctx.metadata["permission_level"] = "SKILL_ADMIN"
            return ctx

        result = await super().before(ctx)
        if result is None:
            return None
        actor_id = ctx.metadata.get("_log_user_id")
        owner_id = ctx.metadata.get("_log_owner_id")
        if actor_id != owner_id:
            if not ctx.metadata.get("permission_level"):
                ctx.response = InterceptedResponse(
                    success=False,
                    message="协作者权限服务暂不可用",
                    error_code=503,
                )
                return None
            if ctx.request is not None:
                setattr(ctx.request.state, self._authorization_state_key, True)
            for value in ctx.route_kwargs.values():
                if isinstance(value, RequestContext):
                    value.metadata[self._authorization_state_key] = True
        return result


@router.post("/{skill_id}/mcp-dependencies", response_model=SkillDetailResponse)
@with_interceptors(_FailClosedSkillMutationPermissionInterceptor(
    params_extractor=_extract_skill_mutation_permission,
    extractor_params={"skill_id": "$skill_id"},
    persist_audit_log=True,
    authorization_state_key="skill_mcp_collaborator_authorized",
))
async def update_skill_mcp_dependencies(
    skill_id: str,
    request: UpdateMCPDependenciesRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    http_request: Request = None,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> SkillDetailResponse:
    """Update skill MCP dependencies by ID.

    权限控制：只有技能的创建者或管理员可以修改 MCP 依赖。
    """
    service = skill_service_factory.create()
    try:
        existing = service.get_skill(skill_id, user_id=user.staffId)
        if not existing:
            raise HTTPException(status_code=404, detail="Skill not found")
        skill_owner = str(existing.get("user_id", ""))
        is_local_skill = str(existing.get("git_path") or "").startswith("local://")
        collaborator_authorized = bool(
            http_request
            and getattr(
                http_request.state,
                "skill_mcp_collaborator_authorized",
                False,
            )
        )
        if (
            user.staffId not in skill_admin()
            and skill_owner
            and skill_owner != user.staffId
            and not (is_local_skill and collaborator_authorized)
        ):
            raise HTTPException(
                status_code=403,
                detail="无权修改此技能的MCP依赖，仅技能创建者、已授权协作者或管理员可操作",
            )
        # 使用认证用户ID替代请求体中的user_id，防止越权
        if request.user_id and request.user_id != user.staffId:
            logger.warning(f"[update_skill_mcp_dependencies] 权限拒绝: user={user.staffId} 尝试以 user_id={request.user_id} 修改 skill_id={skill_id} 的MCP依赖，已使用认证用户ID")
        skill = service.update_skill(skill_id, user_id=user.staffId, mcp_dependencies=request.mcp_dependencies)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return SkillDetailResponse(success=True, data=SkillMetadataResponse(
            id=str(skill.get('id')) if skill.get('id') is not None else "",
            name=skill.get('name'),
            description=skill.get('description'),
            git_path=skill.get('git_path'),
            link_name=skill.get('link_name'),
            category=skill.get('category'),
            tags=parse_tags(skill.get('tags')),
            risk_tags=skill.get('risk_tags') or [],
            mcp_dependencies=skill.get('mcp_dependencies') or [],
            input_schema=skill.get('input_schema'),
            output_schema=skill.get('output_schema'),
            is_public=skill.get('is_public'),
            is_builtin=skill.get('is_builtin'),
            user_id=str(skill.get('user_id')) if skill.get('user_id') is not None else None,
            bot_id=skill.get('bolt_id') if skill.get('bolt_id') else "default",
            gmt_created=skill.get('gmt_created') if skill.get('gmt_created') else "",
            gmt_modified=skill.get('gmt_modified') if skill.get('gmt_modified') else ""
        ))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{link_name}/id", response_model=dict[str, Any])
async def get_skill_id_by_link_name(
    link_name: str,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
) -> Dict[str, Any]:
    """Get skill ID by link_name (e.g., 'infra_demo_odps-sql-generator')."""
    service = skill_service_factory.create()
    skill = service.get_skill_by_link_name(link_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found with link_name: {link_name}")
    return {
        "success": True,
        "id": str(skill.get('id')),
        "link_name": skill.get('link_name'),
        "name": skill.get('name')
    }


# ============================================================================
# 协作者权限提取函数
# ============================================================================
async def extract_from_skill_id(skill_id: str, ctx) -> PermissionParams:
    """从 skill_id 查询 bot_id 和 owner_id.

    用于 CollaboratorPermissionInterceptor 提取权限参数。

    Args:
        skill_id: 技能 ID（由拦截器通过表达式注入）
        ctx: 拦截器上下文，ctx.injector 用于解析服务
    """
    if not skill_id:
        return PermissionParams()

    if ctx.injector is None:
        return PermissionParams()

    try:
        factory = ctx.injector.get(SkillServiceFactoryProtocol)
        service = factory.create()
    except Exception:
        return PermissionParams()

    try:
        skill = service.get_skill(skill_id)
        if not skill:
            return PermissionParams()

        return PermissionParams(
            bot_id=skill.get('bolt_id'),  # Skill.bolt_id 即 bot_id
            owner_id=skill.get('user_id'),  # Skill.user_id 即 owner_id
        )
    except Exception:
        return PermissionParams()


@router.delete("/{skill_id}", response_model=MessageResponse)
@with_interceptors(_FailClosedSkillMutationPermissionInterceptor(
    params_extractor=extract_from_skill_id,
    extractor_params={"skill_id": "$skill_id"},  # 表达式：从路由参数取值
    persist_audit_log=True,  # 记录操作审计日志
    authorization_state_key="skill_delete_collaborator_authorized",
))
async def delete_skill(
    skill_id: str,
    user_id: str | None = Query(None, description="User ID for permission check (optional, uses current user if not provided)"),
    entity_id: str | None = Query(None, description="Entity ID (pure ID, no prefix needed)"),
    entity_type: str | None = Query(None, description="Entity type (staff/proj/team, default: staff)"),
    bot_id: str | None = Query(None, description="Bot ID"),
    engine_type: str | None = Query(
        None,
        description="Legacy compatibility hint; when provided it must match the Bot active_engine",
    ),
    ctx: RequestContext = Depends(get_request_context),
    skill_repo: SkillRepository = Injected(SkillRepository),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    edit_guard: SkillsPoolEditGuard = Injected(SkillsPoolEditGuard),
) -> MessageResponse:
    """Delete a skill.

    权限控制：
    - Owner 可直接删除自己的 skill
    - 协作者（admin 角色）在持锁时可删除
    - 协作者（member 角色）返回 403
    - 管理员可直接删除（Service 层双重保障）
    """
    # 删除的物理上下文必须以已持久化 Skill 所属 Bot 为准。历史客户端
    # 不传 bot_id/entity_id，不能因此回退到 default/openclaw 并删错路径。
    # ``user_id`` is a legacy compatibility hint supplied by the caller and
    # must never override the authenticated actor.  In particular, shared
    # market Skills have no owner row, so trusting the query parameter here
    # would let any authenticated caller impersonate a configured Skill admin.
    current_user_id = ctx.user_id
    if not current_user_id:
        raise HTTPException(status_code=401, detail="未认证用户无法删除技能")
    if not skill_id.isdigit():
        raise HTTPException(status_code=400, detail="Invalid skill ID")

    skill = skill_repo.get_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    persisted_git_path = str(skill.get("git_path") or "")
    is_shared_source = (
        not skill.get("user_id")
        and persisted_git_path.startswith(("git://", "center://"))
    )
    if is_shared_source:
        service = skill_service_factory.create()
        try:
            success = await service.delete_skill(
                skill_id,
                user_id=current_user_id,
            )
            if not success:
                raise HTTPException(status_code=404, detail="Skill not found")
            return MessageResponse(
                success=True,
                message="Skill deleted successfully",
            )
        except SkillReferencedBySkillSetError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "SKILL_REFERENCED_BY_SKILL_SET",
                    "message": "请先从所有技能集中移除该技能，再删除技能",
                    "skill_set_ids": e.skill_set_ids,
                },
            ) from e
        except ValueError as e:
            error_msg = str(e)
            if "无权删除" in error_msg or "Permission denied" in error_msg:
                raise HTTPException(status_code=403, detail=error_msg) from e
            raise HTTPException(status_code=400, detail=error_msg) from e

    effective_bot_id = str(skill.get("bolt_id") or "")
    if not effective_bot_id:
        raise HTTPException(status_code=409, detail="Skill is missing its Bot identity")
    if bot_id and bot_id != effective_bot_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "SKILL_BOT_CONTEXT_MISMATCH",
                "message": "删除技能必须使用 Skill 持久化归属的 Bot",
                "bot_id": effective_bot_id,
            },
        )

    try:
        bot = (
            bot_repo.get_by_id_and_entity(effective_bot_id, entity_id)
            if entity_id
            else bot_repo.get_unique_by_id(effective_bot_id)
        )
    except BotLookupAmbiguousError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "SKILL_BOT_CONTEXT_AMBIGUOUS",
                "message": "历史 Bot ID 不唯一，请提供 entity_id 精确定位",
                "bot_id": effective_bot_id,
            },
        ) from e
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    effective_entity_id = str(
        bot.get("entity_id") or bot.get("owner_id") or ""
    )
    effective_entity_type = str(bot.get("entity_type") or "staff")
    effective_engine = str(bot.get("active_engine") or DEFAULT_ENGINE_TYPE)
    if engine_type and engine_type != effective_engine:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "SKILL_ENGINE_CONTEXT_MISMATCH",
                "message": "删除技能必须使用 Bot 当前的生效引擎",
                "active_engine": effective_engine,
            },
        )
    is_desktop = bot.get("bot_type") == "desktop"

    # teclaw deletes the skill files from the (draft) container; resolve provider
    # so the device-fs path is the workspace-namespace form.
    is_teclaw, local_skill_adapter = _resolve_teclaw_local_skill(resolver, effective_bot_id, effective_entity_id)

    skills_dir = path_factory.get_bot_skills_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type)
    local_dir = path_factory.get_bot_skills_local_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop, is_teclaw=is_teclaw)
    repo_dir = path_factory.get_bot_skills_repo_dir(effective_entity_id, effective_bot_id, effective_engine, effective_entity_type, is_desktop=is_desktop)

    service = skill_service_factory.create(
        active_dir=skills_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        local_skill_path_adapter=local_skill_adapter,
        entity_id=effective_entity_id,
        bot_owner_id=str(bot.get("owner_id") or ""),
        bot_id=effective_bot_id,
        engine_type=effective_engine,
    )
    try:
        edit_lease = edit_guard.acquire_for_edit(
            scope=BotSkillLayoutScope(
                env=str(bot["env"]),
                entity_id=str(bot["entity_id"]),
                bot_id=effective_bot_id,
            )
        )
        try:
            # 路由已先通过 CollaboratorPermissionInterceptor；仍把持久化
            # Bot owner 显式传给 Service 做 scope 双重校验，不能把协作者当成
            # Skill metadata owner。
            success = await service.delete_skill(
                skill_id,
                user_id=current_user_id,
                authorized_bot_owner_id=str(bot.get("owner_id") or ""),
                collaborator_authorization_verified=bool(
                    ctx.metadata.get("skill_delete_collaborator_authorized")
                ),
            )
        finally:
            edit_guard.release(edit_lease)
        if not success:
            raise HTTPException(status_code=404, detail="Skill not found")
        return MessageResponse(success=True, message="Skill deleted successfully")
    except SkillsPoolEditPausedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SkillDeleteConsistencyError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SkillReferencedBySkillSetError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "SKILL_REFERENCED_BY_SKILL_SET",
                "message": "请先从所有技能集中移除该技能，再删除技能",
                "skill_set_ids": e.skill_set_ids,
            },
        ) from e
    except ValueError as e:
        error_msg = str(e)
        # 权限错误返回 403
        if "无权删除" in error_msg or "Permission denied" in error_msg:
            raise HTTPException(status_code=403, detail=error_msg)
        # 其他值错误返回 400
        raise HTTPException(status_code=400, detail=error_msg)


# ==================== Skill Parameters Endpoints (设备文件版) ====================


class _SkillParameterPermissionInterceptor(CollaboratorPermissionInterceptor):
    """Fail closed for owner-backed parameter access when auth is unavailable."""

    async def before(
        self,
        ctx: InterceptorContext,
    ) -> InterceptorContext | None:
        result = await super().before(ctx)
        if result is None:
            return None
        actor_id = ctx.metadata.get("_log_user_id")
        owner_id = ctx.metadata.get("_log_owner_id")
        if (
            actor_id != owner_id
            and not ctx.metadata.get("permission_level")
        ):
            ctx.response = InterceptedResponse(
                success=False,
                message="协作者权限服务暂不可用",
                error_code=503,
            )
            return None
        return result


def _resolve_parameter_bot(
    *,
    skill: dict[str, Any],
    requested_bot_id: str,
    requested_entity_id: str,
    bot_repo: BotRepository,
) -> dict[str, Any]:
    """Resolve the trusted Bot identity that owns a Skill parameter file.

    ``SkillParameterServiceFactory`` uses its ``user_id`` argument to locate the
    Bot's active device binding.  That identity must therefore be the Bot owner,
    not the authenticated actor (who may be an ADMIN collaborator).

    The requested Bot ID is used only as a lookup key; ownership always comes
    from the Bot row. Historical ``Skill.user_id`` values may contain a
    collaborator, so they must never participate in device-owner resolution.
    Bot-private Skills are scoped by ``bolt_id``. Shared market Skills are
    identified by their trusted source scheme (``git://`` or ``center://``)
    and remain usable across Bots regardless of historical ``bolt_id`` values.
    """

    bot = bot_repo.get_by_id_and_entity(requested_bot_id, requested_entity_id)
    if not isinstance(bot, dict):
        raise HTTPException(status_code=404, detail="Bot not found")
    bot_owner_id = str(bot.get("owner_id") or "")
    if not bot_owner_id:
        raise HTTPException(
            status_code=409,
            detail="Bot ownership metadata is incomplete",
        )

    skill_bot_id = str(skill.get("bolt_id") or "")
    git_path = str(skill.get("git_path") or "")
    is_shared_market_skill = git_path.startswith(("git://", "center://"))
    if is_shared_market_skill:
        return bot

    if not skill_bot_id:
        raise HTTPException(
            status_code=409,
            detail="Skill Bot metadata is incomplete",
        )
    if requested_bot_id != skill_bot_id:
        raise HTTPException(
            status_code=409,
            detail="Skill does not belong to the requested Bot",
        )
    return bot


async def _extract_parameter_permission(
    *,
    bot_id: str,
    entity_id: str,
    ctx,
):
    """Resolve parameter authorization from the exact Bot identity scope."""

    from agentclaw.community.core.bot_collaborator.interceptor.extractors import (
        PermissionParams,
    )

    bot_repo = ctx.injector.get(BotRepository) if ctx.injector is not None else None
    bot = (
        bot_repo.get_by_id_and_entity(bot_id, entity_id)
        if bot_repo is not None
        else None
    )
    owner_id = (
        str(bot.get("owner_id") or "")
        if isinstance(bot, dict)
        else ""
    )
    # A non-empty sentinel prevents the generic interceptor from falling back
    # to its bot_id-only legacy lookup. The route will return the precise 404.
    return PermissionParams(
        bot_id=bot_id,
        owner_id=owner_id or "__parameter_bot_not_found__",
    )


async def _create_skill_parameter_service(
    *,
    parameter_service_factory: SkillParameterServiceFactoryProtocol,
    bot_id: str,
    owner_id: str,
):
    """Create the device-backed parameter service with a structured no-binding error."""

    try:
        return await parameter_service_factory.create(
            bot_id=bot_id,
            user_id=owner_id,
        )
    except DeviceNotBoundError as exc:
        raise HTTPException(
            status_code=409,
            detail="Bot has no active device",
        ) from exc


@router.get("/{skill_id}/parameters", response_model=SkillParametersResponse)
@with_interceptors(_SkillParameterPermissionInterceptor(
    params_extractor=_extract_parameter_permission,
    extractor_params={"bot_id": "$bot_id", "entity_id": "$entity_id"},
    persist_audit_log=False,
))
async def get_skill_parameters(
    skill_id: str,
    entity_id: str = Query(..., description="Entity ID (e.g., staff_xxx, proj_xxx)"),
    bot_id: str = Query(..., description="Bot ID"),
    engine_type: str = Query(..., description="Engine type (openclaw)"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    skill_repo: SkillRepository = Injected(SkillRepository),
    parameter_service_factory: SkillParameterServiceFactoryProtocol = Injected(SkillParameterServiceFactoryProtocol),
) -> SkillParametersResponse:
    """获取用户对技能的参数配置（全局，不绑定 skill_set）"""
    # 获取 skill_name
    skill = skill_repo.get_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    bot = _resolve_parameter_bot(
        skill=skill,
        requested_bot_id=bot_id,
        requested_entity_id=entity_id,
        bot_repo=bot_repo,
    )
    skill_name = skill.get('link_name') or skill.get('name')

    # 使用异步工厂函数获取参数服务
    parameter_service = await _create_skill_parameter_service(
        parameter_service_factory=parameter_service_factory,
        bot_id=str(bot["bot_id"]),
        owner_id=str(bot["owner_id"]),
    )
    parameters = parameter_service.get_skill_parameters(skill_name)

    return SkillParametersResponse(success=True, data={"parameters": parameters})


@router.post("/{skill_id}/parameters", response_model=SkillParametersResponse)
@with_interceptors(_SkillParameterPermissionInterceptor(
    params_extractor=_extract_parameter_permission,
    extractor_params={"bot_id": "$bot_id", "entity_id": "$entity_id"},
    # Keep edit-lock enforcement and audit metadata, but never serialize
    # credential-bearing parameter values.
    audit_excluded_params={"request"},
))
async def save_skill_parameters(
    skill_id: str,
    request: SaveSkillParametersRequest,
    entity_id: str = Query(..., description="Entity ID (e.g., staff_xxx, proj_xxx)"),
    bot_id: str = Query(..., description="Bot ID"),
    engine_type: str = Query(..., description="Engine type (openclaw)"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    skill_repo: SkillRepository = Injected(SkillRepository),
    parameter_service_factory: SkillParameterServiceFactoryProtocol = Injected(SkillParameterServiceFactoryProtocol),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
) -> SkillParametersResponse:
    """保存用户对技能的参数配置"""
    # 获取 skill_name 和 parameter_schema (从 SKILL.md 解析)
    skill = skill_repo.get_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    bot = _resolve_parameter_bot(
        skill=skill,
        requested_bot_id=bot_id,
        requested_entity_id=entity_id,
        bot_repo=bot_repo,
    )
    trusted_bot_id = str(bot["bot_id"])
    trusted_owner_id = str(bot["owner_id"])
    trusted_entity_id = str(bot.get("entity_id") or trusted_owner_id)
    trusted_engine_type = str(bot.get("active_engine") or engine_type)
    trusted_entity_type = str(bot.get("entity_type") or "staff")
    skill_name = skill.get('link_name') or skill.get('name')

    # Resolve is_desktop for path construction
    is_desktop = bot.get("bot_type") == "desktop"

    # teclaw owns its local-skill files: parse SKILL.md from the container via
    # device_fs; non-teclaw keeps the host-FS read (byte-identical).
    is_teclaw, local_skill_adapter = _resolve_teclaw_local_skill(
        resolver,
        trusted_bot_id,
        trusted_owner_id,
    )

    # 从 SKILL.md 实时解析 parameters 定义
    parameter_schema = []
    service = skill_service_factory.create(
        active_dir=path_factory.get_bot_skills_dir(
            trusted_entity_id,
            trusted_bot_id,
            trusted_engine_type,
            trusted_entity_type,
        ),
        repo_dir=path_factory.get_bot_skills_repo_dir(
            trusted_entity_id,
            trusted_bot_id,
            trusted_engine_type,
            trusted_entity_type,
            is_desktop=is_desktop,
        ),
        local_dir=path_factory.get_bot_skills_local_dir(
            trusted_entity_id,
            trusted_bot_id,
            trusted_engine_type,
            trusted_entity_type,
            is_desktop=is_desktop,
            is_teclaw=is_teclaw,
        ),
        local_skill_path_adapter=local_skill_adapter,
        entity_id=trusted_entity_id,
        bot_id=trusted_bot_id,
        engine_type=trusted_engine_type,
    )
    if is_teclaw:
        skill_info = await service.parse_local_skill_config(
            skill.get('git_path', ''),
            trusted_bot_id,
            trusted_owner_id,
        )
    else:
        skill_info = service._parse_skill_from_git(skill.get('git_path', ''))
    if skill_info:
        parameter_schema = skill_info.get('config', [])

    # 校验必填项
    if parameter_schema:
        for param in parameter_schema:
            if param.get('required') and not request.parameters.get(param['name']):
                raise HTTPException(
                    status_code=400,
                    detail=f"参数 {param['label']} 是必填项"
                )

    # 使用异步工厂函数获取参数服务
    parameter_service = await _create_skill_parameter_service(
        parameter_service_factory=parameter_service_factory,
        bot_id=trusted_bot_id,
        owner_id=trusted_owner_id,
    )
    await parameter_service.save_skill_parameters(skill_name, request.parameters)

    return SkillParametersResponse(success=True, data={"saved": True})


# ==================== Skill Publish ====================

@router.post("/{skill_id}/publish", response_model=PublishStatusResponse)
async def publish_skill(
    skill_id: str,
    request: PublishSkillRequest | None = None,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """触发技能发布 — developing/rejected → pending"""
    from agentclaw.community.core.skill_center.services.skill_publish_service import InvalidTransitionError

    try:
        user_id = request.user_id if request else ctx.user_id
        zip_path = request.zip_path if request else None
        package_url = request.package_url if request else None
        result = service.publish(skill_id, user_id=user_id, zip_path=zip_path, package_url=package_url, nick_name=ctx.nick_name)
        return PublishStatusResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidTransitionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{skill_id}/publish/upgrade", response_model=PublishStatusResponse)
async def publish_upgrade_skill(
    skill_id: str,
    request: PublishSkillRequest | None = None,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """升级发布 — published → 新建行 pending"""

    try:
        user_id = request.user_id if request else ctx.user_id
        result = service.publish_upgrade(skill_id, user_id=user_id)
        return PublishStatusResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{skill_id}/publish/status", response_model=PublishStatusResponse)
async def get_publish_status(
    skill_id: str,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """轮询发布状态 — 仅 pending 可调用"""

    try:
        result = service.query_status(skill_id)
        return PublishStatusResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{skill_id}/versions", response_model=VersionListResponse)
async def list_skill_versions(
    skill_id: str,
    ctx: RequestContext = Depends(get_request_context),
    service: SkillPublishServiceProtocol = Injected(SkillPublishServiceProtocol),
):
    """获取技能版本列表"""

    try:
        versions = service.list_versions(skill_id)
        return VersionListResponse(success=True, data=versions, count=len(versions))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ==================== Market Online (SkillCenter 透传) ====================


@router.get("/market/center/search", response_model=MarketSearchResponse)
async def search_market_center(
    keyword: str = Query("", description="搜索关键词"),
    tag: str = Query("", description="标签过滤"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """搜索 SkillCenter 公开市场技能（透传）"""

    try:
        result = client.search_market_skills(keyword, tag, page, page_size)
        return MarketSearchResponse(
            success=result.get("success", False),
            data=result.get("data", []),
            total=result.get("total", 0),
        )
    except Exception as e:
        logger.error("[search_market_center] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/center/tags", response_model=MarketTagsResponse)
async def get_market_tags_center(
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取 SkillCenter 市场标签列表（透传）"""

    try:
        tags = client.get_market_tags()
        return MarketTagsResponse(success=True, data=tags)
    except Exception as e:
        logger.error("[get_market_tags_center] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/market/center/{skill_code}/install", response_model=MessageResponse)
async def install_market_skill(skill_code: str):
    """从 SkillCenter 公开市场安装技能（占位，后续扩展下载+创建本地记录）"""
    return MessageResponse(success=True, message=f"技能 {skill_code} 安装接口已就绪，完整安装流程后续迭代")


# ==================== Version Download URL (SkillCenter 透传) ====================


@router.get("/{skill_id}/versions/{version_number}/download-url", response_model=DownloadUrlResponse)
async def get_version_download_url(
    skill_id: str,
    version_number: str,
    ctx: RequestContext = Depends(get_request_context),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
    client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取指定版本的下载 URL（透传 SkillCenter）"""

    service = skill_service_factory.create()
    skill = service.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill_code = skill.get("skill_uuid") or skill.get("link_name") or skill_id
    try:
        result = client.get_download_url(skill_code, version_number)
        return DownloadUrlResponse(
            success=result.get("success", False),
            data=result.get("data", {}),
            message=result.get("message", ""),
        )
    except Exception as e:
        logger.error("[get_version_download_url] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{skill_id}/file-structure", response_model=FileStructureResponse)
async def get_skill_file_structure(
        skill_id: str,
        version: str = Query("", description="版本号（可选，默认最新版本）"),
        ctx: RequestContext = Depends(get_request_context),
        skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
        client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取技能文件结构树（透传 SkillCenter）"""

    service = skill_service_factory.create()
    skill = service.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill_code = skill.get("skill_uuid") or skill.get("link_name") or skill_id
    try:
        result = client.get_file_structure(skill_code, version)
        return FileStructureResponse(
            success=result.get("success", False),
            data=result.get("data", {}),
            message=result.get("message", ""),
        )
    except Exception as e:
        logger.error("[get_skill_file_structure] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{skill_id}/file-content", response_model=FileContentResponse)
async def get_skill_file_content(
        skill_id: str,
        file_path: str = Query(..., alias="filePath", description="文件相对路径"),
        version: str = Query("", description="版本号（可选，默认最新版本）"),
        ctx: RequestContext = Depends(get_request_context),
        skill_service_factory: SkillServiceFactoryProtocol = Injected(SkillServiceFactoryProtocol),
        client: SkillCenterClient = Injected(SkillCenterClient),
):
    """获取技能指定文件内容（透传 SkillCenter）"""

    service = skill_service_factory.create()
    skill = service.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    skill_code = skill.get("skill_uuid") or skill.get("link_name") or skill_id
    try:
        result = client.get_file_content(skill_code, file_path, version)
        return FileContentResponse(
            success=result.get("success", False),
            data=result.get("data", {}),
            message=result.get("message", ""),
        )
    except Exception as e:
        logger.error("[get_skill_file_content] Error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Skill Member Management APIs ====================

@router.get("/{skill_uuid}/members", response_model=SkillMemberListResponse)
async def get_skill_members(
    skill_uuid: str,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol)
) -> SkillMemberListResponse:
    """获取技能的所有成员列表

    Args:
        skill_uuid: 技能 UUID
    """
    try:
        members = member_service.get_members_by_skill_uuid(skill_uuid)

        return SkillMemberListResponse(
            success=True,
            data=[SkillMemberResponse(**m) for m in members],
            count=len(members)
        )
    except Exception as e:
        logger.error(f"[get_skill_members] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{skill_uuid}/members", response_model=SkillMemberOperationResponse)
async def add_skill_member(
    skill_uuid: str,
    request: AddSkillMemberRequest,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol)
) -> SkillMemberOperationResponse:
    """添加技能成员

    Args:
        skill_uuid: 技能 UUID
        request: 添加成员请求，包含 user_id 和 role
    """
    try:
        member = member_service.add_member(
            skill_uuid=skill_uuid,
            user_id=request.user_id,
            role=request.role
        )

        return SkillMemberOperationResponse(
            success=True,
            data=SkillMemberResponse(**member),
            message="Member added successfully"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[add_skill_member] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{skill_uuid}/members/batch", response_model=BatchAddMembersResponse)
async def add_skill_members_batch(
    skill_uuid: str,
    request: BatchAddSkillMembersRequest,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol)
) -> BatchAddMembersResponse:
    """批量添加技能成员

    Args:
        skill_uuid: 技能 UUID
        request: 批量添加成员请求，包含 members 列表
    """
    try:
        results = member_service.add_members_batch(
            skill_uuid=skill_uuid,
            members=request.members
        )

        return BatchAddMembersResponse(
            success=True,
            data=BatchAddMembersResult(
                success=results["success"],
                failed=results["failed"]
            ),
            message=f"Added {len(results['success'])} members, {len(results['failed'])} failed"
        )
    except Exception as e:
        logger.error(f"[add_skill_members_batch] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{skill_uuid}/members/{user_id}", response_model=MessageResponse)
async def remove_skill_member(
    skill_uuid: str,
    user_id: str,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol)
) -> MessageResponse:
    """移除技能成员

    Args:
        skill_uuid: 技能 UUID
        user_id: 用户 ID
    """
    try:
        member_service.remove_member(
            skill_uuid=skill_uuid,
            user_id=user_id
        )

        return MessageResponse(
            success=True,
            message="Member removed successfully"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[remove_skill_member] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{skill_uuid}/members/{user_id}/role", response_model=SkillMemberOperationResponse)
async def update_skill_member_role(
    skill_uuid: str,
    user_id: str,
    request: UpdateSkillMemberRoleRequest,
    ctx: RequestContext = Depends(get_request_context),
    member_service: SkillMemberServiceProtocol = Injected(SkillMemberServiceProtocol)
) -> SkillMemberOperationResponse:
    """更新成员角色

    Args:
        skill_uuid: 技能 UUID
        user_id: 用户 ID
        request: 更新角色请求，包含新角色
    """
    try:
        member = member_service.update_member_role(
            skill_uuid=skill_uuid,
            user_id=user_id,
            role=request.role
        )

        return SkillMemberOperationResponse(
            success=True,
            data=SkillMemberResponse(**member),
            message="Member role updated successfully"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[update_skill_member_role] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
