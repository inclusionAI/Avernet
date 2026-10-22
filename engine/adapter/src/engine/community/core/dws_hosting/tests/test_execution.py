"""Execution port conformance: real SQLite and fake engine, no external delivery."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from engine.community.core.dws_hosting.execution import HostingExecutor, invocation_for
from engine.community.core.dws_hosting.results import parse_decision
from engine.community.kernel.dws_execution import (
    EngineUpdate,
    ExecutionError,
    RunObservation,
)
from engine.community.plugins.dws_hosting.executions import SqliteHostingExecutionStore
from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore
from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore

from .test_state_contract import identity, message


def decision_json(action="reply", version=1):
    value = {"schema_version": 1, "rule_version": version, "action": action}
    if action == "reply":
        value["text"] = "The document is ready."
    if action == "escalate":
        value["summary"] = "Need owner clarification."
    return json.dumps(value)


def result_json(text=None):
    return json.dumps(
        {"payloads": [{"text": text if text is not None else decision_json()}]}
    )


@pytest.fixture
def execution(tmp_path):
    return build_execution(tmp_path)


def build_execution(tmp_path, *, text="hello"):
    directory = tmp_path / "hosting"
    state = SqliteHostingStateStore(directory)
    state.bind(identity())
    state.confirm_rule("binding", "Reply unless you need clarification.")
    state.set_enabled("binding", True, now_ms=1000)
    state.ingest("binding", message(text=text), now_ms=2000)
    turn = state.claim_ready("binding", now_ms=6000)
    return state, SqliteHostingExecutionStore(directory), turn, directory


class Engine:
    def __init__(self, *, text=None, ended=False, failure=None):
        self.calls = []
        self.reviews = []
        self.inspections = []
        self.text, self.ended, self.failure = text, ended, failure
        self.closed = False
        self.on_ack = lambda: None

    async def execute(self, request):
        self.calls.append(request)
        try:
            yield EngineUpdate(run_id=request.run_id, status="accepted")
            self.on_ack()
            if self.failure:
                raise self.failure
            yield EngineUpdate(
                run_id=request.run_id,
                status="completed",
                result_json=result_json(self.text),
            )
        finally:
            self.closed = True

    async def inspect_run(self, run_id):
        self.inspections.append(run_id)
        return RunObservation(run_id=run_id, ended=self.ended)

    async def review_reply(self, request):
        self.reviews.append(request)
        return result_json('{"verdict":"approve","reason_code":"approved","reason":"Permitted and relevant."}')


def ready_more(state, chat="chat"):
    state.ingest("binding", message("next", chat=chat, created=7000), now_ms=7000)
    return state.claim_ready("binding", now_ms=11000)


@pytest.mark.parametrize("action", ["reply", "ignore", "escalate"])
async def test_execute_persists_then_parses_and_never_sends(execution, action):
    state, ledger, turn, _ = execution
    engine = Engine(text=decision_json(action))
    engine.on_ack = lambda: (
        pytest.fail("ACK not persisted")
        if ledger.get("binding", turn.turn_id).state != "accepted"
        else None
    )
    worker = HostingExecutor(state, ledger, engine)
    result = await worker.execute("binding", turn.turn_id)
    assert result.state == "resolved" and result.decision.action == action
    assert result.result.result_json == result_json(decision_json(action))
    assert engine.closed
    assert await worker.execute("binding", turn.turn_id) is None
    assert len(engine.calls) == 1
    assert (ready_more(state) is not None) == (action == "ignore")


def test_request_contains_snapshot_and_isolated_stable_session(execution):
    state, _, turn, _ = execution
    req = invocation_for(turn)
    assert req.session_key == f"agent:main:dws:{turn.session_id}"
    assert req.run_id == f"dws-turn-{turn.turn_id}"
    assert req == invocation_for(turn)
    assert turn.rule_text in req.instructions
    body = json.loads(req.message)
    assert body["current_batch"] == [message().model_dump()]
    assert body["identity"]["self_user_id"] == "self"
    assert body["adapter_state"]["delivery_status"] == "not_sent_by_adapter"
    state.finish_turn("binding", turn.turn_id, "completed")
    next_turn = ready_more(state)
    assert invocation_for(next_turn).session_key == req.session_key
    assert invocation_for(next_turn).run_id != req.run_id
    other = ready_more(state, "other")
    assert invocation_for(other).session_key != req.session_key


@pytest.mark.parametrize("field", ["turn_id", "session_id", "agent_id"])
def test_invalid_hosting_id_cannot_select_chat_session(execution, field):
    turn = execution[2]
    if field == "agent_id":
        turn = turn.model_copy(
            update={"binding": turn.binding.model_copy(update={field: "main:other"})}
        )
    else:
        turn = turn.model_copy(update={field: "agent:main:chat"})
    with pytest.raises(ExecutionError):
        invocation_for(turn)


@pytest.mark.parametrize("control", ["disable", "takeover", "intake"])
async def test_revoked_before_dispatch_never_calls_engine(execution, control):
    state, ledger, turn, directory = execution
    if control == "disable":
        state.set_enabled("binding", False, now_ms=7000)
    elif control == "takeover":
        state.set_takeover("binding", "chat", True, now_ms=7000)
    else:
        SqliteHostingRecoveryStore(directory).block_intake("binding")
    engine = Engine()
    assert (
        await HostingExecutor(state, ledger, engine).execute("binding", turn.turn_id)
        is None
    )
    assert engine.calls == []


@pytest.mark.parametrize("control", ["disable", "takeover", "rules"])
async def test_controls_fence_results_but_rule_update_keeps_snapshot(
    execution, control
):
    state, ledger, turn, _ = execution
    engine = Engine()
    if control == "disable":
        engine.on_ack = lambda: state.set_enabled("binding", False, now_ms=7000)
    elif control == "takeover":
        engine.on_ack = lambda: state.set_takeover("binding", "chat", True, now_ms=7000)
    else:
        engine.on_ack = lambda: state.confirm_rule("binding", "New rules")
    result = await HostingExecutor(state, ledger, engine).execute(
        "binding", turn.turn_id
    )
    assert result.state == ("resolved" if control == "rules" else "cancelled")
    if control == "rules":
        assert result.decision.rule_version == 1
    else:
        assert result.decision is None and not engine.reviews


@pytest.mark.parametrize("ended", [True, False])
async def test_disconnect_restart_checks_status_never_replays(execution, ended):
    state, ledger, turn, directory = execution
    engine = Engine(failure=ExecutionError("disconnected"), ended=ended)
    worker = HostingExecutor(state, ledger, engine)
    assert (await worker.execute("binding", turn.turn_id)).state == "unknown"
    assert ready_more(state) is None
    assert ready_more(state, "other") is not None
    worker = HostingExecutor(
        SqliteHostingStateStore(directory),
        SqliteHostingExecutionStore(directory),
        engine,
    )
    assert await worker.execute("binding", turn.turn_id) is None
    result = await worker.resume("binding", turn.turn_id)
    assert result.state == ("manual" if ended else "unknown")
    assert len(engine.calls) == 1 and len(engine.inspections) == 1
    assert (state.claim_ready("binding", now_ms=11000) is not None) == ended


async def test_worker_cancellation_keeps_unknown_and_closes_iterator(execution):
    state, ledger, turn, _ = execution
    engine = Engine(failure=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await HostingExecutor(state, ledger, engine).execute("binding", turn.turn_id)
    assert ledger.get("binding", turn.turn_id).state == "unknown"
    assert engine.closed


async def test_received_result_survives_restart_without_engine_call(execution):
    state, ledger, turn, directory = execution
    req = invocation_for(turn)
    ledger.reserve(turn, req)
    ledger.record_update(
        "binding",
        turn.turn_id,
        EngineUpdate(run_id=req.run_id, status="completed", result_json=result_json()),
    )
    engine = Engine()
    restarted = SqliteHostingExecutionStore(directory)
    worker = HostingExecutor(state, restarted, engine)
    result = await worker.resume("binding", turn.turn_id)
    assert result.state == "resolved"
    assert await worker.resume("binding", turn.turn_id) == result
    assert engine.calls == engine.inspections == []


@pytest.mark.parametrize("text", ["not JSON", decision_json(version=2)])
async def test_invalid_result_is_retained_for_manual_handling(execution, text):
    state, ledger, turn, _ = execution
    result = await HostingExecutor(state, ledger, Engine(text=text)).execute(
        "binding", turn.turn_id
    )
    assert result.state == "manual" and result.decision is None
    assert result.result.result_json == result_json(text)
    assert ready_more(state) is not None


def test_concurrent_reservation_is_exactly_one_dispatch(execution):
    _, ledger, turn, _ = execution
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(
            pool.map(lambda _: ledger.reserve(turn, invocation_for(turn)), range(8))
        )
    assert sum(record is not None for record in records) == 1


async def test_db_failure_prevents_external_execution(execution):
    state, ledger, turn, directory = execution
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute(
            "CREATE TRIGGER deny_execution BEFORE INSERT ON executions BEGIN SELECT RAISE(ABORT, 'full'); END"
        )
    engine = Engine()
    with pytest.raises(sqlite3.IntegrityError):
        await HostingExecutor(state, ledger, engine).execute("binding", turn.turn_id)
    assert engine.calls == []
    with pytest.raises(KeyError):
        ledger.get("binding", turn.turn_id)


def test_migrate_v2_preserves_snapshot_and_binding(execution):
    state, _, turn, directory = execution
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("DROP TABLE outbox")
        db.execute("DROP TABLE executions")
        db.execute("DROP TABLE hosting_resolutions")
        db.execute("ALTER TABLE inbox DROP COLUMN received_ms")
        db.execute("PRAGMA user_version = 2")
    ledger = SqliteHostingExecutionStore(directory)
    assert state.get_turn("binding", turn.turn_id) == turn
    assert ledger.reserve(turn, invocation_for(turn)).state == "dispatching"


def test_ledger_rejects_scope_snapshot_and_run_mismatch(execution):
    _, ledger, turn, _ = execution
    with pytest.raises(ValueError, match="snapshot"):
        ledger.reserve(
            turn.model_copy(update={"rule_text": "modified"}), invocation_for(turn)
        )
    ledger.reserve(turn, invocation_for(turn))
    with pytest.raises(KeyError):
        ledger.get("other-binding", turn.turn_id)
    with pytest.raises(ValueError, match="run mismatch"):
        ledger.record_update(
            "binding", turn.turn_id, EngineUpdate(run_id="wrong", status="completed")
        )
    with pytest.raises(ValueError, match="successful result"):
        ledger.resolve("binding", turn.turn_id, parse_decision(result_json(), 1))


def test_late_ack_and_status_probe_do_not_overwrite_received_result(execution):
    _, ledger, turn, _ = execution
    req = invocation_for(turn)
    ledger.reserve(turn, req)
    ledger.mark_unknown("binding", turn.turn_id, "disconnected")
    ledger.record_update(
        "binding", turn.turn_id, EngineUpdate(run_id=req.run_id, status="accepted")
    )
    assert ledger.get("binding", turn.turn_id).state == "unknown"
    ledger.record_update(
        "binding",
        turn.turn_id,
        EngineUpdate(run_id=req.run_id, status="completed", result_json=result_json()),
    )
    ledger.mark_unknown("binding", turn.turn_id, "late_disconnect")
    assert (
        ledger.require_manual(
            "binding", turn.turn_id, "ended_result_unavailable", ended=True
        ).state
        == "received"
    )
    with pytest.raises(ValueError, match="rule mismatch"):
        ledger.resolve(
            "binding",
            turn.turn_id,
            parse_decision(result_json(decision_json(version=2)), 2),
        )
    resolved = ledger.resolve("binding", turn.turn_id, parse_decision(result_json(), 1))
    ledger.record_update(
        "binding", turn.turn_id, EngineUpdate(run_id=req.run_id, status="failed")
    )
    assert ledger.resolve("binding", turn.turn_id, resolved.decision) == resolved
    assert (
        ledger.require_manual("binding", turn.turn_id, "late", ended=True) == resolved
    )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("empty", "unknown"),
        ("wrong-run", "unknown"),
        ("failed", "manual"),
        ("unknown", "unknown"),
    ],
)
async def test_engine_port_failure_modes(execution, mode, expected):
    state, ledger, turn, _ = execution

    class Partial(Engine):
        async def execute(self, request):
            if mode == "empty":
                return
            yield EngineUpdate(
                run_id="wrong" if mode == "wrong-run" else request.run_id,
                status="completed" if mode == "wrong-run" else mode,
            )

    record = await HostingExecutor(state, ledger, Partial()).execute(
        "binding", turn.turn_id
    )
    assert record.state == expected


@pytest.mark.parametrize("mode", ["wrong-run", "unavailable"])
async def test_unreliable_inspection_keeps_conversation_blocked(execution, mode):
    state, ledger, turn, _ = execution
    ledger.reserve(turn, invocation_for(turn))

    class Unreliable(Engine):
        async def inspect_run(self, run_id):
            if mode == "unavailable":
                raise ExecutionError("unavailable")
            return RunObservation(run_id="wrong", ended=True)

    worker = HostingExecutor(state, ledger, Unreliable())
    assert (await worker.resume("binding", turn.turn_id)).state == "unknown"
    assert ready_more(state) is None


def test_manual_unknown_does_not_release_slot(execution):
    state, ledger, turn, _ = execution
    ledger.reserve(turn, invocation_for(turn))
    assert (
        ledger.require_manual("binding", turn.turn_id, "uncertain", ended=False).state
        == "unknown"
    )
    assert ready_more(state) is None


async def test_result_write_failure_keeps_dispatch_reserved_without_replay(execution):
    state, ledger, turn, directory = execution
    engine = Engine()

    def fail_result_write():
        with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
            db.execute("""CREATE TRIGGER deny_result BEFORE UPDATE ON executions
                WHEN NEW.state = 'received' BEGIN SELECT RAISE(ABORT, 'full'); END""")

    engine.on_ack = fail_result_write
    worker = HostingExecutor(state, ledger, engine)
    with pytest.raises(sqlite3.IntegrityError):
        await worker.execute("binding", turn.turn_id)
    assert ledger.get("binding", turn.turn_id).state == "accepted"
    assert await worker.execute("binding", turn.turn_id) is None
    assert len(engine.calls) == 1 and engine.closed


async def test_resumption_race_retains_newly_arrived_result(execution):
    state, ledger, turn, _ = execution
    req = invocation_for(turn)
    ledger.reserve(turn, req)

    class Racing(Engine):
        async def inspect_run(self, run_id):
            ledger.record_update(
                "binding",
                turn.turn_id,
                EngineUpdate(
                    run_id=run_id, status="completed", result_json=result_json()
                ),
            )
            return RunObservation(run_id=run_id, ended=True)

    worker = HostingExecutor(state, ledger, Racing())
    assert (await worker.resume("binding", turn.turn_id)).state == "received"
    assert (await worker.resume("binding", turn.turn_id)).state == "resolved"
