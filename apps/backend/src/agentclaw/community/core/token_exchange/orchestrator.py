"""Concurrent orchestration for Caller and independent outbound token plugins."""
from __future__ import annotations

import asyncio
from time import perf_counter

from agentclaw.community.core.caller_identity.caller_iam_token_service_protocol import CallerIamTokenServiceProtocol
from agentclaw.community.core.caller_identity.contracts import (
    CallerIamTokenOutcome,
    CallerIdentityStage,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.token_exchange.pipeline import TokenPluginPipeline
from agentclaw.community.core.token_exchange.protocols import TokenExchangeContext
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthRequestContext


logger = get_logger()


class TokenExchangeOrchestrator:
    """Await both independent pipelines while preserving the Caller outcome."""

    def __init__(
        self,
        *,
        caller_service: CallerIamTokenServiceProtocol,
        token_pipeline: TokenPluginPipeline,
        bot_repository: BotRepository,
        auth_plugin: AuthPlugin,
    ) -> None:
        self._caller_service = caller_service
        self._token_pipeline = token_pipeline
        self._bot_repository = bot_repository
        self._auth_plugin = auth_plugin

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
        session_key: str | None = None,
        friend_user_id: str | None = None,
        token_type: str = "iam",
    ) -> CallerIamTokenOutcome:
        logger.info("caller_token_source_selected bot_id=%s stage=%s token_type=%s",
                    bot_id, stage.value, token_type)
        async def run_caller() -> CallerIamTokenOutcome | Exception:
            caller_kwargs: dict[str, str] = {}
            if token_type != "iam":
                caller_kwargs["token_type"] = token_type
            if session_key is not None:
                caller_kwargs["session_key"] = session_key
            if friend_user_id is not None:
                caller_kwargs["friend_user_id"] = friend_user_id
            try:
                return await self._caller_service.get_iam_token(
                    iam_token=iam_token,
                    auth_request=auth_request,
                    bot_id=bot_id,
                    stage=stage,
                    publish_id=publish_id,
                    entity_id=entity_id,
                    is_test_exchange=is_test_exchange,
                    **caller_kwargs,
                )
            except Exception as exc:
                logger.warning(
                    "caller_pipeline_failed error_type=%s bot_id=%s",
                    type(exc).__name__, bot_id,
                )
                return exc

        started = perf_counter()
        logger.info("token_pipelines_started bot_id=%s stage=%s", bot_id, stage.value)
        try:
            async with asyncio.TaskGroup() as tasks:
                caller = tasks.create_task(run_caller())
                tasks.create_task(self._run_plugins(
                    bot_id=bot_id, entity_id=entity_id, stage=stage,
                    publish_id=publish_id, auth_request=auth_request,
                    friend_user_id=friend_user_id, token_type=token_type,
                ))
        finally:
            logger.info(
                "token_pipelines_finished bot_id=%s stage=%s elapsed_ms=%.1f",
                bot_id, stage.value, (perf_counter() - started) * 1000,
            )
        outcome = caller.result()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def _run_plugins(
        self,
        *,
        bot_id: str | None,
        entity_id: str | None,
        stage: CallerIdentityStage,
        publish_id: int | None,
        auth_request: AuthRequestContext,
        friend_user_id: str | None,
        token_type: str,
    ) -> None:
        context = None
        if token_type == "iam":
            try:
                context = await self._build_context(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    stage=stage,
                    publish_id=publish_id,
                    auth_request=auth_request,
                    friend_user_id=friend_user_id,
                )
            except Exception as exc:
                logger.warning(
                    "token_plugin_context_build_failed bot_id=%s error_type=%s",
                    bot_id,
                    type(exc).__name__,
                )
        else:
            logger.info(
                "token_plugin_context_skipped bot_id=%s reason=non_iam_credential",
                bot_id,
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

    async def _build_context(
        self,
        *,
        bot_id: str | None,
        entity_id: str | None,
        stage: CallerIdentityStage,
        publish_id: int | None,
        auth_request: AuthRequestContext,
        friend_user_id: str | None,
    ) -> TokenExchangeContext | None:
        if not bot_id or not entity_id:
            return None
        runtime_target = auth_request.query_params.get("caller_runtime_target")
        device_uuid = auth_request.query_params.get("device_uuid")
        if runtime_target not in (None, "service", "caller_instance"):
            logger.warning("token_plugin_context_skipped bot_id=%s reason=invalid_runtime_target", bot_id)
            return None
        if device_uuid == "":
            logger.warning("token_plugin_context_skipped bot_id=%s reason=invalid_device_uuid", bot_id)
            return None
        bot = await asyncio.to_thread(
            self._bot_repository.get_by_id_and_entity, bot_id, entity_id,
        )
        if not bot:
            logger.info(
                "token_plugin_context_unavailable bot_id=%s entity_id_present=%s",
                bot_id, True,
            )
            return None
        owner_user_id = str(bot.get("owner_id") or "")
        bot_type = str(bot.get("bot_type") or "")
        if not owner_user_id or not bot_type:
            return None
        # COSEC: resource and rollout identities do not prove the requester's subject.
        actor_user_id = auth_request.verified_actor_user_id
        if actor_user_id is None:
            identity = await self._auth_plugin.resolve_user_from_request(auth_request)
            actor_user_id = getattr(identity, "staffId", None)
        if not isinstance(actor_user_id, str) or not actor_user_id.strip():
            logger.warning(
                "token_plugin_actor_unavailable bot_id=%s reason=missing_authenticated_actor",
                bot_id,
            )
            return None
        if friend_user_id is not None and friend_user_id != actor_user_id:
            logger.warning(
                "token_plugin_actor_unavailable bot_id=%s reason=friend_subject_mismatch",
                bot_id,
            )
            return None
        logger.info(
            "token_plugin_context_built bot_id=%s entity_id=%s owner_user_id=%s "
            "actor_user_id=%s stage=%s",
            bot_id, entity_id, owner_user_id, actor_user_id, stage.value,
        )
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
            actor_user_id=actor_user_id,
            caller_runtime_target=runtime_target,
            device_uuid=device_uuid,
        )


__all__ = ["TokenExchangeOrchestrator"]
