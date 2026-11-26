"""A bot's configuration manifest — ``/openapi/v1/bots/{bot_id}/config-manifest``.

Four operations on their own router, mounted before the ``{bot_id}`` wildcard
group like every other bot-component group.

Nothing about *what a document may contain* is decided here. The router resolves
the bot, hands the service the two fields capabilities are answered from, and
shapes the result; the rules live in ``core/bot_config_manifest``.

**The bot may be someone else's.** These operations are collaborator-scoped —
MEMBER to read, ADMIN to write (``authorization.py``) — so the owner arrives as
``OwnerIdDep`` and the bot is resolved as *theirs*, while ``UserIdDep`` stays the
acting caller. The two are the same person on a bot you own and different on one
you collaborate on, which is exactly why the audit stamp takes the actor and the
bot lookup takes the owner.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CONFIG_MANIFEST_WRITE_RESPONSES,
    USER_SCOPED_403,
    BotIdPath,
    Deleted,
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
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.apply_run_service import (
    ApplyRunServiceProtocol,
)
from agentclaw.community.api.bot_config_manifest_service import (
    MANIFEST_STATE,
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di import Injected
from .config_manifest_support import (
    apply_payload,
    delivery_or_none,
    put_warnings,
    start_put_apply,
    audit_actor,
    capabilities_payload,
    manifest_payload,
    manifest_target,
)
from .schemas import ConfigManifest, ConfigManifestCapabilities, ConfigManifestWrite
from .schemas_config_manifest_apply import ConfigManifestApply
from .source_bot_access import assert_from_bot_source_access

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}",
    tags=["bots"],
    route_class=PublicAPIRoute,
)


@router.get(
    "/config-manifest",
    response_model=Envelope[ConfigManifest],
    responses=USER_SCOPED_403,
    operation_id="get_bot_config_manifest",
)
@envelope_errors
async def get_bot_config_manifest(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
) -> Envelope[ConfigManifest]:
    """Read a bot's configuration manifest.

    A bot that has never had one reads as an **empty document**, not an error —
    a 404 here would make "has none" indistinguishable from "no such bot".

    The document is returned exactly as it was written, byte for byte, including
    the `script` body's quoting and whitespace.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # addressed owner/tenant guard
    entity_id = manifest_target(bot)
    record = manifest_service.get(entity_id=entity_id, bot_id=bot_id)
    return envelope(manifest_payload(bot_id, record), request)


@router.put(
    "/config-manifest",
    response_model=Envelope[ConfigManifest | ConfigManifestApply],
    responses={
        **CONFIG_MANIFEST_WRITE_RESPONSES,
        200: {
            "description": "Stored manifest by default; complete plan for the "
            "submitted document when dry_run=true.",
        },
    },
    operation_id="update_bot_config_manifest",
)
@envelope_errors
async def update_bot_config_manifest(
    bot_id: BotIdPath,
    body: ConfigManifestWrite,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    caller: ActingCallerDep,
    dry_run: Annotated[
        bool,
        Query(description="Preview the submitted document without saving or applying it."),
    ] = False,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
    apply_service: ApplyRunServiceProtocol = Injected(
        ApplyRunServiceProtocol
    ),
    bot_repo: BotRepository = Injected(BotRepository),
) -> Envelope[ConfigManifest | ConfigManifestApply]:
    """Set or replace a bot's configuration manifest, and apply it.

    With `dry_run=true`, validate and preview the submitted document and return
    the complete plan synchronously. The preview may fetch and cache sources.
    It creates no Apply history and leaves the stored manifest untouched.

    **All-or-nothing.** A document is stored only if every part of it is valid
    and supported for this bot: one unsupported category refuses the whole
    document, nothing is written, and the `422` carries the full list of reasons
    with the offending entry named in each. Fixing a document is one pass, not a
    queue.

    Declaring a category this bot's engine cannot be given — or a
    (category, protocol) pair the platform cannot yet resolve — is refused
    rather than stored, because a stored declaration nothing can act on is a
    silent no-op the caller would reasonably read as success. `GET …/config-manifest/capabilities` answers
    from the same rules, so it can never promise what this refuses.

    **Storing starts an apply.** Once the document is stored, an apply of it is
    started — the same apply `POST …/config-manifest/apply` starts — and the
    response's `apply` says whether it started: `RUNNING` with the id to poll,
    or `NOT_STARTED` with `apply_in_progress` (another apply holds the bot;
    poll it, then apply) or `not_started`. The document is stored either way,
    and the response is `200` either way.

    `warnings` carries two notes this surface adds: a declared `script` is
    recorded now but takes effect on the bot's next start; and on a bot that
    is not `ACTIVE`, the categories that need a running container will be
    recorded as failed — re-apply once it is up. A teclaw bot on the
    platform-managed path needs no container, so it gets no such note.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # addressed owner/tenant guard
    entity_id = manifest_target(bot)
    if dry_run:
        report = await apply_service.dry_run(
            entity_id=entity_id,
            bot_id=bot_id,
            bot=bot,
            owner_id=owner_id,
            actor_id=actor_id,
            state=MANIFEST_STATE,
            submitted_document=body.document,
        )
        return envelope(apply_payload(report), request)

    audit = audit_actor(caller, actor_id)
    # Before the document is stored or an apply is started: a ``from: from_bot``
    # local skill reads a source Bot the caller may not be authorized to read.
    # The Copy endpoint checks this at submission; a plain Manifest PUT reaches
    # the same path without it, so the gate lives here too.
    assert_from_bot_source_access(
        document=body.document, owner_id=owner_id, bot_repo=bot_repo
    )
    result = manifest_service.put(
        entity_id=entity_id,
        bot_id=bot_id,
        document=body.document,
        # From the verified caller, never the body — and naming the application
        # when one is acting, not the user it acted for.
        modifier=audit,
        active_engine=bot.get("active_engine"),
        bot_type=bot.get("bot_type"),
    )
    started = start_put_apply(
        apply_service,
        entity_id=entity_id,
        bot_id=bot_id,
        bot=bot,
        owner_id=owner_id,
        actor_id=actor_id,
        audit=audit,
    )
    warnings = put_warnings(
        result, strategy=delivery_or_none(apply_service, bot), bot=bot
    )
    return envelope(
        manifest_payload(bot_id, result.record, warnings=warnings, apply=started),
        request,
    )


@router.delete(
    "/config-manifest",
    response_model=Envelope[Deleted],
    responses=USER_SCOPED_403,
    operation_id="delete_bot_config_manifest",
)
@envelope_errors
async def delete_bot_config_manifest(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Clear a bot's configuration manifest. Idempotent.

    Entities a previous apply produced are **not** removed: this clears the
    declaration, not the assets it named.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # addressed owner/tenant guard
    entity_id = manifest_target(bot)
    manifest_service.delete(entity_id=entity_id, bot_id=bot_id)
    return deleted_envelope(request)


@router.get(
    "/config-manifest/capabilities",
    response_model=Envelope[ConfigManifestCapabilities],
    responses=USER_SCOPED_403,
    operation_id="get_bot_config_manifest_capabilities",
)
@envelope_errors
async def get_bot_config_manifest_capabilities(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    manifest_service: BotConfigManifestServiceProtocol = Injected(
        BotConfigManifestServiceProtocol
    ),
) -> Envelope[ConfigManifestCapabilities]:
    """Which manifest constructs this bot accepts, and why not when it does not.

    Answered by the same resolver `PUT` and creation validation use. Current
    execution categories include one aggregate `capabilities` construct for
    Direct Skills, SkillSets/Default and MCP; historical `skills` and `mcp`
    categories are not advertised separately.

    `source_matrix` describes content transport, including Local Skill
    packages. `skill_references` separately lists `package`, `repo_path`,
    `skill_uuid` and `skill_code`, each with `supported` and `reason` for this
    bot and engine. Direct and SkillSet entries share those verdicts. Git
    package transport support does not imply shared Repo reference support.
    Asset access and availability are checked during resolution.

    An unrecognised engine supports nothing.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # addressed owner/tenant guard
    capabilities = manifest_service.capabilities_for_bot(bot)
    return envelope(capabilities_payload(bot_id, capabilities), request)
