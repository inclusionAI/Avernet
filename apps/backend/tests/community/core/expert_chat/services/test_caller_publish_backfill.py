"""Existing Caller session access reuses publication progress writeback."""
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.community.core.expert_chat.services.test_expert_chat_instance_service import _make_service as make_instance
from tests.community.core.expert_chat.services.test_expert_chat_service import ACTIVE_BOT, _make_service as make_chat


@pytest.fixture
def allocated(mock_repository, mock_bot_repo, mock_device_provider):
    instance, repo, baas, _, _, bindings, builds, identity, _, _ = make_instance()
    row = {"status": "init", "ext": {"bot_uuid": "caller-uuid", "binding_id": 12,
           "baas_publish_id": 31, "service_bot_publish_id": 5}}
    repo.get_instance.side_effect = lambda *args: deepcopy(row)
    def update(user_id, bot_id, owner_id, status=None, ext=None):
        if status is not None:
            row["status"] = status
        if ext is not None:
            row["ext"] = deepcopy(ext)
        return True
    repo.update_instance.side_effect = update
    mock_bot_repo.get_by_id_and_owner.return_value = dict(ACTIVE_BOT, bot_type="service", call_type="caller")
    mock_repository.list_chat_bots.return_value = [{"bot_id": "bot1", "owner_id": "owner1"}]
    transport = MagicMock(invoke=AsyncMock(return_value={"data": []}))
    chat = make_chat(mock_repository, mock_bot_repo, mock_device_provider,
                     mock_instance_service=instance, mock_transport=transport)
    chat._online_chat_binding = MagicMock(return_value="online-binding")
    return chat, instance, row, repo, baas, bindings, builds, identity, transport


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "connect"])
async def test_real_entrypoints_reconcile_progress(allocated, operation):
    chat, _, row, repo, baas, bindings, builds, _, transport = allocated
    baas.get_publish_progress.side_effect = [{"status": "ACTIVE"}, {"status": "SUCCESS"}]
    async def invoke():
        if operation == "list":
            return await chat.list_chat_sessions("user1", "bot1", "owner1")
        return await chat.connect_chat_session("user1", "bot1", "owner1", "existing")
    assert (await invoke())["need_poll"] is True
    assert row["status"] == "init"
    assert bindings.update_status.call_args.kwargs == {"binding_id": 12, "status": "PENDING"}
    transport.invoke.assert_not_awaited()
    transport.invoke.return_value = {"data": {"id": "existing"}} if operation == "connect" else {"data": []}
    ready = await invoke()
    assert not ready.get("need_poll", False)
    assert row["status"] == "success"
    assert bindings.update_status.call_args.kwargs == {"binding_id": 12, "status": "ACTIVE"}
    assert baas.get_publish_progress.call_count == 2
    assert baas.get_publish_progress.call_args.kwargs == {"publish_id": 31, "include_devices": True}
    assert repo.update_instance.call_args.kwargs["user_id"] == "user1"
    builds.release_async.assert_not_called()
    builds.upgrade_async.assert_not_called()
    if operation == "connect":
        assert ready["session_key"] == "existing"
        assert transport.invoke.await_args.args[2] == "/api/sessions/existing"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "failed", "release"])
async def test_terminal_instance_never_polls(allocated, status):
    _, service, row, repo, baas, _, builds, _, _ = allocated
    row["status"] = status
    assert (await service.refresh_existing_caller_instance("user1", "bot1", "owner1"))["status"] == status
    baas.get_publish_progress.assert_not_called()
    repo.update_instance.assert_not_called()
    builds.release_async.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["row", "bot_uuid", "binding_id", "baas_publish_id"])
async def test_incomplete_allocation_is_not_provisioned(allocated, missing):
    _, service, row, repo, baas, _, builds, _, _ = allocated
    if missing == "row":
        repo.get_instance.side_effect = None
        repo.get_instance.return_value = None
    else:
        row["ext"].pop(missing)
    await service.refresh_existing_caller_instance("user1", "bot1", "owner1")
    baas.get_publish_progress.assert_not_called()
    repo.update_instance.assert_not_called()
    builds.release_async.assert_not_called()
    builds.upgrade_async.assert_not_called()


@pytest.mark.asyncio
async def test_failed_publish_is_written_once_without_revival(allocated):
    _, service, row, _, baas, bindings, builds, _, _ = allocated
    baas.get_publish_progress.return_value = {"status": "FAILED"}
    await service.refresh_existing_caller_instance("user1", "bot1", "owner1")
    assert row["status"] == "failed"
    assert bindings.update_status.call_args.kwargs == {"binding_id": 12, "status": "FAILED"}
    await service.refresh_existing_caller_instance("user1", "bot1", "owner1")
    assert baas.get_publish_progress.call_count == 1
    builds.upgrade_async.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["user1", "owner1"])
async def test_list_initializes_only_the_subject_iam(allocated, actor, caplog):
    chat, _, row, _, baas, _, _, identity, _ = allocated
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    identity.exchange_caller_identity.side_effect = lambda **kwargs: row["status"] == "init" or pytest.fail("ready before identity")
    caplog.set_level("INFO")
    await chat.list_chat_sessions("user1", "bot1", "owner1", iam_token="test-iam", actor_user_id=actor, bcn_friend_authorized=True)
    assert row["status"] == "success"
    if actor == "user1":
        args = identity.exchange_caller_identity.call_args.kwargs
        assert args["iam_token"] == "test-iam" and args["caller_user_id"] == "user1"
        assert args["binding_id"] == 12 and args["publish_id"] == 5
        assert args["force_runtime_refresh"] is True
    else:
        identity.exchange_caller_identity.assert_not_called()
    assert "test-iam" not in caplog.text
