"""Actual relay + Bot address resolution behind the public sessions boundary."""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.router import (
    router,
)
from agentclaw.community.api.collaborator_lock_service import (
    CollaboratorLockServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    SeamLocks,
    mount_public_error_handlers,
    user_scoped_client,
)
from tests.community.adapters.http.openapi_v1.engine_runtime.conftest import (
    bind_session_key_codecs,
)
from tests.community.core.engine_runtime.test_handoff_addressing import (
    BOT,
    ENTITY,
    OWNER,
    make_runtime,
)


@pytest.fixture
def handoff_runtime():
    return make_runtime()


@pytest.fixture
def handoff_client(handoff_runtime, friendships, expert):
    def build(actor=OWNER):
        class Bindings(Module):
            def configure(self, binder):
                binder.bind(EngineRuntimeRelayProtocol, to=handoff_runtime.relay)
                binder.bind(BotRepository, to=handoff_runtime.rows)
                binder.bind(
                    CollaboratorServiceProtocol, to=handoff_runtime.collaborators
                )
                binder.bind(CollaboratorLockServiceProtocol, to=SeamLocks())
                binder.bind(BotCollabLogRepositoryProtocol, to=InstanceProvider(Mock()))
                binder.bind(HumanBotFriendshipServiceProtocol, to=friendships)
                binder.bind(ExpertChatServiceProtocol, to=expert)
                bind_session_key_codecs(binder)

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_principal] = lambda: {"user_id": actor}
        mount_public_error_handlers(app)
        attach_injector(app, Injector([Bindings()]))
        return user_scoped_client(app, actor)

    return build


@pytest.mark.parametrize(
    ("parameter", "locator"),
    [("owner_id", ENTITY), ("owner_id", OWNER), ("entity_id", ENTITY)],
)
def test_sessions_list_survives_handoff_and_keeps_owner_filter(
    handoff_client, handoff_runtime, parameter, locator
):
    client = handoff_client()
    response = client.get(
        f"/openapi/v1/bots/{BOT}/sessions",
        params={parameter: locator, "stage": "online"},
    )
    assert response.status_code == 200, response.json()
    assert handoff_runtime.resolver.binding_calls == [(930, OWNER, BOT)]
    assert handoff_runtime.transport.invocations[-1]["params"]["user_id"] == OWNER
    assert (
        handoff_runtime.transport.invocations[-1]["params"]["source"]
        == "all_but_others"
    )


@pytest.mark.parametrize("actor", [ENTITY, "stranger"])
def test_old_owner_and_stranger_are_masked_before_device_access(
    handoff_client, handoff_runtime, actor
):
    response = handoff_client(actor).get(
        f"/openapi/v1/bots/{BOT}/sessions",
        params={"entity_id": ENTITY, "stage": "online"},
    )
    assert response.status_code == 404
    assert (
        not handoff_runtime.transport.calls
        and not handoff_runtime.resolver.binding_calls
    )


@pytest.mark.parametrize("parameter", ["entity_id", "owner_id"])
def test_create_session_uses_stable_address_and_authenticated_actor(
    handoff_client, handoff_runtime, parameter
):
    handoff_runtime.transport._result = {
        "success": True,
        "data": {
            "id": "session-one", "title": "handoff",
            "ext_info": {"openclaw_creation": {
                "session_id": f"agent:main:session:one:user:{OWNER}",
                "model": "provider/model",
            }},
        },
    }
    response = handoff_client().post(
        f"/openapi/v1/bots/{BOT}/sessions",
        params={parameter: ENTITY, "stage": "online"},
        json={"title": "handoff"},
    )
    assert response.status_code == 201, response.json()
    assert handoff_runtime.resolver.binding_calls
    assert all(
        call == (930, OWNER, BOT) for call in handoff_runtime.resolver.binding_calls
    )
