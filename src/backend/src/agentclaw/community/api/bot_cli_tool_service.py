"""Service API Protocol for a bot's platform-managed CLI tools (W9, #1477).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_config_manifest/cli_tools/service_protocol.py``) so the concrete
service can inherit it without a ``core -> api`` waiver; adapters keep importing
it from here — the same arrangement the sibling manifest contracts use.
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.cli_tools.service_protocol import (
    BotCliToolRecord,
    BotCliToolServiceProtocol,
    CliToolConflictError,
    CliToolDecl,
    CliToolNotFoundError,
    CliToolOutcome,
    CliToolRefusedError,
    CliToolUnsupportedError,
)

__all__ = [
    "BotCliToolRecord",
    "BotCliToolServiceProtocol",
    "CliToolConflictError",
    "CliToolDecl",
    "CliToolNotFoundError",
    "CliToolOutcome",
    "CliToolRefusedError",
    "CliToolUnsupportedError",
]
