"""Published-only friend addressing is identical across the chat backends."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from tests.community.core.expert_chat.services.test_expert_chat_uses_resolver import _make_service


def service(*, kind="service", call_type="owner", records=None):
    resolver = MagicMock()
    svc = _make_service(resolver=resolver)
    bot = {"id": 5, "bot_id": "b1", "owner_id": "owner", "entity_id": "owner",
           "bot_type": kind, "call_type": call_type, "status": "ACTIVE", "binding_id": 11}
    svc._find_chat_bot = MagicMock(return_value=bot)
    svc._publish_repository.list_by_source_bot.return_value = records or []
    return svc, resolver, bot


def release(status="success", **bindings):
    return SimpleNamespace(id=1, status=status, ext={"binding": bindings})


@pytest.mark.parametrize("stage", ["draft", "online"])
def test_friend_services_resolve_online_even_with_newer_draft(stage):
    svc, _, _ = service(records=[release("draft"), release(online=71)])
    assert svc.resolve_chat_stage("b1", "owner", stage) == "online"
    assert svc._publish_repository.list_by_source_bot.call_args.args[0] == 5


@pytest.mark.parametrize("stage", ["draft", "online", "verify"])
def test_draft_only_service_never_falls_back_to_workspace(stage):
    svc, resolver, _ = service(records=[release("draft")])
    with pytest.raises(EngineStageNotLiveError):
        svc.resolve_chat_stage("b1", "owner", stage)
    resolver.resolve_for_binding.assert_not_called()


def test_service_friend_cannot_address_verify():
    svc, _, _ = service(records=[release("validating", verify=72), release(online=71)])
    with pytest.raises(EngineStageNotLiveError):
        svc.resolve_chat_stage("b1", "owner", "verify")


def test_personal_friend_keeps_draft_and_has_no_published_stage():
    svc, _, _ = service(kind="personal")
    assert svc.resolve_chat_stage("b1", "owner", "draft") == "draft"
    for stage in ("verify", "online"):
        with pytest.raises(EngineStageNotLiveError):
            svc.resolve_chat_stage("b1", "owner", stage)
    svc._publish_repository.list_by_source_bot.assert_not_called()


@pytest.mark.asyncio
async def test_shared_chat_connects_online_binding_not_workspace():
    svc, resolver, bot = service(records=[release(online=71)])
    await svc._prepare_chat_connection(bot, "friend", "owner", None, bcn_friend_authorized=True)
    resolver.resolve_for_binding.assert_called_once_with(71, "friend", bot_id="b1")


@pytest.mark.asyncio
async def test_caller_chat_checks_publication_before_using_friends_instance():
    svc, resolver, bot = service(call_type="caller", records=[release(online=71)])
    svc._instance_service.get_caller_connection = AsyncMock(return_value={
        "instance": {"ext": {"binding_id": 81}},
    })
    await svc._prepare_chat_connection(bot, "friend", "owner", None, bcn_friend_authorized=True)
    resolver.resolve_for_binding.assert_called_once_with(81, "friend", bot_id="b1")
    svc._publish_repository.list_by_source_bot.return_value = [release("draft")]
    svc._instance_service.get_caller_connection.reset_mock()
    with pytest.raises(EngineStageNotLiveError):
        await svc._prepare_chat_connection(bot, "friend", "owner", None, bcn_friend_authorized=True)
    svc._instance_service.get_caller_connection.assert_not_awaited()


def test_published_service_can_be_added_without_draft_binding():
    svc, _, bot = service(records=[release(online=71)])
    bot["binding_id"] = None
    svc.add_chat_bot("friend", "b1", "owner")
    svc._repo.add_chat_bot.assert_called_once_with("friend", "b1", "owner")


def test_caller_artifact_uses_same_source_pk_and_ignores_newer_draft():
    from tests.community.core.expert_chat.services.test_expert_chat_instance_service import _make_service as make_instance_service

    bots = MagicMock()
    bots.get_by_id_and_owner.return_value = {
        "id": 17, "bot_id": "b1", "owner_id": "owner", "entity_id": "entity-1",
    }
    svc, _, _, publications, *_ = make_instance_service(bot_repo=bots)
    online = SimpleNamespace(id=21, status="success", ext={"migration_path": "published-artifact"})
    publications.list_by_source_bot.return_value = [release("draft"), online]
    record, path = svc._resolve_build_artifact("b1", "owner")
    assert record is online
    assert path == "published-artifact"
    assert publications.list_by_source_bot.call_args.args[0] == 17
    publications.get_by_publish_bot_id.assert_not_called()
