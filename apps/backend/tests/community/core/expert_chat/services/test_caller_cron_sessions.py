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
async def test_empty_ownership_discovers_cron_and_registers_for_connect(caller_service):
    service, transport, repo = caller_service
    key = "agent:bot1:cron:job1"
    transport.invoke.return_value = {"data": [{"id": key, "title": "Scheduled task",
        "gmt_created": "2026-11-11T10:00:00Z", "gmt_modified": "2026-11-12T10:00:00Z"}]}
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result["total"] == 1
    assert result["items"][0]["id"] == key
    repo.add_owned_session.assert_called_once_with("user1", "bot1", "owner1", key)
    repo.get_owned_session.return_value = {"session_key": key}
    connected = await service.connect_chat_session("user1", "bot1", "owner1", key)
    assert connected["session_key"] == key


@pytest.mark.asyncio
async def test_pages_deduplicate_and_filter_before_client_pagination(caller_service, monkeypatch):
    service, transport, repo = caller_service
    monkeypatch.setattr("agentclaw.community.core.expert_chat.services.expert_chat_cron_sessions._PAGE_SIZE", 2)
    first = {"id": "agent:main:cron:first", "gmt_created": "2026-11-10T10:00:00Z"}
    second = {"id": "agent:main:cron:second:run:actual", "gmt_created": "2026-11-11T10:00:00Z"}
    transport.invoke.side_effect = [{"data": [first, second]}, {"data": [first, {"id": "agent:main:chat:foreign"}]}, {"data": []}]
    result = await service.list_chat_sessions("user1", "bot1", "owner1", limit=1, offset=1)
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [first["id"]]
    assert repo.add_owned_session.call_count == 2
    assert [call.kwargs["params"]["offset"] for call in transport.invoke.await_args_list] == [0, 2, 4]
    assert all("user_id" not in call.kwargs["params"] for call in transport.invoke.await_args_list)


@pytest.mark.asyncio
async def test_exact_key_registers_only_requested_actual_cron(caller_service):
    service, transport, repo = caller_service
    key = "agent:main:cron:job:run:actual"
    transport.invoke.return_value = {"data": [
        {"id": "agent:main:cron:other"}, {"id": key}, None,
        {"id": "agent:main:cron:job:run:actual:extra"}, {"id": 1},
    ]}
    result = await service.list_chat_sessions("user1", "bot1", "owner1", session_key=key)
    assert [item["id"] for item in result["items"]] == [key]
    repo.add_owned_session.assert_called_once_with("user1", "bot1", "owner1", key)
    detail = await service.get_owned_chat_session("user1", "bot1", "owner1", key)
    assert detail["id"] == key
    repo.get_owned_session.return_value = {"session_key": key}
    transport.invoke.return_value = {"data": [{"content": "cron result"}], "total": 1}
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
async def test_other_engine_does_not_discover(caller_service):
    service, transport, repo = caller_service
    service._resolver.resolve_for_binding.return_value.conn_info["engine_type"] = "teclaw"
    result = await service.list_chat_sessions("user1", "bot1", "owner1")
    assert result == {"total": 0, "items": []}
    transport.invoke.assert_not_awaited()
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
    assert "caller_cron_sessions_request" in caplog.text
    assert "caller_cron_sessions_failed" in caplog.text
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
    assert "caller_cron_sessions_request" in caplog.text
    assert "caller_cron_sessions_success" in caplog.text
    assert key in caplog.text
    assert "private-connection-credential" not in caplog.text


@pytest.mark.asyncio
async def test_favorite_list_includes_discovered_cron(caller_service):
    service, transport, repo = caller_service
    key = "agent:main:cron:job"
    transport.invoke.side_effect = [{"data": [{"id": key}]}, {"data": []}, {"data": [{"id": key}]}]
    result = await service.list_chat_sessions("user1", "bot1", "owner1", favorite_only=True)
    assert [item["id"] for item in result["items"]] == [key]


@pytest.mark.asyncio
async def test_repeated_page_stops_and_existing_cron_is_not_reregistered(caller_service, monkeypatch):
    service, transport, repo = caller_service
    monkeypatch.setattr("agentclaw.community.core.expert_chat.services.expert_chat_cron_sessions._PAGE_SIZE", 1)
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
async def test_discovery_requires_actual_caller_binding_marker(caller_service):
    service, transport, repo = caller_service
    for bot in [dict(ACTIVE_BOT, bot_type="service", call_type="caller"),
                dict(ACTIVE_BOT, bot_type="service", call_type="caller", _caller_instance_binding_id="other-binding")]:
        assert await service._discover_caller_cron_sessions(bot, {"engine_type": "openclaw"}, "user1", "owner1", [], None) == {}
    transport.invoke.assert_not_awaited()
    repo.add_owned_session.assert_not_called()


@pytest.mark.asyncio
async def test_ownership_write_failure_propagates(caller_service):
    service, transport, repo = caller_service
    transport.invoke.return_value = {"data": [{"id": "agent:main:cron:job"}]}
    repo.add_owned_session.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.list_chat_sessions("user1", "bot1", "owner1")


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
    assert [item["id"] for item in result["items"]] == [key]
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
async def test_two_subjects_discover_only_their_instance_and_persist_real_ownership(caller_service, tmp_path):
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
        key = f"agent:main:cron:{subject}"
        if path == "/api/sessions":
            return {"data": [{"id": key}] if kwargs["params"]["offset"] == 0 else []}
        assert path == f"/api/sessions/agent%3Amain%3Acron%3A{subject}/messages"
        return {"data": [{"content": f"result for {subject}"}], "total": 1}

    service._instance_service.get_caller_connection.side_effect = caller_connection
    service._instance_service.get_existing_caller_instance.side_effect = lambda user_id, bot_id, owner_id: {
        "status": "success", "ext": {"binding_id": f"caller-{user_id}"},
    }
    service._resolver.resolve_for_binding.side_effect = resolve_binding
    transport.invoke.side_effect = invoke
    for user in ("user1", "user2"):
        key = f"agent:main:cron:{user}"
        result = await service.list_chat_sessions(user, "bot1", "owner1")
        assert [item["id"] for item in result["items"]] == [key]
        assert repo.get_owned_session(user, "bot1", "owner1", key) is not None
        connected = await service.connect_chat_session(user, "bot1", "owner1", key)
        assert connected["connection"]["instance"] == f"caller-{user}"
        messages = await service.list_owned_chat_session_messages(user, "bot1", "owner1", key, 20)
        assert messages["items"] == [{"content": f"result for {user}"}]
    assert repo.get_owned_session("user1", "bot1", "owner1", "agent:main:cron:user2") is None
    with pytest.raises(BotNotFoundError):
        await service.connect_chat_session("user1", "bot1", "owner1", "agent:main:cron:user2")


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


@pytest.mark.parametrize("engine,expected", [("openclaw", True), ("teclaw", False), ("hermes", False), ("claude_code", False), ("aicoding", False)])
def test_registered_strategy_declares_cron_discovery(engine, expected):
    from agentclaw.community.core.bot_management.engines import BotProvisioningContext, resolve_provisioning

    ctx = BotProvisioningContext(active_engine=engine, template_type=None, bot_id="bot1", owner_id="owner1", bot_type="service", template_config=None)
    ctx, strategy = resolve_provisioning(bot_id="bot1", owner_id="owner1", bot_type="service", active_engine=engine, template_type="", template_config={})
    assert strategy.supports_caller_cron_session_discovery(ctx) is expected
