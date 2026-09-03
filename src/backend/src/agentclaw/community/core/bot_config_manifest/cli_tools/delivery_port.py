"""How a family gets a CLI tool into a bot (W9, issue #1477).

**Name-addressed, and that is the whole contract.** Every method takes the
command's ``name``; none takes, returns or composes a filesystem path. The
engine chooses the directory, sets the executable bit and exposes the tool to
the agent — all of it inside its own ``install``, which is what makes the
call carry the semantics "make this a CLI tool for this bot" rather than
"write these bytes there" (spec D-3). The platform therefore has no tools
directory constant, no ``chmod``, and no shell command to quote a user-supplied
name into.

**There is deliberately no ``get``.** The platform stores every tool's bytes
itself (``store.py``), so nothing ever reads them back out of a container.
Rev 5 needed a ``get`` because promotion gathered from the engine; rev 6
removed the need by keeping the platform's own copy (spec D-4, D-5). ``list``
remains — not as a source of truth, but so drift between the platform's table
and what a container actually holds is *observable* rather than assumed away.

**``replace_all`` is here, with a default, because a manifest apply is a full
override.** The declared set becomes the installed set, so the interesting
operation on this boundary is a batch, not a single install. The default
removes before installing: a name being removed in the same call as it is
installed is a replacement, and doing it in the other order would delete the
tool that was just placed. A family whose engine offers a real batch endpoint
overrides it; neither family that ships does.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)


class CliToolDeliveryError(RuntimeError):
    """The family could not deliver, and the platform must not record it.

    Carried up to the service, which records nothing for a step that failed —
    so a tool the engine refused never appears in the table as installed.
    """


class CliToolPlacementError(CliToolDeliveryError):
    """The engine refused, or could not be reached, on an install or a delete."""


class CliToolDeliveryPort(ABC):
    """The per-family boundary. Three primitives and one batch over them.

    Declared as a base class rather than a bare ``Protocol`` so that a family
    that forgets a method fails at construction rather than mid-apply — the
    same reason the repository protocols are (Rule 8) — and so that
    ``replace_all`` has one implementation instead of two copies.
    """

    @abstractmethod
    async def install(
        self, ctx: CliToolContext, *, name: str, data: bytes
    ) -> None:
        """Make ``data`` the bot's ``name`` command.

        Raises:
            CliToolPlacementError: the engine refused or was unreachable.
        """

    @abstractmethod
    async def delete(self, ctx: CliToolContext, *, name: str) -> None:
        """Remove the bot's ``name`` command. Removing an absent one succeeds.

        Raises:
            CliToolPlacementError: the engine refused or was unreachable.
        """

    @abstractmethod
    async def list(self, ctx: CliToolContext) -> list[str]:
        """The command names the family believes the bot has.

        For the drift read only. The platform's table is what "installed"
        means; this is the other side of the comparison.
        """

    async def replace_all(
        self,
        ctx: CliToolContext,
        *,
        install: Sequence[tuple[str, bytes]],
        remove: Sequence[str],
    ) -> None:
        """Make the bot's set of tools match a declaration, in one call.

        Removals first, then installs: a name in both lists is a replacement,
        and installing first would delete what was just placed. Fails on the
        first error, leaving the rest undone — the service records per tool,
        so a partial batch is reported as what it is rather than rolled back
        into a state neither side asked for.
        """
        for name in remove:
            await self.delete(ctx, name=name)
        for name, data in install:
            await self.install(ctx, name=name, data=data)


__all__ = [
    "CliToolDeliveryError",
    "CliToolDeliveryPort",
    "CliToolPlacementError",
]
