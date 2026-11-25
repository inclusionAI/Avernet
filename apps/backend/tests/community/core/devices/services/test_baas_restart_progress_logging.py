"""Restart telemetry uses the existing clock/identity without changing outcomes."""

import logging
import shlex
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
    BaasRestartPublishPollHandler,
)
from agentclaw.community.core.task_queue.types import Complete, Reschedule, Retry


@pytest.fixture
def restart(caplog):
    caplog.set_level(logging.INFO, logger="start")
    bindings, bots, baas = MagicMock(), MagicMock(), MagicMock()
    binding = DeviceBindingRecord(
        id=42, entity_id="owner-001", entity_type="staff", device_id="device-001",
        device_provider="baas", env="pre", status="PENDING",
        device_props={"restart_publish_id": "1001", "restart_request_id": "request-001"},
        apply_reason=None, applied_by="owner-001", release_reason=None,
        released_by=None, released_at=None, last_alive_at=None,
        gmt_create=datetime.now(), gmt_modified=datetime.now(),
    )
    bindings.get_by_id.return_value = binding
    bots.get_by_binding_id.return_value = {
        "bot_id": "bot-001", "active_engine": "claude_code", "ext": {},
        "token": "<test-token>",
    }
    bots.get_by_id_and_owner.return_value = {"ext": {}}
    baas.poll_publish_once.return_value = "PENDING"
    now = [700.0]
    handler = BaasRestartPublishPollHandler(
        binding_repository=bindings, bot_repository=bots,
        baas_device_service=baas, baas_service=MagicMock(),
        clock=lambda: now[0], poll_delay_seconds=10,
    )
    payload = {
        "binding_id": 42, "bot_id": "bot-001", "owner_id": "owner-001",
        "publish_id": 1001, "request_id": "request-001",
        "started_at_epoch_s": 100.0, "bot_uuid": "uuid-001",
    }
    return handler, payload, binding, bindings, bots, baas, now


def progress(caplog):
    messages = [r.getMessage() for r in caplog.records
                if "event=bot_restart_progress " in r.getMessage()]
    assert messages, "A valid restart poll must emit structured progress"
    assert all("<test-token>" not in m for m in messages)
    assert all(m.startswith("[BaasRestartPublishPollHandler] event=bot_restart_progress ")
               for m in messages)
    assert all("\n" not in m and "\r" not in m for m in messages)
    return [dict(part.split("=", 1) for part in shlex.split(m)[1:]) for m in messages]


@pytest.mark.parametrize("engine", ["aicoding", "claude_code", "openclaw"])
@pytest.mark.parametrize("elapsed", [599.0, 600.0, 601.0, 1799.9])
def test_pending_logs_per_bot_elapsed_and_engine_without_changing_timeout(
    restart, caplog, engine, elapsed,
):
    handler, payload, binding, bindings, bots, baas, now = restart
    bots.get_by_binding_id.return_value["active_engine"] = engine
    now[0] = payload["started_at_epoch_s"] + elapsed

    assert handler.handle(payload) == Reschedule(10)

    event = progress(caplog)[-1]
    assert {k: event[k] for k in (
        "bot_id", "binding_id", "publish_id", "request_id",
        "engine_type", "env", "status",
    )} == {
        "bot_id": "bot-001", "binding_id": "42",
        "publish_id": "1001", "request_id": "request-001",
        "engine_type": engine, "env": "pre", "status": "PENDING",
    }
    assert float(event["elapsed_s"]) == pytest.approx(elapsed)
    assert event["timeout_s"] == "1800"
    bots.get_by_binding_id.assert_called_once_with(42)
    bindings.transition_baas_restart_terminal.assert_not_called()
    baas.poll_publish_once.assert_called_once_with(publish_id=1001)


def test_transient_query_failure_remains_observable_and_retryable(restart, caplog):
    handler, payload, _, bindings, _, baas, _ = restart
    baas.poll_publish_once.return_value = None

    assert isinstance(handler.handle(payload), Retry)

    assert progress(caplog)[-1]["status"] == "RETRYING"
    bindings.transition_baas_restart_terminal.assert_not_called()


def test_30_minute_timeout_is_logged_without_changing_failure_transition(restart, caplog):
    handler, payload, _, bindings, _, baas, now = restart
    now[0] = payload["started_at_epoch_s"] + 1800

    assert handler.handle(payload) == Complete()

    event = progress(caplog)[-1]
    assert event["status"] == "TIMED_OUT"
    assert float(event["elapsed_s"]) == 1800
    assert bindings.transition_baas_restart_terminal.call_args.kwargs["status"] == "FAILED"
    baas.poll_publish_once.assert_not_called()


def test_workflow_adoption_wait_logs_engine_and_elapsed(restart, caplog):
    handler, payload, binding, _, _, _, _ = restart
    payload.pop("publish_id")
    payload["workflow_baseline"] = 1000
    binding.device_props["restart_publish_id"] = None
    handler._baas_service.list_bot_publishes.return_value = []

    assert handler.handle(payload) == Reschedule(10)

    event = progress(caplog)[-1]
    assert event["status"] == "ADOPTING"
    assert event["engine_type"] == "claude_code"
    assert event["publish_id"] == "-"
    assert float(event["elapsed_s"]) == 600


def test_metadata_unavailable_does_not_break_workflow_adoption(restart, caplog):
    handler, payload, binding, _, bots, _, _ = restart
    payload.pop("publish_id")
    payload["workflow_baseline"] = 1000
    binding.device_props["restart_publish_id"] = None
    handler._baas_service.list_bot_publishes.return_value = []
    bots.get_by_binding_id.side_effect = RuntimeError("<test-token>")

    assert handler.handle(payload) == Reschedule(10)

    assert progress(caplog)[-1]["engine_type"] == "unknown"


@pytest.mark.parametrize("stale", ["different-request", None])
def test_stale_or_uncommitted_intent_emits_no_progress(restart, caplog, stale):
    handler, payload, binding, _, _, baas, _ = restart
    binding.device_props["restart_request_id"] = stale

    handler.handle(payload)

    assert not any("event=bot_restart_progress " in r.getMessage()
                   for r in caplog.records)
    baas.poll_publish_once.assert_not_called()


def test_engine_and_identity_cannot_inject_log_fields(restart, caplog):
    handler, payload, _, _, bots, _, _ = restart
    bots.get_by_binding_id.return_value["active_engine"] = "claude_code\nstatus=FAILED"
    payload["bot_id"] = "bot-001\nengine_type=forged"

    assert handler.handle(payload) == Reschedule(10)

    event = progress(caplog)[-1]
    assert event["status"] == "PENDING"
    assert event["engine_type"] == r"claude_code\nstatus=FAILED"
    assert event["bot_id"] == r"bot-001\nengine_type=forged"


def test_repeated_polls_keep_the_persisted_start_time(restart, caplog):
    handler, payload, _, _, _, _, now = restart
    assert handler.handle(payload) == Reschedule(10)
    now[0] += 10
    assert handler.handle(dict(payload)) == Reschedule(10)

    assert [float(e["elapsed_s"]) for e in progress(caplog)] == [600, 610]
    assert payload["started_at_epoch_s"] == 100.0


def test_backward_clock_does_not_emit_negative_elapsed(restart, caplog):
    handler, payload, _, _, _, _, now = restart
    now[0] = 99.0
    assert handler.handle(payload) == Reschedule(10)
    assert float(progress(caplog)[-1]["elapsed_s"]) == 0
