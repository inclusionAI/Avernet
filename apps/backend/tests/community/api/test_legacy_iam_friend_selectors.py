"""Legacy selectors reuse Caller and JIT validation without extra preflight I/O."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient

from agentclaw.community.core.caller_identity.contracts import CallerIamTokenOutcome, CallerIdentityStage
from agentclaw.community.core.runtime_binding.models import ResolvedRuntimeBinding, RuntimeBindingSource
from agentclaw.community.core.token_exchange.orchestrator import TokenExchangeOrchestrator
from tests.community.api.test_dingtalk_iam_token import _app
from tests.community.core.caller_identity.test_iam_token_service import _service


@pytest.fixture
def world():
    auth = SimpleNamespace(resolve_user_from_request=AsyncMock(return_value=SimpleNamespace(staffId="friend")))
    bots = Mock()
    bots.get_by_id_and_entity.return_value = {"owner_id": "owner", "bot_type": "service", "env": "pre"}
    caller = SimpleNamespace(get_iam_token=AsyncMock(return_value=CallerIamTokenOutcome("caller-result")))
    pipeline = SimpleNamespace(run=AsyncMock(return_value=[]))
    service = TokenExchangeOrchestrator(caller_service=caller, token_pipeline=pipeline,
                                       bot_repository=bots, auth_plugin=auth)
    return SimpleNamespace(**locals())


async def request(world, **overrides):
    params = {"bot_id": "bot", "entity_id": "resource", "stage": "draft", "f_user_id": "friend"}
    params.update(overrides)
    params = {key: value for key, value in params.items() if value is not None}
    async with AsyncClient(transport=ASGITransport(app=_app(world.service)), base_url="http://test") as client:
        return await client.get("/api/v1/token/iam", params=params, headers={"Cookie": "IAM_TOKEN=iam-secret"})


@pytest.mark.asyncio
@pytest.mark.parametrize("device", [None, "", "opaque/副本?2"])
async def test_friend_selectors_preserve_stage_and_require_no_new_preflight(world, device, caplog):
    caplog.set_level("INFO")
    response = await request(world, device_uuid=device)
    assert response.status_code == 200
    assert response.json() == {"success": True, "iam_token": "caller-result"}
    call = world.caller.get_iam_token.await_args.kwargs
    assert call["friend_user_id"] == "friend"
    assert call["stage"] is CallerIdentityStage.DRAFT
    assert "device_uuid" not in call
    # Only JIT's existing Bot/actor reads are needed; no new friend/session services.
    world.auth.resolve_user_from_request.assert_awaited_once()
    world.bots.get_by_id_and_entity.assert_called_once_with("bot", "resource")
    context = world.pipeline.run.await_args.args[0]
    assert context.friend_user_id == "friend"
    assert context.device_uuid == (device or None)
    assert "caller_iam_request_received" in caplog.text
    assert "caller_iam_response_succeeded" in caplog.text
    assert "iam-secret" not in caplog.text
    assert "caller-result" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("friend", [None, ""])
async def test_no_friend_keeps_original_caller_arguments(world, friend):
    response = await request(world, f_user_id=friend, device_uuid="opaque", sessionKey="old-key")
    assert response.status_code == 200
    call = world.caller.get_iam_token.await_args.kwargs
    assert set(call) == {"iam_token", "auth_request", "bot_id", "stage", "publish_id",
                         "entity_id", "is_test_exchange", "session_key"}
    assert call["session_key"] == "old-key"
    assert world.pipeline.run.await_args.args[0].device_uuid == "opaque"


@pytest.mark.asyncio
async def test_dingtalk_ignores_friend_selector(world):
    response = await request(world, token_type="dingtalk", device_uuid="opaque")
    assert response.status_code == 200
    call = world.caller.get_iam_token.await_args.kwargs
    assert call["token_type"] == "dingtalk"
    assert "friend_user_id" not in call
    assert "device_uuid" not in call
    world.pipeline.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_friend_preserves_caller_exception_and_session_alias(world):
    error = RuntimeError("original Caller failure")
    world.caller.get_iam_token.side_effect = error
    with pytest.raises(RuntimeError) as raised:
        await request(world, sessionKey="owned")
    assert raised.value is error
    assert world.caller.get_iam_token.await_args.kwargs["session_key"] == "owned"
    world.pipeline.run.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("friend", ["caller-1", "other-friend"])
async def test_real_caller_enforces_authenticated_subject_without_preflight(world, friend):
    caller, identity, auth, _, _, bindings, _ = _service()
    bindings.resolve.side_effect = None
    bindings.resolve.return_value = ResolvedRuntimeBinding(81, RuntimeBindingSource.CALLER_INSTANCE)
    world.service = TokenExchangeOrchestrator(caller_service=caller, token_pipeline=world.pipeline,
                                              bot_repository=world.bots, auth_plugin=auth)
    response = await request(world, f_user_id=friend, session_key="owned")
    if friend == "caller-1":
        assert response.status_code == 200
        assert bindings.resolve.call_args.args[0].actor_user_id == friend
        assert identity.exchange_caller_identity.call_args.kwargs["binding_id"] == 81
        assert identity.exchange_caller_identity.call_args.kwargs["session_key"] == "owned"
        world.pipeline.run.assert_awaited_once()
    else:
        assert response.status_code == 403
        bindings.resolve.assert_not_called()
        identity.exchange_caller_identity.assert_not_called()
        world.pipeline.run.assert_not_awaited()
