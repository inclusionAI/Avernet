"""Service API Protocol for the bot config manifest document (issue #1469).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_config_manifest/manifest_service_protocol.py``) so the concrete
service can inherit it without a ``core -> api`` waiver; adapters keep
importing it from here. Errors and schema models stay in their core modules —
adapters import them from core directly.
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.manifest_service_protocol import (
    ManifestServiceProtocol,
)

__all__ = [
    "ManifestServiceProtocol",
]
