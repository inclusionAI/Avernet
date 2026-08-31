"""Config-manifest helpers for the ``/openapi/v1/bots`` group (issue #1469).

The four ``{bot_id}/config-manifest`` handlers stay in ``router.py`` with the
rest of the group; what they share lives here, split out following the same
concern-driven cut as ``startup_script_support.py``. Nothing here touches
FastAPI — plain functions over a bot record and the service protocols, which
is what keeps them testable without a client.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_config_manifest_service import (
    ManifestServiceProtocol,
)
from agentclaw.community.api.bot_startup_script_service import (
    SUPPORTED,
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.bot_config_manifest.manifest_schema import (
    ManifestDocument,
)

from .schemas import (
    BotConfigManifest,
    BotConfigManifestCapabilities,
    BotConfigManifestPutResult,
)


def _manifest_engine_axes(bot: dict[str, Any]) -> tuple[str, str]:
    """``(engine_type, bot_type)`` from the bot record — the capability axes."""
    engine_type = bot.get("active_engine") or ""
    bot_type = bot.get("bot_type") or ""
    return engine_type, bot_type


def script_supported_for_bot(
    bot: dict[str, Any],
    startup_script_service: BotStartupScriptServiceProtocol,
) -> bool:
    """The #935 form-factor judgment, shaped as the narrow override.

    ``ManifestService.capabilities`` ANDs this with the engine table: it can
    only narrow. LOCAL/singlebox and legacy ARCA-direct bots answer False even
    on an engine the table supports — the same refusal #935 applies to its own
    PUT, so the two surfaces cannot drift.
    """
    state, _reason = startup_script_service.resolve_support(bot)
    return state == SUPPORTED


def manifest_payload(
    bot_id: str,
    record: Any,
    document: ManifestDocument,
) -> BotConfigManifest:
    """Shape the stored document — or its absence — as the response model.

    Absence reads as the empty document (#1469), never as an error: "no
    declaration" is "no opinion", which is a different state from ``[]``.
    """
    payload = document.model_dump(by_alias=True, exclude_none=True)
    return BotConfigManifest(
        bot_id=bot_id,
        **payload,
        updated_by=record.modifier if record is not None else None,
        updated_at=record.gmt_modified if record is not None else None,
    )


def manifest_put_payload(bot_id: str, result: dict) -> BotConfigManifestPutResult:
    """Shape the service write result as the response model."""
    return BotConfigManifestPutResult(
        bot_id=bot_id,
        schema_version=result["schema_version"],
        warnings=list(result["warnings"]),
        updated_by=result["modifier"],
        updated_at=result["gmt_modified"],
    )


def capabilities_payload(support: Any) -> BotConfigManifestCapabilities:
    """``CategorySupport`` → response model:每 False 附带 reason。"""
    public = support.as_public()
    return BotConfigManifestCapabilities(
        categories=dict(public["categories"]),
        reasons=dict(public["reasons"]),
    )


def resolve_manifest_bot(
    bot_id: str,
    owner_id: str,
    bot_service: BotServiceProtocol,
    manifest_service: ManifestServiceProtocol,
) -> tuple[str, str, str, Any, ManifestDocument]:
    """Resolve the storage axes plus record/document for one bot.

    ``(entity_id, engine_type, bot_type, record, document)``; raises the same
    masked 404 the group's other own-bot handlers do when the record carries no
    entity (mirrors ``_startup_script_target``).

    One read, not two: ``service.get`` resolves through ``get_record`` again
    internally, so the document is parsed here from the record this handler
    already fetched — the empty-document contract is reproduced inline
    (absent = the empty document, never an error).
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership / tenant guard
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    engine_type, bot_type = _manifest_engine_axes(bot)
    record = manifest_service.get_record(entity_id=entity_id, bot_id=bot_id)
    from agentclaw.community.core.bot_config_manifest.manifest_schema import (
        EMPTY_DOCUMENT,
        parse_document,
    )

    document = (
        parse_document(record.document) if record is not None else EMPTY_DOCUMENT
    )
    return entity_id, engine_type, bot_type, record, document
