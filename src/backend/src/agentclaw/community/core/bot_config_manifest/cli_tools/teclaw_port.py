"""teclaw delivery: the composed artifact *is* the delivery.

A teclaw bot boots from an artifact and applies it in full before it reports
ready. So a tool reaches it exactly the way an ``mcp`` server does: the platform
writes its own state — the ``ac_bot_cli_tool`` row and the bytes in the object
store — and an artifact composed from that state carries a ``cliToolRef``
pointing at those bytes. There is no upload call to make, and making one would
be a second delivery mechanism for a category that already has one.

**All three write methods therefore do the same thing**: hand the running
container a freshly composed artifact, once. A single tool and a whole set are
the same operation here, because the artifact always carries the whole set —
which is why this family cannot express an intermediate state and does not have
to (spec D-13).

**The row must already be written when a method here is called** (spec D-14).
The compose reads ``ac_bot_cli_tool``; a port called before the write would
transmit the previous set. That inverts rev 7's order, and the service's
single-tool paths compensate by rolling their row back when this port refuses.

**Who pushes depends on who owns the end of the operation**, which is why
``redeliver`` is optional rather than assumed:

* the **management API** has no other closing step, so its binding carries the
  redeliver and this port makes the push;
* a **manifest apply** ends at ``TeclawDelivery.finish``, which pushes one
  artifact covering every category it wrote. Its binding carries ``None``, and
  this port stays silent — a push from here would arrive mid-apply, with
  ``cli_tools`` final and ``resources`` or ``skills`` not yet written, and be
  followed by the correct one.

Nothing in this module composes or parses a container path: teclaw resolves a
ref's ``store`` and ``path`` against its own store coordinates and decides
where the tool lands, exactly as it does for every other file in the artifact.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.delivery_port import (
    CliToolDeliveryError,
    CliToolDeliveryPort,
    CliToolPlacementError,
    DeliverableCliTool,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: ``(ctx) -> note``: composes the bot's whole artifact and delivers it to the
#: running container, answering ``None`` on success and a human-readable note
#: when the container could not be updated. The same callable a manifest apply
#: closes with — ``TeclawRedeliver``, bound by DI.
ArtifactRedeliver = Callable[[Any], Awaitable[Optional[str]]]


class CliToolDriftUnobservableError(CliToolDeliveryError):
    """This family cannot be asked what a bot currently has."""


class TeclawCliToolPort(CliToolDeliveryPort):
    """No engine CLI call. The composed artifact is the delivery."""

    #: The artifact references the objects; it does not carry them.
    needs_tool_bytes = False

    def __init__(self, *, redeliver: Optional[ArtifactRedeliver] = None) -> None:
        # ``None`` is not "unwired" — it is the apply path saying *I will make
        # that push myself, once, at the end*. See the module docstring.
        self._redeliver = redeliver

    # ── writes: all one operation ────────────────────────────────────────

    async def install(
        self, ctx: CliToolContext, *, name: str, data: bytes
    ) -> None:
        logger.info(
            "[cli_tools/teclaw] install bot=%s name=%s size=%d",
            ctx.bot_id, name, len(data),
        )
        await self._push(ctx, what=f"install {name!r}")

    async def delete(self, ctx: CliToolContext, *, name: str) -> None:
        logger.info("[cli_tools/teclaw] delete bot=%s name=%s", ctx.bot_id, name)
        await self._push(ctx, what=f"delete {name!r}")

    async def replace_all(
        self, ctx: CliToolContext, tools: Sequence[DeliverableCliTool]
    ) -> Mapping[str, str]:
        """One artifact carries the whole set, so this is one push.

        The ``tools`` bytes are not read: they are already in the object store,
        and the artifact references them there. The argument is the port's
        shared shape, not something this family needs — which is the clearest
        statement of what "the artifact is the delivery" means.

        Always an empty mapping: an artifact is accepted or it is not, and a
        rejection raises. There is no per-tool verdict to report because there
        was no per-tool call — the apply report then says what the *platform*
        made of each declaration, which is the half that can differ.
        """
        logger.info(
            "[cli_tools/teclaw] replace bot=%s tools=%d — carried by the "
            "composed artifact",
            ctx.bot_id, len(tools),
        )
        await self._push(ctx, what=f"replace the tool set ({len(tools)} tool(s))")
        return {}

    async def _push(self, ctx: CliToolContext, *, what: str) -> None:
        """Compose and deliver, when this binding owns the push.

        A refusal raises, unlike the pre-rev-8 redeliver that only logged: the
        service's single-tool paths roll their row back on it, so swallowing it
        here would leave the platform claiming a tool the container never got.
        A bot with **no live binding** is not a refusal — the redeliver answers
        ``None`` for it, because provisioning composes the first artifact from
        the state just written.
        """
        if self._redeliver is None:
            return
        note = await self._redeliver(ctx)
        if note:
            raise CliToolPlacementError(
                f"cli_tools: could not {what} for bot {ctx.bot_id}: {note}"
            )

    # ── the read that cannot be made ─────────────────────────────────────

    async def list(self, ctx: CliToolContext) -> list[str]:
        """Refuse the question rather than answer it wrongly.

        Nothing is left to implement here — the refusal *is* the answer, and it
        would stay the right one even if teclaw grew a listing endpoint
        tomorrow. Drift means "the table and the bot disagree", and on this
        family the bot's set is *composed from* the table: an engine that
        applied its last artifact would report exactly the table back, which is
        a tautology dressed as an observation, and one that had not would
        report a stale artifact rather than drift.

        The two wrong answers are worse than refusing. Returning the table
        makes ``drift()`` claim "converged" on a bot nobody has checked.
        Returning ``[]`` reads as "this bot has no tools" — the more dangerous,
        since a removal is computed from it, so a full override would delete
        every tool the bot has.
        """
        raise CliToolDriftUnobservableError(
            "cli_tools: teclaw delivers by artifact, which is composed from the "
            "platform's own table, so there is nothing independent to list"
        )


__all__ = [
    "ArtifactRedeliver",
    "CliToolDriftUnobservableError",
    "TeclawCliToolPort",
]
