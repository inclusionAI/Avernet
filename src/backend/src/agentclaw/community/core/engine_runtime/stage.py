"""Which runtime a stage names, resolved in one place.

A ``service`` bot has up to three long-lived runtimes, separated by *where
their binding is stored*, not by an id scheme: the pre-publication draft binds
on ``ac_bots.binding_id``, while the verify/online runtimes publishing
produces bind only in ``ac_bot_publish.ext.binding.{verify,online}`` on the
publish records (which share the bot's own ``publish_bot_id``; no ``…pub…``
id is ever written). A ``personal`` bot has only its own workspace runtime —
the ``draft`` stage.

Two callers translate a stage into a published binding — the relay's device
resolution and ``EngineConnectionService``'s socket composition — and they
must agree on which runtime a stage names: a socket composed against one
binding while the sessions group forwards to another would let the two
surfaces describe different devices for the same request. So the lookup lives
here, once, like the gate.
"""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
)
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.log import get_logger

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


def require_stage_addressable(bot_type: str, stage: str) -> None:
    """Refuse a stage this bot cannot have, before any device work.

    Two refusals, one answer (:class:`EngineStageNotLiveError`): a stage name
    outside :data:`RUNTIME_STAGES` — unreachable from HTTP, where the
    adapter's enum answers 422 first, but a programmatic caller's typo must
    not sail through to an unmapped 500 at device resolution — and a
    published stage named on anything but a ``service`` bot, which has no
    such runtime to be live. Run by the gate (before device work, for the
    public surface) and by the relay's device resolution (for callers that
    bypass the gate); one implementation so the two cannot drift.
    """
    if stage not in RUNTIME_STAGES:
        raise EngineStageNotLiveError(f"unknown stage {stage!r}")
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


__all__ = [
    "RUNTIME_STAGES",
    "SERVICE_BOT_TYPE",
    "STAGE_DRAFT",
    "STAGE_ONLINE",
    "STAGE_VERIFY",
    "require_stage_addressable",
    "resolve_stage_bind_id",
]
