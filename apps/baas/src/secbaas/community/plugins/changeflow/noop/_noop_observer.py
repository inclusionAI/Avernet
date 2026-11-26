"""No-op ChangeFlow observer — community default (OSS purity, Rule 20/21).

ChangeFlow integration is observe-only and the community build has no
ChangeFlow backend, so the default observer records nothing and never blocks.
Enterprise overlays inject a real HTTP observer via DI.
"""

from __future__ import annotations

from secbaas.community.logger import get_logger
from secbaas.community.spi.changeflow import (
    BatchCtx,
    ChangeFlowObserver,
    PublishCtx,
    TerminalCtx,
)

logger = get_logger("core-service")


class NoopChangeFlowObserver(ChangeFlowObserver):
    """Community no-op: no ChangeFlow backend wired.

    All hooks return immediately. register returns None (no change_id), so the
    caller skips subsequent batch/terminal reports for the publish. This keeps
    the OSS publish path unaffected by ChangeFlow.
    """

    async def on_register(self, ctx: PublishCtx) -> str | None:
        logger.debug(
            "changeflow noop on_register: publish_id=%s publish_type=%s",
            ctx.publish_id,
            ctx.publish_type,
        )
        return None

    async def on_pre_check(self, ctx: BatchCtx) -> None:
        logger.debug(
            "changeflow noop on_pre_check: publish_id=%s batch=%s",
            ctx.publish_id,
            ctx.batch_index,
        )

    async def on_batch_result(self, ctx: BatchCtx) -> None:
        logger.debug(
            "changeflow noop on_batch_result: publish_id=%s batch=%s",
            ctx.publish_id,
            ctx.batch_index,
        )

    async def on_publish_terminal(self, ctx: TerminalCtx) -> None:
        logger.debug(
            "changeflow noop on_publish_terminal: publish_id=%s outcome=%s",
            ctx.publish_id,
            ctx.outcome,
        )
