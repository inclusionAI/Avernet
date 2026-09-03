"""Declarative happy/error coverage for the human-chat OpenAPI surface."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import (
    HumanBotFriendshipServiceProtocol,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_USER = "human-chat-user"
_BOT = "human-chat-bot"
_SESSION = "session:framework:user:human-chat-user"
_KEY = "human-chat-framework-signing-key-32-bytes"
_BASE = "/openapi/v1/bots/{bot_id}/human-chat/sessions"
_PATH = {"bot_id": _BOT}
_SESSION_PATH = {**_PATH, "session_id": _SESSION}
_QUERY = {"user_id": _USER, "owner_id": _USER}
_FORBIDDEN_QUERY = {"user_id": "another-user", "owner_id": _USER}


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
                    "subject": {"id": _USER, "username": "human-chat@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _session() -> dict:
    return {
        "id": _SESSION,
        "title": "Private conversation",
        "agent_id": "main",
        "model": "test/model",
        "permission_mode": "default",
        "cwd": "",
        "runtime": "openclaw",
        "message_count": 1,
        "gmt_created": "2026-09-03T00:00:00Z",
        "gmt_modified": "2026-09-03T00:01:00Z",
    }


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy(world) -> None:
    _seed_verifier(world)

    def is_friend(_self, **_kwargs):
        return True

    def add_chat_bot(_self, *_args, **_kwargs):
        return {}

    async def list_sessions(_self, *_args, **_kwargs):
        return {"items": [_session()], "total": 1}

    async def create_session(_self, *_args, **_kwargs):
        return {"session_key": _SESSION}

    async def get_session(_self, *_args, **_kwargs):
        return _session()

    async def update_session(_self, *_args, **_kwargs):
        return _session()

    async def successful(_self, *_args, **_kwargs):
        return True

    async def list_messages(_self, *_args, **_kwargs):
        return {
            "items": [
                {
                    "id": "message-1",
                    "role": "user",
                    "content": "hello",
                    "gmt_created": "2026-09-03T00:00:01Z",
                }
            ],
            "total": 1,
        }

    async def connect(_self, *_args, **_kwargs):
        return {
            "session_key": _SESSION,
            "connection": {"ws_url": "wss://example.invalid/chat", "token": "opaque"},
        }

    bind_overrides(world, HumanBotFriendshipServiceProtocol, {"is_friend": is_friend})
    bind_overrides(
        world,
        ExpertChatServiceProtocol,
        {
            "add_chat_bot": add_chat_bot,
            "list_chat_sessions": list_sessions,
            "create_chat_session": create_session,
            "get_owned_chat_session": get_session,
            "update_owned_chat_session": update_session,
            "delete_owned_chat_session": successful,
            "list_owned_chat_session_messages": list_messages,
            "clear_owned_chat_session_messages": successful,
            "set_owned_chat_session_favorite": successful,
            "connect_chat_session": connect,
        },
    )


_SESSION_BODY = {
    "session_id": _SESSION,
    "title": "Private conversation",
    "model": "test/model",
    "runtime": "openclaw",
}
_DELETED = {"data": {"deleted": True}}

# method, path, path params, JSON body, HTTP status, response subset
_HAPPY_CASES = (
    ("GET", _BASE, _PATH, None, 200, {"data": {"total": 1, "items": [_SESSION_BODY]}}),
    ("POST", _BASE, _PATH, {}, 201, {"data": _SESSION_BODY}),
    ("GET", f"{_BASE}/favorites", _PATH, None, 200, {"data": {"total": 1}}),
    ("GET", f"{_BASE}/{{session_id}}", _SESSION_PATH, None, 200, {"data": _SESSION_BODY}),
    ("PATCH", f"{_BASE}/{{session_id}}", _SESSION_PATH, {"title": "Renamed"}, 200, {"data": _SESSION_BODY}),
    ("DELETE", f"{_BASE}/{{session_id}}", _SESSION_PATH, None, 200, _DELETED),
    (
        "GET",
        f"{_BASE}/{{session_id}}/connection",
        _SESSION_PATH,
        None,
        200,
        {"data": {"session_id": _SESSION, "need_poll": False}},
    ),
    (
        "GET",
        f"{_BASE}/{{session_id}}/messages",
        _SESSION_PATH,
        None,
        200,
        {"data": {"total": 1, "items": [{"message_id": "message-1"}]}},
    ),
    ("DELETE", f"{_BASE}/{{session_id}}/messages", _SESSION_PATH, None, 200, _DELETED),
    (
        "PUT",
        f"{_BASE}/{{session_id}}/favorite",
        _SESSION_PATH,
        None,
        200,
        {"data": {"session_id": _SESSION, "favorited": True}},
    ),
    (
        "DELETE",
        f"{_BASE}/{{session_id}}/favorite",
        _SESSION_PATH,
        None,
        200,
        {"data": {"session_id": _SESSION, "favorited": False}},
    ),
)


for _method, _route, _path_params, _body, _status, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_route,
        scenario="happy",
        input=CaseInput(
            path_params=_path_params,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_body,
        ),
        seed=_seed_happy,
        expect=ExpectSuccess(status=_status, json_contains=_contains),
    )(lambda: None)


# Every operation must reject a request whose user_id is not the authenticated
# principal. This runs before BCN or ExpertChat, so only the verifier is seeded.
for _method, _route, _path_params, _body, _status, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_route,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_path_params,
            query_params=_FORBIDDEN_QUERY,
            headers=_HEADERS,
            json_body=_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)
