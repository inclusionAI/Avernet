"""IAM friend mode validates friendship, stage and session before credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.token import token_router
from agentclaw.community.api.caller_iam_token_service import CallerIamTokenServiceProtocol
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.caller_identity.contracts import CallerIamTokenOutcome, CallerIdentityStage
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.expert_chat.errors import BotNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client, mount_public_error_handlers
from tests.community.adapters.http.openapi_v1.engine_runtime.conftest import FakeExpertChat, FakeFriendships, FakeRelay


@pytest.fixture
def friend_iam():
    service = SimpleNamespace(get_iam_token=AsyncMock(return_value=CallerIamTokenOutcome("iam")))
    expert, friendships = FakeExpertChat(), FakeFriendships()
    expert.resolve_chat_stage = Mock(return_value="online")
    friendships.allowed = True

    class Ports(Module):
        def configure(self, binder):
            binder.bind(CallerIamTokenServiceProtocol, to=service)
            binder.bind(ExpertChatServiceProtocol, to=expert)
            binder.bind(HumanBotFriendshipServiceProtocol, to=friendships)
            binder.bind(EngineRuntimeRelayProtocol, to=FakeRelay())
            binder.bind(BotRepository, to=SimpleNamespace(
                get_by_id_and_owner=Mock(return_value={"entity_id": "entity-owner"}),
            ))

    app = FastAPI()
    app.include_router(token_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "friend"}
    attach_injector(app, Injector([Ports()]))
    mount_public_error_handlers(app)
    return SimpleNamespace(
        client=user_scoped_client(app, "friend"), service=service,
        expert=expert, friendships=friendships,
    )


def request(context, **overrides):
    params = {"stage": "online", "owner_id": "owner", "f_user_id": "friend", "session_id": "owned-session"}
    params.update(overrides)
    return context.client.post("/openapi/v1/bots/b1/iam-token", params=params)


@pytest.mark.parametrize("stage", ["online", "draft"])
def test_friend_iam_uses_effective_online_stage_and_original_owned_session(friend_iam, stage):
    response = request(friend_iam, stage=stage, entity_id="unrelated-entity")
    assert response.status_code == 200, response.json()
    args = friend_iam.service.get_iam_token.await_args.kwargs
    assert args["stage"] is CallerIdentityStage.ONLINE
    assert args["friend_user_id"] == "friend"
    assert args["entity_id"] == "entity-owner"
    assert args["session_key"] == "owned-session"
    assert friend_iam.expert.calls[-1][0] == "get_owned_chat_session"


@pytest.mark.parametrize("failure", ["not_friend", "other_user", "other_session", "stage_not_live", "no_session"])
def test_friend_iam_failure_never_installs_credentials(friend_iam, failure):
    overrides = {}
    status = 404
    if failure == "not_friend":
        friend_iam.friendships.allowed = False
    elif failure == "other_user":
        overrides["f_user_id"] = "other"
    elif failure == "no_session":
        overrides["session_id"] = ""
        status = 422
    elif failure == "other_session":
        friend_iam.expert.get_owned_chat_session = AsyncMock(side_effect=BotNotFoundError("foreign"))
    else:
        friend_iam.expert.resolve_chat_stage.side_effect = EngineStageNotLiveError("not live")
        status = 409
    response = request(friend_iam, **overrides)
    assert response.status_code == status, response.json()
    friend_iam.service.get_iam_token.assert_not_awaited()
