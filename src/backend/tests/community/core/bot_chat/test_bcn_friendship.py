from __future__ import annotations

import httpx
import pytest

from agentclaw.community.core.bot_chat.bcn_friendship import (
    BcnHumanBotFriendshipService,
    FriendshipSourceUnavailableError,
)


class FakeHttpClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return httpx.Response(
            200,
            json=result,
            request=httpx.Request("GET", f"http://bcn.test{path}"),
        )


def test_matches_qualified_bot_identity_and_forwards_only_auth_headers():
    client = FakeHttpClient(
        [{"allowed": True, "reason_code": "ok", "public_default": False}]
    )
    service = BcnHumanBotFriendshipService(client)

    assert service.is_friend(
        human_id="staff/1",
        bot_id="bot-1",
        owner_id="owner-1",
        request_headers={"Authorization": "Bearer t", "X-Unsafe": "drop"},
    )
    path, kwargs = client.calls[0]
    assert path == "/bots/bot-1%3Aowner-1/admission"
    assert kwargs["params"] == {"actor": "staff/1", "actor_kind": "human"}
    assert kwargs["headers"] == {"Authorization": "Bearer t"}


def test_public_default_does_not_turn_an_audience_member_into_a_friend():
    client = FakeHttpClient(
        [{"allowed": True, "reason_code": "public_default", "public_default": True}]
    )

    assert not BcnHumanBotFriendshipService(client).is_friend(
        human_id="staff", bot_id="wanted", owner_id="owner", request_headers={}
    )
    assert len(client.calls) == 1


def test_malformed_admission_response_fails_closed():
    client = FakeHttpClient([{"allowed": True}])

    with pytest.raises(FriendshipSourceUnavailableError):
        BcnHumanBotFriendshipService(client).is_friend(
            human_id="staff", bot_id="bot", owner_id="owner", request_headers={}
        )


def test_transport_failure_fails_closed_instead_of_using_legacy_relationships():
    client = FakeHttpClient([httpx.ConnectError("down")])

    with pytest.raises(FriendshipSourceUnavailableError):
        BcnHumanBotFriendshipService(client).is_friend(
            human_id="staff", bot_id="bot", owner_id="owner", request_headers={}
        )
