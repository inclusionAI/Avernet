"""Endpoint-framework coverage for Bot Workshop Channels and Space assignment."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.channel_service import ChannelServiceProtocol
from agentclaw.community.api.space_service import SpaceServiceProtocol
from agentclaw.community.core.channel.models import ChannelRecord
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "bot-workshop-channel-owner"
_BOT = "bot-workshop-channel-bot"
_KEY = "bot-workshop-channel-signing-key-at-least-32-bytes"
_CHANNEL_ID = 1


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _OWNER,
                        "username": "bot-workshop-channel@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_CREATE_BODY = {
    "type": "dingding",
    "description": "Endpoint Channel",
    "config": {"client_id": "client-1", "client_secret": "secret-1"},
}


def _boot_verifier() -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _channel(*, status: str = "0") -> ChannelRecord:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return ChannelRecord(
        id=_CHANNEL_ID,
        type="dingding",
        description="Endpoint Channel",
        identity_id=_OWNER,
        bind_bot_id=_BOT,
        config={
            "client_id": "client-1",
            "client_secret": "secret-1",
            "allowlist": ["*"],
        },
        status=status,
        deleted=0,
        gmt_create=now,
        gmt_modified=now,
        env="test",
        stage="draft",
    )


def _seed_channel_surface(world, *, status: str = "0") -> None:
    _boot_verifier()
    make_bot(
        world,
        bot_id=_BOT,
        owner_id=_OWNER,
        bot_type="personal",
        status="ACTIVE",
    )
    record = _channel(status=status)

    def _list_channels(_self, **_kwargs):
        return [record]

    def _create_channel(_self, **_kwargs):
        return _CHANNEL_ID

    def _get_channel_by_id(_self, channel_id: int):
        return record if channel_id == _CHANNEL_ID else None

    def _update_channel(_self, **_kwargs):
        return None

    async def _set_channel_status(_self, _channel_id: int, _status: str):
        return None

    async def _sync_active_channel(_self, _channel_id: int):
        return None

    def _delete(_self, channel_id: int):
        return None

    bind_overrides(
        world,
        ChannelServiceProtocol,
        {
            "list_channels": _list_channels,
            "create_channel": _create_channel,
            "get_channel_by_id": _get_channel_by_id,
            "update_channel": _update_channel,
            "set_channel_status": _set_channel_status,
            "sync_active_channel": _sync_active_channel,
            "delete": _delete,
        },
    )


def _seed_channels(world) -> None:
    _seed_channel_surface(world)


def _seed_active_channel(world) -> None:
    _seed_channel_surface(world, status="1")


def _seed_auth_only(_world) -> None:
    _boot_verifier()


def _wrong_user(
    *, path_params: dict[str, object], json_body: dict[str, object] | None = None
) -> CaseInput:
    return CaseInput(
        path_params=path_params,
        query_params={"user_id": "someone-else"},
        headers=_HEADERS,
        json_body=json_body,
    )


# Channels collection


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/channels",
    scenario="lists_owned_channels",
    seed=_seed_channels,
    input=CaseInput(
        path_params={"bot_id": _BOT}, query_params=_QUERY, headers=_HEADERS
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": [{"id": _CHANNEL_ID}]},
    ),
)
def list_channels_happy():
    """An owner can list the Bot's safe Channel projections."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/channels",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=_wrong_user(path_params={"bot_id": _BOT}),
    expect=ExpectError(status=403),
)
def list_channels_wrong_user():
    """The explicit user_id must match the verified human principal."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/channels",
    scenario="creates_inactive_channel",
    seed=_seed_channels,
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body=_CREATE_BODY,
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={"code": 201000, "data": {"id": _CHANNEL_ID}},
    ),
)
def create_channel_happy():
    """A valid DingTalk configuration creates an inactive draft Channel."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/channels",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=_wrong_user(path_params={"bot_id": _BOT}, json_body=_CREATE_BODY),
    expect=ExpectError(status=403),
)
def create_channel_wrong_user():
    """A caller cannot create a Channel while naming another user_id."""


# One Channel


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    scenario="gets_owned_channel",
    seed=_seed_channels,
    input=CaseInput(
        path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"id": _CHANNEL_ID}},
    ),
)
def get_channel_happy():
    """An owned Channel is readable inside its Bot scope."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=_wrong_user(path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID}),
    expect=ExpectError(status=403),
)
def get_channel_wrong_user():
    """A mismatched explicit user_id is refused before Channel lookup."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    scenario="updates_channel_description",
    seed=_seed_channels,
    input=CaseInput(
        path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"description": "Updated"},
    ),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def update_channel_happy():
    """An owner can update a draft Channel."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=_wrong_user(
        path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID},
        json_body={"description": "Updated"},
    ),
    expect=ExpectError(status=403),
)
def update_channel_wrong_user():
    """A caller cannot update a Channel while naming another user_id."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}/status",
    scenario="keeps_channel_active",
    seed=_seed_active_channel,
    input=CaseInput(
        path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"status": "active"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"status": "active"}},
    ),
)
def update_channel_status_happy():
    """A valid public status is accepted and returned."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}/status",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=_wrong_user(
        path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID},
        json_body={"status": "active"},
    ),
    expect=ExpectError(status=403),
)
def update_channel_status_wrong_user():
    """A mismatched explicit user_id cannot change Channel status."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    scenario="deletes_inactive_channel",
    seed=_seed_channels,
    input=CaseInput(
        path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"code": 200000, "data": {"deleted": True}}
    ),
)
def delete_channel_happy():
    """An inactive Channel can be removed from its Bot."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/channels/{channel_id}",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=_wrong_user(path_params={"bot_id": _BOT, "channel_id": _CHANNEL_ID}),
    expect=ExpectError(status=403),
)
def delete_channel_wrong_user():
    """A caller cannot delete a Channel while naming another user_id."""


# Bot Space assignment


def _seed_bot_and_team_space(world) -> None:
    _boot_verifier()
    make_bot(
        world,
        bot_id=_BOT,
        owner_id=_OWNER,
        bot_type="personal",
        status="ACTIVE",
    )
    world.get(SpaceServiceProtocol).create_team(
        name="Bot Workshop Team", creator_id=_OWNER
    )


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/space",
    scenario="moves_bot_to_joined_space",
    seed=_seed_bot_and_team_space,
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"space_id": 1},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"bot_id": _BOT, "space_id": 1, "changed": True},
        },
    ),
)
def change_bot_space_happy():
    """An owned Bot can move to a team Space its owner belongs to."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/bots/{bot_id}/space",
    scenario="wrong_user",
    seed=_seed_auth_only,
    input=_wrong_user(path_params={"bot_id": _BOT}, json_body={"space_id": 1}),
    expect=ExpectError(status=403),
)
def change_bot_space_wrong_user():
    """The mutation cannot be performed for a different explicit user_id."""
