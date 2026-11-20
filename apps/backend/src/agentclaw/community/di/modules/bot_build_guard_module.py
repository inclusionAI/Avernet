"""BotBuildGuardModule — DI binding for the build-active guard.

Owns the ``ac_bot_build_active`` presence marker binding
(``BotBuildActiveRepositoryProtocol`` → ``BotBuildActiveRepository``). Kept in a
module of its own rather than folded into ``BotManagementModule`` so that
module stays within the R9 1000-line cap (same headroom reason as
``BotCopyModule``).

The impl is ``@inject``-constructed and picks up the swapped ``DatabasePlugin``
(ZDAS prod / SQLite local+test) transparently, like the restart lock it is
shaped after.
"""

from __future__ import annotations

from injector import Module, singleton

from agentclaw.community.core.repository.implementations.bot.build_active import (
    BotBuildActiveRepository,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotBuildActiveRepositoryProtocol,
)


class BotBuildGuardModule(Module):
    """Bind the build-active guard repository."""

    def configure(self, binder) -> None:  # type: ignore[override]
        binder.bind(
            BotBuildActiveRepositoryProtocol,
            to=BotBuildActiveRepository,
            scope=singleton,
        )


__all__ = ["BotBuildGuardModule"]