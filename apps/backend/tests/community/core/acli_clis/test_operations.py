"""The task payload and the task-error text, both of which outlive a deploy."""
from __future__ import annotations

from agentclaw.community.core.acli_clis.operations import (
    AcliAction,
    EngineState,
    OperationKind,
    AcliRuntime,
    EngineOperation,
    OperationPayload,
    cancel_key,
    operation_key,
    parse_task_error,
    task_error,
)
from agentclaw.community.core.service_bot.types import PublishStage


def test_the_payload_round_trips_through_json_shape() -> None:
    payload = OperationPayload(
        operation_id="a" * 32, kind=OperationKind.CANCEL, action=AcliAction.INSTALL,
        cli_code="yuque-cli", bot_pk=7, bot_id="bot-1", entity_id="ent-1", owner_id="owner-1",
        stage=PublishStage.VERIFY,
        actor_id="owner-1", enqueued_at=12.5, target_operation_id="b" * 32, engine="teclaw",
    )

    again = OperationPayload.from_dict(payload.to_dict())

    assert again == payload
    assert again.runtime == AcliRuntime(
        bot_id="bot-1", owner_id="owner-1", stage=PublishStage.VERIFY, engine="teclaw"
    )
    assert again.to_dict()["stage"] == "verify" and type(again.to_dict()["kind"]) is str


def test_a_payload_enqueued_before_the_engine_was_recorded_reads_with_none() -> None:
    old = OperationPayload(
        operation_id="a" * 32, kind=OperationKind.OPERATION, action=AcliAction.INSTALL,
        cli_code="yuque-cli", bot_pk=7, bot_id="bot-1", entity_id="ent-1", owner_id="owner-1",
        stage=PublishStage.DRAFT, actor_id="owner-1", enqueued_at=12.5,
    ).to_dict()
    del old["engine"]

    assert OperationPayload.from_dict(old).runtime.engine == ""


def test_the_two_keys_never_collide() -> None:
    assert operation_key(7, PublishStage.DRAFT) == "acli:op:7:draft"
    assert cancel_key(7, PublishStage.DRAFT) == "acli:cancel:7:draft"


def test_task_errors_parse_back_to_their_code() -> None:
    assert parse_task_error(task_error("install_failed", "no: such cli")) == (
        "install_failed", "no: such cli",
    )
    assert parse_task_error("Traceback (most recent call last): boom") == (
        "failed", "Traceback (most recent call last): boom",
    )
    assert parse_task_error(None) == ("failed", "the operation failed")


def test_an_engine_operation_without_an_error_describes_its_state() -> None:
    op = EngineOperation(
        id="x", action=AcliAction.INSTALL, name="y", state=EngineState.INTERRUPTED
    )

    assert (op.active, op.error_code, op.error_message) == (
        False, "interrupted", "the operation was interrupted",
    )
