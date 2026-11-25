"""BotCopyModule — DI bindings for the bot-copy feature.

Owns the `ac_bot_copy` operation ledger binding (`BotCopyRepositoryProtocol` →
`BotCopyRepositoryImpl`). Kept in a module of its own rather than folded into
`BotManagementModule` so that module does not trip the 1000-line cap
(R9), and because the copy feature's storage is a cohesive concern separate
from the bot-management aggregate.

Same `DatabasePlugin`-injected shape as the apply-record repository — the impl
is `@inject`-constructed and picks up the swapped database (ZDAS prod / SQLite
local+test) transparently.
"""

from __future__ import annotations

from injector import Module, singleton

from agentclaw.community.core.bot_copy.repository.protocol import (
    BotCopyRepositoryProtocol,
)
from agentclaw.community.core.bot_copy.repository.sqlalchemy_impl import (
    BotCopyRepositoryImpl,
)


class BotCopyModule(Module):
    """Bind the bot-copy operation ledger repository."""

    def configure(self, binder) -> None:  # type: ignore[override]
        binder.bind(
            BotCopyRepositoryProtocol,
            to=BotCopyRepositoryImpl,
            scope=singleton,
        )


__all__ = ["BotCopyModule"]
