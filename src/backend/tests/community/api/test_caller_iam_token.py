from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.adapters.http.token_exchange.router import get_iam_token
from agentclaw.community.api.caller_credential import (
    CallerRuntimeUpdater,
    CallerTokenProvider,
)
from agentclaw.community.api.caller_identity_service import (
    CallerIdentityServiceProtocol,
    CallerIdentityStage,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerIamTokenContext,
    CallerIdentityAmbiguousError,
    McpCallType,
)
from agentclaw.community.core.caller_identity.credential import CallerToken
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin


class _Request:
    def __init__(self, dependencies: dict[object, object]) -> None:
        self.cookies = {"IAM_TOKEN": "iam-token"}
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.base_url = "http://test/"
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                injector=SimpleNamespace(
                    get=lambda dependency: dependencies[dependency]
                )
            )
        )


@pytest.mark.asyncio
async def test_iam_route_delegates_caller_exchange_without_returning_token() -> None:
    identity = MagicMock()
    identity.get_iam_token_context.return_value = CallerIamTokenContext(
        bot_id="bot-1",
        owner_id="owner-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        bot_call_type=McpCallType.CALLER,
        should_exchange_caller_token=True,
        binding_id=9,
    )
    auth = MagicMock()
    auth.resolve_user_from_request = AsyncMock(
        return_value=SimpleNamespace(
            id="id-1",
            staffId="caller-1",
            operatorName="Caller",
            nickName="Caller",
            tenantId="tenant-1",
        )
    )
    passport = MagicMock()
    passport.query_token.return_value = "agent-pass-token"
    passport.query_agent_passport.return_value = {"agent_code": "agent-code"}
    token_provider = MagicMock()
    token_provider.exchange.return_value = CallerToken(
        access_token="caller-token",
        subject_user_id="caller-1",
        expires_at=datetime.now(),
        fingerprint="ignored",
    )
    runtime_updater = MagicMock()
    request = _Request(
        {
            CallerIdentityServiceProtocol: identity,
            AuthPlugin: auth,
            PassportPlugin: passport,
            CallerTokenProvider: token_provider,
            CallerRuntimeUpdater: runtime_updater,
        }
    )

    response = await get_iam_token(
        request,
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="entity-1",
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"success": True, "iam_token": "iam-token"}
    identity.get_iam_token_context.assert_called_once_with(
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="entity-1",
    )
    identity.exchange_caller_identity.assert_called_once()
    exchange_kwargs = identity.exchange_caller_identity.call_args.kwargs
    assert exchange_kwargs["caller_user_id"] == "caller-1"
    assert exchange_kwargs["token_provider"] is token_provider
    assert exchange_kwargs["runtime_updater"] is runtime_updater
    assert exchange_kwargs["entity_id"] == "entity-1"
    assert exchange_kwargs["binding_id"] == 9


@pytest.mark.asyncio
async def test_iam_route_rejects_ambiguous_bot_without_entity() -> None:
    identity = MagicMock()
    identity.get_iam_token_context.side_effect = CallerIdentityAmbiguousError()
    request = _Request({CallerIdentityServiceProtocol: identity})

    response = await get_iam_token(
        request,
        bot_id="default",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
    )

    assert response.status_code == 409
    assert json.loads(response.body) == {
        "success": False,
        "error": "CALLER_IDENTITY_AMBIGUOUS",
    }
    identity.exchange_caller_identity.assert_not_called()
