"""Runtime conformance: real SQLite, isolated fake providers, no real messages."""

import asyncio
import sqlite3
import threading

import pytest

from engine.community.core.dws_hosting.delivery import HostingDeliveryWorker
from engine.community.core.dws_hosting.execution import HostingExecutor, invocation_for
from engine.community.core.dws_hosting.runtime import HostingRuntime
from engine.community.core.dws_hosting.storage import durable_call
from engine.community.di.dws_hosting import HostingRuntimeConfig, build_dws_runtime
from engine.community.kernel.dws_connection import DwsError, ListenerNotice
from engine.community.kernel.dws_delivery import DeliveryReceipt
from engine.community.kernel.dws_execution import EngineUpdate
from engine.community.kernel.dws_runtime import (
    HostingCapacityError,
    Resolution,
    RuntimePolicy,
)
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore
from engine.community.plugins.dws_hosting.runtime import SqliteHostingRuntimeStore
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore

from .test_delivery import Clock, Sender, resolve
from .test_execution import Engine, build_execution, result_json
from .test_intake import Connection, wait_until
from .test_state_contract import identity, message
from .test_context import VerifiedContextFixture, permitted_context


class Intake:
    def __init__(self, recovery, clock):
        self.recovery, self.clock = recovery, clock
        self.entered, self.release, self.fail = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        self.closed = False

    async def run(self):
        try:
            self.entered.set()
            await self.release.wait()
            window = self.recovery.recovery_window(
                "binding", now_ms=self.clock(), overlap_ms=1000, window_ms=60000
            )
            assert self.recovery.complete_recovery(window)
            await self.fail.wait()
            raise DwsError("test_listener_failure")
        finally:
            self.closed = True


def runtime_case(tmp_path, *, engine=None, policy=None):
    state, ledger, turn, directory = build_execution(tmp_path)
    clock, sender = Clock(), Sender()
    recovery, outbox = (
        SqliteHostingRecoveryStore(directory),
        SqliteHostingOutbox(directory),
    )
    engine = engine or Engine()
    operations = SqliteHostingRuntimeStore(directory, policy or RuntimePolicy())
    intake = Intake(recovery, clock)
    runtime = HostingRuntime(
        identity(),
        state,
        recovery,
        operations,
        intake,
        HostingExecutor(state, ledger, engine, VerifiedContextFixture()),
        HostingDeliveryWorker(outbox, sender, clock),
        outbox,
        clock_ms=clock,
        policy=RuntimePolicy(tick_seconds=0.01),
    )
    return runtime, state, ledger, turn, directory, sender, engine, intake, clock


async def stop(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_recovery_barrier_then_execute_enqueue_send_and_stop(tmp_path):
    runtime, _, _, _, _, sender, engine, intake, _ = runtime_case(tmp_path)
    task = asyncio.create_task(runtime.run())
    try:
        await intake.entered.wait()
        await asyncio.sleep(0.04)
        assert not engine.calls and not sender.sent
        intake.release.set()
        await wait_until(lambda: len(sender.sent) == 1)
        await asyncio.sleep(0.04)
        assert len(engine.calls) == len(sender.sent) == 1
    finally:
        await stop(task)
    assert intake.closed
    assert not (await runtime.status(identity().owner_id)).intake_ready


@pytest.mark.parametrize("phase", ["received", "resolved", "queued"])
async def test_restart_saved_result_never_reexecutes(tmp_path, phase):
    from engine.community.kernel.dws_execution import HostingScope
    runtime, _, ledger, turn, _, sender, engine, intake, _ = runtime_case(tmp_path)
    request = invocation_for(turn, permitted_context(turn))
    ledger.reserve(turn, request)
    ledger.record_scope("binding", turn.turn_id, HostingScope(decision="allow", reason_code="inside_rule"))
    ledger.record_update(
        "binding",
        turn.turn_id,
        EngineUpdate(
            run_id=request.run_id, status="completed", result_json=result_json()
        ),
    )
    if phase != "received":
        await runtime.executor.resume("binding", turn.turn_id)
    if phase == "queued":
        runtime.outbox.enqueue("binding", turn.turn_id, now_ms=9000)
    intake.release.set()
    task = asyncio.create_task(runtime.run())
    try:
        await wait_until(lambda: len(sender.sent) == 1)
        assert not engine.calls
    finally:
        await stop(task)


@pytest.mark.parametrize("phase", ["dispatching", "accepted", "sending"])
async def test_restart_uncertain_does_not_replay(tmp_path, phase):
    runtime, _, ledger, turn, _, sender, engine, intake, clock = runtime_case(tmp_path)
    if phase == "sending":
        resolve(ledger, turn)
        runtime.outbox.enqueue("binding", turn.turn_id, now_ms=clock())
        claim = runtime.outbox.claim("binding", now_ms=clock())
        runtime.outbox.start_send(claim, now_ms=clock())
    else:
        request = invocation_for(turn)
        ledger.reserve(turn, request)
        if phase == "accepted":
            ledger.record_update(
                "binding",
                turn.turn_id,
                EngineUpdate(run_id=request.run_id, status="accepted"),
            )
    intake.release.set()
    task = asyncio.create_task(runtime.run())
    try:
        await intake.entered.wait()
        await asyncio.sleep(0.07)
        status = await runtime.status(identity().owner_id)
        assert status.unknown_executions + status.unknown_deliveries == 1
        assert not sender.sent and not engine.calls
    finally:
        await stop(task)


async def test_disabled_run_does_not_listen_or_change_binding(tmp_path):
    runtime, state, _, _, _, sender, engine, intake, clock = runtime_case(tmp_path)
    state.set_enabled("binding", False, now_ms=clock())
    await runtime.run()
    assert not intake.entered.is_set() and not engine.calls and not sender.sent


async def test_listener_failure_stops_workers_and_closes_barrier(tmp_path):
    runtime, _, _, _, _, sender, engine, intake, _ = runtime_case(tmp_path)
    intake.release.set()
    intake.fail.set()
    with pytest.raises(DwsError):
        await runtime.run()
    assert (
        intake.closed and not (await runtime.status(identity().owner_id)).intake_ready
    )
    assert len(engine.calls) <= 1 and len(sender.sent) <= 1


async def test_exclusive_owner_blocks_second_scheduler_and_operator(tmp_path):
    runtime, _, _, _, directory, _, _, intake, _ = runtime_case(tmp_path)
    task = asyncio.create_task(runtime.run())
    try:
        await intake.entered.wait()
        with pytest.raises(RuntimeError, match="busy"):
            async with SqliteHostingRuntimeStore(directory).exclusive():
                pytest.fail("second scheduler acquired lock")
        with pytest.raises(RuntimeError, match="busy"):
            await runtime.resolve_manual(
                identity().owner_id,
                Resolution(kind="delivery", target_id="none", evidence="checked"),
            )
    finally:
        await stop(task)
    async with SqliteHostingRuntimeStore(directory).exclusive():
        pass


async def test_disable_stops_listener_and_cancelled_engine_is_not_replayed(tmp_path):
    engine = Engine()
    began = asyncio.Event()

    async def hanging(request):
        engine.calls.append(request)
        began.set()
        yield EngineUpdate(run_id=request.run_id, status="accepted")
        await asyncio.Event().wait()

    engine.execute = hanging
    runtime, state, ledger, turn, _, sender, _, intake, clock = runtime_case(
        tmp_path, engine=engine
    )
    intake.release.set()
    task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(began.wait(), 3)
    state.set_enabled("binding", False, now_ms=clock())
    await asyncio.wait_for(task, 3)
    assert ledger.get("binding", turn.turn_id).state == "cancelled"
    assert intake.closed and not sender.sent


async def test_owner_acknowledgement_requires_ended_run_and_takes_over(tmp_path):
    runtime, state, ledger, turn, directory, _, engine, _, clock = runtime_case(
        tmp_path
    )
    ledger.reserve(turn, invocation_for(turn))
    ledger.mark_unknown("binding", turn.turn_id, "timeout")
    request = Resolution(
        kind="execution",
        target_id=turn.turn_id,
        evidence="Owner checked the engine run",
    )
    with pytest.raises(PermissionError):
        await runtime.status("other")
    with pytest.raises(PermissionError):
        await runtime.resolve_manual("other", request)
    with pytest.raises(ValueError, match="end_not_confirmed"):
        await runtime.resolve_manual(identity().owner_id, request)
    assert ledger.get("binding", turn.turn_id).state == "unknown"
    engine.ended = True
    await runtime.resolve_manual(identity().owner_id, request)
    await runtime.resolve_manual(identity().owner_id, request)
    assert ledger.get("binding", turn.turn_id).state == "cancelled"
    assert (
        state.ingest("binding", message("next", created=11000), now_ms=11000)
        == "ignored"
    )
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM hosting_resolutions").fetchone()[0] == 1
        assert db.execute("SELECT taken_over FROM conversations").fetchone()[0] == 1


async def test_unknown_delivery_owner_resolution_never_resends_or_claims_sent(tmp_path):
    runtime, _, ledger, turn, directory, sender, _, _, clock = runtime_case(tmp_path)
    resolve(ledger, turn)
    delivery = runtime.outbox.enqueue("binding", turn.turn_id, now_ms=clock()).delivery
    sender.receipt = DeliveryReceipt(status="unknown")
    assert (await runtime.delivery.run_once("binding")).state == "unknown"
    resolution = Resolution(
        kind="delivery",
        target_id=delivery.delivery_id,
        evidence="Handled in DingTalk manually",
    )
    await runtime.resolve_manual(identity().owner_id, resolution)
    row = runtime.outbox.get("binding", delivery.delivery_id)
    assert row.state == "manual" and row.reason == "owner_handled"
    assert await runtime.delivery.run_once("binding") is None
    assert len(sender.sent) == 1


def test_cleanup_keeps_dedup_ids_and_session_mapping(tmp_path):
    runtime, state, ledger, turn, directory, _, _, _, clock = runtime_case(tmp_path)
    resolve(ledger, turn, action="ignore")
    runtime.operations.maintain(identity(), now_ms=clock() + 31 * 86_400_000)
    assert state.ingest("binding", message(), now_ms=clock()) == "duplicate"
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT message_json, batch_id FROM inbox").fetchone() == (
            "",
            None,
        )
        assert db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
        assert (
            db.execute("SELECT session_id FROM conversations").fetchone()[0]
            == turn.session_id
        )
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_cleanup_preserves_unknown_records(tmp_path):
    runtime, state, ledger, turn, _, _, _, _, clock = runtime_case(tmp_path)
    ledger.reserve(turn, invocation_for(turn))
    ledger.mark_unknown("binding", turn.turn_id, "unknown")
    runtime.operations.maintain(identity(), now_ms=clock() + 31 * 86_400_000)
    assert state.get_turn("binding", turn.turn_id) == turn
    assert ledger.get("binding", turn.turn_id).state == "unknown"


@pytest.mark.parametrize(
    "field,code",
    [
        ("max_inbox", "inbox_capacity"),
        ("max_pending", "backlog_capacity"),
        ("max_database_bytes", "database_capacity"),
    ],
)
def test_intake_capacity_raises_without_recording_message(tmp_path, field, code):
    _, _, _, directory = build_execution(tmp_path)
    state = SqliteHostingStateStore(
        directory, runtime_policy=RuntimePolicy(**{field: 1})
    )
    with pytest.raises(HostingCapacityError, match=code):
        state.ingest("binding", message("second"), now_ms=6000)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1
    assert state.ingest("binding", message(), now_ms=6000) == "duplicate"


def test_hints_capacity_and_duplicate(tmp_path):
    _, _, _, directory = build_execution(tmp_path)
    recovery = SqliteHostingRecoveryStore(directory, RuntimePolicy(max_hints=1))
    first = ListenerNotice(
        kind="conversation", conversation_id="chat", message_id="a", created_at_ms=2000
    )
    recovery.record_hint("binding", first)
    recovery.record_hint("binding", first)
    with pytest.raises(HostingCapacityError, match="hint_capacity"):
        recovery.record_hint("binding", first.model_copy(update={"message_id": "b"}))


async def test_storage_failure_prevents_all_dispatch(tmp_path):
    runtime, _, _, _, _, sender, engine, intake, _ = runtime_case(
        tmp_path, policy=RuntimePolicy(max_database_bytes=1)
    )
    with pytest.raises(HostingCapacityError):
        await runtime.run()
    assert not intake.entered.is_set() and not sender.sent and not engine.calls


async def test_cancel_waits_for_durable_transaction_thread():
    entered, finish, exited = threading.Event(), threading.Event(), threading.Event()

    def write():
        entered.set()
        finish.wait(3)
        exited.set()

    task = asyncio.create_task(durable_call(write))
    await wait_until(entered.is_set)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


async def test_shutdown_closes_barrier_after_late_reconciliation(tmp_path):
    runtime, _, _, _, _, _, _, intake, clock = runtime_case(tmp_path)

    async def late_recovery():
        intake.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            window = runtime.recovery.recovery_window(
                "binding", now_ms=clock(), overlap_ms=1000, window_ms=60000
            )
            assert runtime.recovery.complete_recovery(window)

    intake.run = late_recovery
    task = asyncio.create_task(runtime.run())
    await intake.entered.wait()
    await stop(task)
    assert not (await runtime.status(identity().owner_id)).intake_ready


def build_config(tmp_path, **updates):
    return HostingRuntimeConfig(
        state_dir=tmp_path / "runtime",
        bot_type="personal",
        engine="openclaw",
        **updates,
    )


def compose(config):
    return build_dws_runtime(
        config,
        identity(),
        connection=Connection(),
        engine=Engine(),
        peer_sender=Sender(),
        owner_sender=Sender(),
        clock_ms=Clock(),
        context=VerifiedContextFixture(),
    )


def test_factory_disabled_has_no_side_effects_and_validates_scope(tmp_path):
    config = build_config(tmp_path)
    assert compose(config) is None and not config.state_dir.exists()
    with pytest.raises(ValueError, match="storage"):
        compose(config.model_copy(update={"enabled": True}))
    for field, value in (
        ("bot_type", "service"),
        ("engine", "hermes"),
        ("engine", "aicoding"),
    ):
        with pytest.raises(ValueError, match="unsupported"):
            compose(config.model_copy(update={"enabled": True, field: value}))
    with pytest.raises(ValueError):
        HostingRuntimeConfig.model_validate({**config.model_dump(), "unknown": True})


async def test_factory_creates_disabled_binding_and_real_intake_flow(tmp_path):
    config = build_config(
        tmp_path,
        enabled=True,
        storage_qualified=True,
        runtime=RuntimePolicy(tick_seconds=0.01),
    )
    runtime = compose(config)
    assert not (await runtime.status(identity().owner_id)).enabled
    await runtime.run()
    runtime.state.confirm_rule("binding", "Reply")
    runtime.state.set_enabled("binding", True, now_ms=1000)
    task = asyncio.create_task(runtime.run())
    try:
        await wait_until(lambda: bool(runtime.intake.connection.calls))
        runtime.clock_ms.now = 15000
        await wait_until(lambda: bool(runtime.executor.engine.calls))
        await wait_until(lambda: bool(runtime.delivery.sender.replies.sent))
    finally:
        await stop(task)
