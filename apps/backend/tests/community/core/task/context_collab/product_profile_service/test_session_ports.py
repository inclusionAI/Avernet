from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    SessionRoute,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.session_messages import (
    BcsStageSessionMessages,
    RoutedStageSessionMessages,
)

pytestmark = pytest.mark.asyncio


async def test_bcs_messages_use_persisted_participant_identity_and_preserve_messages():
    tokens = SimpleNamespace()
    tokens.get_token = Mock(return_value="test-token")
    messages = [
        {"id": "message-1", "role": "tool", "content": "tool detail", "time": 42}
    ]
    bcs = SimpleNamespace(get_session_messages=AsyncMock(return_value=messages))
    adapter = BcsStageSessionMessages(bcs, tokens)
    route = SessionRoute(
        "bcs", "real-bcs-session", "actual-bot", "actual-owner", "production", "run-1"
    )
    assert await adapter.read(route, 7) == messages
    tokens.get_token.assert_called_once_with("actual-bot")
    bcs.get_session_messages.assert_awaited_once_with(
        "real-bcs-session", limit=7, caller_bearer="test-token"
    )


async def test_bcs_missing_identity_does_not_make_anonymous_read():
    tokens = SimpleNamespace()
    tokens.get_token = Mock(return_value=None)
    bcs = SimpleNamespace(get_session_messages=AsyncMock())
    route = SessionRoute("bcs", "real", "bot", "owner", "production", "run")
    with pytest.raises(Forbidden):
        await BcsStageSessionMessages(bcs, tokens).read(route, 50)
    bcs.get_session_messages.assert_not_awaited()


async def test_unknown_session_provider_never_uses_fallback_messages():
    fallback = SimpleNamespace(read=AsyncMock(return_value=[]))
    route = SessionRoute("unknown", "real", "bot", "owner", "production", "run")
    with pytest.raises(Invalid):
        await RoutedStageSessionMessages({"openapi_bot": fallback}).read(route, 10)
    fallback.read.assert_not_awaited()
