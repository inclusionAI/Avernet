"""teclaw delivery: the composed artifact *is* the delivery.

A teclaw bot boots from an artifact and applies it in full before it reports
ready. So a tool reaches it exactly the way an ``mcp`` server does: the
platform writes its own state — the ``ac_bot_cli_tool`` row and the bytes in
the object store — and the next compose carries a ``cliToolRef`` pointing at
those bytes. There is no upload call to make, and making one would be a second
delivery mechanism for a category that already has one.

``install`` and ``delete`` therefore do nothing here beyond letting the
caller's row-and-store write stand. That is not a stub, and the work is not
missing — it is on either side of this port:

===========================  ==========================================
step                         where it happens
===========================  ==========================================
skip an unchanged tool       ``CliToolService.replace_all`` — the
                             convergence key, before ``install`` is
                             called at all
write the bytes to OSS       ``CliToolService.install`` → ``CliToolStore
                             .put``, before this port is reached
record the row               ``CliToolService.install``, after this port
                             returns
**push it to the bot**       **here — and on this family there is nothing
                             to push**
compose the artifact         the next compose reads the table
deliver the artifact         ``TeclawDelivery.finish`` (W8's redeliver),
                             or ``BotCliToolService`` on the API path
===========================  ==========================================

So the port is the one step that differs by family, and teclaw's answer to it
is genuinely "nothing": the store write the service already performed *was*
the delivery. The methods exist so the service has one shape to call on both
families and branches on neither.

Nothing in this module composes or parses a container path: teclaw resolves a
ref's ``store`` and ``path`` against its own store coordinates and decides
where the tool lands, exactly as it does for every other file in the artifact.
"""
from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.delivery_port import (
    CliToolDeliveryError,
    CliToolDeliveryPort,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class CliToolDriftUnobservableError(CliToolDeliveryError):
    """This family cannot be asked what a bot currently has."""


class TeclawCliToolPort(CliToolDeliveryPort):
    """No engine call. The artifact refs are the delivery."""

    async def install(
        self, ctx: CliToolContext, *, name: str, data: bytes
    ) -> None:
        """Nothing to push: the row and the stored bytes are what compose."""
        logger.info(
            "[cli_tools/teclaw] install bot=%s name=%s size=%d — carried by the "
            "next composed artifact",
            ctx.bot_id, name, len(data),
        )

    async def delete(self, ctx: CliToolContext, *, name: str) -> None:
        """Nothing to push: the next artifact simply stops carrying the ref."""
        logger.info(
            "[cli_tools/teclaw] delete bot=%s name=%s — dropped from the next "
            "composed artifact",
            ctx.bot_id, name,
        )

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

        Only ``list`` refuses; ``install`` and ``delete`` are ordinary no-ops,
        because writing platform state is a thing this family can do and
        observing the engine's independently is not.
        """
        raise CliToolDriftUnobservableError(
            "cli_tools: teclaw delivers by artifact, which is composed from the "
            "platform's own table, so there is nothing independent to list"
        )


__all__ = ["CliToolDriftUnobservableError", "TeclawCliToolPort"]
