"""The task payload and the task-error text, both of which outlive a deploy."""
from __future__ import annotations

from agentclaw.community.core.acli_clis.operations import (
    AcliRuntime,
    EngineOperation,
    OperationPayload,
    cancel_key,
    operation_key,
    parse_task_error,
    task_error,
)


def test_the_payload_round_trips_through_json_shape() -> None:
    payload = OperationPayload(
        operation_id="a" * 32, kind="cancel", action="install", cli_code="yuque-cli",
        bot_pk=7, bot_id="bot-1", entity_id="ent-1", owner_id="owner-1", stage="verify",
        actor_id="owner-1", enqueued_at=12.5, target_operation_id="b" * 32,
    )

    again = OperationPayload.from_dict(payload.to_dict())

    assert again == payload
    assert again.runtime == AcliRuntime(bot_id="bot-1", owner_id="owner-1", stage="verify")


def test_the_two_keys_never_collide() -> None:
    assert operation_key(7, "draft") == "acli:op:7:draft"
    assert cancel_key(7, "draft") == "acli:cancel:7:draft"


def test_task_errors_parse_back_to_their_code() -> None:
    assert parse_task_error(task_error("install_failed", "no: such cli")) == (
        "install_failed", "no: such cli",
    )
    assert parse_task_error("Traceback (most recent call last): boom") == (
        "failed", "Traceback (most recent call last): boom",
    )
    assert parse_task_error(None) == ("failed", "the operation failed")


def test_an_engine_operation_without_an_error_describes_its_state() -> None:
    op = EngineOperation(id="x", action="install", name="y", state="interrupted")

    assert (op.active, op.error_code, op.error_message) == (
        False, "interrupted", "the operation was interrupted",
    )
