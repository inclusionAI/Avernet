"""Service API Protocol for tenant source credentials (W3, #1471).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_config_manifest/credentials/service_protocol.py``) so the
concrete service can inherit it without a ``core -> api`` waiver; adapters
keep importing it from here. Error types stay in their core module —
adapters import those from core directly (the repo's established split).
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (  # noqa: F401  re-export
    SourceCredentialServiceProtocol,
)

__all__ = [
    "SourceCredentialServiceProtocol",
]
