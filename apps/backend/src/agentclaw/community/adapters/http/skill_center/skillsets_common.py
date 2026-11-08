"""Shared compatibility helpers for the Legacy skillsets endpoints."""

from typing import Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
)
from agentclaw.community.adapters.http.skill_center.legacy_bot_address import (
    legacy_path_params,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    SkillSetResponse,
)
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    recover_legacy_skill_set_scope,
)
from agentclaw.community.log import get_logger
from fastapi import APIRouter

logger = get_logger()

router = APIRouter(prefix="/api/skillsets", tags=["skillsets"])

_LEGACY_SKILL_SET_BATCH_PARTIAL_CONFLICTS = frozenset(
    {
        "RESOURCE_DIRECT_ACTIVE",
        "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET",
    }
)

_LEGACY_SKILL_SET_BATCH_ERROR_MESSAGES = {
    "RESOURCE_DIRECT_ACTIVE": "该 Skill 已单独激活，请先停用后再加入能力集。",
    "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET": (
        "该 Skill 已由其他能力集管理，不能重复添加。"
    ),
}


def _is_legacy_skill_set_batch_partial_failure(error: Exception) -> bool:
    """Whether the published BFF batch records this domain error per member."""
    return isinstance(
        error, (LocalSkillNotFoundError, SkillSetControlPlaneNotFoundError)
    ) or (
        isinstance(error, SkillSetControlPlaneConflictError)
        and str(error) in _LEGACY_SKILL_SET_BATCH_PARTIAL_CONFLICTS
    )


def _legacy_skill_set_batch_failure(skill_id: str, error: Exception) -> dict[str, str]:
    """Preserve the legacy error code while providing display-safe text."""
    error_code = str(error)
    return {
        "skill_id": skill_id,
        "error": error_code,
        "error_code": error_code,
        "message": _LEGACY_SKILL_SET_BATCH_ERROR_MESSAGES.get(
            error_code,
            "暂时无法添加该 Skill，请刷新后重试。",
        ),
    }


def _legacy_actor(ctx: RequestContext, requested_user_id: str | None) -> str:
    """Use the authenticated subject, retaining the pre-auth test fallback.

    Legacy wire bodies contain ``user_id`` for historical routing.  It is not
    an authority grant: normal requests always use the request principal and
    the control plane checks the addressed Bot ACL.
    """
    return str(ctx.user_id or requested_user_id or "")


def _legacy_skill_set(item: dict) -> SkillSetResponse:
    """Project the canonical item back to the published legacy response."""
    return SkillSetResponse(
        **{
            **item,
            "bot_id": item.get("bolt_id") or item.get("bot_id") or "default",
            "skills": item.get("skills", []),
            "type": item.get("type", "custom"),
        }
    )


def _get_path_params(
    ctx: RequestContext,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> tuple:
    """Resolve legacy locators to stable resource paths; actor stays in ctx."""
    return legacy_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )


def _get_skill_set_path_params(
    ctx: RequestContext,
    *,
    set_id: str,
    entity_id: Optional[str],
    entity_type: Optional[str],
    bot_id: Optional[str],
    engine_type: Optional[str],
    bot_repo: BotRepository,
    control_plane: SkillSetManagementServiceProtocol,
) -> tuple:
    """Resolve the deprecated optional Bot address before normal path handling."""
    entity_id, bot_id = recover_legacy_skill_set_scope(
        set_id=set_id,
        actor_id=_legacy_actor(ctx, entity_id),
        owner_id_hint=entity_id,
        bot_id_hint=bot_id,
        control_plane=control_plane,
    )
    return _get_path_params(
        ctx,
        entity_id,
        entity_type,
        bot_id,
        engine_type,
        bot_repo=bot_repo,
    )
