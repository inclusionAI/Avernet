"""IAM friend mode validates friendship, stage and session before credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.token import token_router
from agentclaw.community.api.caller_iam_token_service import CallerIamTokenServiceProtocol
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.bot_management.addressing import (
    BotAddressResolverProtocol,
    ResolvedBotContext,
)
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.caller_identity.contracts import CallerIamTokenOutcome, CallerIdentityStage
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.expert_chat.errors import BotNotFoundError
from tests.community.adapters.http.openapi_v1.conftest import user_scoped_client, mount_public_error_handlers
from tests.community.adapters.http.openapi_v1.engine_runtime.conftest import FakeExpertChat, FakeFriendships, FakeRelay


@pytest.fixture
def friend_iam():
    service = SimpleNamespace(
        get_iam_token=AsyncMock(return_value=CallerIamTokenOutcome("iam")),
    )
    expert, friendships = FakeExpertChat(), FakeFriendships()
    expert.resolve_chat_stage = Mock(return_value="online")
    friendships.allowed = True
    bot_addresses = Mock(spec=BotAddressResolverProtocol)
    bot_addresses.resolve.return_value = ResolvedBotContext(
        bot_pk=1,
        bot_id="b1",
        entity_id="entity-owner",
        current_owner_id="current-owner",
        bot={
            "id": 1,
            "bot_id": "b1",
            "entity_id": "entity-owner",
            "owner_id": "current-owner",
        },
    )

    class Ports(Module):
        def configure(self, binder):
            binder.bind(CallerIamTokenServiceProtocol, to=service)
            binder.bind(ExpertChatServiceProtocol, to=expert)
            binder.bind(HumanBotFriendshipServiceProtocol, to=friendships)
            binder.bind(EngineRuntimeRelayProtocol, to=FakeRelay())
            binder.bind(
                BotAddressResolverProtocol,
                to=InstanceProvider(bot_addresses),
            )

    app = FastAPI()
    app.include_router(token_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "friend"}
    attach_injector(app, Injector([Ports()]))
    mount_public_error_handlers(app)
    return SimpleNamespace(
        client=user_scoped_client(app, "friend"), service=service,
        expert=expert, friendships=friendships, bot_addresses=bot_addresses,
    )


def request(context, **overrides):
    params = {"stage": "online", "owner_id": "owner", "f_user_id": "friend", "session_id": "owned-session"}
    params.update(overrides)
    return context.client.post("/openapi/v1/bots/b1/iam-token", params=params)


@pytest.mark.parametrize("stage", ["online", "draft"])
def test_friend_iam_uses_effective_online_stage_and_original_owned_session(friend_iam, stage):
    response = request(
        friend_iam,
        stage=stage,
        entity_id="unrelated-entity",
        session_key="untrusted-session",
    )
    assert response.status_code == 200, response.json()
    args = friend_iam.service.get_iam_token.await_args.kwargs
    assert args["stage"] is CallerIdentityStage.ONLINE
    assert args["friend_user_id"] == "friend"
    assert args["entity_id"] == "entity-owner"
    assert args["session_key"] == "owned-session"
    assert friend_iam.expert.calls[-1][0] == "get_owned_chat_session"
    friend_iam.bot_addresses.resolve.assert_called_once_with(
        bot_id="b1",
        legacy_owner_id="owner",
    )


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"session_key": "draft-session"}, "draft-session"),
        ({"sessionKey": "online-session"}, "online-session"),
        ({"session_id": "original-openapi-session"}, "original-openapi-session"),
        (
            {
                "session_key": "canonical-session",
                "sessionKey": "legacy-session",
                "session_id": "original-openapi-session",
            },
            "canonical-session",
        ),
    ],
)
def test_regular_iam_forwards_compatible_session_query_names(friend_iam, params, expected):
    response = friend_iam.client.post(
        "/openapi/v1/bots/b1/iam-token",
        params={"stage": "draft", **params},
    )

    assert response.status_code == 200, response.json()
    assert friend_iam.service.get_iam_token.await_args.kwargs["session_key"] == expected


@pytest.mark.parametrize(
    "failure",
    [
        "not_friend",
        "other_user",
        "other_session",
        "stage_not_live",
        "missing_bot",
        "no_session",
    ],
)
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
    elif failure == "missing_bot":
        friend_iam.bot_addresses.resolve.side_effect = BotAddressNotFoundError("missing")
    else:
        friend_iam.expert.resolve_chat_stage.side_effect = EngineStageNotLiveError("not live")
        status = 409
    response = request(friend_iam, **overrides)
    assert response.status_code == status, response.json()
    friend_iam.service.get_iam_token.assert_not_awaited()


@pytest.fixture
def jit_iam(friend_iam):
    from agentclaw.community.core.token_exchange.orchestrator import TokenExchangeOrchestrator

    refresh = AsyncMock(return_value=CallerIamTokenOutcome("test-token"))
    caller = SimpleNamespace(get_iam_token=refresh)
    pipeline = SimpleNamespace(run=AsyncMock(return_value=[]))
    auth = SimpleNamespace(resolve_user_from_request=AsyncMock(side_effect=AssertionError("no browser identity")))
    bots = Mock()
    row = {"owner_id": "owner", "entity_id": "entity-owner", "bot_type": "service", "env": "test"}
    bots.get_by_id_and_entity.return_value = row
    orchestrator = TokenExchangeOrchestrator(caller_service=caller, token_pipeline=pipeline,
                                              bot_repository=bots, auth_plugin=auth)
    friend_iam.service.get_iam_token.side_effect = orchestrator.get_iam_token
    return SimpleNamespace(http=friend_iam, caller=caller, pipeline=pipeline, bots=bots, auth=auth)


@pytest.mark.parametrize("target", ["caller_instance", "service"])
def test_friend_post_forwards_verbatim_jit_selectors_without_changing_caller(jit_iam, target):
    response = request(jit_iam.http, caller_runtime_target=target, device_uuid=" 副本/A:2 ", entity_id="unrelated")
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["iam_token"] == "test-token"
    jit_iam.caller.get_iam_token.assert_awaited_once()
    args = jit_iam.caller.get_iam_token.await_args.kwargs
    assert args["entity_id"] == "entity-owner"
    assert args["friend_user_id"] == "friend"
    assert args["session_key"] == "owned-session"
    assert args["stage"] is CallerIdentityStage.ONLINE
    assert {"device_uuid", "caller_runtime_target"}.isdisjoint(args)
    context = jit_iam.pipeline.run.await_args.args[0]
    assert (context.friend_user_id, context.device_uuid) == ("friend", " 副本/A:2 ")
    assert not hasattr(context, "caller_runtime_target")
    assert (context.actor_user_id, context.user_list_entity_id) == ("friend", "entity-owner")
    jit_iam.auth.resolve_user_from_request.assert_not_awaited()


@pytest.mark.parametrize("selector", [{"device_uuid": ""}])
def test_post_invalid_selector_only_skips_jit(jit_iam, selector):
    response = request(jit_iam.http, **selector)
    assert response.status_code == 200
    assert response.json()["data"]["iam_token"] == "test-token"
    jit_iam.caller.get_iam_token.assert_awaited_once()
    jit_iam.pipeline.run.assert_not_awaited()


@pytest.mark.parametrize("scoped", [True, False])
def test_post_uses_verified_principal_only_for_an_entity_scoped_jit_request(jit_iam, scoped, monkeypatch):
    from agentclaw.community.adapters.http.openapi_v1 import dependencies
    from agentclaw.community.core.gateway_principal.verifier import PrincipalVerifierConfig
    from tests.community.adapters.http.openapi_v1.test_principal_seam import KEY, mint
    from agentclaw.community.adapters.http.openapi_v1.principal import resolve_user_id_bypass

    # Even a permitted delegated user_id is not the JIT injection actor.
    client = jit_iam.http.client
    del client.app.dependency_overrides[require_principal]
    monkeypatch.setattr(dependencies, "get_principal_verifier_config",
                        lambda: PrincipalVerifierConfig(KEY, "backend", "gateway"))
    client.app.dependency_overrides[resolve_user_id_bypass] = lambda: frozenset({"other-user"})
    scope = {"entity_id": "entity-owner"} if scoped else {}
    response = client.post("/openapi/v1/bots/b1/iam-token", params={
        "user_id": "other-user", "caller_runtime_target": "caller_instance",
        "actor_user_id": "forged", "verified_actor_user_id": "forged", **scope,
    }, headers={dependencies.PRINCIPAL_HEADER: mint(user_id="friend", tenant=None)})
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["iam_token"] == "test-token"
    jit_iam.caller.get_iam_token.assert_awaited_once()
    assert jit_iam.caller.get_iam_token.await_args.kwargs["entity_id"] == scope.get("entity_id")
    jit_iam.bots.get_unique_by_id.assert_not_called()
    if scoped:
        context = jit_iam.pipeline.run.await_args.args[0]
        assert context.actor_user_id == "friend"
        assert context.user_list_entity_id == "entity-owner"
    else:
        jit_iam.pipeline.run.assert_not_awaited()
    jit_iam.auth.resolve_user_from_request.assert_not_awaited()


def test_empty_friend_id_is_regular_iam_and_preserves_requested_stage(friend_iam):
    response = friend_iam.client.post("/openapi/v1/bots/b1/iam-token", params={
        "f_user_id": "", "stage": "verify", "entity_id": "entity-owner",
    })
    assert response.status_code == 200, response.json()
    args = friend_iam.service.get_iam_token.await_args.kwargs
    assert "friend_user_id" not in args
    assert args["stage"] is CallerIdentityStage.VERIFY
    friend_iam.expert.resolve_chat_stage.assert_not_called()
    friend_iam.bot_addresses.resolve.assert_not_called()


def test_iam_schema_removes_runtime_selector_and_preserves_device_and_friend_fields(friend_iam):
    schema = friend_iam.client.app.openapi()
    params = schema["paths"]["/openapi/v1/bots/{bot_id}/iam-token"]["post"]["parameters"]
    names = {param["name"] for param in params}
    assert "caller_runtime_target" not in names
    assert {"f_user_id", "device_uuid", "stage"} <= names
