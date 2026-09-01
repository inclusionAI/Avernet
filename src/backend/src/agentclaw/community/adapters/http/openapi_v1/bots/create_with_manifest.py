"""Creating a bot together with its configuration — two routes (W13, #1696).

Its own router module rather than two more routes on ``bots/router.py``, which
is already one file away from the size cap and mixes four admission modes. These
two are a pair: one submits, the other observes, and neither means anything
without the other.

**What the pair is for.** Creating a bot and configuring it were two calls, and
between them sat a window in which the bot was up and unconfigured — long enough
for a first boot to run without the script that was meant to shape it. Here the
manifest is validated *before* a Passport application is made, stored against the
allocated ``bot_id``, and delivered in two phases: what must exist before the
container starts, and what can only be written once it has.

**The poll is a pure read** and that is a contract, not an implementation note.
It reads durable rows — the creation job's task row, the bot record, the apply
record — and maps them to a state. It queries no external service (the job is
what polls AgentPass), starts no work, and writes nothing, so polling faster
never changes an outcome and a caller that stops polling loses nothing.

**Nothing here decides what a manifest means.** Validation, storage and apply are
W1's and W4's, reached through ``BotCreationManifestSeam``; creation itself is
``create_flow``'s, and ``bot_service.create_bot`` is untouched. This module
composes them and shapes the answer.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.create_job import (
    AUTHORIZATION_WINDOW_ELAPSED,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_PRE_CONTAINER_TRIGGER,
    BotCreationManifestSeam,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_management.create_flow import (
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    BotCreateTemplateValidationMode,
    creation_spec_to_payload,
    submit_bot_creation_with_manifest,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    generate_bot_id,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.task_queue.types import TERMINAL_STATUSES, TaskStatus
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.passport import PassportPlugin

# The same bars the ordinary create applies, imported rather than restated: an
# engine this surface refuses to create on must be refused here too, and a
# second copy of the rule is a second thing to forget.
from .router import (
    _engine_properties_from_body,
    _require_publicly_creatable_engine,
    _require_service_capable_engine,
    _to_bot,
)
from ..clusters import validate_engine_cluster
from .config_manifest_support import apply_payload
from .schemas_create_with_manifest import (
    BotCreateWithManifest,
    BotCreateWithManifestAccepted,
    BotCreateWithManifestStatus,
    CreationState,
)

#: Bot statuses that mean no container is ever coming. The same set the creation
#: job stops on — imported would be better, but the job's copy is keyed to its
#: own step machine and this one to a reported state; they are pinned together
#: by a test rather than shared, so neither can quietly widen the other.
_PROVISIONING_FAILED = frozenset({"FAILED", "DELETED", "INACTIVE"})

router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"], route_class=PublicAPIRoute)


@router.post(
    "/with-manifest",
    status_code=202,
    response_model=Envelope[BotCreateWithManifestAccepted],
    # REFUSED to a machine caller, exactly as the ordinary create is: no bot
    # exists yet for a grant to cover, and creation spends the user's quota.
    dependencies=[Depends(refuse_app_only_caller)],
    responses=USER_SCOPED_403,
    operation_id="create_bot_with_manifest",
)
@envelope_errors
async def create_bot_with_manifest(
    body: BotCreateWithManifest,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    skill_set_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
    manifest_seam: BotCreationManifestSeam = Injected(BotCreationManifestSeam),
) -> Envelope[BotCreateWithManifestAccepted]:
    """Create a bot and its configuration in one request. Always `202`.

    **Submit the manifest once.** The status endpoint never accepts it again, so
    the document that was validated here is necessarily the document that gets
    applied — there is no second copy for a caller to change underneath the one
    that passed.

    **The manifest is validated before anything is spent.** An invalid document
    is a `422` naming every violation at once, with no `bot_id` minted, no
    Passport application made and nothing stored. You never authorize a bot only
    to be told your configuration was wrong.

    One rule beyond what `PUT .../config-manifest` enforces: a category no
    materializer in this build can apply is **refused here** rather than stored
    inert. Accepting it would mean authorizing, creating and only then failing to
    configure. Refusal names the category; create the bot first and `PUT` the
    manifest once the materializer has landed.

    **Iteration 1: a `script` must not depend on anything else the manifest
    declares.** On a first boot the script is baked into the start command and
    runs before any other category has been delivered — a script that expects an
    MCP server or a skill from the same document will not find it. Tracked by
    #1508, which delivers every category before the container starts.

    Then send the user to `iframe_url` or `redirect_url` (whichever is non-empty)
    and poll `GET /openapi/v1/bots/{bot_id}/with-manifest/status`. Nothing is
    created until they authorize, and nothing here waits for them.

    This endpoint is ARCA-only. A teclaw bot is configured by the artifact
    composed when its container is provisioned, which is a different mechanism
    from this pre/post-container delivery; it is refused, naming W8 (#1476).
    """
    _require_publicly_creatable_engine(body.engine)
    validate_engine_cluster(body.engine, body.cluster_name)
    _require_service_capable_engine(body.bot_type, body.engine)
    current_space = space_context.resolve_current(
        owner_id=owner_id,
        header_space_id=body.space_id,
    )
    bot_id = generate_bot_id(owner_id, bot_repo)

    submitted = submit_bot_creation_with_manifest(
        user_id=owner_id,
        bot_id=bot_id,
        document=body.config_manifest,
        modifier=owner_id,
        spec=BotCreateSpec(
            entity_id=owner_id,
            engine_type=body.engine,
            bot_type=body.bot_type,
            bot_name=body.bot_name,
            bot_desc=body.bot_desc,
            space_id=current_space.numeric_id,
            template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
            engine_properties=_engine_properties_from_body(body),
        ),
        context=BotCreateContext(
            deployment_mode=BotCreateDeploymentMode.CLOUD,
            space_kind=current_space.kind,
        ),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        skill_set_factory=skill_set_factory,
        manifest_seam=manifest_seam,
    )

    # Only now, because the job's first step is reading the authorization it
    # cannot see until the application above has been made.
    manifest_seam.start_job(
        bot_id=submitted.bot_id,
        entity_id=owner_id,
        user_id=owner_id,
        document_owner=owner_id,
        spec=creation_spec_to_payload(submitted.spec, submitted.context),
        iframe_url=submitted.iframe_url,
        redirect_url=submitted.redirect_url,
    )

    return accepted(
        BotCreateWithManifestAccepted(
            bot_id=submitted.bot_id,
            # Both handles: Passport returns one or the other and which is not
            # predictable, so dropping either can leave a caller with no way to
            # complete authorization.
            iframe_url=submitted.iframe_url or "",
            redirect_url=submitted.redirect_url or "",
        ),
        request,
    )


@router.get(
    "/{bot_id}/with-manifest/status",
    response_model=Envelope[BotCreateWithManifestStatus],
    dependencies=[Depends(refuse_app_only_caller)],
    responses=USER_SCOPED_403,
    operation_id="get_bot_create_with_manifest_status",
)
@envelope_errors
async def get_bot_create_with_manifest_status(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_repo: BotRepository = Injected(BotRepository),
    apply_service: BotConfigManifestApplyServiceProtocol = Injected(
        BotConfigManifestApplyServiceProtocol
    ),
    manifest_seam: BotCreationManifestSeam = Injected(BotCreationManifestSeam),
) -> Envelope[BotCreateWithManifestStatus]:
    """Where a creation stands. Takes a `bot_id` and nothing else.

    **A pure read.** No manifest, no creation attributes, no query parameters —
    the server already holds everything the creation was for. It calls no
    external service, starts no work and writes nothing, so polling it faster
    changes nothing and a caller that stops polling loses nothing: the creation
    runs to its own end either way.

    The states, and what each one promises about the bot:

    - `AWAITING_AUTHORIZATION` — nothing exists yet. Send the user to the URL.
    - `AUTHORIZATION_REJECTED` / `AUTHORIZATION_EXPIRED` — terminal, and **no
      bot was created**. The stored manifest is deleted with the creation, so
      nothing is left behind to find later.
    - `CREATING` — authorized; the bot record exists and its container is being
      provisioned.
    - `CREATE_FAILED` — terminal, and there is **no usable bot**. Nothing to do
      with the manifest.
    - `APPLYING` — the bot is up and the post-container phase is running.
    - `READY` — terminal; the bot is up and the whole manifest landed.
    - `APPLY_FAILED` — terminal; **the bot is up and running**, and part of its
      configuration did not land. The `apply` report names every entry, and the
      response carries the `bot` so this is visibly not the same as
      `CREATE_FAILED`. Fix the manifest and `POST .../config-manifest/apply`;
      nothing needs recreating.

    A `PARTIAL` apply reports `APPLY_FAILED`, not `READY`: under the manifest's
    category overwrite a partial category can have *removed* entries it then
    failed to replace, so it is a state to act on rather than a success with a
    footnote.

    The report at `READY` and `APPLY_FAILED` covers **both** phases — the
    pre-container `script` is carried into it, so nothing that was applied looks
    as though it vanished.

    `404` when no create-with-manifest was submitted for this `bot_id`, which
    includes a bot created through the ordinary endpoint.
    """
    # Resolved server-side from the caller, exactly as at submission. It is a
    # storage key, never a request parameter — see ``resolve_manifest_entity_id``.
    entity_id = owner_id
    bot = bot_repo.get_by_id_and_entity(bot_id, entity_id)
    report = apply_service.last_apply(entity_id=entity_id, bot_id=bot_id)

    status = _creation_state(
        bot=bot,
        report=report,
        job=lambda: manifest_seam.find_job(entity_id=entity_id, bot_id=bot_id),
    )
    if status is None:
        raise BotNotFoundError(
            f"no create-with-manifest was submitted for bot {bot_id}"
        )
    state, job = status

    return envelope(
        BotCreateWithManifestStatus(
            state=state,
            bot_id=bot_id,
            iframe_url=_handle(job, "iframe_url")
            if state is CreationState.AWAITING_AUTHORIZATION
            else "",
            redirect_url=_handle(job, "redirect_url")
            if state is CreationState.AWAITING_AUTHORIZATION
            else "",
            bot=_to_bot(bot) if bot is not None and _bot_is_shown(state) else None,
            apply=apply_payload(report) if _report_is_shown(state) else None,
            message=_message(state, job),
        ),
        request,
    )


# ── the state table (plan.md §K-8) ─────────────────────────────────────────


def _creation_state(*, bot, report, job) -> Optional[tuple[CreationState, Any]]:
    """Map durable rows to a state. ``None`` means there is nothing here (404).

    ``job`` is a *callable* rather than a record, and that is the read's cost
    model made structural: looking a task up by its idempotency key is served by
    an index only while the task is live, so the branches that can answer from
    the bot record and the apply record must not pay for it. In the states a
    caller polls repeatedly — `CREATING`, `APPLYING`, `READY` — it is either
    never called or hits the live path.
    """
    if bot is None:
        record = job()
        if record is None:
            return None
        if record.status not in TERMINAL_STATUSES:
            return (CreationState.AWAITING_AUTHORIZATION, record)
        return (_bot_less_terminal(record), record)

    # The bot exists. Phase A's record is not an answer about the creation's
    # progress — it is written *before* the bot — so it reads the same as none.
    if report is None or report.trigger == CREATE_PRE_CONTAINER_TRIGGER:
        if str(bot.get("status") or "") in _PROVISIONING_FAILED:
            return (CreationState.CREATE_FAILED, None)
        record = job()
        if record is None:
            # A bot with no creation job was not made here — an ordinary create
            # with a manifest PUT afterwards, say. Reporting a creation state
            # for it would be inventing one.
            return None
        if record.status in TERMINAL_STATUSES and record.status is not (
            TaskStatus.SUCCEEDED
        ):
            # The job gave up with the bot never reaching a container.
            return (CreationState.CREATE_FAILED, record)
        return (CreationState.CREATING, record)

    # A post-container apply exists — the creation got as far as configuring.
    if report.status is ApplyStatus.RUNNING:
        return (CreationState.APPLYING, None)
    if report.status is ApplyStatus.SUCCEEDED:
        return (CreationState.READY, None)
    # PARTIAL and FAILED alike: the bot is up, part of the manifest is not.
    return (CreationState.APPLY_FAILED, None)


def _bot_less_terminal(record) -> CreationState:
    """Declined, or never answered. Two states, because they are two events.

    The queue's own ``TIMED_OUT`` is one half of "never answered"; the other is
    the job noticing the window itself, which it does because a task retired
    DB-side never runs again and so would never clean up after itself. Both mean
    the user did not decide, and reporting `AUTHORIZATION_REJECTED` for either
    would attribute to them a decision they never made.
    """
    if record.status is TaskStatus.TIMED_OUT:
        return CreationState.AUTHORIZATION_EXPIRED
    if (record.last_error or "") == AUTHORIZATION_WINDOW_ELAPSED:
        return CreationState.AUTHORIZATION_EXPIRED
    return CreationState.AUTHORIZATION_REJECTED


def _bot_is_shown(state: CreationState) -> bool:
    """The bot rides on both terminal states that have one.

    `APPLY_FAILED` is the reason this exists: a caller has to be able to see
    that the bot is there and running, rather than infer it from the state's
    name.
    """
    return state in (CreationState.READY, CreationState.APPLY_FAILED)


def _report_is_shown(state: CreationState) -> bool:
    return state in (CreationState.READY, CreationState.APPLY_FAILED)


def _handle(job, field: str) -> str:
    """An authorization URL, from the job's payload — never from AgentPass.

    They were written at submission and ride in the payload precisely so this
    read stays a read. A poll that re-queried Passport would be doing the job's
    work in the caller's request.
    """
    if job is None:
        return ""
    return str((job.payload or {}).get(field) or "")


def _message(state: CreationState, job) -> str:
    """Why, when the state's name does not already say it."""
    if state is CreationState.AUTHORIZATION_REJECTED:
        return str(getattr(job, "last_error", "") or "authorization was declined")
    if state is CreationState.AUTHORIZATION_EXPIRED:
        return "the authorization window elapsed before the user responded"
    if state is CreationState.CREATE_FAILED:
        return str(
            getattr(job, "last_error", "") or "the bot could not be provisioned"
        )
    return ""


__all__ = ["router"]
