"""Recover ACTIVE BaaS creation without losing the Pool confirmation callback."""

import json
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.errors import DeviceServiceError, InvalidDeviceStatusError
from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.protocols import LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.repository.implementations.devices.device import DeviceRepository
from agentclaw.community.core.repository.implementations.skills_pool.layout import SkillsPoolLayoutRepository
from agentclaw.community.core.skills_pool.native_confirmation import SkillsPoolNativeLayoutConfirmationService
from agentclaw.community.core.skills_pool.repository.models import BotSkillLayoutStateModel
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope, SkillLayoutPhase
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.community.database import CommunityDatabase
from tests.community.core.devices.services.test_baas_device_service import (
    _make_binding_record, _make_service, _operator,
)


def _props():
    return {
        "bot_uuid": "active-baas-uuid",
        "publish_id": "17",
        "create_request_id": "request-17",
        "client_id": "client-17",
        "callback_token": "original-token",
        "envs": {
            "AGENTCLAW_SKILLS_LAYOUT": "pool",
            "AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION": "skills-pool-p3-v1",
            "AGENTCLAW_SKILLS_LAYOUT_PHASE": "pool_initializing",
        },
    }


def _retry_service(props):
    repo = MagicMock()
    record = _make_binding_record(id=42, device_props=props)
    repo.recover_baas_creation_binding_if_matches.return_value = record.id
    repo.get_by_id.return_value = record
    svc = _make_service(repo=repo)
    svc._oss_record_repo.get_record.return_value = None
    svc._do_allocate = MagicMock(return_value=AllocatedDevice(
        device_id=record.device_id, device_provider="baas", device_props=props,
    ))
    svc._after_binding_persisted = MagicMock()
    svc._run_container_init = MagicMock()
    return svc, record


def _retry(svc):
    return svc.apply_device(
        apply_reason="retry retained create", entity_id="u001", entity_type="staff",
        operator=_operator(), bot_id="retained-bot", owner_id="u001",
    )


def test_active_pool_creation_recovery_redispatches_only_current_watchdog():
    svc, record = _retry_service(_props())

    assert _retry(svc) is record

    command = svc._baas_service.exec_command_on_bot.call_args.kwargs["cmd"]
    assert "starting_watchdog.sh" in command
    assert "--startup_identity 17" in command
    assert "--token original-token" in command
    assert "--client_id client-17" in command
    svc._after_binding_persisted.assert_not_called()
    svc._run_container_init.assert_not_called()
    svc._repo.insert_binding.assert_not_called()


def test_active_legacy_creation_recovery_preserves_old_startup_wire():
    props = _props()
    props["envs"] = {}
    svc, record = _retry_service(props)

    assert _retry(svc) is record
    svc._baas_service.exec_command_on_bot.assert_not_called()
    svc._after_binding_persisted.assert_not_called()


def test_already_confirmed_active_recovery_does_not_redispatch():
    props = _props()
    props[LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY] = "17"
    svc, record = _retry_service(props)

    assert _retry(svc) is record
    svc._baas_service.exec_command_on_bot.assert_not_called()


def test_recovered_pre_native_pool_binding_preserves_old_watchdog_wire():
    props = _props()
    props["envs"].pop("AGENTCLAW_SKILLS_LAYOUT_PHASE")
    svc, record = _retry_service(props)

    assert _retry(svc) is record
    svc._baas_service.exec_command_on_bot.assert_not_called()


def test_native_recovery_rejects_unsupported_layout_contract():
    props = _props()
    props["envs"]["AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION"] = "unsupported"
    svc, _record = _retry_service(props)

    with pytest.raises(DeviceServiceError, match="unsupported Skills Pool startup contract"):
        _retry(svc)
    svc._baas_service.exec_command_on_bot.assert_not_called()


def test_active_recovery_dispatch_error_is_retryable_without_failing_binding():
    svc, record = _retry_service(_props())
    svc._baas_service.exec_command_on_bot.side_effect = RuntimeError("transport failed")

    with pytest.raises(DeviceServiceError, match="transport failed"):
        _retry(svc)

    assert record.status == "ACTIVE"
    svc._repo.update_status.assert_not_called()
    svc._repo.update_bot_status_on_device_failed.assert_not_called()
    svc._baas_service.exec_command_on_bot.side_effect = None
    assert _retry(svc) is record


@pytest.mark.parametrize("missing", ["publish_id", "bot_uuid", "callback_token"])
def test_active_pool_recovery_rejects_missing_callback_context(missing):
    props = _props()
    props.pop(missing)
    svc, _record = _retry_service(props)

    with pytest.raises(DeviceServiceError):
        _retry(svc)
    svc._baas_service.exec_command_on_bot.assert_not_called()


def test_active_pool_recovery_uses_highest_priority_current_startup_identity():
    props = _props()
    props["startup_identity"] = "current-instance"
    props[LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY] = "old-instance"
    svc, record = _retry_service(props)

    assert _retry(svc) is record
    command = svc._baas_service.exec_command_on_bot.call_args.kwargs["cmd"]
    assert "--startup_identity current-instance" in command
    assert "--startup_identity 17" not in command


def test_active_orphan_recovery_callback_commits_real_pool_layout(tmp_path):
    """Replay the missing-link callback, retry linking, then confirm real DB state."""
    database = CommunityDatabase(f"sqlite:///{tmp_path / 'recovery.db'}", create_schema=False)
    for model in (EntityDeviceBinding, BotModel, BotSkillLayoutStateModel):
        model.__table__.create(database._engine)
    repo = DeviceRepository(database)
    layouts = SkillsPoolLayoutRepository(database)
    props = _props()
    binding_id = repo.insert_binding(
        entity_id="u001", entity_type="staff", device_id="active-baas-uuid",
        device_provider="baas", env="dev", status="ACTIVE", device_props=props,
        apply_reason="create", applied_by="u001",
    )
    with database.transactional_orm_session() as session:
        session.add(BotModel(
            bot_id="retained-bot", owner_id="u001", entity_id="u001", entity_type="staff",
            creator_id="u001", status="PROVISIONING", active_engine="openclaw", env="dev",
        ))
        session.add(BotSkillLayoutStateModel(
            env="dev", entity_id="u001", bot_id="retained-bot", active_layout="pool",
            phase="pool_initializing", layout_contract_version="skills-pool-p3-v1",
            rollout_evidence=json.dumps({
                "env": "dev", "config_id": 1, "config_version": "revision-1",
                "batch_id": None, "engine_type": "openclaw", "decision_reason": "owner_rollout",
            }),
        ))

    def linked_bot(_binding_id):
        with database.orm_session() as session:
            bot = session.query(BotModel).filter_by(binding_id=_binding_id).one_or_none()
            return None if bot is None else {
                "bot_id": bot.bot_id, "owner_id": bot.owner_id,
                "entity_id": bot.entity_id, "entity_type": bot.entity_type,
                "active_engine": bot.active_engine, "status": bot.status, "ext": {},
            }

    bot_query = MagicMock()
    bot_query.get_by_binding_id.side_effect = linked_bot
    svc = _make_service(repo=repo, bot_query=bot_query)
    svc._layout_confirmation = SkillsPoolNativeLayoutConfirmationService(layouts)
    svc._oss_record_repo.get_record.return_value = None
    svc._do_allocate = MagicMock(return_value=AllocatedDevice(
        device_id="active-baas-uuid", device_provider="baas", device_props=props,
    ))
    evidence = {
        "actual_engine": "openclaw", "actual_layout": "pool",
        "layout_contract_version": "skills-pool-p3-v1", "roots_initialized": True,
    }
    callback = dict(
        device_id="active-baas-uuid", status="SUCCEEDED", message=None,
        token="original-token", startup_identity="17", layout_initialization=evidence,
    )
    scope = BotSkillLayoutScope(env="dev", entity_id="u001", bot_id="retained-bot")
    with pytest.raises(InvalidDeviceStatusError, match="Bot not found"):
        svc.report_device_status(**callback)
    assert layouts.get(scope).phase is SkillLayoutPhase.POOL_INITIALIZING

    recovered = _retry(svc)
    assert recovered.id == binding_id
    assert "starting_watchdog.sh" in svc._baas_service.exec_command_on_bot.call_args.kwargs["cmd"]
    svc.report_device_status(**callback)
    assert layouts.get(scope).phase is SkillLayoutPhase.POOL_ACTIVE
    assert repo.get_by_id(binding_id).device_props[LAYOUT_CONFIRMED_STARTUP_IDENTITY_KEY] == "17"
    command_count = svc._baas_service.exec_command_on_bot.call_count
    _retry(svc)
    assert svc._baas_service.exec_command_on_bot.call_count == command_count
    assert layouts.get(scope).migration_generation is None
    database._engine.dispose()
