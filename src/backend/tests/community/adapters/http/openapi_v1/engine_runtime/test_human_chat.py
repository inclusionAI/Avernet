"""Human-chat is a BCN-authorized, caller-owned conversation plane."""

import pytest

from agentclaw.community.adapters.http.openapi_v1.human_chat import router
from agentclaw.community.core.bot_chat.bcn_friendship import (
    FriendshipSourceUnavailableError,
)
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError as ExpertBotNotFoundError,
    ConnectionError as ExpertConnectionError,
)

from .conftest import OWNER, fails, ok


def test_list_is_always_friend_and_caller_scoped(
    make_client, relay, friendships, expert
):
    """Even an operable Bot cannot turn human-chat into a device-wide listing."""
    caller = "dual-role-user"
    relay.add_operator(caller)
    friendships.allowed = True
    expert.sessions = {
        "items": [{"id": "session:mine:user:dual-role-user", "title": "Mine"}],
        "total": 1,
    }
    client = make_client(router, caller=caller)

    data = ok(
        client.get(
            "/openapi/v1/bots/bot/human-chat/sessions", params={"owner_id": OWNER}
        )
    )

    assert data["items"][0]["session_id"] == "session:mine:user:dual-role-user"
    assert friendships.calls[0]["human_id"] == caller
    assert [call[0] for call in expert.calls] == ["add_chat_bot", "list_chat_sessions"]
    assert relay.calls == []


def test_non_friend_is_masked_and_never_reaches_expert(
    make_client, friendships, expert
):
    client = make_client(router, caller="stranger")

    response = client.get(
        "/openapi/v1/bots/bot/human-chat/sessions", params={"owner_id": OWNER}
    )

    fails(response, 404)
    assert len(friendships.calls) == 1
    assert expert.calls == []


def test_connection_is_bound_to_an_owned_session(make_client, friendships, expert):
    friendships.allowed = True
    client = make_client(router, caller="friend")

    data = ok(
        client.get(
            "/openapi/v1/bots/bot/human-chat/sessions/session:mine/connection",
            params={"owner_id": OWNER},
        )
    )

    assert data["session_id"] == "session:mine"
    assert data["need_poll"] is False
    assert data["connection"]["token"] == "opaque"
    call = next(call for call in expert.calls if call[0] == "connect_chat_session")
    assert call[2]["session_key"] == "session:mine"


def test_create_uses_expert_owned_session_flow(make_client, friendships, expert):
    friendships.allowed = True
    expert.session = {"id": "session:new:user:friend", "title": "New"}
    client = make_client(router, caller="friend")

    response = client.post(
        "/openapi/v1/bots/bot/human-chat/sessions",
        params={"owner_id": OWNER},
        json={"title": "Requested"},
    )
    assert response.status_code == 201
    assert response.json()["code"] == 201000
    data = response.json()["data"]

    assert data["session_id"] == "session:new:user:friend"
    assert data["title"] == "Requested"
    assert [call[0] for call in expert.calls] == [
        "add_chat_bot",
        "create_chat_session",
        "update_owned_chat_session",
    ]


def test_create_without_fields_reads_the_created_session(make_client, friendships, expert):
    friendships.allowed = True
    client = make_client(router, caller="friend")

    response = client.post(
        "/openapi/v1/bots/bot/human-chat/sessions", params={"owner_id": OWNER}, json={}
    )

    assert response.status_code == 201
    assert [call[0] for call in expert.calls] == [
        "add_chat_bot",
        "create_chat_session",
        "get_owned_chat_session",
    ]


@pytest.mark.parametrize(
    ("method", "suffix", "body", "expert_method"),
    [
        ("get", "/session:mine", None, "get_owned_chat_session"),
        ("patch", "/session:mine", {"title": "Renamed"}, "update_owned_chat_session"),
        ("delete", "/session:mine", None, "delete_owned_chat_session"),
        ("get", "/session:mine/messages", None, "list_owned_chat_session_messages"),
        ("delete", "/session:mine/messages", None, "clear_owned_chat_session_messages"),
        ("put", "/session:mine/favorite", None, "set_owned_chat_session_favorite"),
        ("delete", "/session:mine/favorite", None, "set_owned_chat_session_favorite"),
    ],
)
def test_session_operations_use_owned_expert_methods(
    make_client, friendships, expert, method, suffix, body, expert_method
):
    friendships.allowed = True
    client = make_client(router, caller="friend")

    response = getattr(client, method)(
        f"/openapi/v1/bots/bot/human-chat/sessions{suffix}",
        params={"owner_id": OWNER},
        **({"json": body} if body is not None else {}),
    )

    assert response.status_code == 200, response.json()
    assert any(call[0] == expert_method for call in expert.calls)


def test_favorites_are_caller_scoped(make_client, friendships, expert):
    friendships.allowed = True
    expert.sessions = {"items": [expert.session], "total": 1}
    client = make_client(router, caller="friend")

    ok(
        client.get(
            "/openapi/v1/bots/bot/human-chat/sessions/favorites",
            params={"owner_id": OWNER},
        )
    )

    call = next(call for call in expert.calls if call[0] == "list_chat_sessions")
    assert call[2]["favorite_only"] is True


def test_bcn_unavailable_fails_closed(make_client, friendships, expert):
    def unavailable(**_kwargs):
        raise FriendshipSourceUnavailableError("BCN unavailable")

    friendships.is_friend = unavailable
    client = make_client(router, caller="friend")

    response = client.get(
        "/openapi/v1/bots/bot/human-chat/sessions", params={"owner_id": OWNER}
    )

    fails(response, 409)
    assert expert.calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [(ExpertBotNotFoundError("missing"), 404), (ExpertConnectionError("down"), 409)],
)
def test_expert_failures_are_normalized(make_client, friendships, expert, error, status):
    async def fail(**_kwargs):
        raise error

    friendships.allowed = True
    expert.list_chat_sessions = fail
    client = make_client(router, caller="friend")

    response = client.get(
        "/openapi/v1/bots/bot/human-chat/sessions", params={"owner_id": OWNER}
    )

    fails(response, status)
