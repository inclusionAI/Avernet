"""Config-manifest helpers for the ``/openapi/v1/bots`` group (issue #1469).

The handlers stay in ``config_manifest.py``; the plain functions they share live
here. Nothing in this module touches FastAPI — these are functions over a bot
record, a stored row and the Service API Protocol, which is what makes them
testable without a client.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.api.bot_config_manifest_apply_service import (
    ALL_PHASES,
    ApplyReport,
    BotConfigManifestApplyServiceProtocol,
    ManifestApplyInProgressError,
)
from agentclaw.community.api.bot_config_manifest_service import (
    ManifestCapabilities,
    ManifestWriteResult,
)
from agentclaw.community.core.bot_config_manifest.apply.delivery import DeliveryStrategy
from agentclaw.community.core.bot_config_manifest.apply.triggers import PUT as PUT_TRIGGER
from agentclaw.community.log import get_logger
from agentclaw.community.core.bot_config_manifest.repository.models import (
    BotConfigManifestRecord,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)

from .schemas_config_manifest_apply import (
    ConfigManifestApply,
    ConfigManifestApplyCategory,
    ConfigManifestApplyEntry,
)
from .schemas import (
    ConfigManifestApplyStarted,
    ConfigManifest,
    ConfigManifestCapabilities,
    ManifestConstruct,
)

logger = get_logger()


def manifest_target(bot: dict[str, Any]) -> str:
    """Resolve the storage key's ``entity_id`` from a bot record.

    ``entity_id`` is a storage key resolved here rather than taken from the
    request — it is never a request parameter or a response field, per the
    group contract.
    """
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    return entity_id


def audit_actor(caller: ActingCaller, actor_id: str) -> str:
    """Who to record as having changed the manifest.

    ``actor_id`` is who made the change — the caller, which on a shared bot is a
    collaborator rather than the owner. Never the addressed owner: attributing a
    collaborator's edit to the bot's owner is the one thing an audit column must
    not do.

    For an application caller ``user_id`` is the *delegating* user, not the
    caller — downstream code cannot tell an admitted application from that user,
    which is the seam's whole point. That is right for scoping and wrong for an
    audit field, so an application is named as itself with the user it acted for
    kept alongside.
    """
    if caller.is_application:
        return f"app:{caller.app_id}:on-behalf-of:{actor_id}"
    return actor_id


def manifest_payload(
    bot_id: str,
    record: Optional[BotConfigManifestRecord],
    warnings: Sequence[str] = (),
    apply: Optional[ConfigManifestApplyStarted] = None,
) -> ConfigManifest:
    """Shape a stored row — or its absence — as the response model.

    Absence is an empty document with a zero size and no author, not a 404: a
    bot that never carried a manifest is an ordinary state.
    """
    return ConfigManifest(
        bot_id=bot_id,
        document=record.document if record is not None else "",
        size_bytes=record.size_bytes if record is not None else 0,
        schema_version=record.schema_version if record is not None else None,
        updated_by=record.modifier if record is not None else "",
        updated_at=record.gmt_modified if record is not None else None,
        warnings=list(warnings),
        apply=apply,
    )


#: The ``PUT`` response's note when the document declares ``script`` (W8):
#: the script is recorded now but is part of the start command, so a running
#: bot carries it from its next start.
SCRIPT_DELIVERY_NOTE = (
    "script: recorded as the bot's startup script; it takes effect the next "
    "time the bot starts, not on this apply"
)


def not_active_note(bot_id: str, status: str) -> str:
    """The ``PUT`` response's note on a bot that is not ``ACTIVE`` (spec D-2).

    Only for a delivery strategy with container-bound constructs: on ARCA the
    non-script categories need a running container, so the apply this write
    started will record them as failed, and the caller re-applies once the
    bot is up. On teclaw with the platform-managed path nothing needs the
    container, so this note is never emitted there.
    """
    return (
        f"the bot is {status or 'not ACTIVE'}: categories that need a running "
        "container will be recorded as failed by the apply this write started; "
        f"once the bot is ACTIVE, POST /openapi/v1/bots/{bot_id}/config-manifest/apply"
    )


def put_warnings(
    result: ManifestWriteResult,
    *,
    strategy: Optional[DeliveryStrategy],
    bot: dict[str, Any],
) -> list[str]:
    """The validator's notes, plus the two delivery notes this surface adds.

    ``strategy=None`` means the bot's delivery could not be resolved (see
    ``delivery_or_none``): the container note needs the strategy to say
    whether anything is container-bound, so it is left out rather than
    guessed.
    """
    warnings = list(result.warnings)
    if result.declares_script:
        warnings.append(SCRIPT_DELIVERY_NOTE)
    status = str(bot.get("status") or "")
    if strategy is not None and strategy.needs_container() and status != "ACTIVE":
        warnings.append(not_active_note(str(bot.get("bot_id") or ""), status))
    return warnings


def delivery_or_none(
    apply_service: BotConfigManifestApplyServiceProtocol, bot: dict[str, Any]
) -> Optional[DeliveryStrategy]:
    """The bot's delivery strategy for the response's notes, or ``None``.

    The same defensive shape as ``start_put_apply``: by the time the
    response is being built the document is stored, and a ``200`` is owed.
    Resolving the strategy can fail on a misconfigured deployment (the
    platform-managed switch on with no platform ports bound) — the apply
    itself already reported ``not_started`` for that, and the warnings must
    not turn it into a ``500`` after the write.
    """
    try:
        return apply_service.delivery_for_bot(bot)
    except Exception:  # noqa: BLE001 — the write already happened; §2.7
        logger.exception(
            "[config_manifest] the bot's delivery strategy could not be resolved "
            "for the PUT response: bot_id=%s",
            bot.get("bot_id"),
        )
        return None


def start_put_apply(
    apply_service: BotConfigManifestApplyServiceProtocol,
    *,
    entity_id: str,
    bot_id: str,
    bot: dict[str, Any],
    owner_id: str,
    actor_id: str,
    audit: str,
) -> ConfigManifestApplyStarted:
    """Start the apply a ``PUT`` owes, and say whether it started (spec D-8).

    Never raises: the document is already stored, and a write that stored must
    answer ``200``. The lock being held is the one expected reason; anything
    else is logged in full and reported as ``not_started``.
    """
    try:
        accepted = apply_service.start_apply(
            entity_id=entity_id,
            bot_id=bot_id,
            bot=bot,
            owner_id=owner_id,
            actor_id=actor_id,
            audit_actor=audit,
            trigger=PUT_TRIGGER,
            phases=ALL_PHASES,
        )
    except ManifestApplyInProgressError:
        return ConfigManifestApplyStarted(
            apply_id="", result="NOT_STARTED", reason="apply_in_progress"
        )
    except Exception:  # noqa: BLE001 — the write already happened; §2.7
        logger.exception(
            "[config_manifest] the apply a PUT started could not start: bot_id=%s",
            bot_id,
        )
        return ConfigManifestApplyStarted(
            apply_id="", result="NOT_STARTED", reason="not_started"
        )
    return ConfigManifestApplyStarted(
        apply_id=accepted.apply_id, result=accepted.status.value, reason=None
    )


def capabilities_payload(
    bot_id: str, capabilities: ManifestCapabilities
) -> ConfigManifestCapabilities:
    """Shape the resolver's answer as the response model."""
    return ConfigManifestCapabilities(
        bot_id=bot_id,
        engine_type=capabilities.engine_type,
        bot_type=capabilities.bot_type,
        schema_versions=list(capabilities.schema_versions),
        # ``as_dict`` is the construct's own wire shape — a ``kind``/``name``
        # pair of plain strings. Reading it here rather than unpacking the enum
        # keeps one definition of how a construct serialises.
        constructs=[ManifestConstruct(**item.as_dict()) for item in capabilities.constructs],
    )


def apply_payload(report: ApplyReport) -> ConfigManifestApply:
    """Shape one apply's report as the response model.

    Reads ``as_payload`` rather than the report's fields directly: that method
    is the one place the wire shape is defined, and it names every field
    explicitly — which is what makes it structurally unable to emit a credential
    value. Rebuilding the shape here would be a second definition, and the one
    that drifted would be the one nobody was reading.
    """
    payload = report.as_payload()
    return ConfigManifestApply(
        apply_id=payload["apply_id"],
        bot_id=payload["bot_id"],
        trigger=payload["trigger"],
        result=payload["result"],
        started_at=report.started_at,
        finished_at=report.finished_at,
        sources=payload["sources"],
        categories=[
            ConfigManifestApplyCategory(**category)
            for category in payload["categories"]
        ],
        entries=[ConfigManifestApplyEntry(**entry) for entry in payload["entries"]],
        notes=list(payload.get("notes") or ()),
    )


def empty_apply_payload(bot_id: str) -> ConfigManifestApply:
    """What a bot with no apply reads as.

    An empty report, not a 404 — the same rule that makes a bot with no manifest
    read as an empty document: a 404 would make "has never applied"
    indistinguishable from "no such bot".
    """
    return ConfigManifestApply(
        apply_id="",
        bot_id=bot_id,
        trigger="",
        result="",
        started_at=None,
        finished_at=None,
        sources=[],
        categories=[],
        entries=[],
    )
