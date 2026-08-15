"""Legacy skills addresses — the awkward group, and honestly so.

Two shapes, and they retire for different reasons.

**The collection and the upload** took ``bot_id`` in the query and spelled the
owner ``owner_entity_id``. Both are re-annotated back; the upload also moves
from ``POST …/skills/upload`` to what it was, since the replacement is a POST on
the collection.

**The four ``{skill_id}`` operations** named no bot at all — the skill id
resolved its own — so there is nothing to put back in the query and nothing for
``require_granted_bot`` to read. They resolve the bot from the skill exactly as
they used to, and check the grant against the ``(bot, owner)`` the record names.

That last part is the second authorization mechanism in the one place it is
still true. The new addresses carry the bot and are checked by the shared
dependency; these cannot be, and refusing them outright would break the
compatibility promise. So the mechanism moves here and dies here — which is the
whole reason it is worth writing this package rather than serving aliases.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.skills import router as skills_router
from agentclaw.community.adapters.http.openapi_v1.skills.router import (
    SkillIdPath,
    _require_skills_grant,
    activate_skill,
    deactivate_skill,
    delete_skill,
    get_skill,
    list_skills,
    upload_skill,
)
from agentclaw.community.adapters.http.openapi_v1.skills.schemas import (
    Skill,
    SkillState,
    SkillUpload,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_state_service import (
    LocalSkillStateServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.di import Injected

from ._requery import LegacyBotIdQuery, deprecated_doc, with_query_parameter
from ._shim import legacy_route, legacy_router

router = legacy_router("/openapi/v1/bots/skills", "skills")

#: The owner parameter under the name only this group used.
LegacyOwnerEntityId = Annotated[
    str | None,
    Query(
        alias="owner_entity_id",
        description="Owner of the bot; defaults to the caller. Name it only "
        "to reach a bot shared with you.",
    ),
]


def _collection_shim(handler, method: str, replacement: str):
    """Bot back in the query, owner back under its old name."""
    shim = with_query_parameter(handler, "bot_id", LegacyBotIdQuery)
    return with_query_parameter(
        shim,
        "owner_id",
        LegacyOwnerEntityId,
        doc=deprecated_doc(handler, f"{method} {replacement}"),
    )


legacy_route(
    router,
    "GET",
    "",
    _collection_shim(list_skills, "GET", "/openapi/v1/bots/{bot_id}/skills"),
    replaces="/openapi/v1/bots/{bot_id}/skills",
    response_model=Envelope[Page[Skill]],
    operation_id="list_skills_deprecated_get",
)

legacy_route(
    router,
    "POST",
    "/upload",
    _collection_shim(upload_skill, "POST", "/openapi/v1/bots/{bot_id}/skills"),
    replaces="/openapi/v1/bots/{bot_id}/skills",
    response_model=Envelope[SkillUpload],
    status_code=201,
    operation_id="upload_skill_deprecated_post",
)


async def _bot_behind(
    query_service: LocalSkillQueryServiceProtocol,
    caller,
    *,
    skill_id: str,
    actor_id: str,
) -> str:
    """The bot a skill belongs to, with the grant checked against it.

    Both halves come off the record, and the user-scoped read runs first — so
    another user's skill is refused before the grant is consulted, and the
    grant check never becomes the thing that leaks a skill's existence.
    """
    record = query_service.get_local_skill(skill_id=skill_id, actor_id=actor_id)
    _require_skills_grant(caller, record)
    return str(record["bolt_id"])


async def get_skill_legacy(
    skill_id: SkillIdPath,
    actor_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    query_service: LocalSkillQueryServiceProtocol = Injected(
        LocalSkillQueryServiceProtocol
    ),
) -> Envelope[Skill]:
    """Get public metadata for one Local Skill; the Skill ID selects its Bot.

    Deprecated: use GET /openapi/v1/bots/{bot_id}/skills/{skill_id}.
    """
    bot_id = await _bot_behind(
        query_service, caller, skill_id=skill_id, actor_id=actor_id
    )
    return await get_skill(
        bot_id=bot_id,
        skill_id=skill_id,
        actor_id=actor_id,
        caller=caller,
        request=request,
        query_service=query_service,
    )


async def delete_skill_legacy(
    skill_id: SkillIdPath,
    actor_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    delete_service: LocalSkillDeleteServiceProtocol = Injected(
        LocalSkillDeleteServiceProtocol
    ),
    query_service: LocalSkillQueryServiceProtocol = Injected(
        LocalSkillQueryServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Delete a skill by id.

    Deprecated: use DELETE /openapi/v1/bots/{bot_id}/skills/{skill_id}.
    """
    bot_id = await _bot_behind(
        query_service, caller, skill_id=skill_id, actor_id=actor_id
    )
    return await delete_skill(
        bot_id=bot_id,
        skill_id=skill_id,
        actor_id=actor_id,
        caller=caller,
        request=request,
        delete_service=delete_service,
        query_service=query_service,
    )


def _state_shim(handler, verb: str):
    async def shim(
        skill_id: SkillIdPath,
        actor_id: UserIdDep,
        caller: ActingCallerDep,
        request: Request,
        query_service: LocalSkillQueryServiceProtocol = Injected(
            LocalSkillQueryServiceProtocol
        ),
        state_service: LocalSkillStateServiceProtocol = Injected(
            LocalSkillStateServiceProtocol
        ),
    ) -> Envelope[SkillState]:
        bot_id = await _bot_behind(
            query_service, caller, skill_id=skill_id, actor_id=actor_id
        )
        return await handler(
            bot_id=bot_id,
            skill_id=skill_id,
            actor_id=actor_id,
            caller=caller,
            request=request,
            query_service=query_service,
            state_service=state_service,
        )

    shim.__name__ = f"{handler.__name__}_legacy"
    shim.__doc__ = deprecated_doc(
        handler, f"POST /openapi/v1/bots/{{bot_id}}/skills/{{skill_id}}/{verb}"
    )
    return shim


legacy_route(
    router,
    "GET",
    "/{skill_id}",
    get_skill_legacy,
    replaces="/openapi/v1/bots/{bot_id}/skills/{skill_id}",
    response_model=Envelope[Skill],
    operation_id="get_skill_deprecated_get",
)
legacy_route(
    router,
    "DELETE",
    "/{skill_id}",
    delete_skill_legacy,
    replaces="/openapi/v1/bots/{bot_id}/skills/{skill_id}",
    response_model=Envelope[Deleted],
    operation_id="delete_skill_deprecated_delete",
)
legacy_route(
    router,
    "POST",
    "/{skill_id}/activate",
    _state_shim(activate_skill, "activate"),
    replaces="/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate",
    response_model=Envelope[SkillState],
    operation_id="activate_skill_deprecated_post",
)
legacy_route(
    router,
    "POST",
    "/{skill_id}/deactivate",
    _state_shim(deactivate_skill, "deactivate"),
    replaces="/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate",
    response_model=Envelope[SkillState],
    operation_id="deactivate_skill_deprecated_post",
)

__all__ = ["router"]
