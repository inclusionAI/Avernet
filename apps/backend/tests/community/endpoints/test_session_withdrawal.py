"""Both real HTTP delete adapters reach the transactional service/outbox."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.models import BotFacts
from agentclaw.community.core.repository.protocols.platform import (
    SessionResourceRepositoryProtocol,
    ResourceWithdrawalRepositoryProtocol,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceStatus,
    hash_identifier,
)
from tests.community.core.session_resources.test_repository import _record
from tests.community.endpoints.test_openapi_session_files import (
    _seed_verifier,
    _principal,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import bind_overrides

OWNER, BOT, SESSION = "session-file-owner", "session-file-bot", "withdrawal-session"


def seed(world, scope, status, resource_owner=OWNER):
    make_staff_user(world, user_id=OWNER)
    make_bot(world, bot_id=BOT, owner_id=OWNER)
    _seed_verifier(world)

    async def resolve(_self, *_args, **_kwargs):
        return BotFacts(
            bot_id=BOT, bot_type="personal", active_engine="openclaw", owner_id=OWNER
        )

    bind_overrides(world, EngineRuntimeRelayProtocol, {"resolve_bot_off_loop": resolve})
    repo = world.get(SessionResourceRepositoryProtocol)
    repo.create(
        replace(
            _record(),
            owner_id=resource_owner,
            bot_id=BOT,
            session_key_hash=hash_identifier(SESSION),
            scope_type=scope,
            status=status,
        )
    )
    return world.get(ResourceWithdrawalRepositoryProtocol)


def request(client, formal):
    if formal:
        return client.delete(
            f"/openapi/v1/bots/{BOT}/sessions/{SESSION}/files/sr_001",
            params={"user_id": OWNER},
            headers={PRINCIPAL_HEADER: _principal()},
        )
    return client.delete(
        "/api/session-resources/sr_001",
        params={"bot_id": BOT, "session_key": SESSION},
        headers={"x-user-id": OWNER},
    )


@pytest.mark.parametrize(
    "formal,scope",
    [
        (True, "openapi_session"),
        (False, "personal_bot_chat"),
        (False, "friend_bot_chat"),
    ],
)
@pytest.mark.parametrize(
    "status",
    [
        SessionResourceStatus.READY,
        SessionResourceStatus.UPLOAD_URL_ISSUED,
        SessionResourceStatus.DEVICE_SYNCING,
    ],
)
def test_real_delete_is_durable_for_both_adapters(
    world, app_with_testing_modules, formal, scope, status
):
    outbox = seed(world, scope, status)
    client = TestClient(app_with_testing_modules)
    assert request(client, formal).status_code == 200
    fact = outbox.get("tc.resource.withdrawn:sr_001")
    assert fact.status == "pending"
    assert fact.attempts == 0  # no downstream call on request path
    assert request(client, formal).status_code == 200
    assert outbox.get(fact.event_id) == fact


@pytest.mark.parametrize("formal", [False, True])
def test_wrong_resource_owner_cannot_emit_event(
    world, app_with_testing_modules, formal
):
    outbox = seed(
        world,
        "openapi_session",
        SessionResourceStatus.READY,
        resource_owner="someone-else",
    )
    assert request(TestClient(app_with_testing_modules), formal).status_code == 404
    assert outbox.get("tc.resource.withdrawn:sr_001") is None


@pytest.mark.parametrize("formal", [False, True])
def test_outbox_failure_returns_error_and_rolls_back(
    world, app_with_testing_modules, formal
):
    from sqlalchemy import event
    from agentclaw.community.core.session_resources.withdrawal_models import (
        ResourceWithdrawalModel,
    )

    outbox = seed(world, "openapi_session", SessionResourceStatus.READY)

    def fail(*_):
        raise RuntimeError("simulated write failure")

    event.listen(ResourceWithdrawalModel, "before_insert", fail)
    try:
        response = request(
            TestClient(app_with_testing_modules, raise_server_exceptions=False), formal
        )
    finally:
        event.remove(ResourceWithdrawalModel, "before_insert", fail)
    assert response.status_code == 500
    assert outbox.get("tc.resource.withdrawn:sr_001") is None
    assert (
        world.get(SessionResourceRepositoryProtocol).get_by_resource_id("sr_001").status
        == SessionResourceStatus.READY
    )
