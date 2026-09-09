"""Four-phase, failure-isolated token plugin pipeline."""
from __future__ import annotations

from collections.abc import Iterable

from agentclaw.community.core.token_exchange.protocols import (
    OutboundTokenPlugin,
    TokenExchangeContext,
    TokenOutboundAppender,
    TokenPluginResult,
)
from agentclaw.community.log import get_logger


logger = get_logger()


class TokenPluginPipeline:
    """Run registered plugins serially without cross-plugin failure coupling."""

    def __init__(
        self,
        *,
        plugins: Iterable[OutboundTokenPlugin],
        outbound_appender: TokenOutboundAppender,
    ) -> None:
        self._plugins = tuple(plugins)
        self._outbound_appender = outbound_appender

    async def run(self, context: TokenExchangeContext) -> list[TokenPluginResult]:
        return [await self._run_plugin(plugin=plugin, context=context) for plugin in self._plugins]

    async def _run_plugin(
        self,
        *,
        plugin: OutboundTokenPlugin,
        context: TokenExchangeContext,
    ) -> TokenPluginResult:
        stage = "match"
        try:
            if not plugin.match(context):
                logger.info(
                    "token_plugin_match_completed plugin_code=%s status=skipped bot_id=%s stage=%s",
                    plugin.code,
                    context.bot_id,
                    context.stage,
                )
                return TokenPluginResult.skipped(plugin.code)

            stage = "prepare_exchange_token_request"
            prepared = plugin.prepare_exchange_token_request(context)
            stage = "exchange_token"
            token = await plugin.exchange_token(context=context, prepared=prepared)
            stage = "append_to_outbound"
            plugin.append_to_outbound(
                context=context,
                prepared=prepared,
                token=token,
                appender=self._outbound_appender,
            )
            logger.info(
                "token_plugin_completed plugin_code=%s status=succeeded bot_id=%s stage=%s token_present=%s",
                plugin.code,
                context.bot_id,
                context.stage,
                True,
            )
            return TokenPluginResult.succeeded(plugin.code)
        except Exception as exc:
            logger.warning(
                "token_plugin_failed plugin_code=%s failed_stage=%s bot_id=%s stage=%s error_type=%s",
                plugin.code,
                stage,
                context.bot_id,
                context.stage,
                type(exc).__name__,
            )
            return TokenPluginResult.failed(
                plugin.code,
                failed_stage=stage,
                error_code="TOKEN_PLUGIN_EXECUTION_FAILED",
            )


class NoopTokenOutboundAppender:
    """No-op appender used by community and local profiles."""

    def append_token(self, **kwargs: object) -> bool:
        del kwargs
        return True


class NoopTokenPluginPipeline(TokenPluginPipeline):
    """Empty pipeline preserving profiles without corporate token plugins."""

    def __init__(self) -> None:
        super().__init__(plugins=(), outbound_appender=NoopTokenOutboundAppender())


__all__ = ["NoopTokenOutboundAppender", "NoopTokenPluginPipeline", "TokenPluginPipeline"]
