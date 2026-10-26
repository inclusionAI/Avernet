"""Legacy skills readme routes."""

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    SkillReadmeResponse,
    SyncSkillsRequest,
    SyncSkillsResponse,
    SyncSkillsResult,
)
from agentclaw.community.api.skill_query_service import SkillQueryServiceProtocol
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_engine_for_bot,
    resolve_runtime_engine_for_bot,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterClient,
)
from agentclaw.community.utils.env_utils import get_current_env
from fastapi import (
    Depends,
    HTTPException,
    Query,
)
from starlette.concurrency import run_in_threadpool

from .skills_common import (
    _get_path_params,
    _get_skill_by_id_or_link_name,
    _resolve_teclaw_local_skill,
    logger,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skills", tags=["skills"])

@router.get("/{skill_id}/readme", response_model=SkillReadmeResponse)
async def get_skill_readme(
    skill_id: str,
    entity_id: str | None = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: str | None = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: str | None = Query(None, description="Bot ID (default: default)"),
    engine_type: str | None = Query(
        None, description="Engine type override; defaults to bot's active_engine"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    path_factory: WorkspacePathFactory = Injected(WorkspacePathFactory),
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
    query_service: SkillQueryServiceProtocol = Injected(SkillQueryServiceProtocol),
    skill_center_client: SkillCenterClient = Injected(SkillCenterClient),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
) -> SkillReadmeResponse:
    """Get skill README content.

    Looks up the skill in DB by id/link_name first. Center content comes from
    SkillCenter, Git market content from SkillQueryService, and local content
    from the owning Bot's workspace.
    """
    logger.info(
        "[skills.get_skill_readme] Request: user_id=%s, ctx_bot_id=%s, "
        "skill_id=%s, entity_id=%s, bot_id=%s",
        ctx.user_id,
        ctx.bot_id,
        skill_id,
        entity_id,
        bot_id,
    )

    # Start with the caller/request context only to locate the DB record.
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )

    initial_skills_dir = path_factory.get_bot_skills_dir(
        effective_entity_id, effective_bot_id, runtime_engine, effective_entity_type
    )
    initial_local_dir = path_factory.get_bot_skills_local_dir(
        effective_entity_id,
        effective_bot_id,
        runtime_engine,
        effective_entity_type,
        is_desktop=is_desktop,
        is_teclaw=False,
    )
    path_factory.get_bot_engine_dir(
        effective_entity_id, effective_bot_id, runtime_engine, effective_entity_type
    )
    initial_repo_dir = path_factory.get_bot_skills_repo_dir(
        effective_entity_id,
        effective_bot_id,
        runtime_engine,
        effective_entity_type,
        is_desktop=is_desktop,
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

    skill = _get_skill_by_id_or_link_name(
        service, skill_id, effective_bot_id, ctx.bot_id
    )

    # SkillCenter branch: route center:// skills to file-content API.
    if skill and (skill.get("git_path") or "").startswith("center://"):
        try:
            result = skill_center_client.get_file_content(skill.get("name"), "SKILL.md")
            if result.get("success") and result.get("data", {}).get("content"):
                content = result["data"]["content"]
                # SkillCenter file-content 可能返回 base64 编码
                if isinstance(content, str) and not content.startswith("#"):
                    import base64

                    try:
                        content = base64.b64decode(content).decode("utf-8")
                    except Exception:
                        pass
                logger.info(
                    f"[skills.get_skill_readme] Found in SkillCenter: skill_id={skill_id}"
                )
                return SkillReadmeResponse(success=True, data={"content": content})
        except Exception as e:
            logger.warning(
                f"[skills.get_skill_readme] SkillCenter file-content failed: {e}"
            )
        raise HTTPException(status_code=404, detail="Skill or README not found")

    # Git market content is shared. Its query seam resolves it from the global
    # skills-repo, without treating a historical ``bolt_id`` as content owner.
    if skill and (skill.get("git_path") or "").startswith("git://"):
        try:
            readme = await query_service.get_readme_by_skill(
                skill_id=str(skill.get("id") or skill_id),
                actor_id=ctx.user_id,
            )
        except LocalSkillNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Skill or README not found"
            ) from exc
        if not readme:
            raise HTTPException(status_code=404, detail="Skill or README not found")
        logger.info(
            f"[skills.get_skill_readme] Found in Git market: skill_id={skill_id}"
        )
        return SkillReadmeResponse(success=True, data={"content": readme})

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
                raise HTTPException(
                    status_code=409, detail="Skill's owning bot is ambiguous"
                )
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
    read_runtime_engine = resolve_runtime_engine_for_bot(
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
        read_owner_id, read_bot_id, read_runtime_engine, read_entity_type
    )
    read_local_dir = path_factory.get_bot_skills_local_dir(
        read_owner_id,
        read_bot_id,
        read_runtime_engine,
        read_entity_type,
        is_desktop=read_is_desktop,
        is_teclaw=read_is_teclaw,
    )
    path_factory.get_bot_engine_dir(
        read_owner_id, read_bot_id, read_runtime_engine, read_entity_type
    )
    read_repo_dir = path_factory.get_bot_skills_repo_dir(
        read_owner_id,
        read_bot_id,
        read_runtime_engine,
        read_entity_type,
        is_desktop=read_is_desktop,
    )

    logger.info(
        "[skills.get_skill_readme] Creating read service: skills_dir=%s, "
        "repo_dir=%s, read_bot_id=%s, read_owner_id=%s, is_teclaw=%s",
        read_skills_dir,
        read_repo_dir,
        read_bot_id,
        read_owner_id,
        read_is_teclaw,
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
        skill_id,
        read_owner_id,
        read_bot_id,
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


@router.post("/sync-from-git", response_model=SyncSkillsResponse)
async def sync_skills_from_git(
    request: SyncSkillsRequest,
    skill_service_factory: SkillServiceFactoryProtocol = Injected(
        SkillServiceFactoryProtocol
    ),
) -> SyncSkillsResponse:
    """Sync skills from Git repository to database."""
    service = skill_service_factory.create()
    # `service.sync_skills_from_git` is the sync method on SkillService;
    # the contract scanner false-positives because the API endpoint above
    # shares the name as an async def. allow-run-in-threadpool suppresses
    # only this contract check, not other lint rules.
    results = await run_in_threadpool(  # allow-run-in-threadpool
        service.sync_skills_from_git, user_id=request.user_id
    )
    return SyncSkillsResponse(
        success=True,
        data=SyncSkillsResult(**results),
        message=f"Synced: {results['created']} created, {results['updated']} updated, {results['deleted']} deleted, {results['failed']} failed",
    )
