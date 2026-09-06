"""``CliToolsService`` — placing model-callable commands on a bot.

The engine owns **everything about placement**. An ``install`` carries the
semantics *"make this the bot's ``name`` command"* — not *"write these bytes
somewhere"* — so the directory, the executable bit and exposure to the agent
are all decided behind this interface. The platform sends no ``chmod`` and runs
no shell command, and therefore no user-supplied name ever reaches one.

Contract: ``src/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md``
§4 A2. The platform caller is ``ArcaCliToolPort``
(``src/backend/src/agentclaw/community/core/bot_config_manifest/cli_tools/arca_port.py``).

**Every member is ``@abstractmethod``, and implementations subclass this
Protocol explicitly.** Both halves are load-bearing. This repository runs no
static type checker, so a structurally-satisfied Protocol is verified by
nothing — not at import, not at construction, not in CI. Worse, a plain ``...``
stub is *inherited* in place of a method an implementation forgot: the name
still resolves and the call silently returns ``None``. With ``@abstractmethod``,
an implementation that dropped a method cannot be constructed at all.

The same rule the backend states for its outbound ports
(``src/backend/src/agentclaw/community/core/ports/README.md``), for the same
reason.
"""
from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from engine.community.core.cli_tools.models import (
    CliToolBytes,
    CliToolInfo,
    CliToolPayload,
    ReplaceOutcome,
)


@runtime_checkable
class CliToolsService(Protocol):
    """Place, remove and observe a bot's command-line tools."""

    @abstractmethod
    async def install(self, name: str, data: bytes) -> None:
        """Make ``name`` this bot's command, leaving every other one alone.

        Raises on refusal — a tool the engine could not install must never be
        reported as installed.
        """
        ...

    @abstractmethod
    async def delete(self, name: str) -> None:
        """Remove ``name``. A command that was never there is **success**."""
        ...

    @abstractmethod
    async def list_tools(self) -> list[CliToolInfo]:
        """What this bot has **on disk**, read fresh every call.

        Never a replay of the last install or the last artifact received: a
        replay would be a tautology, and the whole value of this call is
        catching what a replay cannot — a partially delivered set, a manual
        edit inside the container, a restore from an old snapshot, or a tool
        the engine itself dropped.
        """
        ...

    @abstractmethod
    async def read_tool(self, name: str) -> CliToolBytes | None:
        """One command's bytes, or ``None`` when there is no such command.

        ``None`` rather than an exception because "no such tool" is an ordinary
        answer here, and the HTTP layer must not turn it into a 404 — that
        status is reserved for "this engine build has no CLI endpoints" (§4 A2).
        """
        ...

    @abstractmethod
    async def replace_all(
        self,
        tools: Sequence[CliToolPayload],
        *,
        also_keep: Sequence[str] = (),
    ) -> ReplaceOutcome:
        """Make this set **the** command set. Deletion is implied.

        The argument is the desired end state, not a diff: a command present on
        the bot and absent from ``tools`` is removed. An empty sequence is a
        real and meaningful request — "this bot has no commands" — not a no-op.

        ``also_keep`` names entries the caller requested but could not prepare
        — a payload that would not decode, say. They are not installed, but
        they are not pruned either, so a replacement that failed for one entry
        leaves that tool unchanged rather than deleting it.

        Returns one verdict per requested name, successes and failures alike;
        partial failure is an ordinary outcome, not an error. Failures while
        *pruning* ride alongside, because those names have no verdict slot and
        silently dropping them would let a replacement claim success while a
        tool the manifest removed is still callable.
        """
        ...


__all__ = ["CliToolsService"]
