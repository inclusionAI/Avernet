"""Service API Protocol for the per-bot configuration manifest (issue #1469).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_config_manifest/bot_config_manifest_service_protocol.py``) so the
concrete service can inherit it without a ``core -> api`` waiver; adapters keep
importing it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
    MAX_DOCUMENT_BYTES,
    ManifestCapabilities,
    ManifestNotEncodableError,
    ManifestTooLargeError,
    ManifestValidationError,
    ManifestWriteResult,
    ValidationResult,
    Violation,
)

__all__ = [
    "BotConfigManifestServiceProtocol",
    "MAX_DOCUMENT_BYTES",
    "ManifestCapabilities",
    "ManifestNotEncodableError",
    "ManifestTooLargeError",
    "ManifestValidationError",
    "ManifestWriteResult",
    "ValidationResult",
    "Violation",
]
