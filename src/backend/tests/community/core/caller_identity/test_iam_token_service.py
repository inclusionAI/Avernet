from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.caller_identity.contracts import (
    CallerIamTokenContext,
    CallerIdentityStage,
    McpCallType,
)
from agentclaw.community.core.caller_identity.iam_token_service import (
    CallerIamTokenService,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext


@pytest.mark.asyncio
async def test_service_installs_caller_token_without_returning_it() -> None:
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
        return_value=SimpleNamespace(staffId="caller-1")
    )
    service = CallerIamTokenService(
        caller_identity=identity,
        auth_plugin=auth,
        token_provider=MagicMock(),
        runtime_updater=MagicMock(),
    )

    result = await service.get_iam_token(
        iam_token="iam-token",
        auth_request=AuthRequestContext({}, {}, {}, "http://test/"),
        bot_id="bot-1",
        stage=CallerIdentityStage.DRAFT,
        publish_id=None,
        entity_id="entity-1",
        is_test_exchange=False,
    )

    assert result.error is None
    assert result.iam_token == "iam-token"
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 9
