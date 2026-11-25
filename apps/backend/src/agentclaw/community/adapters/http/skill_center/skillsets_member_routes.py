"""Legacy skillsets member routes."""

from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
    get_request_context,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    AddSkillsRequest,
    AddSkillsResponse,
    MessageResponse,
    SkillInSetResponse,
    SkillSetDetailResponse,
    SkillSetResponse,
    SkillSetSkillsResponse,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.di import Injected
from fastapi import Depends, HTTPException, Query

from .skillsets_common import (
    _get_path_params,
    _get_skill_set_path_params,
    _is_legacy_skill_set_batch_partial_failure,
    _legacy_actor,
    _legacy_skill_set_batch_failure,
)


from fastapi import APIRouter
router = APIRouter(prefix="/api/skillsets", tags=["skillsets"])

@router.get("/{skill_set_id}/skills", response_model=SkillSetSkillsResponse)
async def get_skill_set_skills(
    skill_set_id: str,
    user_id: Optional[str] = Query(None, description="User ID for permission check"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> SkillSetSkillsResponse:
    """Get all skills in a skill set."""
    owner_id_hint = entity_id or user_id
    # Get effective path parameters
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=skill_set_id,
        entity_id=owner_id_hint,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    skills = control_plane.list_skills(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, user_id or entity_id),
        set_id=skill_set_id,
    )
    return SkillSetSkillsResponse(
        success=True,
        data=[
            SkillInSetResponse(
                id=str(s.get("id")) if s.get("id") is not None else "",
                name=s.get("name"),
                description=s.get("description"),
                category=s.get("category"),
                git_path=s.get("git_path"),
                tags=s.get("tags") if isinstance(s.get("tags"), list) else [],
            )
            for s in skills
        ],
        count=len(skills),
    )


@router.post("/{skill_set_id}/skills", response_model=AddSkillsResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$request.bot_id",
        owner_id="$request.user_id",
    )
)
async def add_skills_to_set(
    skill_set_id: str,
    request: AddSkillsRequest,
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> AddSkillsResponse:
    """Add skills to a skill set."""
    # The published batch body carries the addressed Bot owner in ``user_id``;
    # the authenticated actor still comes exclusively from ``ctx`` below.
    owner_id_hint = entity_id or request.user_id
    # Preserve omission so the legacy resolver can recover a non-default Bot.
    bot_id_hint = request.bot_id or bot_id
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        _is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=skill_set_id,
        entity_id=owner_id_hint,
        entity_type=entity_type,
        bot_id=bot_id_hint,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    # Historical batch wire permits partial success.  Resolve its loose
    # identifiers here, then pass every durable ID through one control-plane
    # batch so an active Set gets one final runtime projection.
    results: dict[str, list] = {
        "success": [],
        "failed": [],
        "activation_failed": [],
    }
    actor_id = _legacy_actor(ctx, request.user_id or entity_id)
    # Validate the target before the legacy resolver may materialise a
    # missing Repo asset.  A missing or immutable Set must not leave an
    # orphan ac_skill row behind.
    target_set = control_plane.get_set(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=actor_id,
        set_id=skill_set_id,
    )
    if target_set.get("is_default"):
        raise SkillSetControlPlaneConflictError("SYSTEM_DEFAULT_IMMUTABLE")
    resolved: list[tuple[str, str]] = []
    for skill_id in request.skill_ids:
        try:
            stable_skill_id = control_plane.resolve_legacy_skill_id(
                bot_id=effective_bot_id,
                owner_id=effective_entity_id,
                actor_id=actor_id,
                identifier=skill_id,
            )
            resolved.append((str(skill_id), stable_skill_id))
        except (
            LocalSkillNotFoundError,
            SkillSetControlPlaneNotFoundError,
            SkillSetControlPlaneConflictError,
        ) as exc:
            if not _is_legacy_skill_set_batch_partial_failure(exc):
                raise
            results["failed"].append(
                _legacy_skill_set_batch_failure(str(skill_id), exc)
            )
    if resolved:
        outcomes = await control_plane.add_skills(
            bot_id=effective_bot_id,
            owner_id=effective_entity_id,
            user_id=actor_id,
            set_id=skill_set_id,
            skill_ids=[stable_skill_id for _, stable_skill_id in resolved],
        )
        for (legacy_skill_id, stable_skill_id), outcome in zip(resolved, outcomes):
            if outcome.succeeded:
                results["success"].append(
                    {"skill_id": stable_skill_id, "name": legacy_skill_id}
                )
                continue
            assert outcome.error is not None
            if _is_legacy_skill_set_batch_partial_failure(outcome.error):
                results["failed"].append(
                    _legacy_skill_set_batch_failure(legacy_skill_id, outcome.error)
                )
                continue
            raise outcome.error
    success_count = len(results["success"])
    failed_count = len(results["failed"])
    return AddSkillsResponse(
        success=True,
        data=results,
        message=f"成功添加 {success_count} 个技能，失败 {failed_count} 个",
    )


@router.delete("/{skill_set_id}/skills/{skill_id}", response_model=MessageResponse)
@with_interceptors(
    CollaboratorPermissionInterceptor(
        bot_id="$bot_id",
        owner_id="$user_id",
    )
)
async def remove_skill_from_set(
    skill_set_id: str,
    skill_id: str,
    user_id: Optional[str] = Query(None, description="User ID for permission check"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    control_plane: SkillSetManagementServiceProtocol = Injected(
        SkillSetManagementServiceProtocol
    ),
) -> MessageResponse:
    """Remove a skill from a skill set."""
    owner_id_hint = entity_id or user_id
    (
        effective_entity_id,
        effective_bot_id,
        effective_engine,
        runtime_engine,
        effective_entity_type,
        is_desktop,
    ) = _get_skill_set_path_params(
        ctx,
        set_id=skill_set_id,
        entity_id=owner_id_hint,
        entity_type=entity_type,
        bot_id=bot_id,
        engine_type=engine_type,
        bot_repo=bot_repo,
        control_plane=control_plane,
    )

    (result,) = await control_plane.remove_skills(
        bot_id=effective_bot_id,
        owner_id=effective_entity_id,
        user_id=_legacy_actor(ctx, user_id or entity_id),
        set_id=skill_set_id,
        skill_ids=[skill_id],
    )
    if result.error is not None:
        raise result.error
    if not result.changed:
        raise SkillSetControlPlaneNotFoundError("Skill not found in skill set")
    return MessageResponse(success=True, message="Skill removed from skill set")


@router.post("/default/ensure", response_model=SkillSetDetailResponse)
async def ensure_default_skill_set(
    user_id: Optional[str] = Query(None, description="User ID"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SkillSetDetailResponse:
    """Ensure default skill set exists."""
    # Get effective path parameters
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

    service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_set = service.ensure_default_skill_set(
        user_id=effective_entity_id, bolt_id=effective_bot_id
    )
    # Remove 'skills' key if present to avoid Pydantic validation error
    skill_set_clean = {k: v for k, v in skill_set.items() if k != "skills"}
    skill_set_clean["is_active"] = True
    # Map bolt_id to bot_id for frontend
    skill_set_clean["bot_id"] = skill_set_clean.get("bolt_id") or "default"
    # Remove original bolt_id from response
    skill_set_clean.pop("bolt_id", None)
    return SkillSetDetailResponse(
        success=True, data=SkillSetResponse(**skill_set_clean)
    )


@router.get("/default/current", response_model=SkillSetDetailResponse)
async def get_default_skill_set(
    user_id: Optional[str] = Query(None, description="User ID"),
    entity_id: Optional[str] = Query(None, description="Entity ID (纯ID，不需要前缀)"),
    entity_type: Optional[str] = Query(
        None, description="Entity type (staff/proj/team, default: staff)"
    ),
    bot_id: Optional[str] = Query(None, description="Bot ID (default: default)"),
    engine_type: Optional[str] = Query(
        None, description="Engine type (default: moltis)"
    ),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    skill_set_service_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
) -> SkillSetDetailResponse:
    """Get the default skill set."""
    # Get effective path parameters
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

    service = skill_set_service_factory.create(
        entity_id=effective_entity_id,
        bot_id=effective_bot_id,
        engine_type=effective_engine,
        entity_type=effective_entity_type,
    )
    skill_set = service.get_default_skill_set(
        user_id=effective_entity_id, bolt_id=effective_bot_id
    )
    if not skill_set:
        raise HTTPException(status_code=404, detail="No default skill set found")
    # Remove 'skills' key if present to avoid Pydantic validation error
    skill_set_clean = {k: v for k, v in skill_set.items() if k != "skills"}
    skill_set_clean["is_active"] = True
    # Map bolt_id to bot_id for frontend
    skill_set_clean["bot_id"] = skill_set_clean.get("bolt_id") or "default"
    # Remove original bolt_id from response
    skill_set_clean.pop("bolt_id", None)
    return SkillSetDetailResponse(
        success=True, data=SkillSetResponse(**skill_set_clean)
    )
