"""Community-safe no-op ChangeFlow observer."""

from __future__ import annotations

from secbaas.community.spi.changeflow import (
    ChangeFlowBatchContext,
    ChangeFlowRegisterContext,
    ChangeFlowTerminalContext,
)


class NoopChangeFlowObserver:
    """Default observer: every hook is an in-process no-op.

    The community build has no outbound ChangeFlow dependency, so publish
    behaviour is byte-for-byte unchanged when this observer is selected.
    """

    async def on_register(self, ctx: ChangeFlowRegisterContext) -> str | None:
        return None

    async def on_pre_check(self, ctx: ChangeFlowBatchContext) -> None:
        return None

    async def on_batch_result(self, ctx: ChangeFlowBatchContext) -> None:
        return None

    async def on_publish_terminal(self, ctx: ChangeFlowTerminalContext) -> None:
        return None
