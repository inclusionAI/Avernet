"""Which runtime a stage names, resolved in one place.

A ``service`` bot has up to three long-lived runtimes, separated by *where
their binding is stored*, not by an id scheme: the pre-publication draft binds
on ``ac_bots.binding_id``, while the verify/online runtimes publishing
produces bind only in ``ac_bot_publish.ext.binding.{verify,online}`` on the
publish records (which share the bot's own ``publish_bot_id``; no ``…pub…``
id is ever written). A ``personal`` bot has only its own workspace runtime —
the ``draft`` stage.

Several callers translate a stage into a published binding — the relay's device
resolution, ``EngineConnectionService``'s socket composition, and the per-bot
file surfaces (engine config, identity) — and they must agree on which runtime
a stage names: a socket composed against one binding while the sessions group
forwards to another would let the two surfaces describe different devices for
the same request. So the lookup lives here, once, like the gate.

**A stage is not the only way to name a runtime, and the other way is not this
one.** ``select_stage_bind_id`` (``core/service_bot/repository/models.py``)
answers a different question — *one publish record* was named, which of its
bindings is that record's runtime — for the ``publish_id``-addressed internal
reads. It cannot answer this module's question, and this module must not answer
its: :func:`resolve_stage_bind_id` picks the newest record at a status and would
ignore the record the caller actually addressed. Keyed by stage, come here;
keyed by publish record, go there.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TYPE_CHECKING

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
)
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.engine_runtime.errors import (
    EngineStageNotLiveError,
    EngineStageReadOnlyError,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

# Deferred because it is *heavy*, not merely because it is an annotation:
# ``device_context_resolver`` pulls all four conn-info builders and
# ``BaasService`` behind them. ``gate.py`` imports this module and every
# openapi_v1 router imports the gate, so naming the concrete resolver at module
# scope would put that graph in every process that touches the gate. The other
# annotations above are imported normally — their modules are already loaded
# here, and splitting one module's names across two import styles for no saving
# is how ``DeviceNotBoundError`` ends up in the guarded block by accident.
if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )

logger = get_logger()

#: The one bot type with published stages. A ``personal`` bot has only its
#: workspace runtime, so every stage but the draft is meaningless for it.
SERVICE_BOT_TYPE = "service"

# The stage names are ``PublishStage``'s — the publish flow owns this
# vocabulary (``ac_publish_operation.stage``, ``ext.binding`` keys, cron's
# RUNTIME_STAGE_* constants all spell it the same way); aliased here so this
# module cannot drift into a second spelling. ``eval`` is deliberately absent:
# it has no long-lived runtime to address.

#: The pre-publication workspace — ``ac_bots.binding_id``, resolved through the
#: same owner-scoped ``resolve_for_bot`` a personal bot uses. The default on
#: the public surface: a request that names no stage behaves as it always has.
STAGE_DRAFT = PublishStage.DRAFT.value

#: The runtime a verify release runs — while its record validates, or retained
#: after promotion (see :func:`resolve_stage_bind_id`).
STAGE_VERIFY = PublishStage.VERIFY.value

#: The runtime an online release serves once its publish record succeeds.
STAGE_ONLINE = PublishStage.ONLINE.value

#: Every stage the surface can address.
RUNTIME_STAGES = frozenset({STAGE_DRAFT, STAGE_VERIFY, STAGE_ONLINE})


def _require_known_stage(stage: str) -> None:
    """Refuse a stage name outside :data:`RUNTIME_STAGES`.

    Unreachable from HTTP, where the adapter's enum answers 422 first, but a
    programmatic caller's typo must not sail through — and must not be *mistaken
    for something else*, which is why this runs ahead of both the addressable
    and the writable checks rather than being folded into either.
    """
    if stage not in RUNTIME_STAGES:
        raise EngineStageNotLiveError(f"unknown stage {stage!r}")


def require_stage_writable(stage: str) -> None:
    """Refuse a write addressed to a published runtime, before anything else.

    A published runtime is what a release produced; it is replaced by publishing
    again, never edited. Neither thing a write could do here is honest — editing
    the published runtime forks it from the record that describes it, and
    writing the draft instead reports success for an edit the addressed runtime
    never received — so the request is refused outright with
    :class:`EngineStageReadOnlyError`.

    Deliberately **not** conditional on the bot's type or on the stage being
    live. Both would need a row read to answer, and neither changes the answer:
    no runtime at ``verify`` or ``online``, live or not, on a service bot or
    otherwise, accepts a write through this surface. It is also what lets this
    run as a method's *first* statement, before anything is resolved.

    What that buys is narrower than "structural", and worth stating exactly: in
    the two write paths that call it, nothing downstream can reach a published
    binding, because neither resolves :class:`BotFacts` at all. A write surface
    added later inherits none of that and must call this itself.
    """
    _require_known_stage(stage)
    if stage != STAGE_DRAFT:
        raise EngineStageReadOnlyError(
            f"the {stage} runtime is published and does not accept writes"
        )


def require_stage_addressable(bot_type: str, stage: str) -> None:
    """Refuse a stage this bot cannot have, before any device work.

    Two refusals, one answer (:class:`EngineStageNotLiveError`): a stage name
    outside :data:`RUNTIME_STAGES` (see :func:`_require_known_stage`), and a
    published stage named on anything but a ``service`` bot, which has no such
    runtime to be live. Run by the gate (before device work, for the public
    surface) and by the relay's device resolution (for callers that bypass the
    gate); one implementation so the two cannot drift.
    """
    _require_known_stage(stage)
    if stage != STAGE_DRAFT and bot_type != SERVICE_BOT_TYPE:
        raise EngineStageNotLiveError(
            f"a {bot_type} bot has no {stage} runtime; only its workspace"
        )


def _record_binding(record: Any, *, bot_id: str, stage: str) -> int:
    """``record.ext.binding[stage]`` as an int, ``0`` when absent.

    ``BotPublishRecord.ext`` is parsed to a dict by ``to_record()``; the str
    branch mirrors the defensive handling in ``DeviceInstanceService``. An
    unreadable ``ext`` is malformed data on our side, not a dead stage, so it
    stays :class:`DeviceNotBoundError`.
    """
    ext = record.ext or {}
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except (json.JSONDecodeError, TypeError):
            raise DeviceNotBoundError(
                f"engine_runtime: unreadable publish ext for bot={bot_id} "
                f"publish_id={record.id}"
            ) from None
    return int((ext.get("binding") or {}).get(stage) or 0)


def resolve_stage_bind_id(
    publish_repo: BotPublishRepositoryProtocol,
    binding_repo: DeviceBindingRepository,
    *,
    bot_pk: int,
    bot_id: str,
    stage: str,
    env: str,
) -> int:
    """The binding id of ``stage``'s live runtime, or raise.

    ``stage`` must be a published stage (:data:`STAGE_VERIFY` /
    :data:`STAGE_ONLINE`); the draft never reaches a publish record and its
    callers resolve ``ac_bots.binding_id`` directly.

    **Which record is a stage's live runtime** — the same answer cron's
    runtime targeting gives (``cron_relay._get_retained_verify_publish_record``),
    because two surfaces disagreeing on whether a runtime exists is the drift
    this module exists to prevent:

    - ``online`` — the newest record at ``SUCCESS``, its ``ext.binding.online``.
    - ``verify`` — the newest record at ``VALIDATING``, its
      ``ext.binding.verify``. When nothing is validating, the newest
      ``SUCCESS`` record's verify binding **still counts while it is ACTIVE**:
      promotion retains the pre-prod runtime, and refusing it here while cron
      lists and forwards to it would be a 409 for a runtime that is up.

    No other status resolves — an ``upgraded``/``released``/``failed`` record
    is a superseded or dead release — and there is no fallback between
    stages: a verify request is never answered by the online runtime, or the
    reverse. A stage with no live runtime raises
    :class:`EngineStageNotLiveError`, the caller-facing "not live" answer.
    Malformed data on our side (a facts object with no primary key, an
    unreadable ``ext``) stays :class:`DeviceNotBoundError`, exactly as it was
    before stages were addressable.

    **Keyed on the bot's primary key, never on ``bot_id``.** ``bot_id`` is not
    unique across owners — the column carries no unique constraint, and
    ``create_bot_for_others`` gives every user a bot called ``default`` — so a
    lookup by ``(bot_id, env)`` alone selects whichever owner published most
    recently, and could hand one caller another owner's running device. The
    ``ac_bots`` primary key is the identity of the exact row the operator
    adjudication was proven against.
    """
    if stage not in RUNTIME_STAGES or stage == STAGE_DRAFT:
        raise ValueError(f"not a published stage: {stage!r}")
    if not bot_pk:
        raise DeviceNotBoundError(
            f"engine_runtime: no bot primary key for bot={bot_id}; "
            "cannot resolve a published runtime without one"
        )

    # Records come back newest-first; scoped to this bot row, so "newest"
    # cannot mean "some other owner's".
    records = publish_repo.list_by_source_bot(bot_pk, env)

    if stage == STAGE_ONLINE:
        record = next(
            (r for r in records if r.status == PublishStatus.SUCCESS.value), None
        )
        if record is not None:
            bind_id = _record_binding(record, bot_id=bot_id, stage=stage)
            if bind_id:
                return bind_id
        raise EngineStageNotLiveError(
            f"no live online runtime for bot={bot_id} env={env}"
        )

    # stage == STAGE_VERIFY: a validating release decides outright. Its
    # binding may not be written yet (mid-publish) — that is "not live yet",
    # NOT a licence to answer from the outgoing release: cron consults the
    # retained record only when *no* validating record exists
    # (`_get_retained_verify_publish_record`), and falling through here would
    # silently serve the previous runtime and then flip once the new binding
    # lands.
    record = next(
        (r for r in records if r.status == PublishStatus.VALIDATING.value), None
    )
    if record is not None:
        bind_id = _record_binding(record, bot_id=bot_id, stage=stage)
        if bind_id:
            return bind_id
        raise EngineStageNotLiveError(
            f"verify runtime not yet bound for bot={bot_id} "
            f"publish_id={record.id}"
        )

    # Nothing validating — the promoted record's retained verify runtime, but
    # only while its binding is ACTIVE. A released retained runtime is a dead
    # stage, not a "retry later": without the status check it would resolve
    # and then fail as device-not-ready, promising a retry that never helps.
    record = next(
        (r for r in records if r.status == PublishStatus.SUCCESS.value), None
    )
    if record is not None:
        bind_id = _record_binding(record, bot_id=bot_id, stage=stage)
        if bind_id and _binding_is_active(binding_repo, bind_id):
            return bind_id
    raise EngineStageNotLiveError(
        f"no live verify runtime for bot={bot_id} env={env}"
    )


def _binding_is_active(binding_repo: DeviceBindingRepository, bind_id: int) -> bool:
    """Whether the retained binding row is still ACTIVE. Unreadable → not live.

    Fails toward "not live" rather than raising: this only guards the
    retained-verify branch, and the honest degraded answer there is the same
    409 a dead stage gives — with the failure logged, not swallowed silently.
    """
    try:
        binding = binding_repo.get_by_id(bind_id)
    except Exception:
        logger.exception(
            "[engine_runtime] retained-verify binding lookup failed for "
            "binding_id=%s; treating the stage as not live",
            bind_id,
        )
        return False
    return binding is not None and str(binding.status) == DeviceBindingStatus.ACTIVE


async def resolve_published_device_context_off_loop(
    resolver: "DeviceContextResolver",
    publish_repo: BotPublishRepositoryProtocol,
    binding_repo: DeviceBindingRepository,
    *,
    facts: BotFacts,
    stage: str,
) -> DeviceContext:
    """:func:`resolve_published_device_context`, run in a worker thread.

    The safe call for anything on an event loop, and the reason it exists rather
    than a warning in prose: the work below is blocking I/O with a 30-second
    provider timeout, and every caller of this seam is an ``async`` service
    method. ``relay.resolve_bot_off_loop`` is the same pattern for the same
    reason.
    """
    return await asyncio.to_thread(
        resolve_published_device_context,
        resolver,
        publish_repo,
        binding_repo,
        facts=facts,
        stage=stage,
    )


def resolve_published_device_context(
    resolver: DeviceContextResolver,
    publish_repo: BotPublishRepositoryProtocol,
    binding_repo: DeviceBindingRepository,
    *,
    facts: BotFacts,
    stage: str,
) -> DeviceContext:
    """The device context of the published runtime ``stage`` names.

    The one entry point for a *read* that addresses a published stage, so the
    surfaces that read a bot's files cannot drift from the surfaces that forward
    to it: which runtime a stage names is :func:`resolve_stage_bind_id`'s rule,
    shared with cron, the relay and the connection socket.

    **Published stages only** — the draft is deliberately absent. Its runtime is
    the bot's own binding, reachable through the owner-scoped
    ``resolve_for_bot(bot_id, owner_id)`` its callers already use, and answering
    it here would oblige every caller to resolve :class:`BotFacts` first: a row
    read on the path that must stay exactly as cheap as it was before stages
    were addressable. So the caller keeps the branch, which is also how
    ``relay._resolve_device`` is shaped.

    :func:`require_stage_addressable` runs first, so a published stage named on
    a bot that has no such runtime is refused before any device work.

    ``resolve_for_binding`` rather than the relay's
    ``resolve_for_binding_invoke``: callers here address a **filesystem** on the
    resolved device and need the full connection info the invoke variant omits.
    That one line is why this is a sibling of
    ``relay._resolve_published_device`` rather than a shared body; what the two
    share is :func:`resolve_stage_bind_id`, which is the rule that must not
    drift.

    One consequence of that choice, shared with every other publish-addressed
    file read (``ResourceFileService``, ``IdentityService._read_from_publish_
    device``, ``EngineConfigService.read_publish_config``): ``resolve_for_binding``
    derives ``ctx.bot_type`` from ``get_by_binding_id``, which finds nothing for
    a published service binding — those ids are not on ``ac_bots.binding_id`` —
    so the returned context carries an **empty** ``bot_type``.

    One consumer reads it in production: ``DefaultDeviceFileSystemResolver``
    forks ``bot_type == "desktop"`` inside its baas branch. A published
    *service* bot is not ``desktop`` whether the field is filled or empty, so it
    lands on the cloud filesystem either way — and this is exactly what the three
    existing publish-addressed reads already get. A second arm added beside that
    fork would receive an empty string here and mis-dispatch silently.

    **Do not "fix" the empty value by filling it in.** The other reader,
    ``_validate_bot_device_combination``, lists ``("service", "baas")`` in
    ``_ILLEGAL_BOT_DEVICE_COMBINATIONS`` — the combination is not yet supported
    — and raises ``DeviceServiceError`` for it. It skips the check entirely when
    ``bot_type`` is empty, and it is reached only from the dispatcher's legacy
    direct-construction path, so nothing breaks today; but a correctly-filled
    ``bot_type`` would turn every published service-bot read on that path into a
    refusal. The emptiness is load-bearing, not merely tolerated.

    **Synchronous, and blocking.** The publish scan, the binding read and the
    provider resolve are all blocking I/O — on the BaaS path
    ``resolve_for_binding`` reaches ``get_ws_info`` over a sync ``httpx`` client
    with a 30-second timeout. ``relay._resolve_device`` carries the same warning
    and its caller offloads to a worker thread; an ``async`` caller here must do
    the same rather than calling this inline, or one slow stage read parks the
    event loop for every unrelated request on the worker.

    **Resolver errors are not translated**, deliberately. ``DeviceNotBoundError``
    and ``ConnInfoBuildError`` propagate as themselves, exactly as they do from
    the ``resolve_for_bot`` call on the draft leg of the same endpoints. The
    relay folds them into ``EngineDeviceNotReadyError`` because it presents a
    device-forwarding contract; doing that here would make ``?stage=online``
    answer differently from ``?stage=draft`` on one endpoint, which is a worse
    inconsistency than the one it would fix.
    """
    if stage == STAGE_DRAFT:
        raise ValueError(
            "resolve_published_device_context does not answer the draft; "
            "resolve the bot's own binding with resolve_for_bot instead"
        )
    require_stage_addressable(facts.bot_type, stage)
    bind_id = resolve_stage_bind_id(
        publish_repo,
        binding_repo,
        bot_pk=facts.bot_pk,
        bot_id=facts.bot_id,
        stage=stage,
        env=get_current_env(),
    )
    return resolver.resolve_for_binding(bind_id, facts.owner_id, bot_id=facts.bot_id)


__all__ = [
    "RUNTIME_STAGES",
    "SERVICE_BOT_TYPE",
    "STAGE_DRAFT",
    "STAGE_ONLINE",
    "STAGE_VERIFY",
    "require_stage_addressable",
    "require_stage_writable",
    "resolve_published_device_context",
    "resolve_published_device_context_off_loop",
    "resolve_stage_bind_id",
]
