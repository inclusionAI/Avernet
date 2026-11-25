"""Friend IAM updates are confined to the verified subject's existing instance."""

import pytest

from agentclaw.community.core.caller_identity.contracts import CallerIdentityStage
from agentclaw.community.core.runtime_binding.models import (
    ResolvedRuntimeBinding, RuntimeBindingSource, RuntimeBindingTarget,
)
from tests.community.core.caller_identity.test_iam_token_service import _service, _request


@pytest.mark.asyncio
async def test_friend_iam_targets_only_the_caller_instance_and_keeps_session_key():
    svc, identity, _, _, _, bindings, locks = _service()
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(81, RuntimeBindingSource.CALLER_INSTANCE)
    result = await svc.get_iam_token(
        iam_token="iam", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE, publish_id=None, entity_id="owner-1",
        is_test_exchange=False, session_key="friend-session", friend_user_id="caller-1",
    )
    assert result.error is None
    request = bindings.resolve.call_args.args[0]
    assert request.stage == "online"
    assert request.actor_user_id == "caller-1"
    assert request.target is RuntimeBindingTarget.CALLER_INSTANCE
    locks.get_by_key.assert_not_called()
    assert identity.exchange_caller_identity.call_count == 1
    assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 81
    assert identity.exchange_caller_identity.call_args.kwargs["session_key"] == "friend-session"


@pytest.mark.asyncio
async def test_owner_cannot_install_own_iam_in_another_friends_instance():
    svc, identity, _, _, _, bindings, _ = _service()
    result = await svc.get_iam_token(
        iam_token="iam", auth_request=_request(), bot_id="bot-1",
        stage=CallerIdentityStage.ONLINE, publish_id=None, entity_id="owner-1",
        is_test_exchange=False, session_key="other-session", friend_user_id="other-friend",
    )
    assert result.error == "CALLER_IDENTITY_FORBIDDEN"
    bindings.resolve.assert_not_called()
    identity.exchange_caller_identity.assert_not_called()
