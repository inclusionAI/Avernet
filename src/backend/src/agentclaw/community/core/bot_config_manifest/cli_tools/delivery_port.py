"""How a family gets a CLI tool into a bot (W9, issue #1477).

**Name-addressed, and that is the whole contract.** Every method takes the
command's ``name``; none takes, returns or composes a filesystem path. The
engine chooses the directory, sets the executable bit and exposes the tool to
the agent — all of it inside its own ``install``, which is what makes the
call carry the semantics "make this a CLI tool for this bot" rather than
"write these bytes there" (spec D-3). The platform therefore has no tools
directory constant, no ``chmod``, and no shell command to quote a user-supplied
name into.

**Two shapes, because there are two kinds of caller** (spec D-13). A bot owner
editing one tool on a live bot uses ``install`` or ``delete``; a manifest apply
is a full override and uses :meth:`CliToolDeliveryPort.replace_all`, which says
*this is the entire set* in one call. Driving an apply through the single-tool
methods instead would mean one engine round trip per tool and, worse, a
sequence of intermediate states on the wire: removals precede installs, so a
container would first be told it has lost tools it is about to regain. A set is
delivered as a set.

Both are needed. A one-tool edit expressed as a whole-set call would
re-transmit every binary the bot has, which on this category means megabytes.

**There is deliberately no ``get``.** The platform stores every tool's bytes
itself (``store.py``), so nothing ever reads them back out of a container.
Rev 5 needed a ``get`` because promotion gathered from the engine; rev 6
removed the need by keeping the platform's own copy (spec D-4, D-5). ``list``
remains — not as a source of truth, but so drift between the platform's table
and what a container actually holds is *observable* rather than assumed away.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Sequence

from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)


class CliToolDeliveryError(RuntimeError):
    """The family could not deliver.

    Carried up to the service, which turns it into a failed outcome — and, on
    the single-tool paths, into a rollback of the row it had just written.
    """


class CliToolPlacementError(CliToolDeliveryError):
    """The engine refused, or could not be reached, on an install or a delete."""


@dataclass(frozen=True)
class DeliverableCliTool:
    """One tool as the delivery boundary sees it: a name and the bytes.

    Nothing else crosses. No digest — the platform already proved the bytes
    match it, and re-checking on the far side would make the engine a second
    place the pin is enforced. No path — the engine owns placement. No version
    — it is metadata the artifact carries and the engine ignores.
    """

    name: str
    data: bytes


class CliToolDeliveryPort(ABC):
    """The per-family boundary: three single-tool primitives and a whole-set one.

    Declared as a base class rather than a bare ``Protocol`` so that a family
    that forgets a method fails at construction rather than mid-apply — the
    same reason the repository protocols are (Rule 8).
    """

    #: Whether :meth:`replace_all` needs each tool's **bytes**, or only its
    #: name. A family that POSTs the binaries needs them; one whose artifact
    #: *references* the object store does not, and reading a few hundred
    #: megabytes back out to hand it an argument it ignores would be pure
    #: waste. Declared here so the service can decide without asking which
    #: family it holds.
    needs_tool_bytes: bool = True

    @abstractmethod
    async def install(
        self, ctx: CliToolContext, *, name: str, data: bytes
    ) -> None:
        """Make ``data`` the bot's ``name`` command, leaving its others alone.

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

    @abstractmethod
    async def replace_all(
        self, ctx: CliToolContext, tools: Sequence[DeliverableCliTool]
    ) -> Mapping[str, str]:
        """Make ``tools`` the bot's entire set. Anything else it has is gone.

        Removals are implicit, which is what "replace" means: the caller states
        the destination, not the diff. That is also why this cannot be built
        out of ``install`` and ``delete`` — those describe a journey, and a
        journey has intermediate states an observer can see.

        Returns a mapping of **name to failure reason, for the failures only**;
        an empty mapping means every tool landed. Per name rather than one
        verdict because the apply report is per declared entry, and collapsing
        four tools into one "the batch failed" would lose which of them the
        engine actually objected to (spec D-15).

        Raises:
            CliToolDeliveryError: the call itself did not complete — the engine
                was unreachable, or answered in a shape that cannot be read as
                per-name results. A partial answer is returned, not raised; an
                unreadable one is raised, because reporting silence as success
                would record tools nobody confirmed.
        """


__all__ = [
    "CliToolDeliveryError",
    "CliToolDeliveryPort",
    "CliToolPlacementError",
    "DeliverableCliTool",
]
