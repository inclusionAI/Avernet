"""The bot-facing CLI-tools contract the HTTP adapter binds to (W9, #1477).

A separate Protocol from :class:`CliToolService` because they answer different
questions. ``CliToolService`` takes a fully resolved
:class:`CliToolContext` — it is the component that *does the work*, and both
callers reach it. This one takes a ``bot_id`` and a caller, resolves the bot's
env, entity and engine family itself, and hands the work down. A route must not
be the place that resolves a bot's storage coordinates, and a materialiser
already has them, which is why there are two shapes rather than one.

Declared in ``core`` and re-exported from ``api/`` so the concrete service can
inherit it without a ``core -> api`` waiver — the arrangement the sibling
manifest contracts use.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, Sequence, runtime_checkable

from agentclaw.community.core.bot_config_manifest.cli_tools.declarations import (
    CliToolDecl,
    CliToolOutcome,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.models import (
    BotCliToolRecord,
)


class CliToolNotFoundError(LookupError):
    """The bot has no tool by that name."""


class CliToolConflictError(ValueError):
    """The bot already has a tool by that name."""


class CliToolRefusedError(ValueError):
    """The declaration, the bytes or the engine refused the install."""


class CliToolUnsupportedError(ValueError):
    """This bot's engine cannot take CLI tools at all."""


@runtime_checkable
class BotCliToolServiceProtocol(Protocol):
    """Install, list and remove a bot's CLI tools, addressed by ``bot_id``."""

    @abstractmethod
    async def install(
        self, *, bot_id: str, owner_id: str, actor_id: str, decl: CliToolDecl
    ) -> BotCliToolRecord:
        """Install one tool and return its record.

        Raises:
            CliToolUnsupportedError: the bot's engine takes no CLI tools.
            CliToolConflictError: the bot already has a tool by that name.
            CliToolRefusedError: the declaration, the bytes or the engine
                refused — the reason is the service's own outcome detail.
        """
        ...

    @abstractmethod
    def list(
        self, *, bot_id: str, owner_id: str, actor_id: str
    ) -> Sequence[BotCliToolRecord]:
        """Every tool the platform records for the bot, in name order."""
        ...

    @abstractmethod
    async def remove(
        self, *, bot_id: str, owner_id: str, actor_id: str, name: str
    ) -> CliToolOutcome:
        """Remove one tool.

        Raises:
            CliToolNotFoundError: the bot has no tool by that name.
            CliToolRefusedError: the engine refused the removal.
        """
        ...


__all__ = [
    # Re-exported so the ``api/`` contract has one source: every name an
    # adapter needs to call this Protocol arrives from the module that
    # declares it, rather than the adapter reaching past it into the
    # implementation package for the types in its own signatures.
    "BotCliToolRecord",
    "BotCliToolServiceProtocol",
    "CliToolDecl",
    "CliToolConflictError",
    "CliToolNotFoundError",
    "CliToolOutcome",
    "CliToolRefusedError",
    "CliToolUnsupportedError",
]
