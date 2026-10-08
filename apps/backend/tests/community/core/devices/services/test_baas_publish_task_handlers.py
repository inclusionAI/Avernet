from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.devices.models import AllocatedDevice, DeviceBindingStatus
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
)
from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
    BAAS_CREATE_INIT_TASK,
    BAAS_CREATE_PUBLISH_POLL_TASK,
    BAAS_RESTART_PUBLISH_POLL_TASK,
    BaasCreateInitTaskHandler,
    BaasCreatePublishPollHandler,
    BaasPublishTaskLifecycle,
    BaasRestartPublishPollHandler,
    build_create_init_payload,
    build_create_publish_poll_payload,
    build_restart_publish_poll_payload,
)
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import (
    BaasPublishCompletedEvent,
    DeviceActivatedEvent,
    RuntimeProjectionRequestedEvent,
)
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry


def _make_binding(*, status: str, device_props: dict) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=42,
        entity_id="owner-001",
        entity_type="staff",
        device_id="device-001",
        device_provider="baas",
        env="dev",
        device_props=device_props,
        status=status,
        apply_reason=None,
        applied_by="owner-001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


def _make_baas_device_service(
    *,
    repo: MagicMock,
    bot_query: MagicMock | None = None,
    vault: TokenVault | None = None,
    template_service: MagicMock | None = None,
) -> BaasDeviceService:
    return BaasDeviceService(
        repository=repo,
        baas_service=MagicMock(),
        bot_query=bot_query or MagicMock(),
        bot_sync=MagicMock(),
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
        template_resolver=MagicMock(),
        vault=vault,
        template_service=template_service,
    )


def _make_restart_handler(
    *,
    repo: MagicMock,
    bot_repository: MagicMock,
    baas_device_service: MagicMock,
    publish_repository: MagicMock | None = None,
    template_service: MagicMock | None = None,
    baas_service: MagicMock | None = None,
    common_config_service: MagicMock | None = None,
    clock=lambda: 200.0,
) -> tuple[BaasRestartPublishPollHandler, MagicMock]:
    if common_config_service is None:
        common_config_service = MagicMock()
        common_config_service.get_value.return_value = {
            "image": "registry/arca:default"
        }
    handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        bot_repository=bot_repository,
        publish_repository=publish_repository or MagicMock(),
        common_config_service=common_config_service,
        baas_device_service=baas_device_service,
        template_service=template_service or MagicMock(),
        poll_delay_seconds=10.0,
        clock=clock,
    )
    return handler, baas_device_service


def test_restart_task_adopts_workflow_after_process_loses_publish_id():
    repo = MagicMock()
    request_id = "restart-request-1"
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={
            "restart_request_id": request_id,
            "restart_workflow_baseline": 1000,
            "restart_publish_id": None,
        },
    )
    baas_service = MagicMock()
    baas_service.list_bot_publishes.return_value = [
        {"id": 1000, "publish_type": "UPDATE"},
        {"id": 1001, "publish_type": "UPDATE"},
    ]
    baas_device_service = MagicMock()
    baas_device_service.poll_publish_once.return_value = DeviceBindingStatus.PENDING.value
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=MagicMock(),
        baas_service=baas_service,
        baas_device_service=baas_device_service,
    )

    outcome = handler.handle(
        build_restart_publish_poll_payload(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=None,
            started_at_epoch_s=190.0,
            bot_uuid="uuid-001",
            request_id=request_id,
            workflow_baseline=1000,
        )
    )

    assert outcome == Reschedule(10.0)
    repo.update_device_props.assert_called_once_with(
        binding_id=42,
        props={"publish_id": "1001", "restart_publish_id": "1001"},
    )
    baas_device_service.poll_publish_once.assert_called_once_with(publish_id=1001)


def test_restart_task_waits_for_binding_intent_commit():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.ACTIVE.value,
        device_props={},
    )
    baas_service = MagicMock()
    baas_device_service = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=MagicMock(),
        baas_service=baas_service,
        baas_device_service=baas_device_service,
    )

    outcome = handler.handle(
        build_restart_publish_poll_payload(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=None,
            started_at_epoch_s=190.0,
            bot_uuid="uuid-001",
            request_id="not-committed-yet",
            workflow_baseline=1000,
        )
    )

    assert outcome == Reschedule(10.0)
    baas_service.list_bot_publishes.assert_not_called()
    baas_device_service.poll_publish_once.assert_not_called()


def test_read_codefuse_token_supports_personal_coding():
    template_service = MagicMock()
    template_service.get_template_config.return_value = {"token": "enc:v1:token"}
    handler, _ = _make_restart_handler(
        repo=MagicMock(),
        bot_repository=MagicMock(),
        baas_device_service=MagicMock(),
        template_service=template_service,
    )

    token = handler._read_codefuse_token(
        bot_id="bot-001",
        bot={
            "owner_id": "owner-001",
            "active_engine": "aicoding",
            "bot_type": "service",
            "template_type": "personalCoding",
        },
    )

    assert token == "enc:v1:token"
    template_service.get_template_config.assert_called_once_with("bot-001")


def test_read_codefuse_token_uses_template_type_fallback_when_engine_missing():
    template_service = MagicMock()
    template_service.get_template_config.return_value = {"token": "enc:v1:token"}
    handler, _ = _make_restart_handler(
        repo=MagicMock(),
        bot_repository=MagicMock(),
        baas_device_service=MagicMock(),
        template_service=template_service,
    )

    token = handler._read_codefuse_token(
        bot_id="bot-001",
        bot={
            "owner_id": "owner-001",
            "active_engine": None,
            "bot_type": "service",
            "template_type": "personalCoding",
        },
    )

    assert token == "enc:v1:token"


def test_read_codefuse_token_skips_non_coding_template():
    template_service = MagicMock()
    handler, _ = _make_restart_handler(
        repo=MagicMock(),
        bot_repository=MagicMock(),
        baas_device_service=MagicMock(),
        template_service=template_service,
    )

    token = handler._read_codefuse_token(
        bot_id="bot-001",
        bot={
            "owner_id": "owner-001",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "template_type": "normalCC",
        },
    )

    assert token is None
    template_service.get_template_config.assert_not_called()


def test_create_poll_completes_when_binding_terminal():
    repo = MagicMock()
    baas_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        task_queue_service=task_queue_service,
    )
    payload = {
        "binding_id": 42,
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "publish_id": 1001,
        "started_at_epoch_s": 123.4,
    }

    for terminal_status in (
        DeviceBindingStatus.ACTIVE.value,
        DeviceBindingStatus.FAILED.value,
    ):
        repo.reset_mock()
        baas_service.reset_mock()
        task_queue_service.reset_mock()
        repo.get_by_id.return_value = _make_binding(
            status=terminal_status,
            device_props={"publish_id": 1001},
        )

        outcome = handler.handle(payload)

        assert outcome == Complete()
        repo.get_by_id.assert_called_once_with(42)
        baas_service.get_publish_progress.assert_not_called()
        task_queue_service.enqueue.assert_not_called()


def test_create_poll_completes_when_publish_id_is_stale():
    repo = MagicMock()
    baas_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        task_queue_service=task_queue_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 2002},
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Complete()
    baas_service.get_publish_progress.assert_not_called()
    task_queue_service.enqueue.assert_not_called()


def test_payload_builders_include_publish_round_fields():
    assert build_create_publish_poll_payload(
        binding_id=42,
        bot_id="bot-001",
        owner_id="owner-001",
        publish_id=1001,
        started_at_epoch_s=123.4,
    ) == {
        "binding_id": 42,
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "publish_id": 1001,
        "started_at_epoch_s": 123.4,
    }
    assert build_restart_publish_poll_payload(
        binding_id=42,
        bot_id="bot-001",
        owner_id="owner-001",
        publish_id=1001,
        started_at_epoch_s=123.4,
        bot_uuid=None,
    ) == {
        "binding_id": 42,
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "publish_id": 1001,
        "started_at_epoch_s": 123.4,
        "bot_uuid": None,
    }


def test_create_poll_fails_when_publish_id_is_bool():
    repo = MagicMock()
    baas_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        task_queue_service=MagicMock(),
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": True,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Fail("invalid payload: field publish_id must be int")
    repo.get_by_id.assert_not_called()


def test_create_poll_fails_when_owner_id_is_not_string():
    repo = MagicMock()
    baas_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        task_queue_service=MagicMock(),
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": 100014,
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Fail("invalid payload: field owner_id must be str")
    repo.get_by_id.assert_not_called()


def test_create_poll_completes_when_binding_is_not_baas():
    repo = MagicMock()
    baas_device_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_device_service,
        task_queue_service=task_queue_service,
    )
    binding = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    binding.device_provider = "arca"
    repo.get_by_id.return_value = binding

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Complete()
    baas_device_service.poll_publish_once.assert_not_called()
    task_queue_service.enqueue.assert_not_called()


def test_create_poll_retries_when_publish_poll_is_transient_error():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_device_service,
        task_queue_service=MagicMock(),
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    baas_device_service.poll_publish_once.return_value = None

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Retry("get_publish_progress transient error")


def test_create_poll_retries_on_unexpected_status():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_device_service,
        task_queue_service=MagicMock(),
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    baas_device_service.poll_publish_once.return_value = "UNKNOWN"

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Retry("unexpected publish status: UNKNOWN")


def test_create_poll_reschedules_when_publish_id_matches_string_device_props():
    repo = MagicMock()
    baas_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        task_queue_service=task_queue_service,
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": "1001"},
    )
    baas_service.poll_publish_once.return_value = "PENDING"

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Reschedule(5.0)
    baas_service.get_publish_progress.assert_not_called()
    task_queue_service.enqueue.assert_not_called()


def test_create_poll_reschedules_when_publish_pending():
    repo = MagicMock()
    baas_device_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_device_service,
        task_queue_service=task_queue_service,
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    baas_device_service.poll_publish_once.return_value = "PENDING"

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Reschedule(5.0)
    baas_device_service.poll_publish_once.assert_called_once_with(publish_id=1001)
    task_queue_service.enqueue.assert_not_called()


def test_create_publish_poll_enqueues_init_when_publish_success():
    repo = MagicMock()
    baas_device_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_device_service,
        task_queue_service=task_queue_service,
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    baas_device_service.poll_publish_once.return_value = "ACTIVE"
    payload = {
        "binding_id": 42,
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "publish_id": 1001,
        "started_at_epoch_s": 123.4,
    }

    outcome = handler.handle(payload)

    assert outcome == Complete()
    task_queue_service.enqueue.assert_called_once_with(
        BAAS_CREATE_INIT_TASK,
        build_create_init_payload(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        ),
        deadline_seconds=86400,
    )
    baas_device_service._mark_service_start_failed.assert_not_called()


def test_poll_publish_once_treats_baas_active_as_pending():
    baas_api = MagicMock()
    baas_api.get_publish_progress.return_value = {"status": "ACTIVE"}
    service = object.__new__(BaasDeviceService)
    service._baas_service = baas_api

    status = service.poll_publish_once(publish_id=1001)

    assert status == DeviceBindingStatus.PENDING.value
    baas_api.get_publish_progress.assert_called_once_with(
        publish_id=1001,
        include_devices=False,
    )


def test_create_poll_marks_failed_on_baas_failed():
    repo = MagicMock()
    baas_device_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_device_service,
        task_queue_service=task_queue_service,
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    baas_device_service.poll_publish_once.return_value = "FAILED"

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Complete()
    baas_device_service._mark_service_start_failed.assert_called_once_with(
        binding_id=42,
        error="BaaS publish FAILED: publish_id=1001",
    )
    task_queue_service.enqueue.assert_not_called()


def test_create_poll_marks_failed_on_business_timeout():
    repo = MagicMock()
    baas_device_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_device_service,
        task_queue_service=task_queue_service,
        clock=lambda: 723.4,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Complete()
    baas_device_service._mark_service_start_failed.assert_called_once_with(
        binding_id=42,
        error="BaaS publish polling timeout after 600s (publish_id=1001)",
    )
    baas_device_service.poll_publish_once.assert_not_called()
    task_queue_service.enqueue.assert_not_called()


def test_restart_poll_completes_when_restart_publish_id_is_stale():
    repo = MagicMock()
    baas_device_service = MagicMock()
    bot_repository = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": 3003},
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Complete()
    baas_device_service.poll_publish_once.assert_not_called()


def test_restart_poll_reschedules_when_pending():
    repo = MagicMock()
    baas_device_service = MagicMock()
    bot_repository = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": "1001"},
    )
    bot_repository.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "template_type": "applicationCoding",
        "ext": {},
    }
    baas_device_service.poll_publish_once.return_value = (
        DeviceBindingStatus.PENDING.value
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Reschedule(10.0)
    baas_device_service.poll_publish_once.assert_called_once_with(publish_id=1001)
    bot_repository.update_by_owner.assert_not_called()
    repo.update_status.assert_not_called()


def test_restart_poll_marks_active_and_clears_old_baas_failure_ext_on_success():
    repo = MagicMock()
    baas_device_service = MagicMock()
    bot_repository = MagicMock()
    template_service = MagicMock()
    encrypted_token = TokenVault("master-key-123").encrypt("plain-token")
    handler, baas_device_service = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
        template_service=template_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": "1001"},
    )
    bot_repository.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "template_type": "applicationCoding",
        "ext": {
            "start_status": "FAILED",
            "start_message": "BaaS publish FAILED: publish_id=999",
            "keep": "value",
        },
    }
    bot_repository.get_by_id_and_owner.return_value = {
        "ext": {
            "start_status": "FAILED",
            "start_message": "BaaS publish FAILED: publish_id=999",
            "keep": "value",
        },
    }
    template_service.get_template_config.return_value = {"token": encrypted_token}
    baas_device_service.poll_publish_once.return_value = (
        DeviceBindingStatus.ACTIVE.value
    )
    baas_device_service.refresh_codefuse_token_on_publish_success.return_value = None

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Complete()
    template_service.get_template_config.assert_called_once_with("bot-001")
    baas_device_service.refresh_codefuse_token_on_publish_success.assert_called_once_with(
        bot_uuid="uuid-001",
        codefuse_token=encrypted_token,
    )
    bot_repository.update_by_owner.assert_called_once_with(
        "bot-001",
        "owner-001",
        {"status": DeviceBindingStatus.ACTIVE.value},
    )
    bot_repository.compare_and_set_ext.assert_called_once_with(
        bot_id="bot-001",
        owner_id="owner-001",
        expected_ext={
            "start_status": "FAILED",
            "start_message": "BaaS publish FAILED: publish_id=999",
            "keep": "value",
        },
        ext={"keep": "value", "restart_publish_id": "1001"},
    )
    repo.update_status.assert_called_once_with(
        binding_id=42,
        status=DeviceBindingStatus.ACTIVE.value,
    )


def test_restart_poll_marks_failed_with_current_publish_id_on_failure():
    repo = MagicMock()
    baas_device_service = MagicMock()
    bot_repository = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": "1001"},
    )
    bot_repository.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "template_type": "applicationCoding",
        "ext": {"keep": "value"},
    }
    bot_repository.get_by_id_and_owner.return_value = {"ext": {"keep": "value"}}
    baas_device_service.poll_publish_once.return_value = (
        DeviceBindingStatus.FAILED.value
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Complete()
    bot_repository.update_by_owner.assert_called_once_with(
        "bot-001",
        "owner-001",
        {"status": DeviceBindingStatus.FAILED.value},
    )
    bot_repository.compare_and_set_ext.assert_called_once_with(
        bot_id="bot-001",
        owner_id="owner-001",
        expected_ext={"keep": "value"},
        ext={
            "keep": "value",
            "start_status": "FAILED",
            "start_message": "BaaS publish FAILED: publish_id=1001",
            "restart_publish_id": "1001",
        },
    )
    repo.update_status.assert_called_once_with(
        binding_id=42,
        status=DeviceBindingStatus.FAILED.value,
    )


def test_restart_status_cas_preserves_nullable_bot_ext_snapshot():
    repo = MagicMock()
    bot_repository = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=MagicMock(),
    )
    bot_repository.update_by_owner.return_value = {"status": "FAILED", "ext": None}
    bot_repository.get_by_id_and_owner.return_value = {"ext": None}
    bot_repository.compare_and_set_ext.return_value = {"ext": {"start_status": "FAILED"}}

    handler._persist_restart_status(
        bot_id="bot-001",
        owner_id="owner-001",
        binding_id=42,
        status=DeviceBindingStatus.FAILED.value,
        publish_id=None,
        failure_message="workflow adoption failed",
    )

    bot_repository.compare_and_set_ext.assert_called_once_with(
        bot_id="bot-001",
        owner_id="owner-001",
        expected_ext=None,
        ext={
            "start_status": "FAILED",
            "start_message": "workflow adoption failed",
        },
    )


def test_restart_poll_marks_failed_on_business_timeout():
    repo = MagicMock()
    baas_device_service = MagicMock()
    bot_repository = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
        clock=lambda: 723.4,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": "1001"},
    )
    bot_repository.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "template_type": "applicationCoding",
        "ext": {"keep": "value"},
    }
    bot_repository.get_by_id_and_owner.return_value = {"ext": {"keep": "value"}}

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Complete()
    baas_device_service.poll_publish_once.assert_not_called()
    bot_repository.update_by_owner.assert_called_once_with(
        "bot-001",
        "owner-001",
        {"status": DeviceBindingStatus.FAILED.value},
    )
    bot_repository.compare_and_set_ext.assert_called_once_with(
        bot_id="bot-001",
        owner_id="owner-001",
        expected_ext={"keep": "value"},
        ext={
            "keep": "value",
            "start_status": "FAILED",
            "start_message": "BaaS publish timeout after 600s (publish_id=1001)",
            "restart_publish_id": "1001",
        },
    )
    repo.update_status.assert_called_once_with(
        binding_id=42,
        status=DeviceBindingStatus.FAILED.value,
    )


def test_restart_poll_ignores_stale_restart_publish_id():
    repo = MagicMock()
    baas_device_service = MagicMock()
    bot_repository = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": "3003"},
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Complete()
    baas_device_service.poll_publish_once.assert_not_called()
    bot_repository.update_by_owner.assert_not_called()
    repo.update_status.assert_not_called()


def test_create_init_marks_active_after_init_and_alive():
    repo = MagicMock()
    encrypted_token = TokenVault("master-key-123").encrypt("plain-token")
    binding = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={
            "publish_id": "1001",
            "bot_uuid": "BAAS-CTR-001",
            "callback_token": "tok-123",
        },
    )
    updated_binding = _make_binding(
        status=DeviceBindingStatus.ACTIVE.value,
        device_props=binding.device_props,
    )
    repo.get_by_id.side_effect = [binding, updated_binding]
    repo.get_by_device_id.return_value = binding
    bot_query = MagicMock()
    bot_query.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "active_engine": "aicoding",
        "bot_type": "service",
        "admins": ["u1001", "u1002"],
        "template_type": "applicationCoding",
        # ac_bots does not own template_config; create-init must reload it from
        # ac_templates through TemplateService.  Keep this empty to guard the
        # historical bug where token lookup used the wrong table.
        "template_config": {},
    }
    template_service = MagicMock()
    template_service.get_template_config.return_value = {"token": encrypted_token}
    service = _make_baas_device_service(
        repo=repo,
        bot_query=bot_query,
        vault=TokenVault("master-key-123"),
        template_service=template_service,
    )
    service._run_container_init = MagicMock()
    service._sync_bot_config_when_device_active = MagicMock()
    service._sync_mcps_when_device_active = MagicMock()

    ok, _ = service.run_create_init_once(
        binding_id=42,
        bot_id="bot-001",
        owner_id="owner-001",
        publish_id=1001,
    )

    assert ok is True
    template_service.get_template_config.assert_called_once_with("bot-001")
    service._run_container_init.assert_called_once_with(
        bot_uuid="BAAS-CTR-001",
        device=AllocatedDevice(
            device_id="device-001",
            device_provider="baas",
            device_props=binding.device_props,
        ),
        engine="aicoding",
        bot_type="service",
        bot_id="bot-001",
        owner_id="owner-001",
        callback_token="tok-123",
        admins=["u1001", "u1002"],
        codefuse_token="plain-token",
    )
    repo.update_status_and_alive_at.assert_called_once_with(
        binding_id=42,
        status=DeviceBindingStatus.ACTIVE.value,
    )
    repo.update_bot_status_on_device_active.assert_called_once_with(binding_id=42)


def test_create_init_reads_codefuse_token_from_template_service_and_writes_container():
    repo = MagicMock()
    vault = TokenVault("master-key-123")
    encrypted_token = vault.encrypt("plain-token")
    assert encrypted_token.startswith("enc:v1:")
    binding = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={
            "publish_id": "1001",
            "bot_uuid": "BAAS-CTR-001",
            "callback_token": "tok-123",
        },
    )
    updated_binding = _make_binding(
        status=DeviceBindingStatus.ACTIVE.value,
        device_props=binding.device_props,
    )
    repo.get_by_id.side_effect = [binding, updated_binding]
    repo.get_by_device_id.return_value = binding
    bot_query = MagicMock()
    bot_query.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        # Missing active_engine should still use template_type fallback for coding
        # token provisioning, matching the BaaS restart-poll path.
        "active_engine": None,
        "bot_type": "service",
        "admins": [],
        "template_type": "applicationCoding",
        # Historical bug guard: ac_bots has no template_config, so this value
        # must not be the source for CodeFuse token lookup.
        "template_config": {},
    }
    template_service = MagicMock()
    template_service.get_template_config.return_value = {"token": encrypted_token}
    service = _make_baas_device_service(
        repo=repo,
        bot_query=bot_query,
        vault=vault,
        template_service=template_service,
    )
    service._sync_bot_config_when_device_active = MagicMock()
    service._sync_mcps_when_device_active = MagicMock()

    with (
        patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
        ),
        patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas",
        ) as writer,
    ):
        ok, message = service.run_create_init_once(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        )

    assert ok is True, message
    template_service.get_template_config.assert_called_once_with("bot-001")
    writer.assert_called_once()
    assert writer.call_args.args[1:] == ("BAAS-CTR-001", "plain-token")


def test_create_init_requires_template_service_for_coding_template():
    repo = MagicMock()
    binding = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": "1001", "bot_uuid": "BAAS-CTR-001"},
    )
    repo.get_by_id.return_value = binding
    bot_query = MagicMock()
    bot_query.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "active_engine": None,
        "bot_type": "service",
        "admins": [],
        "template_type": "personalCoding",
    }
    service = _make_baas_device_service(repo=repo, bot_query=bot_query)
    service._run_container_init = MagicMock()

    ok, message = service.run_create_init_once(
        binding_id=42,
        bot_id="bot-001",
        owner_id="owner-001",
        publish_id=1001,
    )

    assert ok is False
    assert "template_service required" in message
    service._run_container_init.assert_not_called()


def test_create_init_marks_failed_when_init_fails():
    repo = MagicMock()
    binding = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    repo.get_by_id.return_value = binding
    baas_device_service = _make_baas_device_service(repo=repo)
    baas_device_service.run_create_init_once = MagicMock(
        return_value=(False, "init boom")
    )
    handler = BaasCreateInitTaskHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
        }
    )

    assert outcome == Complete()
    repo.update_bot_start_status.assert_called_once_with(
        binding_id=42,
        status="FAILED",
        message="init boom",
    )
    repo.update_bot_status_on_device_failed.assert_called_once_with(binding_id=42)
    repo.update_status.assert_called_once_with(
        binding_id=42,
        status=DeviceBindingStatus.FAILED.value,
    )


def test_create_init_completes_when_binding_is_terminal_or_not_baas():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasCreateInitTaskHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
    )
    payload = {
        "binding_id": 42,
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "publish_id": 1001,
    }
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.FAILED.value,
        device_props={"publish_id": 1001},
    )

    assert handler.handle(payload) == Complete()

    non_baas = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 1001},
    )
    non_baas.device_provider = "arca"
    repo.get_by_id.return_value = non_baas

    assert handler.handle(payload) == Complete()
    baas_device_service.run_create_init_once.assert_not_called()


def test_create_init_completes_when_publish_id_is_stale():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasCreateInitTaskHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"publish_id": 2002},
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
        }
    )

    assert outcome == Complete()
    baas_device_service.run_create_init_once.assert_not_called()


def test_create_init_is_complete_when_binding_already_active():
    repo = MagicMock()
    binding = _make_binding(
        status=DeviceBindingStatus.ACTIVE.value,
        device_props={"publish_id": 1001, "bot_uuid": "BAAS-CTR-001"},
    )
    repo.get_by_id.return_value = binding
    service = _make_baas_device_service(repo=repo)
    service._run_container_init = MagicMock()
    service.report_device_alive = MagicMock()

    ok, message = service.run_create_init_once(
        binding_id=42,
        bot_id="bot-001",
        owner_id="owner-001",
        publish_id=1001,
    )

    assert ok is True
    assert "already ACTIVE" in message
    service._run_container_init.assert_not_called()
    service.report_device_alive.assert_not_called()


def test_restart_poll_fails_when_bot_uuid_is_missing():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
        clock=lambda: 200.0,
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
        }
    )

    assert outcome == Fail("invalid payload: missing required field: bot_uuid")
    repo.get_by_id.assert_not_called()
    baas_device_service.poll_publish_once.assert_not_called()


def test_restart_poll_allows_none_bot_uuid_and_uses_stale_guard():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": 3003},
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": None,
        }
    )

    assert outcome == Complete()
    baas_device_service.poll_publish_once.assert_not_called()


def test_create_poll_fails_when_started_at_epoch_s_is_string():
    repo = MagicMock()
    baas_service = MagicMock()
    task_queue_service = MagicMock()
    handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        task_queue_service=task_queue_service,
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": "123.4",
        }
    )

    assert outcome == Fail(
        "invalid payload: field started_at_epoch_s must be int or float"
    )
    repo.get_by_id.assert_not_called()
    baas_service.get_publish_progress.assert_not_called()


def test_restart_poll_fails_when_bot_uuid_has_wrong_type():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": 123,
        }
    )

    assert outcome == Fail("invalid payload: field bot_uuid must be str or None")
    repo.get_by_id.assert_not_called()


def test_restart_poll_completes_when_binding_is_terminal_or_not_baas():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
    )
    payload = {
        "binding_id": 42,
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "publish_id": 1001,
        "started_at_epoch_s": 123.4,
        "bot_uuid": "uuid-001",
    }
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.ACTIVE.value,
        device_props={"restart_publish_id": 1001},
    )

    assert handler.handle(payload) == Complete()

    non_baas = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": 1001},
    )
    non_baas.device_provider = "arca"
    repo.get_by_id.return_value = non_baas

    assert handler.handle(payload) == Complete()
    baas_device_service.poll_publish_once.assert_not_called()


def test_restart_poll_retries_when_baas_device_service_missing():
    repo = MagicMock()
    handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        clock=lambda: 200.0,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": 1001},
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": 123.4,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Retry("restart publish status service unavailable")


def test_restart_poll_retries_on_transient_status_and_direct_unexpected_status():
    repo = MagicMock()
    baas_device_service = MagicMock()
    bot_repository = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
    )
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={"restart_publish_id": 1001},
    )
    bot_repository.get_by_binding_id.return_value = {
        "status": DeviceBindingStatus.PENDING.value
    }
    payload = {
        "binding_id": 42,
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "publish_id": 1001,
        "started_at_epoch_s": 123.4,
        "bot_uuid": "uuid-001",
    }
    baas_device_service.poll_publish_once.return_value = None

    assert handler.handle(payload) == Retry("get_publish_progress transient error")

    assert handler._handle_terminal_restart_status(
        status="ODD",
        bot_id="bot-001",
        owner_id="owner-001",
        binding_id=42,
        publish_id=1001,
        bot_uuid="uuid-001",
        bot={},
        binding=repo.get_by_id.return_value,
    ) == Retry("unexpected publish status: ODD")


def test_restart_poll_fails_when_started_at_epoch_s_is_bool():
    repo = MagicMock()
    baas_device_service = MagicMock()
    handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
    )

    outcome = handler.handle(
        {
            "binding_id": 42,
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "publish_id": 1001,
            "started_at_epoch_s": True,
            "bot_uuid": "uuid-001",
        }
    )

    assert outcome == Fail(
        "invalid payload: field started_at_epoch_s must be int or float"
    )
    repo.get_by_id.assert_not_called()
    baas_device_service.poll_publish_once.assert_not_called()


def test_handler_invalid_payload_fails_without_side_effects():
    repo = MagicMock()
    baas_service = MagicMock()
    baas_device_service = MagicMock()
    task_queue_service = MagicMock()
    create_poll_handler = BaasCreatePublishPollHandler(
        binding_repository=repo,
        baas_service=baas_service,
        task_queue_service=task_queue_service,
    )
    create_init_handler = BaasCreateInitTaskHandler(
        binding_repository=repo,
        baas_device_service=baas_device_service,
    )
    restart_poll_handler = BaasRestartPublishPollHandler(
        binding_repository=repo,
        baas_device_service=baas_service,
    )

    outcomes = [
        create_poll_handler.handle({}),
        create_init_handler.handle({}),
        restart_poll_handler.handle({}),
    ]

    for outcome in outcomes:
        assert isinstance(outcome, Fail)
        assert outcome.error.startswith("invalid payload:")
    repo.get_by_id.assert_not_called()
    baas_service.get_publish_progress.assert_not_called()
    baas_service.poll_publish_once.assert_not_called()
    baas_device_service.assert_not_called()
    task_queue_service.enqueue.assert_not_called()


def test_baas_publish_task_lifecycle_registers_all_handlers():
    import asyncio

    registry = HandlerRegistry()
    lifecycle = BaasPublishTaskLifecycle(
        registry=registry,
        binding_repository=MagicMock(),
        baas_service=MagicMock(),
        task_queue_service=MagicMock(),
        baas_device_service=MagicMock(),
        bot_repository=MagicMock(),
        common_config_service=MagicMock(),
        template_service=MagicMock(),
    )

    asyncio.run(lifecycle.bootstrap())

    assert isinstance(
        registry.get(BAAS_CREATE_PUBLISH_POLL_TASK),
        BaasCreatePublishPollHandler,
    )
    assert isinstance(registry.get(BAAS_CREATE_INIT_TASK), BaasCreateInitTaskHandler)
    assert isinstance(
        registry.get(BAAS_RESTART_PUBLISH_POLL_TASK),
        BaasRestartPublishPollHandler,
    )


def test_create_init_success_publishes_baas_reconciliation_wakeup():
    reset_event_bus()
    received: list[BaasPublishCompletedEvent] = []
    get_event_bus().subscribe(BaasPublishCompletedEvent, received.append)
    try:
        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding(
            status=DeviceBindingStatus.PENDING.value,
            device_props={"publish_id": 1001},
        )
        baas_device_service = MagicMock()
        baas_device_service.run_create_init_once.return_value = (True, "ok")
        handler = BaasCreateInitTaskHandler(
            binding_repository=repo,
            baas_device_service=baas_device_service,
        )

        outcome = handler.handle(
            build_create_init_payload(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1001,
            )
        )

        assert outcome == Complete()
        assert received == [
            BaasPublishCompletedEvent(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1001,
                publish_kind="create",
            )
        ]
    finally:
        reset_event_bus()


def test_create_init_replay_after_active_reemits_reconciliation_wakeup():
    reset_event_bus()
    received: list[BaasPublishCompletedEvent] = []
    get_event_bus().subscribe(BaasPublishCompletedEvent, received.append)
    try:
        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding(
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"publish_id": 1001},
        )
        baas_device_service = MagicMock()
        handler = BaasCreateInitTaskHandler(
            binding_repository=repo,
            baas_device_service=baas_device_service,
        )

        outcome = handler.handle(
            build_create_init_payload(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1001,
            )
        )

        assert outcome == Complete()
        baas_device_service.run_create_init_once.assert_not_called()
        assert received == [
            BaasPublishCompletedEvent(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1001,
                publish_kind="create",
            )
        ]
    finally:
        reset_event_bus()


def test_restart_success_requests_runtime_projection_and_baas_reconciliation():
    reset_event_bus()
    received: list[BaasPublishCompletedEvent] = []
    projected: list[RuntimeProjectionRequestedEvent] = []
    activated: list[DeviceActivatedEvent] = []
    delivery_order: list[str] = []
    get_event_bus().subscribe(BaasPublishCompletedEvent, received.append)
    get_event_bus().subscribe(RuntimeProjectionRequestedEvent, projected.append)
    get_event_bus().subscribe(DeviceActivatedEvent, activated.append)
    get_event_bus().subscribe(
        BaasPublishCompletedEvent,
        lambda _event: delivery_order.append("baas_completed"),
    )
    get_event_bus().subscribe(
        RuntimeProjectionRequestedEvent,
        lambda _event: delivery_order.append("runtime_projection"),
    )
    try:
        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding(
            status=DeviceBindingStatus.PENDING.value,
            device_props={"restart_publish_id": 1002},
        )
        bot_repository = MagicMock()
        bot_repository.get_by_binding_id.return_value = {
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
        bot_repository.get_by_id_and_owner.return_value = {
            "ext": {},
        }
        baas_device_service = MagicMock()
        baas_device_service.poll_publish_once.return_value = (
            DeviceBindingStatus.ACTIVE.value
        )
        baas_device_service.refresh_codefuse_token_on_publish_success.return_value = (
            None
        )
        handler, _ = _make_restart_handler(
            repo=repo,
            bot_repository=bot_repository,
            baas_device_service=baas_device_service,
        )

        outcome = handler.handle(
            build_restart_publish_poll_payload(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1002,
                started_at_epoch_s=190.0,
                bot_uuid="baas-bot-1",
            )
        )

        assert outcome == Complete()
        assert received == [
            BaasPublishCompletedEvent(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1002,
                publish_kind="restart",
            )
        ]
        assert projected == [
            RuntimeProjectionRequestedEvent(
                device_id="device-001",
                binding_id=42,
                entity_id="owner-001",
                entity_type="staff",
                device_provider="baas",
                sandbox_id=None,
            )
        ]
        assert activated == []
        assert delivery_order == ["runtime_projection", "baas_completed"]
    finally:
        reset_event_bus()


def test_restart_persist_failure_does_not_publish_completion() -> None:
    reset_event_bus()
    received: list[BaasPublishCompletedEvent] = []
    get_event_bus().subscribe(BaasPublishCompletedEvent, received.append)
    try:
        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding(
            status=DeviceBindingStatus.PENDING.value,
            device_props={"restart_publish_id": 1002},
        )
        repo.update_status.side_effect = RuntimeError("database unavailable")
        bot_repository = MagicMock()
        bot_repository.get_by_binding_id.return_value = {
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }
        bot_repository.get_by_id_and_owner.return_value = {"ext": {}}
        baas_device_service = MagicMock()
        baas_device_service.poll_publish_once.return_value = (
            DeviceBindingStatus.ACTIVE.value
        )
        baas_device_service.refresh_codefuse_token_on_publish_success.return_value = (
            None
        )
        handler, _ = _make_restart_handler(
            repo=repo,
            bot_repository=bot_repository,
            baas_device_service=baas_device_service,
        )

        with pytest.raises(RuntimeError, match="database unavailable"):
            handler.handle(
                build_restart_publish_poll_payload(
                    binding_id=42,
                    bot_id="bot-001",
                    owner_id="owner-001",
                    publish_id=1002,
                    started_at_epoch_s=190.0,
                    bot_uuid="baas-bot-1",
                )
            )

        assert received == []
    finally:
        reset_event_bus()


def test_restart_replay_after_active_reemits_reconciliation_wakeup():
    reset_event_bus()
    received: list[BaasPublishCompletedEvent] = []
    get_event_bus().subscribe(BaasPublishCompletedEvent, received.append)
    try:
        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding(
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={"restart_publish_id": 1002},
        )
        bot_repository = MagicMock()
        baas_device_service = MagicMock()
        baas_device_service.poll_publish_once.return_value = (
            DeviceBindingStatus.ACTIVE.value
        )
        baas_device_service.refresh_codefuse_token_on_publish_success.return_value = (
            None
        )
        handler, _ = _make_restart_handler(
            repo=repo,
            bot_repository=bot_repository,
            baas_device_service=baas_device_service,
        )

        outcome = handler.handle(
            build_restart_publish_poll_payload(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1002,
                started_at_epoch_s=190.0,
                bot_uuid="baas-bot-1",
            )
        )

        assert outcome == Complete()
        baas_device_service.poll_publish_once.assert_called_once_with(publish_id=1002)
        assert received == [
            BaasPublishCompletedEvent(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1002,
                publish_kind="restart",
            )
        ]
    finally:
        reset_event_bus()


def test_restart_default_policy_is_persisted_only_after_active():
    reset_event_bus()
    received: list[BaasPublishCompletedEvent] = []
    get_event_bus().subscribe(BaasPublishCompletedEvent, received.append)
    try:
        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding(
            status=DeviceBindingStatus.PENDING.value,
            device_props={
                "restart_publish_id": 1002,
                "restart_image_policy_on_success": "default",
            },
        )
        bot_repository = MagicMock()
        bot_repository.get_by_binding_id.return_value = {
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "status": DeviceBindingStatus.PENDING.value,
        }
        publish_repository = MagicMock()
        baas_device_service = MagicMock()
        baas_device_service.poll_publish_once.return_value = (
            DeviceBindingStatus.ACTIVE.value
        )
        baas_device_service.refresh_codefuse_token_on_publish_success.return_value = (
            None
        )
        handler, _ = _make_restart_handler(
            repo=repo,
            bot_repository=bot_repository,
            publish_repository=publish_repository,
            baas_device_service=baas_device_service,
        )

        with patch(
            "agentclaw.community.core.devices.services."
            "baas_publish_task_handlers.persist_default_image_policy"
        ) as persist:
            outcome = handler.handle(
                build_restart_publish_poll_payload(
                    binding_id=42,
                    bot_id="bot-001",
                    owner_id="owner-001",
                    publish_id=1002,
                    started_at_epoch_s=190.0,
                    bot_uuid="baas-bot-1",
                )
            )

        assert outcome == Complete()
        persist.assert_called_once_with(
            bot_repository=bot_repository,
            publish_repository=publish_repository,
            bot_id="bot-001",
            owner_id="owner-001",
            env="dev",
            common_config_service=handler._common_config_service,
        )
        repo.update_device_props.assert_called_once_with(
            binding_id=42,
            props={
                "restart_request_id": None,
                "restart_workflow_baseline": None,
                "restart_image_policy_on_success": None,
            },
        )
        assert len(received) == 1
        assert received[0].publish_kind == "restart"
    finally:
        reset_event_bus()


@pytest.mark.parametrize("config_value", [None, {}, {"image": ""}])
def test_restart_success_does_not_persist_default_policy_when_switch_is_inactive(
    config_value,
):
    repo = MagicMock()
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={
            "restart_publish_id": 1002,
            "restart_image_policy_on_success": "default",
        },
    )
    bot_repository = MagicMock()
    bot_repository.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "status": DeviceBindingStatus.PENDING.value,
    }
    bot_repository.get_by_id_and_owner.return_value = {
        "ext": {
            "sbot_use_default_image": True,
            "sbot_pin_image": True,
            "sbot_docker_image": "stale:v1",
        }
    }
    publish_repository = MagicMock()
    publish_repository.get_draft_by_publish_bot_id.return_value = None
    baas_device_service = MagicMock()
    baas_device_service.poll_publish_once.return_value = DeviceBindingStatus.ACTIVE.value
    baas_device_service.refresh_codefuse_token_on_publish_success.return_value = None
    common_config = MagicMock()
    common_config.get_value.return_value = config_value
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        publish_repository=publish_repository,
        baas_device_service=baas_device_service,
        common_config_service=common_config,
    )

    outcome = handler.handle(
        build_restart_publish_poll_payload(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1002,
            started_at_epoch_s=190.0,
            bot_uuid="baas-bot-1",
        )
    )

    assert outcome == Complete()
    bot_repository.compare_and_set_ext.assert_called_once_with(
        bot_id="bot-001",
        owner_id="owner-001",
        expected_ext={
            "sbot_use_default_image": True,
            "sbot_pin_image": True,
            "sbot_docker_image": "stale:v1",
        },
        ext={
            "sbot_use_default_image": True,
            "sbot_pin_image": True,
            "sbot_docker_image": "stale:v1",
            "restart_publish_id": "1002",
        },
    )
    publish_repository.compare_and_set_ext.assert_not_called()
    repo.update_device_props.assert_called_once_with(
        binding_id=42,
        props={
            "restart_request_id": None,
            "restart_workflow_baseline": None,
            "restart_image_policy_on_success": None,
        },
    )


def test_restart_failed_publish_does_not_persist_default_policy():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={
            "restart_publish_id": 1002,
            "restart_image_policy_on_success": "default",
        },
    )
    bot_repository = MagicMock()
    bot_repository.get_by_binding_id.return_value = {
        "status": DeviceBindingStatus.PENDING.value
    }
    baas_device_service = MagicMock()
    baas_device_service.poll_publish_once.return_value = (
        DeviceBindingStatus.FAILED.value
    )
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        publish_repository=MagicMock(),
        baas_device_service=baas_device_service,
    )

    with patch(
        "agentclaw.community.core.devices.services."
        "baas_publish_task_handlers.persist_default_image_policy"
    ) as persist:
        outcome = handler.handle(
            build_restart_publish_poll_payload(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1002,
                started_at_epoch_s=190.0,
                bot_uuid="baas-bot-1",
            )
        )

    assert outcome == Complete()
    persist.assert_not_called()


def test_restart_codefuse_failure_clears_recovery_intent():
    request_id = "restart-request-1"
    repo = MagicMock()
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.PENDING.value,
        device_props={
            "restart_request_id": request_id,
            "restart_workflow_baseline": 1000,
            "restart_publish_id": 1002,
        },
    )
    bot_repository = MagicMock()
    bot_repository.get_by_binding_id.return_value = {
        "bot_id": "bot-001",
        "owner_id": "owner-001",
        "active_engine": "openclaw",
        "bot_type": "personal",
    }
    bot_repository.get_by_id_and_owner.return_value = {"ext": {}}
    bot_repository.update_by_owner.return_value = {"status": "FAILED"}
    bot_repository.compare_and_set_ext.return_value = {"status": "FAILED"}
    baas_device_service = MagicMock()
    baas_device_service.poll_publish_once.return_value = (
        DeviceBindingStatus.ACTIVE.value
    )
    baas_device_service.refresh_codefuse_token_on_publish_success.return_value = (
        "write codefuse token failed"
    )
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        baas_device_service=baas_device_service,
    )

    outcome = handler.handle(
        build_restart_publish_poll_payload(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1002,
            started_at_epoch_s=190.0,
            bot_uuid="baas-bot-1",
            request_id=request_id,
            workflow_baseline=1000,
        )
    )

    assert outcome == Complete()
    repo.update_status.assert_called_once_with(
        binding_id=42,
        status=DeviceBindingStatus.FAILED.value,
    )
    repo.update_device_props.assert_called_once_with(
        binding_id=42,
        props={
            "restart_request_id": None,
            "restart_workflow_baseline": None,
            "restart_image_policy_on_success": None,
        },
    )


def test_restart_failed_replay_retries_recovery_intent_cleanup():
    request_id = "restart-request-1"
    repo = MagicMock()
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.FAILED.value,
        device_props={
            "restart_request_id": request_id,
            "restart_workflow_baseline": 1000,
            "restart_publish_id": 1002,
        },
    )
    baas_device_service = MagicMock()
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=MagicMock(),
        baas_device_service=baas_device_service,
    )

    outcome = handler.handle(
        build_restart_publish_poll_payload(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1002,
            started_at_epoch_s=190.0,
            bot_uuid="baas-bot-1",
            request_id=request_id,
            workflow_baseline=1000,
        )
    )

    assert outcome == Complete()
    repo.update_device_props.assert_called_once_with(
        binding_id=42,
        props={
            "restart_request_id": None,
            "restart_workflow_baseline": None,
            "restart_image_policy_on_success": None,
        },
    )
    baas_device_service.poll_publish_once.assert_not_called()


def test_restart_default_policy_persistence_failure_retries_without_completion():
    reset_event_bus()
    received: list[BaasPublishCompletedEvent] = []
    get_event_bus().subscribe(BaasPublishCompletedEvent, received.append)
    try:
        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding(
            status=DeviceBindingStatus.ACTIVE.value,
            device_props={
                "restart_publish_id": 1002,
                "restart_image_policy_on_success": "default",
            },
        )
        baas_device_service = MagicMock()
        baas_device_service.poll_publish_once.return_value = (
            DeviceBindingStatus.ACTIVE.value
        )
        baas_device_service.refresh_codefuse_token_on_publish_success.return_value = (
            None
        )
        handler, _ = _make_restart_handler(
            repo=repo,
            bot_repository=MagicMock(),
            publish_repository=MagicMock(),
            baas_device_service=baas_device_service,
        )

        with patch(
            "agentclaw.community.core.devices.services."
            "baas_publish_task_handlers.persist_default_image_policy",
            side_effect=RuntimeError("database unavailable"),
        ):
            outcome = handler.handle(
                build_restart_publish_poll_payload(
                    binding_id=42,
                    bot_id="bot-001",
                    owner_id="owner-001",
                    publish_id=1002,
                    started_at_epoch_s=190.0,
                    bot_uuid="baas-bot-1",
                )
            )

        assert outcome == Retry("database unavailable")
        repo.update_device_props.assert_not_called()
        assert received == []
    finally:
        reset_event_bus()


def test_restart_replay_after_active_finishes_default_policy_persistence():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.ACTIVE.value,
        device_props={
            "restart_publish_id": 1002,
            "restart_image_policy_on_success": "default",
        },
    )
    baas_device_service = MagicMock()
    baas_device_service.poll_publish_once.return_value = (
        DeviceBindingStatus.ACTIVE.value
    )
    baas_device_service.refresh_codefuse_token_on_publish_success.return_value = None
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=MagicMock(),
        publish_repository=MagicMock(),
        baas_device_service=baas_device_service,
    )

    with patch(
        "agentclaw.community.core.devices.services."
        "baas_publish_task_handlers.persist_default_image_policy"
    ) as persist:
        outcome = handler.handle(
            build_restart_publish_poll_payload(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1002,
                started_at_epoch_s=190.0,
                bot_uuid="baas-bot-1",
            )
        )

    assert outcome == Complete()
    persist.assert_called_once()
    repo.update_device_props.assert_called_once_with(
        binding_id=42,
        props={
            "restart_request_id": None,
            "restart_workflow_baseline": None,
            "restart_image_policy_on_success": None,
        },
    )


def test_restart_old_active_does_not_finalize_while_current_publish_is_pending():
    repo = MagicMock()
    repo.get_by_id.return_value = _make_binding(
        status=DeviceBindingStatus.ACTIVE.value,
        device_props={
            "restart_publish_id": 1002,
            "restart_image_policy_on_success": "default",
        },
    )
    bot_repository = MagicMock()
    bot_repository.get_by_binding_id.return_value = {
        "status": DeviceBindingStatus.ACTIVE.value
    }
    baas_device_service = MagicMock()
    baas_device_service.poll_publish_once.return_value = (
        DeviceBindingStatus.PENDING.value
    )
    handler, _ = _make_restart_handler(
        repo=repo,
        bot_repository=bot_repository,
        publish_repository=MagicMock(),
        baas_device_service=baas_device_service,
    )

    with patch(
        "agentclaw.community.core.devices.services."
        "baas_publish_task_handlers.persist_default_image_policy"
    ) as persist:
        outcome = handler.handle(
            build_restart_publish_poll_payload(
                binding_id=42,
                bot_id="bot-001",
                owner_id="owner-001",
                publish_id=1002,
                started_at_epoch_s=190.0,
                bot_uuid="baas-bot-1",
            )
        )

    assert outcome == Reschedule(10.0)
    baas_device_service.poll_publish_once.assert_called_once_with(publish_id=1002)
    persist.assert_not_called()
    repo.update_status.assert_not_called()
