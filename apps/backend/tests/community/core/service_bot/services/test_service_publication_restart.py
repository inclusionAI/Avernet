"""Lifecycle HTTP responses preserve restart identity and serving state."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.service_publications.router import (
    get_lifecycle,
    restart_lifecycle,
)
from agentclaw.community.adapters.http.openapi_v1.service_publications.schemas import (
    LifecycleRestartRequest,
    ServicePublicationOperation,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from tests.community.core.service_bot.services.test_service_publication_facade import (
    deps as deps,
    record,
)

from tests.community.core.service_bot.services.test_publish_restart_veto import (
    published as published,
)
from tests.community.core.bot_management.aicoding.test_restart_backup import response
from tests.community.core.bot_management.aicoding.test_restart_fastdisk import executor

OPERATION = "a" * 32


def request():
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_restart_receipt_preserves_operation_identity_without_provider_details(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    deps.bot_repo.get_by_id_and_owner.return_value["active_engine"] = "aicoding"
    row = record(1, PublishStatus.SUCCESS)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.flow_service.restart_bot.return_value = {
        "success": True, "ext": {"aicoding": {"restart": {"operation_id": OPERATION, "status": "RUNNING", "phase": "QUEUED", "message": None}}},
        "bot_uuid": "provider-private-id", "message": "provider-private-message",
    }

    response = await restart_lifecycle(
        "bot-1", LifecycleRestartRequest(stage="online"), request(), "owner", "owner", deps.facade,
    )

    payload = response.model_dump()["data"]
    assert response.code == 202000
    assert payload["ext"]["aicoding"]["restart"]["operation_id"] == OPERATION
    assert payload["accepted"] is True
    assert "provider-private" not in str(payload)
    assert row.status == PublishStatus.SUCCESS.value
    deps.flow_service.restart_bot.assert_called_once_with(1, "owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["aicoding", "claude_code"])
@pytest.mark.parametrize("serving_status, public_status, stage", [
    (PublishStatus.SUCCESS, "running", "online"),
    (PublishStatus.VALIDATING, "prestable", "verify"),
])
async def test_lifecycle_reports_backup_veto_without_changing_serving_state(
    deps, monkeypatch, engine, serving_status, public_status, stage,
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    deps.bot_repo.get_by_id_and_owner.return_value["active_engine"] = engine
    row = record(1, serving_status, ext={
        "binding": {stage: 42}, "unrelated": {"keep": [1, 2]},
        "restart": {
            "engine": engine, "operation_id": OPERATION, "status": "FAILED",
            "phase": "BACKING_UP", "message": "private-diagnostic\nprovider-private-id",
        },
    })
    original_ext = deepcopy(row.ext)
    deps.publish_repo.list_by_source_bot.return_value = [row]

    response = await get_lifecycle("bot-1", request(), "owner", "owner", deps.facade)

    payload = response.model_dump()["data"]["items"][0]
    assert response.code == 200000
    assert payload["status"] == public_status
    assert payload["internal_status"] == serving_status.value
    assert payload["deployment"] is None
    assert payload["ext"]["aicoding"]["restart"] == {
        "operation_id": OPERATION, "status": "FAILED", "phase": "BACKING_UP",
        "message": "快盘备份失败，本次重启已终止，旧容器未销毁；请查看 Bot 日志。",
    }
    assert "private-diagnostic" not in str(payload)
    assert "provider-private-id" not in str(payload)
    assert row.ext == original_ext
    assert not deps.flow_service.mock_calls  # Query adds no workflow/provider I/O.


@pytest.mark.asyncio
@pytest.mark.parametrize("status, phase", [
    ("RUNNING", "QUEUED"), ("RUNNING", "BACKING_UP"),
    ("RUNNING", "WAITING_READY"), ("SUCCEEDED", "WAITING_READY"),
    ("FAILED", "WAITING_READY"),
])
async def test_lifecycle_projects_task_progress_without_reading_an_old_workflow(
    deps, monkeypatch, status, phase,
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    state = {"engine": "aicoding", "operation_id": OPERATION, "status": status,
             "phase": phase, "message": "provider-private-diagnostic"}
    row = record(1, PublishStatus.SUCCESS, ext={"restart": state})
    deps.publish_repo.list_by_source_bot.return_value = [row]

    response = await get_lifecycle("bot-1", request(), "owner", "owner", deps.facade)

    payload = response.model_dump()["data"]["items"][0]
    assert payload["status"] == "running"
    assert payload["ext"]["aicoding"]["restart"] == {
        "operation_id": OPERATION, "status": status, "phase": phase,
        "message": "本次重启失败，请查看 Bot 日志。" if status == "FAILED" else None,
    }
    assert state["message"] == "provider-private-diagnostic"
    assert not deps.flow_service.mock_calls
    assert not deps.device_service.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["openclaw", "hermes", "moltis", "teclaw", None])
async def test_other_engines_do_not_get_a_restart_view(deps, monkeypatch, engine):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    deps.bot_repo.get_by_id_and_owner.return_value["active_engine"] = engine
    row = record(1, PublishStatus.SUCCESS, ext={"restart": {
        "engine": engine, "operation_id": OPERATION,
        "phase": "BACKING_UP", "status": "FAILED", "message": "foreign error",
    }})
    deps.publish_repo.list_by_source_bot.return_value = [row]

    response = await get_lifecycle("bot-1", request(), "owner", "owner", deps.facade)

    payload = response.model_dump()["data"]["items"][0]
    assert payload["status"] == "running"
    assert payload["deployment"] is None
    assert "restart" not in payload
    assert "ext" not in payload
    assert not deps.flow_service.mock_calls
    assert not deps.device_service.mock_calls


@pytest.mark.asyncio
async def test_each_version_card_keeps_its_own_restart_result(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    online = record(1, PublishStatus.SUCCESS, ext={"restart": {
        "engine": "aicoding", "operation_id": OPERATION,
        "status": "FAILED", "phase": "BACKING_UP",
    }})
    draft = record(2, PublishStatus.DRAFT)
    deps.publish_repo.list_by_source_bot.return_value = [online, draft]

    response = await get_lifecycle("bot-1", request(), "owner", "owner", deps.facade)

    cards = response.model_dump()["data"]["items"]
    assert [card["publication_id"] for card in cards] == [2, 1]
    assert "restart" not in cards[0]
    assert "ext" not in cards[0]
    assert cards[1]["status"] == "running"
    assert cards[1]["ext"]["aicoding"]["restart"]["operation_id"] == OPERATION
    assert "restart_publish" in cards[1]["available_actions"]


@pytest.mark.parametrize("action", ["restart_publish", "publish_online", "offline"])
def test_legacy_operation_json_does_not_gain_null_fields(action):
    original = {
        "bot_id": "bot-1", "publication_id": 1, "action": action,
        "accepted": True, "operation_status": "pending", "approval": None,
    }
    assert ServicePublicationOperation.model_validate(original).model_dump() == original


@pytest.mark.asyncio
async def test_real_backup_veto_is_visible_through_the_public_lifecycle(deps, published, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    flow, _, baas, build, row = published
    row.source_bot_pk, row.source_bot_id, row.owner_id = 10, "bot-1", "owner"
    flow._bot_service.get_bot_by_address.return_value.update(
        bot_id="bot-1", owner_id="owner", entity_id="owner",
    )
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.flow_service.restart_bot.side_effect = flow.restart_bot
    accepted = await restart_lifecycle(
        "bot-1", LifecycleRestartRequest(stage="online"), request(), "owner", "owner", deps.facade,
    )
    operation_id = accepted.data.ext["aicoding"]["restart"]["operation_id"]
    before = await get_lifecycle("bot-1", request(), "owner", "owner", deps.facade)
    assert before.data.items[0].ext["aicoding"]["restart"]["operation_id"] == operation_id
    assert before.data.items[0].ext["aicoding"]["restart"]["status"] == "RUNNING"
    execute = executor([response("failed", operation_id=operation_id)])
    baas.post_bots_api = Mock(side_effect=lambda **kw: execute(kw["payload"]["cmd"]))

    result = await flow.execute_restart(1, "online", "owner", operation_id=operation_id)

    assert result["success"] is False
    after = await get_lifecycle("bot-1", request(), "owner", "owner", deps.facade)
    card = after.data.items[0]
    assert card.status == "running"
    assert card.internal_status == "success"
    assert card.deployment is None
    assert card.ext["aicoding"]["restart"]["operation_id"] == operation_id
    assert card.ext["aicoding"]["restart"]["status"] == "FAILED"
    assert card.ext["aicoding"]["restart"]["phase"] == "BACKING_UP"
    assert "快盘备份失败" in card.ext["aicoding"]["restart"]["message"]
    assert row.ext["unrelated"] == {"keep": True}
    build.upgrade_async.assert_not_called()
    build.retire_superseded_bot.assert_not_called()


@pytest.mark.asyncio
async def test_post_submission_failure_keeps_the_existing_failed_publication_view(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(1, PublishStatus.FAILED, ext={
        "source_status": "success", "error_message": "provider-private-diagnostic",
        "restart": {"engine": "aicoding", "operation_id": OPERATION,
                    "status": "FAILED", "phase": "WAITING_READY"},
    })
    deps.publish_repo.list_by_source_bot.return_value = [row]

    response = await get_lifecycle("bot-1", request(), "owner", "owner", deps.facade)

    card = response.model_dump()["data"]["items"][0]
    assert card["status"] == "deploying"
    assert card["internal_status"] == "failed"
    assert card["deployment"]["action"] == "restart_publish"
    assert card["deployment"]["status"] == "failed"
    assert card["ext"]["aicoding"]["restart"]["status"] == "FAILED"
    assert card["ext"]["aicoding"]["restart"]["phase"] == "WAITING_READY"
    assert "旧容器未销毁" not in card["ext"]["aicoding"]["restart"]["message"]
    assert "provider-private-diagnostic" not in str(card)
