"""Publication upgrades submit a restart only for a newly created draft."""
from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishService


_SENTINEL = "credential" + "-sentinel"
_CHAIN_SENTINEL = "credential" + "-chain-sentinel"


def publication(record_id, status, **fields):
    return BotPublishRecord(
        id=record_id, source_bot_pk=100, source_bot_id="source-bot",
        publish_bot_id="published-bot", name="Bot", owner_id="entity",
        status=status, version=record_id, last_pub_id=record_id - 1,
        env="dev", permission_owner="owner", **fields,
    )


@pytest.fixture
def deps():
    repo = Mock()
    original = publication(1, PublishStatus.SUCCESS)
    draft = publication(2, PublishStatus.DRAFT)
    repo.get_by_id.return_value = original
    repo.get_by_last_pub_id.return_value = None
    bot_repo = Mock()
    bot = {
        "id": 100, "bot_id": "source-bot", "owner_id": "current-owner",
        "entity_id": "entity", "active_engine": "openclaw", "env": "dev",
    }
    bot_repo.get_by_pk.return_value = bot
    lifecycle = Mock()
    lifecycle.is_teclaw_bot.return_value = False
    lifecycle.restart_bot.return_value = {"status": "PENDING", "binding_id": 77}
    service = BotPublishService(
        bot_publish_repo=repo, bot_repo=bot_repo,
        publish_flow_service_provider=Mock(), bot_service=lifecycle,
        device_binding_repo=Mock(), bcn_service=Mock(), quality_task_service=Mock(),
        publish_operation_repo=Mock(), task_queue_service=Mock(),
        bot_process_registry=Mock(), common_config_service=Mock(),
    )
    service._get_next_version = Mock(return_value=2)
    service.create_publish = Mock(return_value=draft)
    return service, repo, bot_repo, lifecycle, original, draft


def test_new_draft_restarts_source_with_current_owner(deps, caplog):
    service, _, bot_repo, lifecycle, _, draft = deps
    lifecycle.restart_bot.return_value.update({"secret": _SENTINEL})
    with caplog.at_level("INFO"):
        assert service.upgrade_publish(1, "collaborator") is draft
    bot_repo.get_by_pk.assert_called_once_with(100)
    lifecycle.restart_bot.assert_called_once_with(
        bot_id="source-bot", user_id="current-owner",
    )
    assert "draft_restart.request" in caplog.text
    assert "draft_restart.response" in caplog.text
    assert _SENTINEL not in caplog.text
    assert "current-owner" in caplog.text
    assert "'original_publish_id': 1" in caplog.text
    assert "'draft_publish_id': 2" in caplog.text
    assert "status=PENDING binding_id=77" in caplog.text


@pytest.mark.parametrize("status", [PublishStatus.SUCCESS, PublishStatus.UPGRADED])
def test_existing_successor_does_not_restart(deps, status, caplog):
    service, repo, _, lifecycle, original, draft = deps
    original.status = status
    repo.get_by_last_pub_id.return_value = draft
    with caplog.at_level("INFO"):
        assert service.upgrade_publish(1, "collaborator") is draft
    lifecycle.restart_bot.assert_not_called()
    service.create_publish.assert_not_called()
    assert "draft_restart.skipped reason=existing_successor" in caplog.text
    assert "original_publish_id=1 draft_publish_id=2 source_bot_pk=100" in caplog.text
    assert "source_bot_id=source-bot operator_id=collaborator" in caplog.text


def test_teclaw_draft_is_created_without_restart(deps, caplog):
    service, _, bot_repo, lifecycle, _, draft = deps
    bot_repo.get_by_pk.return_value["active_engine"] = "teclaw"
    lifecycle.is_teclaw_bot.return_value = True
    with caplog.at_level("INFO"):
        assert service.upgrade_publish(1, "collaborator") is draft
    lifecycle.restart_bot.assert_not_called()
    assert "teclaw_unsupported" in caplog.text


@pytest.mark.parametrize("result", [RuntimeError("token=" + _SENTINEL), {"success": False}, {"status": "FAILED"}])
def test_restart_failure_retains_draft_and_does_not_leak_credentials(deps, result, caplog):
    from agentclaw.community.core.service_bot.services.publish_exceptions import UpgradeDraftRestartError

    service, repo, _, lifecycle, _, draft = deps
    if isinstance(result, Exception):
        lifecycle.restart_bot.side_effect = result
    else:
        lifecycle.restart_bot.return_value = result
    with caplog.at_level("INFO"), pytest.raises(UpgradeDraftRestartError) as raised:
        service.upgrade_publish(1, "collaborator")
    assert raised.value.draft_record is draft
    assert _SENTINEL not in caplog.text
    assert "draft_restart.failure" in caplog.text
    assert "draft_retained=true" in caplog.text
    assert "'source_bot_pk': 100" in caplog.text
    assert "'draft_publish_id': 2" in caplog.text
    assert _SENTINEL not in str(raised.value)
    repo.get_by_last_pub_id.return_value = draft
    assert service.upgrade_publish(1, "collaborator") is draft
    assert lifecycle.restart_bot.call_count == 1


@pytest.mark.parametrize("bot", [None, {"id": 100, "bot_id": "other", "owner_id": "owner"}])
def test_source_missing_or_mismatched_never_restarts_another_bot(deps, bot):
    from agentclaw.community.core.service_bot.services.publish_exceptions import UpgradeDraftRestartError

    service, _, bot_repo, lifecycle, _, draft = deps
    bot_repo.get_by_pk.return_value = bot
    with pytest.raises(UpgradeDraftRestartError) as raised:
        service.upgrade_publish(1, "collaborator")
    assert raised.value.draft_record is draft
    lifecycle.restart_bot.assert_not_called()
    bot_repo.get_by_id_and_owner.assert_not_called()


@pytest.mark.asyncio
async def test_openapi_error_logging_does_not_include_provider_exception_chain(deps, caplog):
    from starlette.requests import Request

    from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors
    from agentclaw.community.core.service_bot.services.service_publication_facade import ServicePublicationFacade

    service, _, bot_repo, lifecycle, original, _ = deps
    lifecycle.restart_bot.side_effect = RuntimeError("token=" + _CHAIN_SENTINEL)
    facade = ServicePublicationFacade.__new__(ServicePublicationFacade)
    facade._publish_service = service
    facade._resolve_publication = Mock(return_value=(bot_repo.get_by_pk.return_value, original, None))

    @envelope_errors
    async def upgrade(request):
        return facade.upgrade_publication(
            "source-bot", 1, actor_id="collaborator", owner_id="current-owner",
        )

    request = Request({"type": "http", "method": "POST", "path": "/openapi/v1/bots/source-bot/lifecycle/1/upgrade", "headers": []})
    with caplog.at_level("INFO"):
        response = await upgrade(request=request)
    assert response.status_code == 502
    assert _CHAIN_SENTINEL not in caplog.text
    assert "draft_restart.failure" in caplog.text
