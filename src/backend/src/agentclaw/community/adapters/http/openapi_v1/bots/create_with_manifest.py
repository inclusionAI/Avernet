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

from typing import Callable, Optional

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
from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    CreationSequence,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    ApplyStatus,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    AUTHORIZATION_WINDOW_ELAPSED,
    BOT_COULD_NOT_BE_PROVISIONED,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
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
    submit_bot_creation_with_manifest,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    generate_bot_id,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.task_queue.types import (
    TERMINAL_STATUSES,
    TaskRecord,
    TaskStatus,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.passport import PassportPlugin

# The same bars the ordinary create applies, imported rather than restated: an
# engine this surface refuses to create on must be refused here too, and a
# second copy of the rule is a second thing to forget.
from .router import (
    BOT_QUOTA_CONFLICT_RESPONSES,
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

#: Bot statuses that mean the container is up and the bot is usable. Pinned to
#: the job's own set by a test, for the same reason as the one above.
_CONTAINER_READY = frozenset({"ACTIVE"})

router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"], route_class=PublicAPIRoute)


@router.post(
    "/with-manifest",
    status_code=202,
    response_model=Envelope[BotCreateWithManifestAccepted],
    # Refused to an app-only caller, and the mechanism matters: this route's
    # owner comes from ``UserIdDep``, which hands an application the ``user_id``
    # it asked for and leaves the authorization to a grant dependency. There is
    # no grant to check before the bot exists, so without this an application
    # could create a bot as any user. See ``admission.py``.
    dependencies=[Depends(refuse_app_only_caller)],
    responses={**USER_SCOPED_403, **BOT_QUOTA_CONFLICT_RESPONSES},
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
            space_quota=True,
        ),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        skill_set_factory=skill_set_factory,
        manifest_seam=manifest_seam,
    )

    # Starting the job is submission's last step, not the router's: it lives
    # inside the same boundary that discards the stored manifest when anything
    # after the persist fails. A route that started it would be a place where a
    # failure could strand a document under a bot_id the caller never saw.
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

    # Which order this bot's creation ran in (W8): with a record, the bot's
    # own engine decides; before one exists the answer does not matter, since
    # every state without a bot reads the same under both sequences.
    sequence = (
        apply_service.delivery_for_bot(bot).creation_sequence
        if bot is not None
        else CreationSequence.CREATE_BETWEEN_PHASES
    )
    status = _creation_state(
        bot=bot,
        report=report,
        job=lambda: manifest_seam.find_job(entity_id=entity_id, bot_id=bot_id),
        sequence=sequence,
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
            apply=apply_payload(report) if _report_is_shown(state, report, sequence) else None,
            message=_message(state, job),
        ),
        request,
    )


# ── the state table (plan.md §K-8) ─────────────────────────────────────────


def _creation_state(
    *,
    bot: Optional[dict],
    report: Optional[ApplyReport],
    job: Callable[[], Optional[TaskRecord]],
    sequence: CreationSequence = CreationSequence.CREATE_BETWEEN_PHASES,
) -> Optional[tuple[CreationState, Optional[TaskRecord]]]:
    """Map durable rows to a state. ``None`` means there is nothing here (404).

    ``sequence`` (W8) names which of the creation's own records is the terminal
    one: the post-container phase's under ``CREATE_BETWEEN_PHASES``, the single
    pre-container phase's under ``RECORD_APPLY_PROVISION`` — where that phase
    runs against the record and *before* the container, so its terminal
    record with a bot that is not yet running reads as `CREATING`, not as an
    outcome.

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

    # The bot exists, and the sequence's terminal phase is the only record that
    # answers how far the creation got. Under ``CREATE_BETWEEN_PHASES`` phase A's is
    # written *before* the bot, and a later `explicit` apply belongs to a bot
    # that is already configured — both read the same as no record here.
    if report is not None and report.trigger == _terminal_trigger(sequence):
        if report.status is ApplyStatus.RUNNING:
            return (CreationState.APPLYING, None)
        if sequence is CreationSequence.CREATE_BETWEEN_PHASES or _bot_is_running(bot):
            # Under ``CREATE_BETWEEN_PHASES`` this record exists only once the bot is
            # up; under ``RECORD_APPLY_PROVISION`` it is the outcome once the
            # bot is up. PARTIAL and FAILED alike: part of the manifest is not.
            if report.status is ApplyStatus.SUCCEEDED:
                return (CreationState.READY, None)
            return (CreationState.APPLY_FAILED, None)
        # ``RECORD_APPLY_PROVISION``, the phase finished, the container not up:
        # still being provisioned from what the phase wrote, or never coming.
        # The job's row below tells the two apart, exactly as it does for a
        # bot with no record of its own.

    # The second textual ``job()``, never a second call: the ``bot is None``
    # branch above returns on every path, so exactly one of the two runs.
    record = job()
    if record is None:
        # A bot with no creation job was not made here — an ordinary create with
        # a manifest PUT afterwards, say. It has a bot record and no
        # post-container apply, which is the shape of `CREATING`; reporting that
        # would be inventing a creation that never happened.
        return None
    if str(bot.get("status") or "") in _PROVISIONING_FAILED:
        return (CreationState.CREATE_FAILED, record)
    if record.status in TERMINAL_STATUSES and _bot_is_running(bot):
        # The job gave up, but the bot is up. Whatever went wrong was on the
        # configuration side — starting the post-container phase kept failing,
        # say — and `CREATE_FAILED` would be a lie with a cost: a caller told
        # their creation failed creates a second bot, and the first is already
        # running and billable. Under this API a bot that exists and works is an
        # apply failure, whatever stopped the job.
        return (
            CreationState.READY
            if record.status is TaskStatus.SUCCEEDED
            else CreationState.APPLY_FAILED,
            record,
        )
    if record.status is TaskStatus.SUCCEEDED:
        # The job finished, so the creation is over — even though the newest
        # apply record is not the post-container one. That happens when an
        # explicit apply has landed since, which this endpoint deliberately does
        # not report: it answers "how did the creation end", and
        # `GET .../config-manifest/last-apply` answers "how is the bot
        # configured now". Reading the creation's own record back would need a
        # trigger-filtered query the design chose not to add.
        return (CreationState.READY, record)
    if record.status in TERMINAL_STATUSES:
        # The job gave up with the bot never reaching a container.
        return (CreationState.CREATE_FAILED, record)
    return (CreationState.CREATING, record)


def _terminal_trigger(sequence: CreationSequence) -> str:
    """The creation's own terminal record, per sequence (W8)."""
    if sequence is CreationSequence.RECORD_APPLY_PROVISION:
        return CREATE_PRE_CONTAINER_TRIGGER
    return CREATE_ON_CONTAINER_TRIGGER


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
    last_error = record.last_error or ""
    if last_error == AUTHORIZATION_WINDOW_ELAPSED:
        return CreationState.AUTHORIZATION_EXPIRED
    if last_error.startswith(BOT_COULD_NOT_BE_PROVISIONED):
        # W8: under ``RECORD_APPLY_PROVISION`` a provisioning failure soft-deletes
        # the record the job had written, so the poll sees no bot beside a
        # terminal job — the shape of a decline, which it is not: the user
        # authorized, and the platform could not provision.
        return CreationState.CREATE_FAILED
    return CreationState.AUTHORIZATION_REJECTED


def _bot_is_running(bot) -> bool:
    """Whether this bot is usable, which is what separates the two failures.

    Read positively — a status that is *known* to mean running — rather than as
    "not in the failed set". A status nobody anticipated should not be reported
    as a working bot.
    """
    return str(bot.get("status") or "") in _CONTAINER_READY


def _bot_is_shown(state: CreationState) -> bool:
    """The bot rides on both terminal states that have one.

    `APPLY_FAILED` is the reason this exists: a caller has to be able to see
    that the bot is there and running, rather than infer it from the state's
    name.
    """
    return state in (CreationState.READY, CreationState.APPLY_FAILED)


def _report_is_shown(
    state: CreationState,
    report,
    sequence: CreationSequence = CreationSequence.CREATE_BETWEEN_PHASES,
) -> bool:
    """Only the creation's own report, and only where it means something.

    The newest apply record is not always the creation's: an `explicit` apply
    landing afterwards supersedes it. Returning that one under a creation state
    would answer a question the caller did not ask, and would look like the
    creation's outcome had changed.
    """
    if state not in (CreationState.READY, CreationState.APPLY_FAILED):
        return False
    return report is not None and report.trigger == _terminal_trigger(sequence)


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
