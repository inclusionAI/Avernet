"""Public lifecycle routes for Bot-owned Local Skills.

This router deliberately exposes only the six ratified Local Skill operations.
Git, Center, marketplace, and install semantics remain on their separate,
non-public surfaces.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    EXAMPLE_TRACE_ID,
    BotIdPath,
    Deleted,
    Envelope,
    ErrorEnvelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.api.local_skill_state_service import (
    LocalSkillStateServiceProtocol,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillInvalidPackageError,
    LocalSkillNotFoundError,
)
from agentclaw.community.di import Injected

from .schemas import Skill, SkillState, SkillUpload

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/skills", tags=["skills"])

#: The path parameter naming the skill an operation addresses. The id alone
#: still resolves the skill's bot and owner; the address names the bot as well
#: so that every operation in the group is reached the same way, and so the
#: shared grant check can see it. :func:`_require_addressed_bot` is what keeps
#: the two from disagreeing.
SkillIdPath = Annotated[
    str,
    Path(
        description="The skill's id, as returned by the listing or upload "
        "(decimal digits, e.g. '42'). The id alone identifies the skill and "
        "its bot."
    ),
]


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(tag) for tag in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return [str(tag) for tag in parsed] if isinstance(parsed, list) else []
    return []


def _authorize_skills_bot(
    caller: ActingCaller,
    query_service: LocalSkillQueryServiceProtocol,
    *,
    skill_id: str,
    actor_id: str,
    bot_id: str,
) -> None:
    """Authorize the bot behind a skill, for an application caller.

    The four ``{skill_id}`` operations name no bot, and the services beneath
    them scope by *user* alone — so without this an application holding a grant
    on one of a user's bots would reach that user's skills on **every** bot they
    own. Admitting them unchecked was never an option; the choice was between
    this and refusing all four.

    The bot is resolved through the ordinary user-scoped read, deliberately.
    That read already refuses a skill belonging to someone else, so another
    user's skill is rejected *before* the grant is consulted and the grant check
    never becomes the thing that leaks a skill's existence.

    **The grant half is a no-op for a human caller.** Their own operation's
    user-scoped resolve is the check; re-deciding it here would risk a second,
    different answer.

    The read itself is no longer skipped for them, because the addressed bot has
    to be checked against the skill's own for every caller — an address that is
    only verified for applications is not an address. That costs one query per
    request on these four operations, which is the price of the ``{bot_id}``
    segment meaning what it says.
    """
    record = query_service.get_local_skill(skill_id=skill_id, actor_id=actor_id)
    _require_addressed_bot(record, bot_id)
    if not caller.is_application:
        return
    _require_skills_grant(caller, record)


def _require_addressed_bot(record: dict[str, Any], bot_id: str) -> None:
    """The skill must belong to the bot the address names.

    Without this the ``{bot_id}`` segment on the four ``{skill_id}`` operations
    would be decorative — a client could name any bot and reach a skill on
    another one, which is the precise defect this addressing change exists to
    remove. A skill id resolves its own bot, so the two can be compared, and a
    mismatch is answered as the skill not existing.

    Masked as a 404 rather than reported as a mismatch, for the same reason the
    rest of the surface masks: a distinguishable "wrong bot" answer confirms the
    skill exists somewhere, which is an enumeration oracle over other people's
    bots.

    The legacy addresses take no bot and so cannot make this comparison; they
    keep exactly the behaviour they have today.
    """
    if str(record["bolt_id"]) != bot_id:
        raise LocalSkillNotFoundError()


def _require_skills_grant(caller: ActingCaller, record: dict[str, Any]) -> None:
    """Bind the grant to the ``(bot, owner)`` this skill actually belongs to.

    Both halves come off the record. A skill can belong to another owner's bot
    and still be readable here — the user-scoped read admits a collaborator — so
    checking the grant against the *caller* rather than the skill's owner would
    authorize work on one bot with a grant for a different, same-named one.
    """
    caller.require_bot(
        str(record["bolt_id"]), owner_id=str(record["user_id"])
    )


def _to_skill(record: dict[str, Any]) -> Skill:
    return Skill(
        skill_id=str(record["id"]),
        name=str(record["name"]),
        description=record.get("description"),
        category=record.get("category"),
        tags=_tags(record.get("tags")),
        active=bool(record["active"]),
        created_at=record.get("gmt_created"),
        updated_at=record.get("gmt_modified"),
    )


@router.get("", response_model=Envelope[Page[Skill]])
@envelope_errors
async def list_skills(
    page: PageParamsDep,
    actor_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    bot_id: BotIdPath,
    owner_id: str | None = Query(
        default=None,
        description="Owner of the bot; defaults to the caller. Name it only "
        "to list skills of a bot shared with you.",
    ),
    active: bool | None = Query(
        default=None,
        description="Filter: true for only active skills, false for only "
        "inactive ones; omit for both.",
    ),
    keyword: str | None = Query(
        default=None,
        description="Filter: case-insensitive substring match against the "
        "skill's name and description.",
    ),
    query_service: LocalSkillQueryServiceProtocol = Injected(
        LocalSkillQueryServiceProtocol
    ),
) -> Envelope[Page[Skill]]:
    """List a bot's skills from stored desired state (paginated).

    Answers even while the bot is offline: active reflects the desired
    state, not the live runtime.
    """
    # Grant-checked here rather than by the shared dependency, because only
    # this handler knows whose bot it is about to read: owner_id names
    # an owner and defaults to the caller. Checking against the caller instead
    # would be wrong in both directions — it would let a grant on the caller's
    # own same-named bot authorize a read of someone else's, and refuse a
    # legitimate grant on a bot shared with them.
    caller.require_bot(bot_id, owner_id=owner_id or actor_id)
    total, records = query_service.list_local_skills(
        bot_id=bot_id,
        owner_id=owner_id or actor_id,
        actor_id=actor_id,
        page=page.page,
        page_size=page.page_size,
        active=active,
        keyword=keyword,
    )
    return page_envelope(total, [_to_skill(record) for record in records], request)


@router.get("/{skill_id}", response_model=Envelope[Skill])
@envelope_errors
async def get_skill(
    bot_id: BotIdPath,
    skill_id: SkillIdPath,
    actor_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    query_service: LocalSkillQueryServiceProtocol = Injected(
        LocalSkillQueryServiceProtocol
    ),
) -> Envelope[Skill]:
    """Get public metadata for one Local Skill; the Skill ID selects its Bot."""
    record = query_service.get_local_skill(
        skill_id=skill_id, actor_id=actor_id
    )
    _require_addressed_bot(record, bot_id)
    # The record is already in hand, so this one checks the grant directly
    # rather than through the helper — one read, not two.
    _require_skills_grant(caller, record)
    return envelope(_to_skill(record), request)


@router.post(
    "",
    status_code=201,
    response_model=Envelope[SkillUpload],
    responses={
        200: {
            "model": Envelope[SkillUpload],
            "description": "Existing Local Skill safely replaced.",
        },
        413: {
            "model": ErrorEnvelope,
            "description": "ZIP package exceeds an upload limit.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 413101,
                        "message": "Skill package is too large",
                        "data": None,
                        "request_id": EXAMPLE_TRACE_ID,
                    }
                }
            },
        },
    },
)
@envelope_errors
async def upload_skill(
    bot_id: BotIdPath,
    actor_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    response: Response,
    package: bytes = Body(..., media_type="application/zip"),
    owner_id: str | None = Query(
        default=None, description="Verified Bot owner locator."
    ),
    upload_service: LocalSkillUploadServiceProtocol = Injected(
        LocalSkillUploadServiceProtocol
    ),
) -> Envelope[SkillUpload]:
    """Upload a skill package (raw ZIP) to create or replace a bot's skill.

    The body is the ZIP's raw bytes with content type application/zip — not a
    multipart form. The archive must contain exactly one SKILL.md manifest,
    whose front matter names the skill. When the bot already has a skill of
    that name, its package is replaced in place (200, operation 'updated');
    otherwise a new, inactive skill is created (201, operation 'created').
    Limits: 10 MB compressed, 50 MB uncompressed, 500 files (413 beyond).
    """
    # Grant-checked here for the same reason as the listing above: the owner
    # this writes under is `owner_id or actor_id`, which only the
    # handler knows. A write makes the mis-binding worse — it would create a
    # skill on a bot the application was never granted.
    caller.require_bot(bot_id, owner_id=owner_id or actor_id)
    if (
        request.headers.get("content-type", "").split(";", 1)[0].lower()
        != "application/zip"
    ):
        raise LocalSkillInvalidPackageError()
    result = await upload_service.upload_local_skill(
        bot_id=bot_id,
        owner_id=owner_id or actor_id,
        actor_id=actor_id,
        package=package,
    )
    operation = str(result["operation"])
    if operation == "updated":
        response.status_code = 200
    return envelope(
        SkillUpload(operation=operation, skill=_to_skill(result["skill"])),
        request,
        code=201000 if operation == "created" else 200000,
        message="Created" if operation == "created" else "OK",
    )


@router.post(
    "/{skill_id}/activate",
    response_model=Envelope[SkillState],
)
@envelope_errors
async def activate_skill(
    bot_id: BotIdPath,
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
    """Activate a skill so its bot can use it.

    Idempotent — activating an already-active skill succeeds with changed
    false. The bot's runtime is reconciled synchronously either way.
    """
    _authorize_skills_bot(
        caller, query_service, skill_id=skill_id, actor_id=actor_id, bot_id=bot_id
    )
    result = await state_service.set_local_skill_active(
        skill_id=skill_id, actor_id=actor_id, active=True
    )
    return envelope(
        SkillState(skill=_to_skill(result), changed=bool(result["changed"])),
        request,
    )


@router.post(
    "/{skill_id}/deactivate",
    response_model=Envelope[SkillState],
)
@envelope_errors
async def deactivate_skill(
    bot_id: BotIdPath,
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
    """Deactivate a skill so its bot stops using it.

    Idempotent — deactivating an already-inactive skill succeeds with changed
    false. The bot's runtime is reconciled synchronously either way.
    """
    _authorize_skills_bot(
        caller, query_service, skill_id=skill_id, actor_id=actor_id, bot_id=bot_id
    )
    result = await state_service.set_local_skill_active(
        skill_id=skill_id, actor_id=actor_id, active=False
    )
    return envelope(
        SkillState(skill=_to_skill(result), changed=bool(result["changed"])),
        request,
    )


@router.delete("/{skill_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_skill(
    bot_id: BotIdPath,
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

    Only an inactive skill can be deleted — deactivate it first; deleting an
    active one answers 409.
    """
    _authorize_skills_bot(
        caller, query_service, skill_id=skill_id, actor_id=actor_id, bot_id=bot_id
    )
    await delete_service.delete_local_skill(
        skill_id=skill_id, actor_id=actor_id
    )
    return envelope(Deleted(), request)
