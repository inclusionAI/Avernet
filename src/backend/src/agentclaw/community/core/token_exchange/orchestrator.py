"""Caller-first orchestration for independent outbound token plugins."""
from __future__ import annotations

from agentclaw.community.core.caller_identity.caller_iam_token_service_protocol import CallerIamTokenServiceProtocol
from agentclaw.community.core.caller_identity.contracts import (
    CallerIamTokenOutcome,
    CallerIdentityStage,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.token_exchange.pipeline import TokenPluginPipeline
from agentclaw.community.core.token_exchange.protocols import TokenExchangeContext
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthRequestContext


logger = get_logger()


class TokenExchangeOrchestrator:
    """Run the existing Caller service first, then independent token plugins."""

    def __init__(
        self,
        *,
        caller_service: CallerIamTokenServiceProtocol,
        token_pipeline: TokenPluginPipeline,
        bot_repository: BotRepository,
    ) -> None:
        self._caller_service = caller_service
        self._token_pipeline = token_pipeline
        self._bot_repository = bot_repository

    async def get_iam_token(
        self,
        *,
        iam_token: str,
        auth_request: AuthRequestContext,
        bot_id: str | None,
        stage: CallerIdentityStage,
        publish_id: int | None,
        entity_id: str | None,
        is_test_exchange: bool,
    ) -> CallerIamTokenOutcome:
        caller_error: Exception | None = None
        caller_outcome: CallerIamTokenOutcome | None = None
        try:
            caller_outcome = await self._caller_service.get_iam_token(
                iam_token=iam_token,
                auth_request=auth_request,
                bot_id=bot_id,
                stage=stage,
                publish_id=publish_id,
                entity_id=entity_id,
                is_test_exchange=is_test_exchange,
            )
        except Exception as exc:
            caller_error = exc
            logger.warning(
                "caller_first_execution_failed error_type=%s bot_id=%s",
                type(exc).__name__,
                bot_id,
            )

        try:
            context = self._build_context(
                bot_id=bot_id,
                entity_id=entity_id,
                stage=stage,
                publish_id=publish_id,
            )
        except Exception as exc:
            context = None
            logger.warning(
                "token_plugin_context_build_failed bot_id=%s error_type=%s",
                bot_id,
                type(exc).__name__,
            )
        if context is not None:
            try:
                await self._token_pipeline.run(context)
            except Exception as exc:
                # Pipeline and individual plugins are expected to isolate errors;
                # this guard preserves the legacy Caller contract if they do not.
                logger.warning(
                    "token_plugin_pipeline_failed bot_id=%s error_type=%s",
                    context.bot_id,
                    type(exc).__name__,
                )

        if caller_error is not None:
            raise caller_error
        assert caller_outcome is not None
        return caller_outcome

    def _build_context(
        self,
        *,
        bot_id: str | None,
        entity_id: str | None,
        stage: CallerIdentityStage,
        publish_id: int | None,
    ) -> TokenExchangeContext | None:
        if not bot_id or not entity_id:
            return None
        bot = self._bot_repository.get_by_id_and_entity(bot_id, entity_id)
        if not bot:
            logger.info(
                "token_plugin_context_unavailable bot_id=%s entity_id_present=%s",
                bot_id,
                True,
            )
            return None
        owner_user_id = str(bot.get("owner_id") or "")
        bot_type = str(bot.get("bot_type") or "")
        if not owner_user_id or not bot_type:
            return None
        return TokenExchangeContext(
            bot_id=bot_id,
            bot_type=bot_type,
            owner_user_id=owner_user_id,
            user_list_entity_id=entity_id,
            entity_id=entity_id,
            stage=stage.value,
            env=str(bot.get("env") or ""),
            publish_id=publish_id,
            binding_id=None,
            request_id=None,
        )


__all__ = ["TokenExchangeOrchestrator"]
