"""Service API Protocol for applying a bot's configuration manifest (#1472).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_config_manifest/bot_config_manifest_apply_service_protocol.py``) so
the concrete service can inherit it without a ``core -> api`` waiver; adapters
keep importing it from here — the same arrangement the sibling manifest contract
uses.
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    ApplyAccepted,
    ApplyPhase,
    ApplyReport,
    BotConfigManifestApplyServiceProtocol,
    ManifestApplyInProgressError,
)

__all__ = [
    "ApplyAccepted",
    "ApplyPhase",
    "ApplyReport",
    "BotConfigManifestApplyServiceProtocol",
    "ManifestApplyInProgressError",
]
