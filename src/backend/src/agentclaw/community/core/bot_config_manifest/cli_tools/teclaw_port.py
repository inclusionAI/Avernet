"""teclaw delivery: the composed artifact *is* the delivery.

A teclaw bot boots from an artifact and applies it in full before it reports
ready. So a tool reaches it exactly the way an ``mcp`` server does: the
platform writes its own state — the ``ac_bot_cli_tool`` row and the bytes in
the object store — and the next compose carries a ``cliToolRef`` pointing at
those bytes. There is no upload call to make, and making one would be a second
delivery mechanism for a category that already has one.

``install`` and ``delete`` therefore do nothing here beyond letting the
caller's row-and-store write stand. That is not a stub: it is the statement
that on this family the write the service already performed *was* the
delivery, and the strategy's closing redeliver (W8) is what pushes it. The
methods exist so that the service has one shape to call on both families and
branches on neither.

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

        Drift means "the table and the bot disagree". On teclaw the artifact is
        composed *from* the table, so there is no independent observation to
        make: returning the table back would be a tautology dressed as an
        observation, and returning ``[]`` would read as "this bot has no
        tools" — the more dangerous of the two, since it is what a removal
        would be computed from. The service reports the drift read as
        unobservable on this family instead.
        """
        raise CliToolDriftUnobservableError(
            "cli_tools: teclaw delivers by artifact, which is composed from the "
            "platform's own table, so there is nothing independent to list"
        )


__all__ = ["CliToolDriftUnobservableError", "TeclawCliToolPort"]
