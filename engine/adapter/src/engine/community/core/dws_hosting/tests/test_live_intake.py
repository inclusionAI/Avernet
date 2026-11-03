"""Live hints must neither wait for, nor reopen, the global recovery barrier."""

import asyncio
import sqlite3

import pytest

from engine.community.kernel.dws_connection import DwsError, HistoryResult
from engine.community.core.dws_hosting.execution import invocation_for
from engine.community.plugins.dws_hosting.executions import SqliteHostingExecutionStore
from .test_intake import setup as intake_setup
from .test_recovery_contract import hint, window
from .test_state_contract import message
from .test_delivery import case as delivery_case, enqueue, worker

setup = intake_setup
case = delivery_case


async def test_slow_background_scan_does_not_hold_live_conversation(setup):
    state, recovery, client, intake = setup
    assert await intake.reconcile_once()
    state.finish_turn(
        "binding", state.claim_ready("binding", now_ms=9000).turn_id, "completed"
    )
    recovery.remember_conversation("binding", "slow")
    entered, release = asyncio.Event(), asyncio.Event()
    incoming = message("next", created=10000)
    intake.clock_ms = lambda: 11000

    async def history(cid, start, end):
        if cid == "slow":
            entered.set()
            await release.wait()
            return HistoryResult(messages=())
        return HistoryResult(messages=(incoming,), observed_ids=(incoming.message_id,))

    client.history = history
    scan = asyncio.create_task(intake.reconcile_once())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        recovery.record_hint("binding", hint("next", created=10000))
        assert state.claim_ready("binding", now_ms=16000) is None
        await intake.hints_once()
        turn = state.claim_ready("binding", now_ms=16000)
        assert turn is not None and turn.messages == (incoming,)
        assert not scan.done()
    finally:
        release.set()
        await scan


async def test_hidden_hint_blocks_only_its_own_conversation(setup):
    state, recovery, client, intake = setup
    assert await intake.reconcile_once()
    recovery.record_hint("binding", hint("unavailable", created=2000, chat="other"))
    state.ingest("binding", message("other", chat="other"), now_ms=4000)
    client.result = HistoryResult(messages=())
    await intake.hints_once()
    assert state.claim_ready("binding", now_ms=10000).conversation_id == "chat"
    assert state.claim_ready("binding", now_ms=10000) is None


async def test_hint_epoch_id_fence_and_no_global_checkpoint_advance(setup):
    state, recovery, _, intake = setup
    assert await intake.reconcile_once()
    recovery.record_hint("binding", hint("late", created=3000))
    first = recovery.pending_hints("binding", now_ms=6000)[0]
    recovery.record_hint("binding", hint("new", created=3500))
    recovery.complete_hints(first, ("late", "new"))
    assert len(recovery.pending_hints("binding", now_ms=6000)) == 1
    with sqlite3.connect(recovery._database.path) as db:
        assert (
            db.execute("SELECT complete_until_ms FROM intake_checkpoints").fetchone()[0]
            == 5000
        )
    state.set_enabled("binding", False, now_ms=7000)
    state.set_enabled("binding", True, now_ms=8000)
    recovery.record_hint("binding", hint("new-epoch", created=9000))
    recovery.complete_hints(first, ("new-epoch",))
    assert recovery.pending_hints("binding", now_ms=10000)


async def test_live_receives_without_full_discovery(setup):
    state, recovery, client, intake = setup
    recovery.complete_recovery(window(recovery, 5000))
    client.error = DwsError("discovery_must_not_run")
    recovery.record_hint("binding", hint())
    await intake.hints_once()
    assert state.claim_ready("binding", now_ms=10000) is not None


@pytest.mark.parametrize("error", ["incomplete_history", "history_scope_mismatch"])
async def test_failed_live_history_does_not_clear_hint(setup, error):
    state, recovery, client, intake = setup
    recovery.complete_recovery(window(recovery, 5000))
    recovery.record_hint("binding", hint())

    async def history(*args):
        raise DwsError(error)

    client.history = history
    if error == "incomplete_history":
        await intake.hints_once()
    else:
        with pytest.raises(DwsError, match=error):
            await intake.hints_once()
    assert recovery.pending_hints("binding", now_ms=5000)
    assert state.claim_ready("binding", now_ms=10000) is None


async def test_new_hint_between_claim_and_reservation_blocks_only_that_turn(setup):
    state, recovery, _, intake = setup
    assert await intake.reconcile_once()
    turn = state.claim_ready("binding", now_ms=9000)
    ledger = SqliteHostingExecutionStore(recovery._database.path.parent)
    recovery.record_hint("binding", hint("next", created=3000))
    assert ledger.reserve(turn, invocation_for(turn)) is None
    live = recovery.pending_hints("binding", now_ms=5000)[0]
    recovery.complete_hints(live, ("next",))
    assert ledger.reserve(turn, invocation_for(turn)) is not None


async def test_unrelated_hint_does_not_delay_outgoing_reply(case):
    from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore

    recovery = SqliteHostingRecoveryStore(case[3])
    assert recovery.complete_recovery(window(recovery, 6000))
    recovery.record_hint("binding", hint("other", chat="other"))
    enqueue(case)
    assert (await worker(case).run_once("binding")).state == "sent"


async def test_same_conversation_hint_fences_outgoing_reply(case):
    from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore

    recovery = SqliteHostingRecoveryStore(case[3])
    assert recovery.complete_recovery(window(recovery, 6000))
    recovery.record_hint("binding", hint("later"))
    enqueue(case)
    assert await worker(case).run_once("binding") is None
    assert not case[6].sent
