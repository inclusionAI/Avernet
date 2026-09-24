"""Assembled endpoint coverage for desktop avatar and link metadata."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.link_workflow_service import LinkWorkflowProtocol
from agentclaw.community.core.resources.models import Resource, ResourceType
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

_OWNER = "desktop-metadata-owner"
_BOT_ID = "desktop-metadata-bot"
_RESOURCE_ID = "desktop-link-1"
_KEY = "desktop-metadata-signing-key-at-least-32-bytes"
_AVATAR_PATH = "/openapi/v1/bots/{bot_id}/avatar"
_LINKS_PATH = "/openapi/v1/bots/{bot_id}/links"
_LINK_PATH = f"{_LINKS_PATH}/{{resource_id}}"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_RESOURCE_PATH_PARAMS = {**_PATH_PARAMS, "resource_id": _RESOURCE_ID}


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
                        "username": "desktop-metadata@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _query(owner_id: str = _OWNER) -> dict[str, str]:
    return {"user_id": owner_id}


def _resource() -> Resource:
    return Resource(
        id=_RESOURCE_ID,
        name="Desktop handbook",
        resource_type=ResourceType.LINK,
        user_id=_OWNER,
        bolt_id=_BOT_ID,
        attributes={
            "url": "https://example.test/handbook",
            "link_type": "antcode",
            "access_modes": ["READ"],
        },
    )


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER,
        bot_type="desktop",
        active_engine="openclaw",
    )

    def get_bot(_self, _bot_id, _owner_id):
        return {
            "bot_id": _BOT_ID,
            "owner_id": _OWNER,
            "ext": {"avatar_url": "https://example.test/avatar.png"},
        }

    def update_bot(_self, _bot_id, _owner_id, **_kwargs):
        return get_bot(_self, _bot_id, _owner_id)

    bind_overrides(
        world,
        BotServiceProtocol,
        {"get_bot": get_bot, "update_bot": update_bot},
    )

    def list_links(_self, _bot_id, _owner_id):
        return [_resource()]

    async def create_links(_self, _bot_id, _owner_id, _links):
        return [_resource()]

    async def update_link(_self, _bot_id, _owner_id, _resource_id, _changes):
        return _resource()

    async def delete_link(_self, _bot_id, _owner_id, _resource_id):
        return None

    bind_overrides(
        world,
        LinkWorkflowProtocol,
        {
            "list": list_links,
            "create": create_links,
            "update": update_link,
            "delete": delete_link,
        },
    )


_LINK_WRITE = {
    "url": "https://example.test/handbook",
    "name": "Desktop handbook",
    "link_type": "antcode",
    "access_modes": ["READ"],
}

_HAPPY_CASES = (
    (
        "GET",
        _AVATAR_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS),
        {"data": {"avatar_url": "https://example.test/avatar.png"}},
    ),
    (
        "PUT",
        _AVATAR_PATH,
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(),
            headers=_HEADERS,
            json_body={"avatar_url": "https://example.test/new-avatar.png"},
        ),
        {"data": {"avatar_url": "https://example.test/new-avatar.png"}},
    ),
    (
        "GET",
        _LINKS_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS),
        {"data": [{"id": _RESOURCE_ID, "link_type": "antcode"}]},
    ),
    (
        "POST",
        _LINKS_PATH,
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(),
            headers=_HEADERS,
            json_body={"links": [_LINK_WRITE]},
        ),
        {"data": [{"id": _RESOURCE_ID, "url": _LINK_WRITE["url"]}]},
    ),
    (
        "PUT",
        _LINK_PATH,
        CaseInput(
            path_params=_RESOURCE_PATH_PARAMS,
            query_params=_query(),
            headers=_HEADERS,
            json_body={"name": "Updated handbook"},
        ),
        {"data": {"id": _RESOURCE_ID, "url": _LINK_WRITE["url"]}},
    ),
    (
        "DELETE",
        _LINK_PATH,
        CaseInput(
            path_params=_RESOURCE_PATH_PARAMS,
            query_params=_query(),
            headers=_HEADERS,
        ),
        {"data": {"deleted": True}},
    ),
)


for _method, _path, _input, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=200, json_contains=_contains),
    )(lambda: None)


for _method, _path, _input, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_query("another-user"),
            headers=_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(
            status=403,
            json_contains={"code": 403000, "data": None},
        ),
    )(lambda: None)
