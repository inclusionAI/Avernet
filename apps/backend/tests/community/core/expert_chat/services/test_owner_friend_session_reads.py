"""Owner inspection keeps friend-session ownership and runtime identity separate."""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from agentclaw.community.core.expert_chat.errors import ChatPermissionError, ConnectionError
from tests.community.core.expert_chat.services.test_expert_chat_uses_resolver import _make_service


def service():
    resolver = MagicMock()
    resolver.resolve_for_binding.return_value.conn_info = {
        "url": "http://runtime", "headers": {}, "use_proxy": False, "engine_type": "openclaw",
    }
    svc = _make_service(resolver=resolver)
    bot = {"bot_id": "b1", "owner_id": "owner", "binding_id": 123, "public": "0"}
    svc._get_authorized_chat_bot = Mock(return_value=bot)
    svc._repo.get_session.return_value = None
    svc._repo.list_owned_sessions.return_value = [{"session_key": "friend-session", "user_id": "friend", "bot_id": "b1"}]
    svc._list_owned_adapter_sessions = AsyncMock(return_value={"friend-session": {"id": "friend-session"}})
    return svc, resolver, bot


@pytest.mark.asyncio
async def test_owner_reads_friend_index_using_owner_connection_identity():
    svc, resolver, _ = service()

    def owner_only(binding, actor, **kwargs):
        if actor != "owner":
            raise PermissionError("private Bot connection requires its owner")
        return MagicMock(conn_info={"url": "http://runtime", "headers": {}, "use_proxy": False})

    resolver.resolve_for_binding.side_effect = owner_only
    result = await svc.list_chat_sessions(
        "friend", "b1", "owner", bcn_friend_authorized=True, actor_user_id="owner",
    )
    assert result["items"][0]["id"] == "friend-session"
    svc._repo.list_owned_sessions.assert_called_once_with(
        user_id="friend", bot_id="b1", owner_id="owner", session_key=None,
    )
    resolver.resolve_for_binding.assert_called_once_with(123, "owner", bot_id="b1")


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,authorized", [("stranger", True), ("collaborator", True), ("owner", False)])
async def test_only_verified_owner_can_read_as_another_subject(actor, authorized):
    svc, resolver, _ = service()
    with pytest.raises(ChatPermissionError):
        await svc.list_chat_sessions(
            "friend", "b1", "owner", bcn_friend_authorized=authorized, actor_user_id=actor,
        )
    resolver.resolve_for_binding.assert_not_called()


@pytest.mark.asyncio
async def test_ordinary_friend_is_not_silently_elevated_to_owner():
    svc, resolver, _ = service()
    resolver.resolve_for_binding.side_effect = PermissionError("not an operator")
    with pytest.raises(ConnectionError):
        await svc.list_chat_sessions(
            "friend", "b1", "owner", bcn_friend_authorized=True, actor_user_id="friend",
        )
    resolver.resolve_for_binding.assert_called_once_with(123, "friend", bot_id="b1")


@pytest.mark.asyncio
async def test_caller_mode_keeps_the_friends_instance_and_connection_identity():
    svc, resolver, bot = service()
    bot["call_type"] = "caller"
    svc._instance_service.get_caller_connection = AsyncMock(return_value={
        "instance": {"ext": {"binding_id": 456}},
    })
    await svc.list_chat_sessions(
        "friend", "b1", "owner", bcn_friend_authorized=True, actor_user_id="owner",
    )
    assert svc._instance_service.get_caller_connection.await_args.kwargs["user_id"] == "friend"
    resolver.resolve_for_binding.assert_called_once_with(456, "friend", bot_id="b1")
