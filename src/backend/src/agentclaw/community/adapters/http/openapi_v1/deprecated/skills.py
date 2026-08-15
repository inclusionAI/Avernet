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

import inspect
from typing import Annotated, Any

from fastapi import Query, Request
from fastapi.routing import APIRoute

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
    """Bot back in the query, owner back under its old name, grant re-checked.

    The grant check is the part that is easy to lose here, and losing it is a
    hole rather than an inconvenience. The replacement addresses get it from
    ``dependencies=_GRANT_CHECKED`` on their own routes, so their handlers no
    longer call ``require_bot`` themselves — but ``legacy_route`` registers an
    *endpoint*, not a route, and route-level dependencies are not carried
    across. These two addresses are mounted self-checked (their owner is
    published as ``owner_entity_id``, which the shared dependency does not
    know), so without :func:`_check_collection_grant` an application holding no
    grant at all would read and write skills through them.

    That is not hypothetical: it is exactly what this file did for one commit,
    caught in review.
    """
    shim = with_query_parameter(handler, "bot_id", LegacyBotIdQuery)
    shim = with_query_parameter(
        shim,
        "owner_id",
        LegacyOwnerEntityId,
        doc=deprecated_doc(handler, f"{method} {replacement}"),
    )
    return _grant_checked(shim)


def _check_collection_grant(caller, *, bot_id: str, owner_id: str | None, actor_id: str) -> None:
    """The owner-aware check the retiring collection addresses carry themselves.

    Identical to what ``list_skills`` and ``upload_skill`` used to do inline:
    bind the grant to ``(bot, owner_id or the caller)`` — the same pair the
    handler is about to act on, so the check and the resolution cannot mean
    different bots. A no-op for a human caller, whose own user-scoped resolve is
    the check.
    """
    if not caller.is_application:
        return
    caller.require_bot(bot_id, owner_id=owner_id or actor_id)


def _grant_checked(shim):
    """*shim* with a ``caller`` parameter added, checked before it runs.

    The parameter has to be added to the **synthesized** signature rather than
    declared in a wrapper's own, because FastAPI builds the route from
    ``__signature__`` — a wrapper taking ``**kwargs`` would publish an operation
    with no parameters at all.
    """
    signature = inspect.signature(shim)
    if "caller" in signature.parameters:
        return shim

    async def guarded(**kwargs: Any) -> Any:
        caller = kwargs.pop("caller")
        _check_collection_grant(
            caller,
            bot_id=kwargs["bot_id"],
            owner_id=kwargs.get("owner_id"),
            actor_id=kwargs["actor_id"],
        )
        return await shim(**kwargs)

    caller_param = inspect.Parameter(
        "caller",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=ActingCallerDep,
    )
    # Ahead of any parameter carrying a default, or the signature is invalid.
    parameters = list(signature.parameters.values())
    cut = next(
        (i for i, p in enumerate(parameters) if p.default is not inspect.Parameter.empty),
        len(parameters),
    )
    guarded.__signature__ = signature.replace(
        parameters=parameters[:cut] + [caller_param] + parameters[cut:]
    )
    guarded.__name__ = getattr(shim, "__name__", "shim")
    guarded.__doc__ = shim.__doc__
    return guarded


def _source_responses(endpoint) -> dict:
    """The response table the current route publishes for *endpoint*.

    Read off the real route rather than restated. ``relocate()`` does this for
    every address it registers; these two are hand-registered because their
    path does not follow a pattern, and they need the same treatment for the
    same reason. Restating a subset is how the upload address came to publish
    neither its ``200`` replacement case nor its ``413`` — both of which the
    reused handler can still return.
    """
    for route in skills_router.routes:
        if isinstance(route, APIRoute) and route.endpoint is endpoint:
            return route.responses
    raise LookupError(f"no current route serves {endpoint.__name__}")


legacy_route(
    router,
    "GET",
    "",
    _collection_shim(list_skills, "GET", "/openapi/v1/bots/{bot_id}/skills"),
    replaces="/openapi/v1/bots/{bot_id}/skills",
    response_model=Envelope[Page[Skill]],
    responses=_source_responses(list_skills),
    operation_name="list_skills",
)

legacy_route(
    router,
    "POST",
    "/upload",
    _collection_shim(upload_skill, "POST", "/openapi/v1/bots/{bot_id}/skills"),
    replaces="/openapi/v1/bots/{bot_id}/skills",
    response_model=Envelope[SkillUpload],
    responses=_source_responses(upload_skill),
    status_code=201,
    operation_name="upload_skill",
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
    operation_name="get_skill",
)
legacy_route(
    router,
    "DELETE",
    "/{skill_id}",
    delete_skill_legacy,
    replaces="/openapi/v1/bots/{bot_id}/skills/{skill_id}",
    response_model=Envelope[Deleted],
    operation_name="delete_skill",
)
legacy_route(
    router,
    "POST",
    "/{skill_id}/activate",
    _state_shim(activate_skill, "activate"),
    replaces="/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate",
    response_model=Envelope[SkillState],
    operation_name="activate_skill",
)
legacy_route(
    router,
    "POST",
    "/{skill_id}/deactivate",
    _state_shim(deactivate_skill, "deactivate"),
    replaces="/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate",
    response_model=Envelope[SkillState],
    operation_name="deactivate_skill",
)

__all__ = ["router"]
