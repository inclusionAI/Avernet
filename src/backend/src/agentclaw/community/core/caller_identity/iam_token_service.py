"""Core application service for optional Caller identity installation."""

from __future__ import annotations

import asyncio

from agentclaw.community.core.caller_identity.credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CallerCredentialError,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityAmbiguousError,
    CallerIamTokenOutcome,
    CallerIdentityPermissionError,
    CallerIdentityStage,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLockRepositoryProtocol,
)
from agentclaw.community.core.runtime_binding.errors import RuntimeBindingResolutionError
from agentclaw.community.core.runtime_binding.models import (
    ResolvedRuntimeBinding,
    RuntimeBindingRequest,
    RuntimeBindingSource,
    RuntimeBindingTarget,
)
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.caller_identity.protocols import (
    CallerIdentityTokenExchangeProtocol,
    CallerRuntimeUpdaterProtocol,
    CallerTokenProviderProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthRequestContext


logger = get_logger()


class CallerIamTokenService:
    """Keep Caller exchange policy out of the HTTP router."""

    def __init__(
        self,
        *,
        caller_identity: CallerIdentityTokenExchangeProtocol,
        auth_plugin: AuthPlugin,
        token_provider: CallerTokenProviderProtocol,
        runtime_updater: CallerRuntimeUpdaterProtocol,
        runtime_bindings: RuntimeBindingResolutionService,
        lock_repository: BotCollabLockRepositoryProtocol,
    ) -> None:
        self._caller_identity = caller_identity
        self._auth_plugin = auth_plugin
        self._token_provider = token_provider
        self._runtime_updater = runtime_updater
        self._runtime_bindings = runtime_bindings
        self._lock_repository = lock_repository

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
        if not iam_token:
            return CallerIamTokenOutcome(iam_token="", error="IAM_TOKEN cookie not found")
        if is_test_exchange and not bot_id:
            return CallerIamTokenOutcome(iam_token="", error=CALLER_CREDENTIAL_REQUEST_INVALID)
        if not bot_id:
            return CallerIamTokenOutcome(iam_token=iam_token)
        try:
            context = await asyncio.to_thread(
                self._caller_identity.get_iam_token_context,
                bot_id=bot_id,
                stage=stage,
                publish_id=publish_id,
                entity_id=entity_id,
                is_test_exchange=is_test_exchange,
            )
        except CallerIdentityAmbiguousError as exc:
            return CallerIamTokenOutcome(iam_token="", error=exc.detail)
        except CallerIdentityPermissionError as exc:
            return CallerIamTokenOutcome(iam_token="", error=exc.detail)
        except Exception:
            logger.warning("caller_token_context_unavailable bot_id=%s stage=%s", bot_id, stage.value)
            return CallerIamTokenOutcome(iam_token=iam_token)
        if not context.should_exchange_caller_token:
            return CallerIamTokenOutcome(iam_token=iam_token)
        if not context.owner_id:
            return CallerIamTokenOutcome(iam_token="", error=CALLER_CREDENTIAL_REQUEST_INVALID)
        try:
            identity = await self._auth_plugin.resolve_user_from_request(auth_request)
            if is_test_exchange:
                self._caller_identity.authorize_iam_token_exchange(
                    caller_user_id=identity.staffId,
                    owner_user_id=context.owner_id,
                    is_test_exchange=True,
                )
            if is_test_exchange:
                await self._exchange_target(
                    iam_token=iam_token,
                    caller_user_id=identity.staffId,
                    bot_id=bot_id,
                    owner_id=context.owner_id,
                    stage=stage,
                    publish_id=publish_id,
                    entity_id=entity_id,
                    binding_id=context.binding_id,
                    is_test_exchange=True,
                )
                logger.info("caller_token_exchange_succeeded bot_id=%s stage=%s", bot_id, stage.value)
                return CallerIamTokenOutcome(iam_token=iam_token)

            targets = await asyncio.to_thread(
                self._resolve_refresh_targets,
                bot_id=bot_id,
                owner_id=context.owner_id,
                actor_user_id=identity.staffId,
                stage=stage,
            )
            updated_targets = 0
            for target in targets:
                try:
                    await self._exchange_target(
                        iam_token=iam_token,
                        caller_user_id=identity.staffId,
                        bot_id=bot_id,
                        owner_id=context.owner_id,
                        stage=stage,
                        publish_id=publish_id,
                        entity_id=entity_id,
                        binding_id=target.binding_id,
                        is_test_exchange=False,
                    )
                    updated_targets += 1
                    logger.info(
                        "caller_target_refresh_succeeded bot_id=%s source=%s stage=%s",
                        bot_id,
                        target.source.value,
                        stage.value if target.source is not RuntimeBindingSource.CALLER_INSTANCE else "instance",
                    )
                except Exception as exc:
                    logger.warning(
                        "caller_target_refresh_failed bot_id=%s source=%s error_type=%s",
                        bot_id,
                        target.source.value,
                        type(exc).__name__,
                    )
            if updated_targets == 0:
                return CallerIamTokenOutcome(iam_token="", error=CALLER_OUTBOUND_UPDATE_FAILED)
        except CallerCredentialError as exc:
            return CallerIamTokenOutcome(iam_token="", error=exc.code)
        except CallerIdentityPermissionError as exc:
            return CallerIamTokenOutcome(iam_token="", error=exc.detail)
        except Exception as exc:
            logger.warning(
                "caller_runtime_update_failed bot_id=%s stage=%s error_type=%s",
                bot_id,
                stage.value,
                type(exc).__name__,
            )
            return CallerIamTokenOutcome(iam_token="", error=CALLER_OUTBOUND_UPDATE_FAILED)
        logger.info("caller_token_exchange_succeeded bot_id=%s stage=%s", bot_id, stage.value)
        return CallerIamTokenOutcome(iam_token=iam_token)

    async def _exchange_target(
        self,
        *,
        iam_token: str,
        caller_user_id: str,
        bot_id: str,
        owner_id: str,
        stage: CallerIdentityStage,
        publish_id: int | None,
        entity_id: str | None,
        binding_id: int | None,
        is_test_exchange: bool,
    ) -> None:
        await asyncio.to_thread(
            self._caller_identity.exchange_caller_identity,
            iam_token=iam_token,
            caller_user_id=caller_user_id,
            bot_id=bot_id,
            owner_user_id=owner_id,
            token_provider=self._token_provider,
            runtime_updater=self._runtime_updater,
            stage=stage.value,
            publish_id=publish_id,
            entity_id=entity_id,
            binding_id=binding_id,
            is_test_exchange=is_test_exchange,
        )

    def _resolve_refresh_targets(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_user_id: str,
        stage: CallerIdentityStage,
    ) -> list[ResolvedRuntimeBinding]:
        targets: list[ResolvedRuntimeBinding] = []
        lock = self._lock_repository.get_by_key(f"{bot_id}:{owner_id}")
        service_allowed = lock is None and actor_user_id == owner_id
        service_allowed = service_allowed or (
            lock is not None and lock.holder_user_id == actor_user_id
        )
        if service_allowed:
            try:
                targets.append(
                    self._runtime_bindings.resolve(
                        RuntimeBindingRequest(
                            bot_id=bot_id,
                            owner_id=owner_id,
                            actor_user_id=actor_user_id,
                            stage=stage.value,
                            target=RuntimeBindingTarget.CALLER_SERVICE,
                        )
                    )
                )
            except RuntimeBindingResolutionError:
                logger.info(
                    "caller_target_refresh_unavailable bot_id=%s source=caller_service stage=%s",
                    bot_id,
                    stage.value,
                )
        try:
            targets.append(
                self._runtime_bindings.resolve(
                    RuntimeBindingRequest(
                        bot_id=bot_id,
                        owner_id=owner_id,
                        actor_user_id=actor_user_id,
                        stage=stage.value,
                        target=RuntimeBindingTarget.CALLER_INSTANCE,
                    )
                )
            )
        except RuntimeBindingResolutionError:
            logger.info(
                "caller_target_refresh_unavailable bot_id=%s source=caller_instance",
                bot_id,
            )
        return self._dedupe_targets(targets)

    @staticmethod
    def _dedupe_targets(
        targets: list[ResolvedRuntimeBinding],
    ) -> list[ResolvedRuntimeBinding]:
        seen: set[int] = set()
        unique: list[ResolvedRuntimeBinding] = []
        for target in targets:
            if target.binding_id in seen:
                continue
            seen.add(target.binding_id)
            unique.append(target)
        return unique
