"""Build rules round-trip through real HTTP, authorization, DI and SQLite."""

import pytest

from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/service-bot/publish/ops/build-ignore"
_OWNER = "build-ignore-owner"
_BOT = "build-ignore-bot"
_PARAMS = {"bot_id": _BOT, "entity_id": _OWNER}
_HEADERS = {"x-user-id": _OWNER}


def seed_build_ignore(world):
    """An offline service Bot: no runtime binding is needed for build rules."""
    make_staff_user(world, user_id=_OWNER)
    world.get(BotRepository).insert({
        "bot_id": _BOT, "bot_name": "Build Ignore Bot",
        "owner_id": _OWNER, "owner_name": "Owner",
        "entity_id": _OWNER, "entity_type": "staff",
        "creator_id": _OWNER, "bot_type": "service",
        "status": "ACTIVE", "active_engine": "openclaw",
    })


@endpoint_test(
    method="GET", path=_PATH, scenario="happy",
    input=CaseInput(headers=_HEADERS, query_params=_PARAMS),
    seed=seed_build_ignore,
    expect=ExpectSuccess(status=200, json_contains={
        "success": True,
        "data": {"paths": [], "revision": 0, "scope": "build"},
    }),
)
def build_ignore_empty():
    """No stored config is an empty successful build snapshot."""


@endpoint_test(
    method="GET", path=_PATH, scenario="missing_bot",
    input=CaseInput(headers=_HEADERS, query_params={**_PARAMS, "bot_id": "absent"}),
    seed=seed_build_ignore,
    expect=ExpectError(status=200, json_contains={
        "success": False, "message": "bot_not_found",
    }),
)
def build_ignore_missing_bot():
    """A missing Bot must not look like a successful empty rules list."""


@endpoint_test(
    method="POST", path=_PATH, scenario="happy",
    input=CaseInput(headers=_HEADERS, json_body={
        **_PARAMS, "operation": "add", "path": "workspace/bin",
    }),
    seed=seed_build_ignore,
    expect=ExpectSuccess(status=200, json_contains={
        "success": True,
        "data": {"paths": ["workspace/bin"], "revision": 1, "changed": True},
    }),
)
def build_ignore_add():
    """Owner can save rules without a draft or published container."""


@endpoint_test(
    method="POST", path=_PATH, scenario="invalid_path",
    input=CaseInput(headers=_HEADERS, json_body={
        **_PARAMS, "operation": "add", "path": "../outside",
    }),
    seed=seed_build_ignore,
    expect=ExpectError(status=200, json_contains={
        "success": False, "message": "invalid_ignore_path",
    }),
)
def build_ignore_invalid_path():
    """The real service rejects a rule escaping the artifact root."""


@pytest.mark.asyncio
async def test_build_ignore_persisted_roundtrip(world, async_client):
    seed_build_ignore(world)
    for operation, changed, revision, paths in [
        ("add", True, 1, ["workspace/bin"]),
        ("add", False, 1, ["workspace/bin"]),
        ("remove", True, 2, []),
        ("remove", False, 2, []),
    ]:
        response = await async_client.post(_PATH, headers=_HEADERS, json={
            **_PARAMS, "operation": operation, "path": "workspace/bin",
        })
        payload = response.json()
        assert response.status_code == 200
        assert payload["success"] is True, payload
        assert payload["data"]["changed"] is changed
        assert payload["data"]["revision"] == revision
        query = await async_client.get(_PATH, headers=_HEADERS, params=_PARAMS)
        stored = query.json()
        assert stored["success"] is True, stored
        assert stored["data"]["paths"] == paths
        assert stored["data"]["revision"] == revision
        assert stored["data"]["engine_type"] == "openclaw"


@pytest.mark.asyncio
async def test_build_ignore_other_user_cannot_read_or_change(world, async_client):
    seed_build_ignore(world)
    make_staff_user(world, user_id="build-ignore-stranger")
    headers = {"x-user-id": "build-ignore-stranger"}
    read = await async_client.get(_PATH, headers=headers, params=_PARAMS)
    change = await async_client.post(_PATH, headers=headers, json={
        **_PARAMS, "operation": "add", "path": "workspace/bin",
    })
    for response in (read, change):
        payload = response.json()
        assert payload["success"] is False, payload
        assert payload["message"] == "permission_denied"
    owner_read = await async_client.get(_PATH, headers=_HEADERS, params=_PARAMS)
    assert owner_read.json()["data"]["paths"] == []
    assert owner_read.json()["data"]["revision"] == 0
