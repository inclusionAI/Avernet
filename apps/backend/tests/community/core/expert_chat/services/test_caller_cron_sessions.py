"""Independent Caller runtime cron discovery regression tests."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.community.core.expert_chat.services.test_expert_chat_service import (
    ACTIVE_BOT,
    _make_service,
)


@pytest.fixture
def caller_service(mock_repository, mock_bot_repo, mock_device_provider):
    bot = dict(ACTIVE_BOT, bot_type="service", call_type="caller")
    mock_bot_repo.get_by_id_and_owner.return_value = bot
    mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
    instance = MagicMock()
    instance.get_caller_connection = AsyncMock(return_value={
        "instance": {"ext": {"binding_id": "caller-binding"}},
    })
    instance.get_existing_caller_instance.return_value = {
        "status": "success", "ext": {"binding_id": "caller-binding"},
    }
    transport = MagicMock()
    transport.invoke = AsyncMock(return_value={"data": []})
    service = _make_service(mock_repository, mock_bot_repo, mock_device_provider,
                            mock_instance_service=instance, mock_transport=transport)
    service._online_chat_binding = MagicMock(return_value="online-binding")
    return service, transport, mock_repository


@pytest.mark.asyncio
async def test_empty_ownership_discovers_cron_without_registration(caller_service):
    service, transport, repo = caller_service
    key = "agent:bot1:cron:job1"
    transport.invoke.return_value = {"data": [{"id": key, "title": "Scheduled task",
        "gmt_created": "2026-11-11T10:00:00Z", "gmt_modified": "2026-11-12T10:00:00Z"}]}
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result["total"] == 1
    assert result["items"][0]["id"] == key
    repo.add_owned_session.assert_not_called()
    transport.invoke.return_value = {"data": {"id": key}}
    connected = await service.connect_chat_session("user1", "bot1", "owner1", key)
    assert connected["session_key"] == key


@pytest.mark.asyncio
async def test_pages_deduplicate_and_filter_before_client_pagination(caller_service, monkeypatch):
    service, transport, repo = caller_service
    monkeypatch.setattr("agentclaw.community.core.expert_chat.services.expert_chat_caller_sessions._PAGE_SIZE", 2)
    first = {"id": "agent:main:cron:first", "gmt_created": "2026-11-10T10:00:00Z"}
    second = {"id": "agent:main:cron:second:run:actual", "gmt_created": "2026-11-11T10:00:00Z"}
    transport.invoke.side_effect = [{"data": [first, second]}, {"data": [first, {"id": ""}]}, {"data": []}]
    result = await service.list_chat_sessions("user1", "bot1", "owner1", limit=1, offset=1)
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [first["id"]]
    repo.add_owned_session.assert_not_called()
    assert [call.kwargs["params"]["offset"] for call in transport.invoke.await_args_list] == [0, 2]
    assert all("user_id" not in call.kwargs["params"] for call in transport.invoke.await_args_list)


@pytest.mark.asyncio
async def test_exact_key_reads_requested_runtime_session(caller_service):
    service, transport, repo = caller_service
    key = "ordinary-session"
    transport.invoke.return_value = {"data": {"id": key}}
    result = await service.list_chat_sessions("user1", "bot1", "owner1", session_key=key)
    assert [item["id"] for item in result["items"]] == [key]
    repo.add_owned_session.assert_not_called()
    detail = await service.get_owned_chat_session("user1", "bot1", "owner1", key)
    assert detail["id"] == key
    transport.invoke.side_effect = [{"data": {"id": key}}, {"data": [{"content": "cron result"}], "total": 1}]
    messages = await service.list_owned_chat_session_messages("user1", "bot1", "owner1", key, 20)
    assert messages == {"items": [{"content": "cron result"}], "total": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("call_type", "owner"), ("bot_type", "personal")])
async def test_shared_runtime_does_not_discover(caller_service, field, value):
    service, transport, repo = caller_service
    service._bot_repo.get_by_id_and_owner.return_value[field] = value
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result == {"total": 0, "items": []}
    transport.invoke.assert_not_awaited()
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_trusted_engine_overrides_connection_fallback(caller_service):
    service, transport, repo = caller_service
    service._resolver.resolve_for_binding.return_value.conn_info["engine_type"] = "teclaw"
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result == {"total": 0, "items": []}
    assert transport.invoke.await_args.args[0]["engine_type"] == "openclaw"
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_pending_instance_is_not_discovered(caller_service):
    service, transport, repo = caller_service
    service._instance_service.get_existing_caller_instance.return_value = {"status": "init"}
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result == {"total": 0, "items": [], "need_poll": True}
    transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{"data": None}, {"success": False, "data": []}, RuntimeError("credential-must-stay-private")])
async def test_discovery_failure_logs_no_upstream_secrets(caller_service, caplog, response):
    service, transport, repo = caller_service
    if isinstance(response, Exception):
        transport.invoke.side_effect = response
    else:
        transport.invoke.return_value = response
    with caplog.at_level("INFO"):
        result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result == {"total": 0, "items": []}
    assert "caller_runtime_sessions_request" in caplog.text
    assert "caller_runtime_sessions_failed" in caplog.text
    assert "credential-must-stay-private" not in caplog.text
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_success_logs_session_keys_not_connection_credentials(caller_service, caplog):
    service, transport, repo = caller_service
    service._resolver.resolve_for_binding.return_value.conn_info["headers"] = {"Authorization": "private-connection-credential"}
    key = "agent:main:cron:job"
    transport.invoke.return_value = {"data": [{"id": key}]}
    with caplog.at_level("INFO"):
        await service.list_chat_sessions("user1", "bot1", "owner1")
    assert "caller_runtime_sessions_request" in caplog.text
    assert "caller_runtime_sessions_success" in caplog.text
    assert key in caplog.text
    assert "private-connection-credential" not in caplog.text


@pytest.mark.asyncio
async def test_favorite_list_includes_discovered_session(caller_service):
    service, transport, repo = caller_service
    key = "ordinary-session"
    transport.invoke.side_effect = [{"data": [{"id": key}]}, {"data": []}, {"data": [{"id": key}]}]
    result = await service.list_chat_sessions("user1", "bot1", "owner1", favorite_only=True)
    assert [item["id"] for item in result["items"]] == [key]


@pytest.mark.asyncio
async def test_repeated_page_stops_and_existing_cron_is_not_reregistered(caller_service, monkeypatch):
    service, transport, repo = caller_service
    monkeypatch.setattr("agentclaw.community.core.expert_chat.services.expert_chat_caller_sessions._PAGE_SIZE", 1)
    key = "agent:main:cron:job"
    repo.list_owned_sessions.return_value = [{"session_key": key, "user_id": "user1", "bot_id": "bot1", "gmt_create": "2026-11-01T00:00:00Z"}]
    transport.invoke.return_value = {"data": [{"id": key}]}
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result["total"] == 1
    assert transport.invoke.await_count == 2
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_lists_preserve_runtime_cron_order(caller_service):
    service, transport, repo = caller_service
    key = "agent:main:cron:job"
    item = {"id": key, "gmt_created": "2026-10-01T00:00:00Z", "gmt_modified": "2026-11-11T00:00:00Z"}
    transport.invoke.return_value = {"data": [item]}
    first = await service.list_chat_sessions("user1", "bot1", "owner1")
    repo.list_owned_sessions.return_value = [{"session_key": key, "user_id": "user1", "bot_id": "bot1", "gmt_create": "2026-11-12T00:00:00Z"}]
    second = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert first["items"][0]["gmt_modified"] == second["items"][0]["gmt_modified"] == item["gmt_modified"]


@pytest.mark.asyncio
async def test_ownership_repository_failure_is_irrelevant(caller_service):
    service, transport, repo = caller_service
    transport.invoke.return_value = {"data": [{"id": "agent:main:cron:job"}]}
    repo.add_owned_session.side_effect = RuntimeError("database unavailable")
    assert (await service.list_chat_sessions("user1", "bot1", "owner1"))["total"] == 1
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_short_runtime_page_does_not_hide_later_cron(caller_service):
    service, transport, repo = caller_service
    key = "agent:main:cron:later"
    transport.invoke.side_effect = [
        {"data": [{"id": "agent:main:chat:existing"}]},
        {"data": [{"id": key}]},
        {"data": []},
    ]
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert {item["id"] for item in result["items"]} == {"agent:main:chat:existing", key}
    assert [call.kwargs["params"]["offset"] for call in transport.invoke.await_args_list] == [0, 500, 1000]


@pytest.mark.asyncio
async def test_authoritative_teclaw_metadata_overrides_caller_fallback(caller_service):
    service, transport, repo = caller_service
    service._bot_repo.get_by_id_and_owner.return_value["active_engine"] = "teclaw"
    assert service._resolver.resolve_for_binding.return_value.conn_info["engine_type"] == "openclaw"
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result == {"total": 0, "items": []}
    transport.invoke.assert_not_awaited()
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_two_subjects_access_only_their_instance_without_ownership(caller_service, tmp_path):
    from agentclaw.community.core.repository.implementations.chat.expert_chat import ExpertChatRepository
    from agentclaw.community.core.expert_chat.errors import BotNotFoundError
    from tests.community.repository.chat.test_expert_chat_unified import _make_db

    service, transport, _ = caller_service
    repo = ExpertChatRepository(_make_db(tmp_path))
    for user in ("user1", "user2"):
        repo.add_chat_bot(user, "bot1", "owner1")
    service._repo = repo

    async def caller_connection(**kwargs):
        return {"instance": {"ext": {"binding_id": f"caller-{kwargs['user_id']}"}}}

    def resolve_binding(binding_id, user_id, **kwargs):
        assert binding_id == f"caller-{user_id}"
        return MagicMock(conn_info={"engine_type": "openclaw", "instance": binding_id})

    async def invoke(connection, method, path, **kwargs):
        subject = connection["instance"].removeprefix("caller-")
        key = f"agent:main:subagent:{subject}"
        if path == "/api/sessions":
            return {"data": [{"id": key}] if kwargs["params"]["offset"] == 0 else []}
        if path == f"/api/sessions/agent%3Amain%3Asubagent%3A{subject}":
            return {"data": {"id": key}}
        if path == f"/api/sessions/agent%3Amain%3Asubagent%3A{subject}/messages":
            return {"data": [{"content": f"result for {subject}"}], "total": 1}
        return {"data": None}

    service._instance_service.get_caller_connection.side_effect = caller_connection
    service._instance_service.get_existing_caller_instance.side_effect = lambda user_id, bot_id, owner_id: {
        "status": "success", "ext": {"binding_id": f"caller-{user_id}"},
    }
    service._resolver.resolve_for_binding.side_effect = resolve_binding
    transport.invoke.side_effect = invoke
    for user in ("user1", "user2"):
        key = f"agent:main:subagent:{user}"
        result = await service.list_chat_sessions(user, "bot1", "owner1")
        assert [item["id"] for item in result["items"]] == [key]
        assert repo.get_owned_session(user, "bot1", "owner1", key) is None
        connected = await service.connect_chat_session(user, "bot1", "owner1", key)
        assert connected["connection"]["instance"] == f"caller-{user}"
        messages = await service.list_owned_chat_session_messages(user, "bot1", "owner1", key, 20)
        assert messages["items"] == [{"content": f"result for {user}"}]
    assert repo.get_owned_session("user1", "bot1", "owner1", "agent:main:subagent:user2") is None
    with pytest.raises(BotNotFoundError):
        await service.connect_chat_session("user1", "bot1", "owner1", "agent:main:subagent:user2")


@pytest.mark.asyncio
async def test_missing_caller_instance_does_not_provision_on_list(caller_service):
    service, transport, repo = caller_service
    service._instance_service.get_existing_caller_instance.return_value = None
    assert await service.list_chat_sessions("user1", "bot1", "owner1") == {"total": 0, "items": []}
    service._instance_service.get_caller_connection.assert_not_awaited()
    transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["init", "failed"])
async def test_unready_caller_is_not_rebuilt_by_discovery(caller_service, status):
    service, transport, repo = caller_service
    service._instance_service.get_existing_caller_instance.return_value = {"status": status, "ext": {"binding_id": "old-binding"}}
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result == {"total": 0, "items": [], "need_poll": True}
    service._instance_service.get_caller_connection.assert_not_awaited()
    transport.invoke.assert_not_awaited()


def test_existing_caller_lookup_only_reads_repository():
    from agentclaw.community.core.expert_chat.services.expert_chat_instance_service import ExpertChatInstanceService

    service = object.__new__(ExpertChatInstanceService)
    service._instance_repo = MagicMock()
    expected = {"status": "success", "ext": {"binding_id": "caller-binding"}}
    service._instance_repo.get_instance.return_value = expected
    assert service.get_existing_caller_instance("subject", "bot", "owner") is expected
    service._instance_repo.get_instance.assert_called_once_with("subject", "bot", "owner")
    service._instance_repo.upsert_instance.assert_not_called()
    service._instance_repo.update_instance.assert_not_called()


@pytest.mark.asyncio
async def test_discovers_all_real_runtime_ids_and_ignores_invalid_duplicates(caller_service):
    service, transport, repo = caller_service
    keys = ["ordinary-session", "agent:main:cron:job", "agent:main:subagent:child"]
    transport.invoke.side_effect = [{"data": [
        *({"id": key} for key in keys), {"id": keys[0]}, {"id": ""},
        {"id": "   "}, {"id": None}, {"id": 7}, {}, None,
    ]}, {"data": []}]
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert {item["id"] for item in result["items"]} == set(keys)
    assert result["total"] == 3
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_empty_teclaw_shared_runtime_never_reads_caller_ledger(caller_service):
    service, transport, repo = caller_service
    service._bot_repo.get_by_id_and_owner.return_value["active_engine"] = "teclaw"
    service._instance_service.get_existing_caller_instance.side_effect = AssertionError("shared runtime must not read Caller ledger")
    assert await service.list_chat_sessions("user1", "bot1", "owner1") == {"total": 0, "items": []}
    service._instance_service.get_caller_connection.assert_not_awaited()
    transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "hermes", "claude_code", "aicoding"])
@pytest.mark.parametrize("operation", ["list", "detail", "messages", "connect", "favorite", "rename", "delete", "clear"])
async def test_container_authorized_operations_never_touch_owned_table(caller_service, tmp_path, operation, engine):
    from agentclaw.community.core.repository.implementations.chat.expert_chat import ExpertChatRepository
    from tests.community.repository.chat.test_expert_chat_unified import _make_db

    service, transport, _ = caller_service
    repo = ExpertChatRepository(_make_db(tmp_path))
    repo.add_chat_bot("user1", "bot1", "owner1")
    service._repo = repo
    def forbidden(*args, **kwargs):
        raise AssertionError("independent Caller must not access owned session table")
    for name in ("get_owned_session", "list_owned_sessions", "add_owned_session", "delete_owned_session", "delete_all_owned_sessions"):
        setattr(repo, name, forbidden)
    service._bot_repo.get_by_id_and_owner.return_value["active_engine"] = engine
    key = "runtime-only"
    async def invoke(connection, method, path, **kwargs):
        assert connection["engine_type"] == engine
        if path == "/api/sessions":
            return {"data": [{"id": key}] if kwargs["params"]["offset"] == 0 else []}
        if path.endswith("/messages") and method == "GET":
            return {"data": [{"content": "real history"}], "total": 1}
        return {"data": {"id": key, "title": "renamed"}, "success": True}
    transport.invoke.side_effect = invoke
    if operation == "list":
        assert (await service.list_chat_sessions("user1", "bot1", "owner1"))["items"][0]["id"] == key
    elif operation == "detail":
        assert (await service.get_owned_chat_session("user1", "bot1", "owner1", key))["id"] == key
    elif operation == "messages":
        assert (await service.list_owned_chat_session_messages("user1", "bot1", "owner1", key, 20))["total"] == 1
    elif operation == "connect":
        assert (await service.connect_chat_session("user1", "bot1", "owner1", key))["session_key"] == key
        assert repo.get_session("user1", "bot1", "owner1") == key
    elif operation == "favorite":
        assert await service.set_owned_chat_session_favorite("user1", "bot1", "owner1", key, True)
    elif operation == "rename":
        assert (await service.update_owned_chat_session("user1", "bot1", "owner1", key, {"title": "renamed"}))["title"] == "renamed"
    elif operation == "delete":
        assert await service.delete_owned_chat_session("user1", "bot1", "owner1", key)
    else:
        assert await service.clear_owned_chat_session_messages("user1", "bot1", "owner1", key)
    service._instance_service.get_caller_connection.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "init", "failed"])
@pytest.mark.parametrize("operation", ["connect", "messages", "favorite", "rename", "delete", "clear"])
async def test_exact_operations_do_not_provision_missing_or_pending_caller(caller_service, status, operation):
    from agentclaw.community.core.expert_chat.errors import BotNotFoundError, ConnectionError
    service, transport, repo = caller_service
    service._instance_service.get_existing_caller_instance.return_value = None if status is None else {"status": status}
    calls = {
        "connect": lambda: service.connect_chat_session("user1", "bot1", "owner1", "runtime-only"),
        "messages": lambda: service.list_owned_chat_session_messages("user1", "bot1", "owner1", "runtime-only", 20),
        "favorite": lambda: service.set_owned_chat_session_favorite("user1", "bot1", "owner1", "runtime-only", True),
        "rename": lambda: service.update_owned_chat_session("user1", "bot1", "owner1", "runtime-only", {"title": "T"}),
        "delete": lambda: service.delete_owned_chat_session("user1", "bot1", "owner1", "runtime-only"),
        "clear": lambda: service.clear_owned_chat_session_messages("user1", "bot1", "owner1", "runtime-only"),
    }
    if operation == "connect" and status == "init":
        result = await calls[operation]()
        assert result["need_poll"] is True
        assert result["connection"] is None
    else:
        with pytest.raises(BotNotFoundError if status is None else ConnectionError):
            await calls[operation]()
    service._instance_service.get_caller_connection.assert_not_awaited()
    transport.invoke.assert_not_awaited()
    repo.get_owned_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["forged-other-container", "   "])
async def test_unrecognized_session_fails_before_mutation(caller_service, key):
    from agentclaw.community.core.expert_chat.errors import BotNotFoundError
    service, transport, repo = caller_service
    transport.invoke.return_value = {"data": {"id": "different-session"}}
    with pytest.raises(BotNotFoundError):
        await service.update_owned_chat_session("user1", "bot1", "owner1", key, {"title": "unsafe"})
    assert all(call.args[1] == "GET" for call in transport.invoke.await_args_list)
    assert all(call.args[0]["engine_type"] == "openclaw" for call in transport.invoke.await_args_list)
    repo.get_owned_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["messages", "favorite", "rename", "delete", "clear"])
async def test_runtime_operation_failures_are_sanitized(caller_service, caplog, operation):
    from agentclaw.community.core.expert_chat.errors import ConnectionError
    service, transport, repo = caller_service
    transport.invoke.side_effect = [{"data": {"id": "runtime-only"}}, RuntimeError("private-upstream-credential")]
    calls = {
        "messages": lambda: service.list_owned_chat_session_messages("user1", "bot1", "owner1", "runtime-only", 20),
        "favorite": lambda: service.set_owned_chat_session_favorite("user1", "bot1", "owner1", "runtime-only", True),
        "rename": lambda: service.update_owned_chat_session("user1", "bot1", "owner1", "runtime-only", {"title": "T"}),
        "delete": lambda: service.delete_owned_chat_session("user1", "bot1", "owner1", "runtime-only"),
        "clear": lambda: service.clear_owned_chat_session_messages("user1", "bot1", "owner1", "runtime-only"),
    }
    with caplog.at_level("INFO"), pytest.raises(ConnectionError) as caught:
        await calls[operation]()
    assert "private-upstream-credential" not in str(caught.value) + caplog.text
    assert "caller_runtime_session_failed" in caplog.text
    assert "duration_ms=" in caplog.text
    repo.get_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_operation_boundary_excludes_nested_credentials_and_preserves_business_fields(caller_service, caplog):
    service, transport, repo = caller_service
    transport.invoke.side_effect = [{"data": {"id": "runtime-only"}}, {
        "data": {"id": "runtime-only", "title": "visible", "nested": [{"token": "<response-placeholder>"}, {"Cookie": "<cookie-placeholder>"}]},
    }]
    with caplog.at_level("INFO"):
        result = await service.update_owned_chat_session("user1", "bot1", "owner1", "runtime-only", {
            "title": "visible", "nested": [{"Authorization": "<request-placeholder>"}, {"session": "<session-placeholder>"}, {"password": "<password-placeholder>"}],
        })
    assert result["title"] == "visible"
    assert "visible" in caplog.text
    assert "nested" not in caplog.text
    assert not any(secret in caplog.text for secret in ("<request-placeholder>", "<response-placeholder>", "<cookie-placeholder>", "<session-placeholder>", "<password-placeholder>"))


@pytest.mark.asyncio
async def test_runtime_404_is_not_found_without_owned_or_pointer_write(caller_service):
    from agentclaw.community.core.expert_chat.errors import BotNotFoundError
    service, transport, repo = caller_service
    transport.invoke.side_effect = RuntimeError("Adapter returned 404 secret-message")
    with pytest.raises(BotNotFoundError):
        await service.connect_chat_session("user1", "bot1", "owner1", "missing")
    repo.get_owned_session.assert_not_called()
    repo.save_session.assert_not_called()


@pytest.mark.asyncio
async def test_teclaw_shared_sessions_still_require_owned_record(caller_service):
    from agentclaw.community.core.expert_chat.errors import BotNotFoundError
    service, transport, repo = caller_service
    service._bot_repo.get_by_id_and_owner.return_value["active_engine"] = "teclaw"
    repo.get_owned_session.return_value = None
    with pytest.raises(BotNotFoundError):
        await service.list_owned_chat_session_messages("user1", "bot1", "owner1", "runtime-only", 20)
    repo.get_owned_session.assert_called_once_with("user1", "bot1", "owner1", "runtime-only")
    service._instance_service.get_existing_caller_instance.assert_not_called()
    transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_reads_friend_caller_without_changing_subject(caller_service):
    service, transport, repo = caller_service
    transport.invoke.side_effect = [{"data": {"id": "friend-session"}}, {"data": [], "total": 0}]
    await service.list_owned_chat_session_messages("user1", "bot1", "owner1", "friend-session", 20,
        bcn_friend_authorized=True, actor_user_id="owner1")
    service._instance_service.get_existing_caller_instance.assert_called_once_with("user1", "bot1", "owner1")
    service._resolver.resolve_for_binding.assert_called_once_with("caller-binding", "user1", bot_id="bot1")
    repo.get_owned_session.assert_not_called()
    repo.list_owned_sessions.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,friend_authorized", [("stranger", True), ("owner1", False)])
async def test_other_actor_cannot_read_subject_caller(caller_service, actor, friend_authorized):
    from agentclaw.community.core.expert_chat.errors import ChatPermissionError
    service, transport, repo = caller_service
    with pytest.raises(ChatPermissionError):
        await service.list_owned_chat_session_messages("user1", "bot1", "owner1", "friend-session", 20,
            bcn_friend_authorized=friend_authorized, actor_user_id=actor)
    service._instance_service.get_existing_caller_instance.assert_not_called()
    transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_caller_detail_is_not_ready(caller_service):
    from agentclaw.community.core.expert_chat.errors import ConnectionError
    service, transport, repo = caller_service
    service._instance_service.get_existing_caller_instance.return_value = {"status": "init"}
    with pytest.raises(ConnectionError):
        await service.get_owned_chat_session("user1", "bot1", "owner1", "runtime-only")
    transport.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["get", "create"])
async def test_default_session_entrypoints_preserve_original_registration(caller_service, entrypoint):
    service, transport, repo = caller_service
    service._create_session = AsyncMock(return_value="created-session")
    if entrypoint == "get":
        result = await service.get_chat_session("user1", "bot1", "owner1")
    else:
        result = await service.create_chat_session("user1", "bot1", "owner1")
    assert result["session_key"] == "created-session"
    repo.save_session.assert_called_once_with("user1", "bot1", "owner1", "created-session")

    repo.add_owned_session.assert_called_once_with("user1", "bot1", "owner1", "created-session")


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [True, False])
async def test_default_session_reuse_keeps_original_owned_index_behavior(caller_service, exists):
    service, transport, repo = caller_service
    repo.get_session.return_value = "previous-session"
    service._check_session_exists = AsyncMock(return_value=exists)
    service._create_session = AsyncMock(return_value="new-session")
    result = await service.get_chat_session("user1", "bot1", "owner1")
    expected = "previous-session" if exists else "new-session"
    assert result["session_key"] == expected
    repo.add_owned_session.assert_called_once_with("user1", "bot1", "owner1", expected)
    if exists:
        service._create_session.assert_not_awaited()
        repo.delete_owned_session.assert_not_called()
    else:
        repo.delete_owned_session.assert_called_once_with("user1", "bot1", "owner1", "previous-session")
