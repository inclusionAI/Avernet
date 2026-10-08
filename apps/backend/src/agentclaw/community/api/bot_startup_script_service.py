"""Service API Protocol for the per-bot startup script (issue #926).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_startup_script/bot_startup_script_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
    MAX_SCRIPT_BYTES,
    StartupScriptNotEncodableError,
    StartupScriptTooLargeError,
    SUPPORTED,
    UNSUPPORTED,
)

__all__ = [
    "BotStartupScriptServiceProtocol",
    "MAX_SCRIPT_BYTES",
    "StartupScriptNotEncodableError",
    "StartupScriptTooLargeError",
    "SUPPORTED",
    "UNSUPPORTED",
]
