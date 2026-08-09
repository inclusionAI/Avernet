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

from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
)
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishStatus,
    select_stage_bind_id,
)

#: The one bot type with published stages. A ``personal`` bot has only its
#: workspace runtime, so every stage but the draft is meaningless for it.
SERVICE_BOT_TYPE = "service"

#: The pre-publication workspace — ``ac_bots.binding_id``, resolved through the
#: same owner-scoped ``resolve_for_bot`` a personal bot uses. The default on
#: the public surface: a request that names no stage behaves as it always has.
STAGE_DRAFT = "draft"

#: The runtime a verify release runs while its publish record is validating.
STAGE_VERIFY = "verify"

#: The runtime an online release serves once its publish record succeeds.
STAGE_ONLINE = "online"

#: Every stage the surface can address.
RUNTIME_STAGES = frozenset({STAGE_DRAFT, STAGE_VERIFY, STAGE_ONLINE})

#: A published stage is live exactly while its record holds this status. No
#: other status resolves: an ``upgraded``/``released``/``failed`` record is a
#: superseded or dead release, and answering from one would hand a caller a
#: runtime that is no longer (or not yet) the stage they named.
_STAGE_LIVE_STATUS = {
    STAGE_VERIFY: PublishStatus.VALIDATING.value,
    STAGE_ONLINE: PublishStatus.SUCCESS.value,
}


def resolve_stage_bind_id(
    publish_repo: BotPublishRepositoryProtocol,
    *,
    bot_pk: int,
    bot_id: str,
    stage: str,
    env: str,
) -> int:
    """The binding id of ``stage``'s live runtime, or raise.

    ``stage`` must be a published stage (:data:`STAGE_VERIFY` /
    :data:`STAGE_ONLINE`); the draft never reaches a publish record and its
    callers resolve ``ac_bots.binding_id`` directly. A stage with no live
    record — nothing validating for verify, nothing succeeded for online —
    raises :class:`EngineStageNotLiveError`, the caller-facing "not live"
    answer. Malformed data on our side (a facts object with no primary key, an
    unreadable ``ext``) stays :class:`DeviceNotBoundError`, exactly as it was
    before stages were addressable.

    **Keyed on the bot's primary key, never on ``bot_id``.** ``bot_id`` is not
    unique across owners — the column carries no unique constraint, and
    ``create_bot_for_others`` gives every user a bot called ``default`` — so a
    lookup by ``(bot_id, env)`` alone selects whichever owner published most
    recently, and could hand one caller another owner's running device. The
    ``ac_bots`` primary key is the identity of the exact row the operator
    adjudication was proven against.

    No fallback between stages: a verify request is never answered by the
    online runtime, or the reverse — wrong data, and a hidden dependency on
    release timing.
    """
    if stage not in _STAGE_LIVE_STATUS:
        raise ValueError(f"not a published stage: {stage!r}")
    if not bot_pk:
        raise DeviceNotBoundError(
            f"engine_runtime: no bot primary key for bot={bot_id}; "
            "cannot resolve a published runtime without one"
        )

    # Records come back newest-first; the newest record at the stage's live
    # status is that stage's runtime. Scoped to this bot row, so "newest"
    # cannot mean "some other owner's".
    record = next(
        (
            r
            for r in publish_repo.list_by_source_bot(bot_pk, env)
            if r.status == _STAGE_LIVE_STATUS[stage]
        ),
        None,
    )
    if record is None:
        raise EngineStageNotLiveError(
            f"no live {stage} runtime for bot={bot_id} env={env}"
        )

    # ``BotPublishRecord.ext`` is parsed to a dict by ``to_record()``; the str
    # branch mirrors the defensive handling in ``DeviceInstanceService``.
    ext = record.ext or {}
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except (json.JSONDecodeError, TypeError):
            raise DeviceNotBoundError(
                f"engine_runtime: unreadable publish ext for bot={bot_id} "
                f"publish_id={record.id}"
            ) from None

    # ``select_stage_bind_id`` is the shared stage→binding selector rather
    # than a second copy of the rule; on a record filtered to the stage's live
    # status it yields exactly that stage's binding.
    bind_id = select_stage_bind_id(ext.get("binding") or {}, record.status)
    if not bind_id:
        raise EngineStageNotLiveError(
            f"no {stage} binding for bot={bot_id} publish_id={record.id} "
            f"status={record.status}"
        )
    return int(bind_id)


__all__ = [
    "RUNTIME_STAGES",
    "SERVICE_BOT_TYPE",
    "STAGE_DRAFT",
    "STAGE_ONLINE",
    "STAGE_VERIFY",
    "resolve_stage_bind_id",
]
