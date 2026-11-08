"""The task handlers' edge cases: a missing row, a lost submit, a failing hop."""
from __future__ import annotations

import asyncio
import time

import pytest

from agentclaw.community.core.acli_clis import tasks as tasks_module
from agentclaw.community.core.acli_clis.operations import (
    AcliAction,
    EngineState,
    OperationKind,
    EngineOperation,
    OperationPayload,
)
from agentclaw.community.core.acli_clis.port import AcliPortError
from agentclaw.community.core.acli_clis.repository.models import AcliOperationRecord
from agentclaw.community.core.acli_clis.tasks import AcliTaskLifecycle
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.core.task_queue.services.registry import HandlerRegistry
from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule

from .fakes import AgentPass, Bots, Engine, Queue, Store, bot, handlers

OP_ID = "a" * 32
TARGET_ID = "b" * 32


def _payload(**fields) -> dict:
    return OperationPayload.from_dict({
        "operation_id": OP_ID, "kind": "operation", "action": "install",
        "cli_code": "yuque-cli", "bot_pk": 7, "bot_id": "bot-1", "entity_id": "owner-1",
        "owner_id": "owner-1", "stage": "draft", "actor_id": "owner-1",
        "enqueued_at": time.time(), **fields,
    }).to_dict()


def _row(op_id: str = OP_ID, *, kind: str = "operation", **fields) -> AcliOperationRecord:
    return AcliOperationRecord(
        operation_id=op_id, task_id=fields.pop("task_id", 1), kind=OperationKind(kind),
        action=AcliAction.INSTALL, cli_code="yuque-cli", bot_pk=7, stage=PublishStage.DRAFT,
        **fields,
    )


@pytest.fixture
def world():
    store, engine, queue = Store(), Engine(), Queue()
    operation, cancel = handlers(
        store=store, engine=engine, bots=Bots(bot()), agentpass=AgentPass([]), queue=queue,
    )
    return store, engine, queue, operation, cancel


@pytest.mark.asyncio
async def test_a_task_waits_briefly_for_its_row_then_gives_up(world) -> None:
    _, engine, _, operation, cancel = world

    assert isinstance(await operation._step(_payload()), Reschedule)
    old = _payload(enqueued_at=time.time() - tasks_module.RECORD_GRACE_SECONDS - 1)
    outcome = await operation._step(old)
    assert isinstance(outcome, Fail) and outcome.error.startswith("not_recorded:")
    assert isinstance(await cancel._step(old | {"kind": "cancel"}), Fail)
    assert engine.calls == []


@pytest.mark.asyncio
async def test_a_lost_submit_is_found_again_by_its_busy_answer(world) -> None:
    store, engine, _, operation, _ = world
    engine.autorun = None
    store.create(_row())
    # The first run submitted, and its lease was lost before the id was written.
    earlier = await engine.submit(
        OperationPayload.from_dict(_payload()).runtime, action=AcliAction.INSTALL, name="yuque-cli"
    )

    outcome = await operation._step(_payload())

    assert isinstance(outcome, Reschedule)
    assert store.get(OP_ID).engine_operation_id == earlier.id


@pytest.mark.asyncio
async def test_someone_else_s_busy_operation_is_waited_out(world) -> None:
    store, engine, _, operation, _ = world
    engine.autorun = None
    store.create(_row())
    await engine.submit(
        OperationPayload.from_dict(_payload()).runtime, action=AcliAction.UNINSTALL, name="other-cli"
    )

    assert isinstance(await operation._step(_payload()), Reschedule)
    assert store.get(OP_ID).engine_operation_id is None


@pytest.mark.parametrize(
    ("code", "outcome"),
    [("timeout", Reschedule), ("engine_unreachable", Reschedule), ("not_running", Fail)],
)
@pytest.mark.asyncio
async def test_polling_failures(world, code, outcome) -> None:
    store, engine, _, operation, _ = world
    store.create(_row(engine_operation_id="e" * 32))
    engine.errors["get"] = [AcliPortError(code, "x")]

    assert isinstance(await operation._step(_payload()), outcome)


@pytest.mark.asyncio
async def test_an_operation_the_engine_forgot_fails(world) -> None:
    store, _, _, operation, _ = world
    store.create(_row(engine_operation_id="e" * 32))

    outcome = await operation._step(_payload())

    assert isinstance(outcome, Fail) and outcome.error.startswith("operation_lost:")


@pytest.mark.asyncio
async def test_an_uninstall_needs_no_grant(world) -> None:
    store, engine, _, operation, _ = world
    store.create(_row())

    outcome = await operation._step(_payload(action="uninstall"))

    assert isinstance(outcome, Complete)
    assert engine.calls[0][:3] == ("submit", "draft", "uninstall")


@pytest.mark.asyncio
async def test_an_engine_without_the_endpoints_fails_the_operation_at_once(world) -> None:
    """A missing endpoint does not come back: no retry holds the stage's slot."""
    store, engine, _, operation, _ = world
    store.create(_row())
    engine.errors["submit"] = [AcliPortError("engine_outdated", "no acli endpoints")]

    outcome = await operation._step(_payload())

    assert isinstance(outcome, Fail) and outcome.error.startswith("engine_outdated:")
    assert store.get(OP_ID).engine_operation_id is None


def _cancel_world(world, *, target_engine_id: str | None = "e" * 32, own_engine_id=None):
    store, engine, queue, _, cancel = world
    task = queue.enqueue("acli_clis.operation", {}, 60, idempotency_key="k")
    store.create(_row(TARGET_ID, task_id=task.record.id, engine_operation_id=target_engine_id))
    store.create(_row(OP_ID, kind="cancel", target_operation_id=TARGET_ID,
                      engine_operation_id=own_engine_id))
    return store, engine, queue, cancel, _payload(kind="cancel", target_operation_id=TARGET_ID)


@pytest.mark.asyncio
async def test_a_cancel_whose_target_ended_before_the_engine_is_not_active(world) -> None:
    store, engine, queue, cancel, payload = _cancel_world(world, target_engine_id=None)
    queue.apply(next(iter(queue.tasks.values())), Fail("grant_failed: x"))

    outcome = await cancel._step(payload)

    assert isinstance(outcome, Fail) and outcome.error.startswith("not_active:")


@pytest.mark.asyncio
async def test_a_cancel_of_a_vanished_target_is_not_active(world) -> None:
    store, *_ , cancel = world
    store.create(_row(OP_ID, kind="cancel", target_operation_id=TARGET_ID))

    outcome = await cancel._step(_payload(kind="cancel", target_operation_id=TARGET_ID))

    assert isinstance(outcome, Fail) and outcome.error.startswith("not_active:")


@pytest.mark.parametrize(
    ("error", "outcome", "prefix"),
    [
        (AcliPortError("timeout", "x"), Reschedule, None),
        (AcliPortError("not_active", "x"), Fail, "not_active:"),
        (AcliPortError("engine_outdated", "x"), Fail, "engine_outdated:"),
    ],
)
@pytest.mark.asyncio
async def test_forwarding_a_cancel(world, error, outcome, prefix) -> None:
    _, engine, _, cancel, payload = _cancel_world(world)
    engine.errors["cancel"] = [error]

    result = await cancel._step(payload)

    assert isinstance(result, outcome)
    if prefix:
        assert result.error.startswith(prefix)


@pytest.mark.asyncio
async def test_a_cancel_that_reached_another_operation_is_not_active(world) -> None:
    _, engine, _, cancel, payload = _cancel_world(world, target_engine_id="f" * 32)
    engine.autorun = None
    await engine.submit(
        OperationPayload.from_dict(payload).runtime, action=AcliAction.INSTALL, name="yuque-cli"
    )

    outcome = await cancel._step(payload)

    assert isinstance(outcome, Fail) and outcome.error.startswith("not_active:")


@pytest.mark.parametrize(
    ("state", "outcome", "prefix"),
    [
        ("cancelling", Reschedule, None),
        ("cancelled", Complete, None),
        ("succeeded", Fail, "not_active:"),
        ("failed", Fail, "revert_failed:"),
    ],
)
@pytest.mark.asyncio
async def test_a_forwarded_cancel_ends_with_the_engine_operation(
    world, state, outcome, prefix
) -> None:
    _, engine, _, cancel, payload = _cancel_world(world, own_engine_id="e" * 32)
    engine.ops["e" * 32] = EngineOperation(
        id="e" * 32, action=AcliAction.INSTALL, name="yuque-cli", state=EngineState(state),
        error={"code": "revert_failed", "message": "x"} if state == "failed" else None,
    )

    result = await cancel._step(payload)

    assert isinstance(result, outcome)
    if prefix:
        assert result.error.startswith(prefix)


@pytest.mark.parametrize(("code", "outcome"), [("timeout", Reschedule), ("not_running", Fail)])
@pytest.mark.asyncio
async def test_polling_a_forwarded_cancel_fails_like_an_operation(world, code, outcome) -> None:
    _, engine, _, cancel, payload = _cancel_world(world, own_engine_id="e" * 32)
    engine.errors["get"] = [AcliPortError(code, "x")]

    assert isinstance(await cancel._step(payload), outcome)


@pytest.mark.asyncio
async def test_a_forwarded_cancel_the_engine_forgot_fails(world) -> None:
    _, _, _, cancel, payload = _cancel_world(world, own_engine_id="e" * 32)

    outcome = await cancel._step(payload)

    assert isinstance(outcome, Fail) and outcome.error.startswith("operation_lost:")


def test_handle_runs_a_step_and_completes_an_empty_payload(world) -> None:
    store, _, _, operation, cancel = world
    store.create(_row())

    assert isinstance(operation.handle(_payload()), Complete)
    assert isinstance(operation.handle(None), Complete)
    assert isinstance(cancel.handle({}), Complete)


def test_the_lifecycle_registers_both_types(world) -> None:
    *_, operation, cancel = world
    registry = HandlerRegistry()

    asyncio.run(AcliTaskLifecycle(
        registry=registry, operation_handler=operation, cancel_handler=cancel
    ).bootstrap())

    assert registry.get(operation.task_type) is operation
    assert registry.get(cancel.task_type) is cancel
    assert registry.wakes_on_enqueue(operation.task_type)


@pytest.mark.asyncio
async def test_an_install_that_resolved_an_alias_fails_with_the_market_name(world) -> None:
    """What landed is granted too, so installed ⇒ granted still holds."""
    store, engine, _, operation, _ = world
    store.create(_row(engine_operation_id="e" * 32))
    engine.ops["e" * 32] = EngineOperation(
        id="e" * 32, action=AcliAction.INSTALL, name="di", state=EngineState.SUCCEEDED,
        result={"name": "deepinsight-cli", "command": "di"},
    )

    outcome = await operation._step(_payload(cli_code="di"))

    assert isinstance(outcome, Fail)
    assert outcome.error == "name_mismatch: 'di' is a command; use 'deepinsight-cli'"
    assert operation._reconciler.codes == ["deepinsight-cli"]


@pytest.mark.asyncio
async def test_an_alias_install_whose_market_name_cannot_be_granted_says_so(world) -> None:
    store, engine, _, operation, _ = world
    operation._reconciler.drops_writes = True
    store.create(_row(engine_operation_id="e" * 32))
    engine.ops["e" * 32] = EngineOperation(
        id="e" * 32, action=AcliAction.INSTALL, name="di", state=EngineState.SUCCEEDED,
        result={"name": "deepinsight-cli", "command": "di"},
    )

    outcome = await operation._step(_payload(cli_code="di"))

    assert isinstance(outcome, Fail) and outcome.error.startswith("grant_failed:")
