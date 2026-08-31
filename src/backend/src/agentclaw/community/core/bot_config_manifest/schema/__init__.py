"""Manifest schema v1 — parsing, validation, and the limits a write can check.

Import from here rather than from the submodules: the split between
``validator`` (document shape) and ``entries`` (per-entry fields) is a
file-layout decision made for the 1000-line module cap, not an API.
"""
from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.schema.limits import (
    MAX_DOCUMENT_BYTES,
    MAX_ENTRIES_PER_CATEGORY,
    MAX_INLINE_CONTENT_BYTES,
    MAX_SCRIPT_BYTES,
)
from agentclaw.community.core.bot_config_manifest.schema.placeholders import (
    ALLOWED_PLACEHOLDERS,
    BOT_ARCH_VALUE,
    resolve as resolve_placeholders,
    unknown_placeholders,
)
from agentclaw.community.core.bot_config_manifest.schema.validator import (
    ManifestNotEncodableError,
    ManifestTooLargeError,
    ValidationResult,
    validate_document,
)
from agentclaw.community.core.bot_config_manifest.schema.violations import (
    ManifestValidationError,
    Violation,
)

__all__ = [
    "ALLOWED_PLACEHOLDERS",
    "BOT_ARCH_VALUE",
    "MAX_DOCUMENT_BYTES",
    "MAX_ENTRIES_PER_CATEGORY",
    "MAX_INLINE_CONTENT_BYTES",
    "MAX_SCRIPT_BYTES",
    "ManifestNotEncodableError",
    "ManifestTooLargeError",
    "ManifestValidationError",
    "ValidationResult",
    "Violation",
    "resolve_placeholders",
    "unknown_placeholders",
    "validate_document",
]
