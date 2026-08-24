from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.service_publications import (
    edit_lock_router,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.service_publications.router import (
    acquire_edit_lock,
    advance_lifecycle,
    cancel_staging,
    delete_initial_draft,
    get_approval_config,
    get_edit_lock,
    get_lifecycle,
    offline_lifecycle,
    release_edit_lock,
    restart_lifecycle,
    retry_lifecycle,
    steal_edit_lock,
    update_approval_config,
    upgrade_to_service,
)
from agentclaw.community.adapters.http.openapi_v1.service_publications.schemas import (
    LifecycleAdvanceRequest,
    LifecycleRestartRequest,
    ServiceBotConfigUpdate,
)


NOW = datetime(2026, 8, 17, 12, 0, 0)


def request() -> Request:
    req = Request({"type": "http", "method": "GET", "path": "/"})
    req.state.trace_id = "trace-1"
    return req


def publication() -> dict:
    return {
        "bot_id": "bot-1",
        "publication_id": 7,
        "card_id": "service:bot-1:7",
        "version": 7,
        "status": "draft",
        "internal_status": "draft",
        "live_version": None,
        "deployment": None,
        "approval": None,
        "available_actions": ["publish_staging", "delete"],
        "created_at": NOW,
        "updated_at": NOW,
    }


def operation(action: str) -> dict:
    return {
        "bot_id": "bot-1",
        "publication_id": 7,
        "action": action,
        "accepted": True,
        "operation_status": "pending",
        "approval": None,
    }


@pytest.mark.asyncio
async def test_upgrade_and_lifecycle_handlers():
    facade = Mock()
    facade.convert_to_service.return_value = publication()
    facade.list_publications.return_value = {
        "bot_id": "bot-1",
        "items": [publication()],
    }

    upgraded = await upgrade_to_service("bot-1", request(), "actor", "owner", facade)
    lifecycle = await get_lifecycle("bot-1", request(), "actor", "owner", facade)

    assert upgraded.data.publication_id == 7
    assert lifecycle.data.items[0].card_id == "service:bot-1:7"
    assert lifecycle.request_id == "trace-1"


@pytest.mark.asyncio
async def test_approval_config_handlers():
    facade = Mock()
    facade.get_service_config.return_value = {
        "bot_id": "bot-1",
        "should_approval": False,
    }
    facade.update_service_config.return_value = {
        "bot_id": "bot-1",
        "should_approval": True,
    }

    current = await get_approval_config("bot-1", request(), "member", "owner", facade)
    updated = await update_approval_config(
        "bot-1",
        ServiceBotConfigUpdate(should_approval=True),
        request(),
        "owner",
        "owner",
        facade,
    )

    assert current.data.should_approval is False
    assert updated.data.should_approval is True
    facade.update_service_config.assert_called_once_with(
        "bot-1",
        actor_id="owner",
        owner_id="owner",
        should_approval=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "action"),
    [("staging", "publish_staging"), ("online", "publish_online")],
)
async def test_advance_handler(stage, action):
    facade = Mock()
    facade.advance = AsyncMock(return_value=operation(action))

    response = await advance_lifecycle(
        "bot-1",
        LifecycleAdvanceRequest(stage=stage),
        request(),
        "actor",
        "owner",
        facade,
    )

    assert response.code == 202000
    assert response.data.action == action
    facade.advance.assert_awaited_once_with(
        "bot-1", stage, actor_id="actor", owner_id="owner"
    )


@pytest.mark.asyncio
async def test_restart_handler_selects_stage():
    facade = Mock()
    facade.restart.return_value = operation("restart_publish")

    response = await restart_lifecycle(
        "bot-1",
        LifecycleRestartRequest(stage="online"),
        request(),
        "actor",
        "owner",
        facade,
    )

    assert response.data.action == "restart_publish"
    facade.restart.assert_called_once_with(
        "bot-1", "online", actor_id="actor", owner_id="owner"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "method", "action"),
    [
        (cancel_staging, "cancel_staging", "cancel_staging"),
        (offline_lifecycle, "offline", "offline"),
        (retry_lifecycle, "retry", "retry"),
    ],
)
async def test_bodyless_lifecycle_action_handlers(handler, method, action):
    facade = Mock()
    setattr(facade, method, AsyncMock(return_value=operation(action)))

    response = await handler("bot-1", request(), "actor", "owner", facade)

    assert response.code == 202000
    assert response.data.action == action


@pytest.mark.asyncio
async def test_delete_initial_draft_handler():
    facade = Mock()
    facade.delete_initial_draft.return_value = True

    response = await delete_initial_draft("bot-1", request(), "owner", "owner", facade)

    assert response.data.deleted is True
    facade.delete_initial_draft.assert_called_once_with(
        "bot-1", actor_id="owner", owner_id="owner"
    )


@pytest.mark.asyncio
async def test_edit_lock_handlers():
    facade = Mock()
    unlocked = SimpleNamespace(
        lock=None,
        holder_name=None,
        has_collaborators=True,
        is_owner=False,
    )
    locked = SimpleNamespace(
        lock=SimpleNamespace(holder_user_id="actor"),
        holder_name="Actor",
        has_collaborators=True,
        is_owner=False,
    )
    facade.get_lock.side_effect = [unlocked, locked, locked, locked]
    facade.acquire_lock.return_value = locked.lock
    facade.release_lock.return_value = True
    facade.steal_lock.return_value = locked.lock

    info = await get_edit_lock("bot-1", request(), "actor", "owner", facade)
    acquired = await acquire_edit_lock("bot-1", request(), "actor", "owner", facade)
    released = await release_edit_lock("bot-1", request(), "actor", "owner", facade)
    stolen = await steal_edit_lock("bot-1", request(), "actor", "owner", facade)

    assert info.data.locked is False
    assert info.data.need_lock is True
    assert acquired.data.acquired is True
    assert released.data.released is True
    assert stolen.data.holder_user_id == "actor"
    assert stolen.data.acquired is True


@pytest.mark.asyncio
async def test_lock_payload_reports_draft_applicability_and_failed_takeover():
    facade = Mock()
    facade.steal_lock.return_value = None
    facade.get_lock.return_value = SimpleNamespace(
        lock=None,
        holder_name=None,
        has_collaborators=True,
        is_owner=False,
        need_lock=False,
    )

    response = await steal_edit_lock("bot-1", request(), "actor", "owner", facade)

    assert response.data.has_collaborators is True
    assert response.data.need_lock is False
    assert response.data.acquired is False


def test_routes_follow_component_first_contract():
    lifecycle_paths = {route.path for route in router.routes}
    lock_paths = {route.path for route in edit_lock_router.routes}

    assert "/openapi/v1/bots/{bot_id}/lifecycle" in lifecycle_paths
    assert "/openapi/v1/bots/{bot_id}/lifecycle/advance" in lifecycle_paths
    assert "/openapi/v1/bots/{bot_id}/lifecycle/cancel-staging" in lifecycle_paths
    assert all(
        path.startswith("/openapi/v1/bots/{bot_id}/lifecycle")
        for path in lifecycle_paths
    )
    assert lock_paths == {
        "/openapi/v1/bots/{bot_id}/edit-lock",
        "/openapi/v1/bots/{bot_id}/edit-lock/steal",
    }
