"""Applying a bot's configuration manifest — the three operations (#1472).

Its own router module rather than an addition to ``config_manifest.py``. The two
groups carry **different bars** — reading and replacing a document is
collaborator-scoped, while applying one is owner-only with the edit lock — and
routes that live in one file invite someone to give a new one its neighbour's
row.

Nothing about *what applying does* is decided here. The router resolves the bot,
calls the service, and shapes the result; the rules live in
``core/bot_config_manifest/apply``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.di import Injected

from .config_manifest_support import (
    apply_payload,
    audit_actor,
    empty_apply_payload,
    manifest_target,
)
from .schemas_config_manifest_apply import (
    ConfigManifestApply,
    ConfigManifestApplyAccepted,
)


router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=PublicAPIRoute,
)

ApplyIdPath = Annotated[
    str,
    Path(description="The id returned by `POST .../config-manifest/apply`."),
]


#: Two shapes from one operation, so the model is declared per-response rather
#: than as a single ``response_model``. FastAPI coerces a returned object to the
#: declared model, so a single declaration silently truncated a dry run's plan to
#: the two fields of the accepted shape — caught by the endpoint test, and the
#: reason ``response_model`` is ``None`` here.
_APPLY_RESPONSES: dict[int | str, dict[str, object]] = {
    **USER_SCOPED_403,
    200: {
        "model": Envelope[ConfigManifestApply],
        "description": "A dry run's plan, computed without performing it.",
    },
    202: {
        "model": Envelope[ConfigManifestApplyAccepted],
        "description": "The apply has started; poll it with the returned id.",
    },
}


@router.post(
    "/config-manifest/apply",
    response_model=None,
    responses=_APPLY_RESPONSES,
    status_code=202,
    operation_id="apply_bot_config_manifest",
)
@envelope_errors
async def apply_bot_config_manifest(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    dry_run: Annotated[
        bool,
        Query(
            description="Return the plan without performing it. Synchronous, and "
            "writes nothing at all — no configuration change, and no apply "
            "record, so a dry run mints no `apply_id` and appears in no history."
        ),
    ] = False,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    apply_service: BotConfigManifestApplyServiceProtocol = Injected(
        BotConfigManifestApplyServiceProtocol
    ),
) -> Envelope[ConfigManifestApplyAccepted] | Envelope[ConfigManifestApply]:
    """Apply this bot's stored manifest. Returns immediately with an id to poll.

    **This does not wait for the apply.** Applying writes to a bot's device and,
    in later releases, fetches over the network; the response is a `202` carrying
    an `apply_id`, and the work continues in the background. Poll it with
    `GET .../config-manifest/applies/{apply_id}`, or read the newest with
    `GET .../config-manifest/last-apply`.

    What can be answered immediately is answered immediately, **before** an id
    exists: a `409` if another apply is already running for this bot, and a `422`
    if the stored document no longer validates for it. You never hold an id for
    an apply that did not start.

    **A declared category is overwritten to equal the declaration.** Anything in
    that category's area that the manifest does not declare is **removed** —
    including MCP servers enabled by hand through the console. A category the
    manifest does not mention is not touched at all, and deleting a manifest
    therefore deletes nothing.

    **A category is written all-or-nothing.** Every refusal that can be foreseen
    is checked before the first write, so if any declared entry cannot be
    materialized that whole category is left exactly as it was and its other
    entries report `skipped` — a momentary failure never deletes something that
    was working. Categories do not affect each other.

    A write can still fail for a reason no check can foresee: the underlying
    service is down, or a concurrent change lands. There is no transaction
    spanning those calls to roll back, so such a category reports
    `partially_written` and re-applying converges it. That flag is the one case
    where `aborted` does not mean "nothing changed".

    A bot with no stored manifest applies nothing and reports nothing applied.
    That is not an error.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # addressed owner/tenant guard
    entity_id = manifest_target(bot)
    # Two values, deliberately not one. ``actor_id`` is the principal every
    # downstream authorization check is made against; ``audit_actor`` is a label
    # for the record's actor column, and for an application caller it is a
    # synthetic ``app:<id>:on-behalf-of:<user>`` string that no owner or
    # collaborator row will ever match. W1 uses it as the manifest's ``modifier``
    # — an audit column — which is exactly what it is for. Sending it on as the
    # principal denied every application caller at ``can_manage_bot``.
    audit = audit_actor(caller, actor_id)

    if dry_run:
        # A dry run is finished when it answers — nothing was accepted for later
        # processing — so it is a 200, not the route's default 202.
        response.status_code = 200
        report = await apply_service.dry_run(
            entity_id=entity_id,
            bot_id=bot_id,
            bot=bot,
            owner_id=owner_id,
            # A dry run writes no record, so it has no audit column to fill and
            # needs only the principal.
            actor_id=actor_id,
        )
        return envelope(apply_payload(report), request)

    accepted = apply_service.start_apply(
        entity_id=entity_id,
        bot_id=bot_id,
        bot=bot,
        owner_id=owner_id,
        actor_id=actor_id,
        audit_actor=audit,
    )
    return envelope(
        ConfigManifestApplyAccepted(
            apply_id=accepted.apply_id, result=accepted.status.value
        ),
        request,
    )


@router.get(
    "/config-manifest/applies/{apply_id}",
    response_model=Envelope[ConfigManifestApply],
    responses=USER_SCOPED_403,
    operation_id="get_bot_config_manifest_apply",
)
@envelope_errors
async def get_bot_config_manifest_apply(
    bot_id: BotIdPath,
    apply_id: ApplyIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    apply_service: BotConfigManifestApplyServiceProtocol = Injected(
        BotConfigManifestApplyServiceProtocol
    ),
) -> Envelope[ConfigManifestApply]:
    """Read one apply's report, whether it is still running or finished.

    `result` is `RUNNING` until the work ends, then `SUCCEEDED`, `PARTIAL` or
    `FAILED` — derived from the per-entry outcomes, which say exactly what was
    delivered and what was not.

    An `apply_id` that belongs to a different bot is not found here: the id is a
    handle for polling, not something that grants access.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # addressed owner/tenant guard
    entity_id = manifest_target(bot)
    report = apply_service.get_apply(
        entity_id=entity_id, bot_id=bot_id, apply_id=apply_id
    )
    if report is None:
        return envelope(empty_apply_payload(bot_id), request)
    return envelope(apply_payload(report), request)


@router.get(
    "/config-manifest/last-apply",
    response_model=Envelope[ConfigManifestApply],
    responses=USER_SCOPED_403,
    operation_id="get_bot_config_manifest_last_apply",
)
@envelope_errors
async def get_bot_config_manifest_last_apply(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    apply_service: BotConfigManifestApplyServiceProtocol = Injected(
        BotConfigManifestApplyServiceProtocol
    ),
) -> Envelope[ConfigManifestApply]:
    """The most recent apply for this bot.

    This is the authoritative answer to "did my manifest take effect?".

    A bot that has never been applied reads as an **empty report**, not an error
    — the same rule that makes a bot with no manifest read as an empty document.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # addressed owner/tenant guard
    entity_id = manifest_target(bot)
    report = apply_service.last_apply(entity_id=entity_id, bot_id=bot_id)
    if report is None:
        return envelope(empty_apply_payload(bot_id), request)
    return envelope(apply_payload(report), request)
