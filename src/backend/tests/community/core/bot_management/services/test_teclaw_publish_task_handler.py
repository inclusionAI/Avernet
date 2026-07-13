import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.teclaw_publish_task_handler import (
    TECLAW_CREATE_PUBLISH_POLL_TASK,
    TECLAW_PUBLISH_TASK_DEADLINE_SECONDS,
    TeclawPublishTaskHandler,
    TeclawPublishTaskLifecycle,
    build_teclaw_publish_poll_payload,
    map_publish_status,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, Retry


def _binding(
    *,
    status: str = "PENDING",
    provider: str = "teclaw",
    publish_id: int | str = 9,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=77,
        entity_id="staff-1",
        entity_type="staff",
        device_id="BOT-x",
        device_provider=provider,
        env="dev",
        device_props={"publish_id": publish_id},
        status=status,
        apply_reason=None,
        applied_by="u1",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


def _payload(**updates) -> dict:
    payload = build_teclaw_publish_poll_payload(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        started_at_epoch_s=100.0,
    )
    payload.update(updates)
    return payload


def _handler(*, clock=lambda: 200.0):
    baas = MagicMock()
    bot_repo = MagicMock()
    bot_repo.update_by_owner.return_value = {"status": "PENDING"}
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = _binding()
    handler = TeclawPublishTaskHandler(
        baas_service=baas,
        bot_repository=bot_repo,
        device_binding_repo=binding_repo,
        poll_delay_seconds=10.0,
        clock=clock,
    )
    return handler, baas, bot_repo, binding_repo


def test_build_publish_poll_payload_and_deadline():
    assert TECLAW_PUBLISH_TASK_DEADLINE_SECONDS == 86400
    assert build_teclaw_publish_poll_payload(
        binding_id=77,
        bot_id="b1",
        owner_id="u1",
        publish_id=9,
        started_at_epoch_s=100.0,
    ) == {
        "binding_id": 77,
        "bot_id": "b1",
        "owner_id": "u1",
        "publish_id": 9,
        "started_at_epoch_s": 100.0,
    }


@pytest.mark.parametrize(
    "publish_status,expected",
    [
        ("SUCCESS", "ACTIVE"),
        ("FAILED", "FAILED"),
        ("REJECTED", "FAILED"),
        ("REVOKED", "FAILED"),
        ("PENDING", "PENDING"),
        (None, "PENDING"),
    ],
)
def test_map_publish_status(publish_status, expected):
    assert map_publish_status(publish_status) == expected


def test_pending_publish_reschedules_before_timeout():
    handler, baas, bot_repo, binding_repo = _handler(clock=lambda: 699.0)
    baas.get_publish_progress.return_value = {"status": "PENDING"}

    assert handler.handle(_payload()) == Reschedule(10.0)
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


def test_timeout_polls_once_then_preserves_pending():
    handler, baas, bot_repo, binding_repo = _handler(clock=lambda: 700.0)
    baas.get_publish_progress.return_value = {"status": "PENDING"}

    assert handler.handle(_payload()) == Complete()
    baas.get_publish_progress.assert_called_once_with(9)
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


@pytest.mark.parametrize(
    "publish_status,stored_status",
    [
        ("SUCCESS", "ACTIVE"),
        ("FAILED", "FAILED"),
        ("REJECTED", "FAILED"),
        ("REVOKED", "FAILED"),
    ],
)
def test_terminal_publish_persists_bot_then_binding(publish_status, stored_status):
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": publish_status}

    assert handler.handle(_payload()) == Complete()
    bot_repo.update_by_owner.assert_called_once_with(
        "b1", "u1", {"status": stored_status}
    )
    binding_repo.update_status.assert_called_once_with(
        binding_id=77, status=stored_status
    )


def test_terminal_publish_still_converges_after_business_timeout():
    handler, baas, bot_repo, binding_repo = _handler(clock=lambda: 900.0)
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}

    assert handler.handle(_payload()) == Complete()
    bot_repo.update_by_owner.assert_called_once_with("b1", "u1", {"status": "ACTIVE"})
    binding_repo.update_status.assert_called_once_with(binding_id=77, status="ACTIVE")


def test_bot_write_failure_retries_before_binding_write():
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    bot_repo.update_by_owner.side_effect = RuntimeError("bot db down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    binding_repo.update_status.assert_not_called()


def test_missing_bot_write_retries_before_binding_write():
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    bot_repo.update_by_owner.return_value = None

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    binding_repo.update_status.assert_not_called()


def test_partial_terminal_write_retries_until_binding_converges():
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    binding_repo.update_status.side_effect = [RuntimeError("db down"), None]

    first = handler.handle(_payload())
    second = handler.handle(_payload())

    assert isinstance(first, Retry)
    assert second == Complete()
    assert bot_repo.update_by_owner.call_count == 2
    assert binding_repo.update_status.call_count == 2


def test_transient_publish_query_returns_retry():
    handler, baas, bot_repo, binding_repo = _handler()
    baas.get_publish_progress.side_effect = RuntimeError("baas down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


def test_lifecycle_registers_handler():
    registry = HandlerRegistry()
    lifecycle = TeclawPublishTaskLifecycle(
        registry=registry,
        baas_service=MagicMock(),
        bot_repository=MagicMock(),
        device_binding_repo=MagicMock(),
    )

    asyncio.run(lifecycle.bootstrap())

    assert isinstance(
        registry.get(TECLAW_CREATE_PUBLISH_POLL_TASK),
        TeclawPublishTaskHandler,
    )


@pytest.mark.parametrize(
    "binding",
    [
        None,
        _binding(status="ACTIVE"),
        _binding(status="FAILED"),
        _binding(status="RELEASED"),
        _binding(provider="baas"),
        _binding(publish_id=10),
    ],
)
def test_stale_or_terminal_binding_completes_without_polling(binding):
    handler, baas, bot_repo, binding_repo = _handler()
    binding_repo.get_by_id.return_value = binding

    assert handler.handle(_payload()) == Complete()
    baas.get_publish_progress.assert_not_called()
    bot_repo.update_by_owner.assert_not_called()
    binding_repo.update_status.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        _payload(binding_id=True),
        _payload(bot_id=7),
        _payload(owner_id=7),
        _payload(publish_id="9"),
        _payload(started_at_epoch_s="100"),
    ],
)
def test_invalid_payload_fails_before_repository_access(payload):
    handler, baas, _, binding_repo = _handler()

    outcome = handler.handle(payload)

    assert isinstance(outcome, Fail)
    assert outcome.error.startswith("invalid payload:")
    binding_repo.get_by_id.assert_not_called()
    baas.get_publish_progress.assert_not_called()


def test_binding_read_failure_returns_retry():
    handler, baas, _, binding_repo = _handler()
    binding_repo.get_by_id.side_effect = RuntimeError("binding db down")

    outcome = handler.handle(_payload())

    assert isinstance(outcome, Retry)
    assert "binding db down" in outcome.error
    baas.get_publish_progress.assert_not_called()
