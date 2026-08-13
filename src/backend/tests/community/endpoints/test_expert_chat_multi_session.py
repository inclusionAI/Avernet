"""Endpoint coverage for the expert-chat multi-session API."""

from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.core.expert_chat.errors import BotNotFoundError
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)


_PATH = "/api/v1/expert-chats/{bot_id}/{owner_id}/sessions"
_CONNECT_PATH = f"{_PATH}/connect"
_PATH_PARAMS = {"bot_id": "multi-session-bot", "owner_id": "multi-session-owner"}
_HEADERS = {"x-user-id": "multi-session-user"}
_SESSION_KEY = "agent:main:session:endpoint:user:multi-session-user"


def _seed_happy_service(world) -> None:
    """Bind the thin router boundary to deterministic successful outcomes."""

    async def list_sessions(_self, *_args, **_kwargs):
        return {
            "total": 1,
            "items": [
                {
                    "id": _SESSION_KEY,
                    "title": "Endpoint session",
                    "user_id": "multi-session-user",
                    "agent_id": "multi-session-bot",
                    "gmt_created": "2026-08-11T00:00:00Z",
                    "gmt_modified": "2026-08-11T00:00:00Z",
                    "message_count": 0,
                    "last_message": None,
                }
            ],
        }

    async def create_session(_self, *_args, **_kwargs):
        return {
            "session_key": _SESSION_KEY,
            "is_new": True,
            "connection": {"type": "websocket"},
        }

    async def connect_session(_self, *_args, **_kwargs):
        return {
            "session_key": _SESSION_KEY,
            "is_new": False,
            "connection": {"type": "websocket"},
        }

    async def delete_session(_self, *_args, **_kwargs):
        return True

    bind_overrides(
        world,
        ExpertChatServiceProtocol,
        {
            "list_chat_sessions": list_sessions,
            "create_chat_session": create_session,
            "connect_chat_session": connect_session,
            "delete_owned_chat_session": delete_session,
        },
    )


def _seed_create_not_found(world) -> None:
    """Drive the create route's domain-error mapping through DI."""

    async def create_session(_self, *_args, **_kwargs):
        raise BotNotFoundError("Bot不存在")

    bind_overrides(
        world,
        ExpertChatServiceProtocol,
        {"create_chat_session": create_session},
    )


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="happy",
    seed=_seed_happy_service,
    input=CaseInput(path_params=_PATH_PARAMS, headers=_HEADERS),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "error_code": 0,
            "data": {"total": 1, "items": [{"id": _SESSION_KEY}]},
        },
    ),
)
def list_sessions_happy():
    """The plural list route returns the service result."""


@endpoint_test(
    method="GET",
    path=_PATH,
    scenario="invalid_limit",
    input=CaseInput(
        path_params=_PATH_PARAMS,
        query_params={"limit": 0},
        headers=_HEADERS,
    ),
    expect=ExpectError(status=422),
)
def list_sessions_invalid_limit():
    """The list route rejects an invalid page size."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="happy",
    seed=_seed_happy_service,
    input=CaseInput(path_params=_PATH_PARAMS, headers=_HEADERS),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "error_code": 0,
            "data": {"session_key": _SESSION_KEY, "is_new": True},
        },
    ),
)
def create_session_happy():
    """The create route returns a newly-created session."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="bot_not_found",
    seed=_seed_create_not_found,
    input=CaseInput(path_params=_PATH_PARAMS, headers=_HEADERS),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def create_session_bot_not_found():
    """The create route maps a missing Bot to its error envelope."""


@endpoint_test(
    method="POST",
    path=_CONNECT_PATH,
    scenario="happy",
    seed=_seed_happy_service,
    input=CaseInput(
        path_params=_PATH_PARAMS,
        headers=_HEADERS,
        json_body={"session_key": _SESSION_KEY},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "error_code": 0,
            "data": {"session_key": _SESSION_KEY, "is_new": False},
        },
    ),
)
def connect_session_happy():
    """The connect route returns an existing session connection."""


@endpoint_test(
    method="POST",
    path=_CONNECT_PATH,
    scenario="missing_session_key",
    input=CaseInput(
        path_params=_PATH_PARAMS,
        headers=_HEADERS,
        json_body={},
    ),
    expect=ExpectError(status=422),
)
def connect_session_missing_key():
    """The connect route requires a session key."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="happy",
    seed=_seed_happy_service,
    input=CaseInput(
        path_params=_PATH_PARAMS,
        query_params={"session_key": _SESSION_KEY},
        headers=_HEADERS,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "error_code": 0},
    ),
)
def delete_session_happy():
    """The delete route returns success after the service completes."""


@endpoint_test(
    method="DELETE",
    path=_PATH,
    scenario="missing_session_key",
    input=CaseInput(path_params=_PATH_PARAMS, headers=_HEADERS),
    expect=ExpectError(status=422),
)
def delete_session_missing_key():
    """The delete route requires a session key."""
