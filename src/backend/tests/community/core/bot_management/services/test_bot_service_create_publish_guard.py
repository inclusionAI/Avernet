"""Publish-record guards for the direct (one-shot) service create.

An openapi create-as-service is fulfilled by ``create_bot`` directly, so the
service publish record — the thing the caller's publish orchestration advances
after creation — is part of the create contract:

* a persistence failure must surface (soft-delete + raise, the same contract
  a workspace-hosting failure has), not return a 201 for a bot that can never
  be published;
* an auth-status replay of the same bot must converge the recoverable state:
  publish record missing → create it, publish record present → leave it.

Drives the real ``BotService.create_bot`` with mocked repositories, so the
failure and retry semantics below the transport are exercised, not stubbed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


def _make_service() -> tuple[BotService, MagicMock]:
    """Minimal BotService able to run create_bot to the publish-record step."""
    svc = BotService.__new__(BotService)
    svc._bot_app_grant_provider = lambda: MagicMock()
    svc._repository = MagicMock()
    svc._repository.count_by_owner.return_value = 0
    svc._repository.get_by_id_and_owner.return_value = None
    svc._repository.exists_by_bot_name.return_value = False
    svc._repository.insert.side_effect = lambda data: {"id": 1, **data}
    svc._repository.update_by_owner.return_value = None
    svc._repository.soft_delete_by_owner.return_value = None

    svc._allocation_config = SimpleNamespace(mode="multi", max_devices_per_entity=10)
    svc._passport_plugin = MagicMock()
    svc._oss_record_repo = MagicMock()
    svc._device_binding_repo = MagicMock()
    svc._device_binding_repo.list_by_owner.return_value = []
    svc._cleanup_service = MagicMock()
    svc._bcn_service = MagicMock()
    svc._template_service = MagicMock()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="", aixcore_base_url_pre="")

    skill_set_service = MagicMock()
    skill_set_service.get_symlink_mappings.return_value = []
    svc._skill_set_factory = MagicMock()
    svc._skill_set_factory.create.return_value = skill_set_service

    svc._bot_publish_repo = MagicMock()
    svc._bot_publish_repo.get_by_publish_bot_id.return_value = None

    publish_service = MagicMock()
    publish_service.create_publish.return_value = MagicMock(
        to_dict=lambda: {"publish_id": "p1"}
    )
    svc._bot_publish_provider = lambda: publish_service

    teclaw_provision = MagicMock()
    teclaw_provision.is_teclaw.return_value = False
    svc._teclaw_provision_provider = lambda: teclaw_provision
    svc._common_config_service = None
    svc._policy_service = None
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None

    device_service = MagicMock()
    device_service.apply_device.return_value = DeviceBindingRecord(
        id=1,
        entity_id="u1",
        entity_type="staff",
        device_id="dev-1",
        device_provider="arca",
        env="dev",
        device_props={},
        status=DeviceBindingStatus.ACTIVE.value,
        apply_reason=None,
        applied_by="u1",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=None,
        gmt_modified=None,
    )
    svc._device_service_provider = lambda: device_service

    svc.restart_lock_repo = MagicMock()
    svc._bcn_register_enabled = None
    return svc, publish_service


@pytest.mark.unit
class TestServiceCreatePublishRecordGuard:
    def test_publish_persistence_failure_raises_and_soft_deletes(self):
        """The 201 must not stand for a service bot that can never publish."""
        svc, publish_service = _make_service()
        publish_service.create_publish.side_effect = RuntimeError("db down")

        with pytest.raises(BotServiceError, match="publish"):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="svc-bot",
                bot_id="svc-1",
                engine_type="claude_code",
                bot_type="service",
            )

        svc._repository.soft_delete_by_owner.assert_called_once_with("svc-1", "u1")

    def test_replay_of_existing_service_bot_backfills_missing_publish(self):
        """The auth-status replay converges a missing publish record."""
        svc, publish_service = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {
            "id": 7,
            "bot_id": "svc-1",
            "owner_id": "u1",
            "bot_type": "service",
            "bot_name": "svc-bot",
            "status": "ACTIVE",
            "ext": {},
        }
        svc._bot_publish_repo.get_by_publish_bot_id.return_value = None

        record = svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_id="svc-1",
            engine_type="claude_code",
            bot_type="service",
        )

        assert record["bot_id"] == "svc-1"
        publish_service.create_first_publish_for_bot.assert_called_once()
        backfill_kwargs = publish_service.create_first_publish_for_bot.call_args.kwargs
        assert backfill_kwargs["bot_id"] == "svc-1"
        assert backfill_kwargs["owner_id"] == "u1"

    def test_replay_of_existing_service_bot_with_publish_leaves_it_alone(self):
        svc, publish_service = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {
            "id": 7,
            "bot_id": "svc-1",
            "owner_id": "u1",
            "bot_type": "service",
            "bot_name": "svc-bot",
            "status": "ACTIVE",
            "ext": {},
        }
        svc._bot_publish_repo.get_by_publish_bot_id.return_value = SimpleNamespace(
            id=99, version=1
        )

        record = svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_id="svc-1",
            engine_type="claude_code",
            bot_type="service",
        )

        assert record["bot_id"] == "svc-1"
        publish_service.create_first_publish_for_bot.assert_not_called()

    def test_replay_of_personal_bot_does_not_backfill(self):
        svc, publish_service = _make_service()
        svc._repository.get_by_id_and_owner.return_value = {
            "id": 7,
            "bot_id": "per-1",
            "owner_id": "u1",
            "bot_type": "personal",
            "bot_name": "per-bot",
            "status": "ACTIVE",
            "ext": {},
        }

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_id="per-1",
            engine_type="claude_code",
            bot_type="personal",
        )

        publish_service.create_first_publish_for_bot.assert_not_called()
