"""Config-manifest helpers for the ``/openapi/v1/bots`` group (issue #1469).

The handlers stay in ``config_manifest.py``; the plain functions they share live
here. Nothing in this module touches FastAPI — these are functions over a bot
record, a stored row and the Service API Protocol, which is what makes them
testable without a client.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.api.bot_config_manifest_apply_service import ApplyReport
from agentclaw.community.api.bot_config_manifest_service import ManifestCapabilities
from agentclaw.community.core.bot_config_manifest.repository.models import (
    BotConfigManifestRecord,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)

from .config_manifest_apply_schemas import (
    ConfigManifestApply,
    ConfigManifestApplyCategory,
    ConfigManifestApplyEntry,
)
from .schemas import (
    ConfigManifest,
    ConfigManifestCapabilities,
    ManifestConstruct,
)


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
