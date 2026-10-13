"""Real DB worker takeover must fence delayed data-init results and writes."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.claimed_data_init_state import DataInitClaimLostError
from agentclaw.community.core.bot_management.services.data_init_service import DataInitService
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.devices.repository.record import DataInitExecutionClaim
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.plugin_api.models import BotModel
from tests.community.repository.devices.test_device_repository_unified import (
    _binding, _bot, db as db, repo as repo,
)


def _ready(repo):
    binding_id = repo.insert_binding(**_binding(
        entity_id="u001", entity_type="staff", device_id="runtime-17",
        device_provider="baas", env="dev", status="ACTIVE",
        device_props={"publish_id": "17", "layout_confirmed_startup_identity": "17"},
    ))
    _bot(repo._db, bot_id="pool-bot", owner_id="u001", entity_id="u001",
         binding_id=binding_id, device_id="runtime-17", status="ACTIVE", env="dev",
         ext=json.dumps({"start_status": "SUCCEEDED", "data_init_status": "pending_init"}))
    return binding_id


def _claim(repo, binding_id):
    result = repo.claim_pool_data_init_trigger_if_ready(
        binding_id=binding_id, device_id="runtime-17", startup_identity="17",
    )
    assert result is not None
    return DataInitExecutionClaim(binding_id, "runtime-17", "17", result.claim_token)


def _expire(repo, binding_id):
    stale = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    with repo._db.orm_session() as session:
        binding = session.query(EntityDeviceBinding).filter_by(id=binding_id).one()
        props = json.loads(binding.device_props)
        props["data_init_trigger_claimed_at"] = stale
        binding.device_props = json.dumps(props)
        bot = session.query(BotModel).filter_by(binding_id=binding_id).one()
        ext = json.loads(bot.ext)
        ext.update(data_init_status="in_progress", data_init_started_at=stale)
        bot.ext = json.dumps(ext)


def _bot_state(repo):
    with repo._db.orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="pool-bot").one()
        return {"status": bot.status, "binding_id": bot.binding_id, "ext": json.loads(bot.ext)}


def _service(repo):
    device_service = DeviceService.__new__(DeviceService)
    device_service._repo = repo
    bots = MagicMock()
    bots.get_bot.side_effect = lambda *_args: _bot_state(repo)
    service = DataInitService(
        resource_repo=MagicMock(), device_service=device_service,
        skill_set_factory=MagicMock(), device_plugin=MagicMock(),
        bot_service_provider=lambda: bots, skill_md_path="/test/SKILL.md", resolver=MagicMock(),
    )
    service.MAX_RETRIES = 1
    service._collect_link_resources = MagicMock(return_value=[])
    return service, bots


@pytest.mark.parametrize("old_outcome", ["success", "failure"])
@pytest.mark.asyncio
async def test_delayed_worker_cannot_overwrite_completed_takeover(repo, old_outcome):
    binding_id = _ready(repo)
    first = _claim(repo, binding_id)
    old, old_bots = _service(repo)
    entered, release = Event(), Event()

    def delayed_result(**_kwargs):
        entered.set()
        assert release.wait(10), "test worker was not released"
        if old_outcome == "failure":
            raise RuntimeError("old LLM failed")
        return {"summary": {"capability": "old result"}}

    old._send_to_engine = delayed_result
    task = asyncio.create_task(old.trigger_init(
        "pool-bot", "u001", "u001", "staff", execution_claim=first,
    ))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        _expire(repo, binding_id)
        second = _claim(repo, binding_id)
        assert second.claim_token != first.claim_token
        replacement, replacement_bots = _service(repo)
        replacement._send_to_engine = MagicMock(return_value={"summary": {"capability": "new result"}})
        with patch(
            "agentclaw.community.core.bot_management.services.sync_to_downstream.sync_all",
            return_value={"ecb": {"success": True}, "bcsfuse": {"success": True}},
        ) as sync:
            result = await replacement.trigger_init(
                "pool-bot", "u001", "u001", "staff", execution_claim=second,
                resume_stale_in_progress=True,
            )
            assert result["status"] == "completed"
            committed = _bot_state(repo)["ext"]
            release.set()
            with pytest.raises(DataInitClaimLostError):
                await task
            assert sync.call_count == 1
            assert sync.call_args.args[2] == {"capability": "new result"}
        assert _bot_state(repo)["ext"] == committed
        assert committed["data_init_status"] == "completed"
        old_bots.update_bot_ext.assert_not_called()
        replacement_bots.update_bot_ext.assert_not_called()
        assert repo.release_pool_data_init_trigger_if_matches(
            binding_id=binding_id, device_id="runtime-17", startup_identity="17",
            claim_token=first.claim_token,
        ) is False
    finally:
        release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("updates", [
    {"data_init_status": "in_progress"}, {"data_init_status": "completed"},
    {"data_init_status": "failed"}, {"iam_token": None},
])
def test_superseded_claim_cannot_write_any_worker_state(repo, updates):
    binding_id = _ready(repo)
    old = _claim(repo, binding_id)
    _expire(repo, binding_id)
    current = _claim(repo, binding_id)
    assert repo.update_pool_data_init_ext_if_claim_matches(
        bot_id="pool-bot", owner_id="u001", claim=current,
        updates={"data_init_status": "completed", "downstream_sync": {"new": True}},
    ) is True
    committed = _bot_state(repo)["ext"]
    assert repo.update_pool_data_init_ext_if_claim_matches(
        bot_id="pool-bot", owner_id="u001", claim=old, updates=updates,
    ) is False
    assert _bot_state(repo)["ext"] == committed


def test_claim_write_failure_propagates_without_partial_state(repo):
    binding_id = _ready(repo)
    claim = _claim(repo, binding_id)
    committed = _bot_state(repo)["ext"]
    with patch("agentclaw.community.core.repository.implementations.devices.guarded_lifecycle.json.dumps",
               side_effect=RuntimeError("storage failed")):
        with pytest.raises(RuntimeError, match="storage failed"):
            repo.update_pool_data_init_ext_if_claim_matches(
                bot_id="pool-bot", owner_id="u001", claim=claim,
                updates={"data_init_status": "completed"},
            )
    assert _bot_state(repo)["ext"] == committed


@pytest.mark.asyncio
async def test_trigger_checkpoint_failure_is_not_reported_as_skipped(repo):
    binding_id = _ready(repo)
    claim = _claim(repo, binding_id)
    service, _bots = _service(repo)
    with patch("agentclaw.community.core.repository.implementations.devices.guarded_lifecycle.json.dumps",
               side_effect=RuntimeError("storage failed")):
        with pytest.raises(RuntimeError, match="storage failed"):
            await service.trigger_init("pool-bot", "u001", "u001", "staff", execution_claim=claim)


def test_fenced_write_rejects_wrong_owner(repo):
    binding_id = _ready(repo)
    claim = _claim(repo, binding_id)
    assert repo.update_pool_data_init_ext_if_claim_matches(
        bot_id="pool-bot", owner_id="other-owner", claim=claim,
        updates={"data_init_status": "completed"},
    ) is False


@pytest.mark.parametrize("change", ["startup", "status", "device", "confirmation"])
def test_fenced_write_rejects_changed_runtime_identity(repo, change):
    binding_id = _ready(repo)
    claim = _claim(repo, binding_id)
    with repo._db.orm_session() as session:
        binding = session.query(EntityDeviceBinding).filter_by(id=binding_id).one()
        props = json.loads(binding.device_props)
        if change == "startup":
            props["startup_identity"] = "replacement-startup"
        elif change == "confirmation":
            props["layout_confirmed_startup_identity"] = "replacement-startup"
        elif change == "status":
            binding.status = "STOPPED"
        else:
            binding.device_id = "replacement-runtime"
        binding.device_props = json.dumps(props)
    assert repo.update_pool_data_init_ext_if_claim_matches(
        bot_id="pool-bot", owner_id="u001", claim=claim,
        updates={"data_init_status": "completed"},
    ) is False


def test_current_claim_checkpoint_renews_without_changing_bot_state(repo):
    binding_id = _ready(repo)
    claim = _claim(repo, binding_id)
    _expire(repo, binding_id)
    committed = _bot_state(repo)["ext"]
    assert repo.update_pool_data_init_ext_if_claim_matches(
        bot_id="pool-bot", owner_id="u001", claim=claim, updates={},
    ) is True
    assert _bot_state(repo)["ext"] == committed
    assert repo.claim_pool_data_init_trigger_if_ready(
        binding_id=binding_id, device_id="runtime-17", startup_identity="17",
    ) is None
