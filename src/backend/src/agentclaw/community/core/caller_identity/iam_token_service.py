"""Core application service for optional Caller identity installation."""

from __future__ import annotations

import asyncio

from agentclaw.community.api.caller_credential import (
    CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE,
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CallerCredentialError,
    CallerRuntimeUpdater,
    CallerTokenProvider,
)
from agentclaw.community.api.caller_iam_token_service import CallerIamTokenResult
from agentclaw.community.api.caller_identity_service import CallerIdentityServiceProtocol
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityAmbiguousError,
    CallerIdentityPermissionError,
    CallerIdentityStage,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthRequestContext


logger = get_logger()


class CallerIamTokenService:
    """Keep Caller exchange policy out of the HTTP router."""

    def __init__(
        self,
        *,
        caller_identity: CallerIdentityServiceProtocol,
        auth_plugin: AuthPlugin,
        token_provider: CallerTokenProvider,
        runtime_updater: CallerRuntimeUpdater,
    ) -> None:
        self._caller_identity = caller_identity
        self._auth_plugin = auth_plugin
        self._token_provider = token_provider
        self._runtime_updater = runtime_updater

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
    ) -> CallerIamTokenResult:
        if not iam_token:
            return CallerIamTokenResult(
                iam_token="", error="IAM_TOKEN cookie not found", status_code=400
            )
        if is_test_exchange and not bot_id:
            return CallerIamTokenResult(
                iam_token="", error=CALLER_CREDENTIAL_REQUEST_INVALID, status_code=400
            )
        if not bot_id:
            return CallerIamTokenResult(iam_token=iam_token)
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
            return CallerIamTokenResult(iam_token="", error=exc.detail, status_code=409)
        except CallerIdentityPermissionError as exc:
            return CallerIamTokenResult(iam_token="", error=exc.detail, status_code=403)
        except Exception:
            logger.warning("caller_token_context_unavailable bot_id=%s stage=%s", bot_id, stage.value)
            return CallerIamTokenResult(iam_token=iam_token)
        if not context.should_exchange_caller_token:
            return CallerIamTokenResult(iam_token=iam_token)
        if not context.owner_id:
            return CallerIamTokenResult(
                iam_token="", error=CALLER_CREDENTIAL_REQUEST_INVALID, status_code=400
            )
        try:
            identity = await self._auth_plugin.resolve_user_from_request(auth_request)
            if is_test_exchange:
                self._caller_identity.authorize_iam_token_exchange(
                    caller_user_id=identity.staffId,
                    owner_user_id=context.owner_id,
                    is_test_exchange=True,
                )
            await asyncio.to_thread(
                self._caller_identity.exchange_caller_identity,
                iam_token=iam_token,
                caller_user_id=identity.staffId,
                bot_id=bot_id,
                owner_user_id=context.owner_id,
                token_provider=self._token_provider,
                runtime_updater=self._runtime_updater,
                stage=stage.value,
                publish_id=publish_id,
                entity_id=entity_id,
                binding_id=context.binding_id,
                is_test_exchange=is_test_exchange,
            )
        except CallerCredentialError as exc:
            status = 400 if exc.code == CALLER_CREDENTIAL_REQUEST_INVALID else 503 if exc.code == CALLER_CREDENTIAL_PROVIDER_UNAVAILABLE else 502
            return CallerIamTokenResult(iam_token="", error=exc.code, status_code=status)
        except CallerIdentityPermissionError as exc:
            return CallerIamTokenResult(iam_token="", error=exc.detail, status_code=403)
        except Exception:
            logger.warning("caller_runtime_update_failed bot_id=%s stage=%s", bot_id, stage.value)
            return CallerIamTokenResult(
                iam_token="", error=CALLER_OUTBOUND_UPDATE_FAILED, status_code=502
            )
        logger.info("caller_token_exchange_succeeded bot_id=%s stage=%s", bot_id, stage.value)
        return CallerIamTokenResult(iam_token=iam_token)
