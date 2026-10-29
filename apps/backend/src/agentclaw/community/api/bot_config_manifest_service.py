"""Service API Protocol for the per-bot configuration manifest (issue #1469).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_config_manifest/bot_config_manifest_service_protocol.py``) so the
concrete service can inherit it without a ``core -> api`` waiver; adapters keep
importing it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
    ManifestBindingMismatchError,
    bound_manifest_state,
    MANIFEST_STATE,
    MAX_DOCUMENT_BYTES,
    Capabilities,
    ManifestNotEncodableError,
    ManifestTooLargeError,
    ManifestValidationError,
    ManifestWriteResult,
    ValidationResult,
    Violation,
)

__all__ = [
    "ManifestBindingMismatchError",
    "bound_manifest_state",
    "BotConfigManifestServiceProtocol",
    "MANIFEST_STATE",
    "MAX_DOCUMENT_BYTES",
    "Capabilities",
    "ManifestNotEncodableError",
    "ManifestTooLargeError",
    "ManifestValidationError",
    "ManifestWriteResult",
    "ValidationResult",
    "Violation",
]
