import json
import sqlite3

import pytest

from engine.community.core.dws_hosting.dialogue import HostingDialogue
from engine.community.core.dws_hosting.execution import HostingExecutor
from engine.community.kernel.dws_connection import (
    AuthorizationStatus,
    DwsError,
    HistoryResult,
)
from engine.community.plugins.dws_hosting.executions import SqliteHostingExecutionStore
from engine.community.plugins.dws_hosting.outbox import SqliteHostingOutbox
from .test_context import VerifiedContextFixture
from .test_execution import Engine, build_execution
from .test_state_contract import message, identity


class History:
    def __init__(self, messages=()):
        self.messages = messages
        self.error = None

    async def authorization_status(self):
        b = identity()
        return AuthorizationStatus(
            authenticated=True, user_id=b.account_id, organization_id=b.organization_id
        )

    async def history(self, cid, start, end):
        if self.error:
            raise self.error
        return HistoryResult(messages=self.messages)


async def test_incremental_generation_frozen_review_and_restart(tmp_path):
    state, ledger, turn, directory = build_execution(tmp_path, text="对呢")
    owner = message("owner-1", sender="self", text="我会晚到", created=1500)
    source = History((owner, *turn.messages))
    now = [6000]
    engine = Engine()
    executor = HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(source, lambda: now[0]),
    )
    record = await executor.execute("binding", turn.turn_id)
    assert record.state == "resolved"
    first = json.loads(engine.calls[0].message)
    assert [(m["role"], m["text"]) for m in first["conversation_updates"]] == [
        ("owner", "我会晚到")
    ]
    assert len(first["current_batch"]) == 1
    frozen = record.invocation.dialogue_snapshot
    outbox = SqliteHostingOutbox(directory)
    sent = outbox.enqueue("binding", turn.turn_id, now_ms=7000)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("UPDATE outbox SET state='sent', updated_ms=8000")
        db.execute("UPDATE batches SET state='completed'")
    incoming = message("next", text="好的", created=9000)
    owner2 = message("owner-2", sender="self", text="已经出发了", created=8500)
    source.messages = (owner, *turn.messages, owner2, incoming)
    state.ingest("binding", incoming, now_ms=9000)
    next_turn = state.claim_ready("binding", now_ms=14000)
    now[0] = 14000
    # Rebuild the ledger/executor, as on process restart. No in-memory cursor.
    ledger = SqliteHostingExecutionStore(directory)
    executor = HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(source, lambda: now[0]),
    )
    second = await executor.execute("binding", next_turn.turn_id)
    data = json.loads(engine.calls[1].message)
    assert [m["text"] for m in data["conversation_updates"]] == ["已经出发了"]
    assert any(m["text"] == "我会晚到" for m in data["latest_exchange"])
    assert data["latest_exchange"][-1]["text"] == "好的"
    assert data["latest_exchange"][-1]["current_batch"] is True
    assert data["delivery_updates"][0]["status"] == "sent"
    review = json.loads(engine.reviews[1].message.split("\nINPUT_JSON:\n")[1])
    facts = review["dialogue"]
    assert any(m["role"] == "owner" and m["text"] == "我会晚到" for m in facts)
    assert any(
        m["role"] == "assistant_sent" and m["text"] == sent.delivery.text for m in facts
    )
    assert ledger.get("binding", turn.turn_id).invocation.dialogue_snapshot == frozen
    assert await executor.execute("binding", next_turn.turn_id) is None
    assert len(engine.calls) == 2
    assert second.invocation.dialogue_snapshot is not None
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("UPDATE batches SET state='completed'")
    third_message = message("third", created=15000, text="收到")
    source.messages = (*source.messages, third_message)
    state.ingest("binding", third_message, now_ms=15000)
    third_turn = state.claim_ready("binding", now_ms=20000)
    now[0] = 20000
    third = await executor.execute("binding", third_turn.turn_id)
    updates = json.loads(third.invocation.message)["delivery_updates"]
    assert all(m["run_id"] != record.invocation.run_id for m in updates)
    assert len(updates) == 1 and updates[0]["run_id"] == second.invocation.run_id


@pytest.mark.parametrize("bad", ["scope", "sender", "conflict", "unavailable"])
async def test_bad_history_never_reaches_engine(tmp_path, bad):
    state, ledger, turn, _ = build_execution(tmp_path)
    source = History()
    if bad == "unavailable":
        source.error = DwsError("incomplete_history")
    else:
        original = turn.messages[0]
        updates = (
            {"conversation_id": "other"}
            if bad == "scope"
            else {"sender_id": "stranger"}
            if bad == "sender"
            else {"text": "changed"}
        )
        source.messages = (original.model_copy(update=updates),)
    engine = Engine()
    record = await HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(source, lambda: 6000),
    ).execute("binding", turn.turn_id)
    assert record.state == "manual" and record.reason == "dialogue_history_unavailable"
    assert not engine.calls


async def test_history_is_bounded_not_queued_and_future_peer_excluded(tmp_path):
    state, ledger, turn, _ = build_execution(tmp_path)
    history = tuple(
        message("old-" + str(i), sender="self", text="a" * 2100, created=1000 + i)
        for i in range(50)
    )
    future = message("future", created=7000)
    snapshot = await HostingDialogue(
        History((*history, *turn.messages, future)), lambda: 8000
    ).prepare(turn)
    assert len(snapshot.messages) == 40
    assert all(len(m.text) <= 2000 for m in snapshot.messages)
    assert all(m.key != "message:future" for m in snapshot.messages)
    with sqlite3.connect(ledger._database.path) as db:
        assert db.execute("SELECT count(*) FROM inbox").fetchone()[0] == 1


async def test_disable_during_history_does_not_reserve_or_advance_sync(tmp_path):
    state, ledger, turn, _ = build_execution(tmp_path)

    class Disable(History):
        async def history(self, *args):
            state.set_enabled("binding", False, now_ms=6000)
            return HistoryResult(messages=turn.messages)

    engine = Engine()
    record = await HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(Disable(), lambda: 6000),
    ).execute("binding", turn.turn_id)
    assert record is None and not engine.calls
    with pytest.raises(KeyError):
        ledger.get("binding", turn.turn_id)


async def test_reenable_preserves_real_history_without_reinjecting_seen_messages(
    tmp_path,
):
    state, ledger, turn, directory = build_execution(tmp_path)
    owner = message("owner", sender="self", text="我会晚到", created=1500)
    source = History((owner, *turn.messages))
    engine = Engine()
    worker = HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(source, lambda: 6000),
    )
    await worker.execute("binding", turn.turn_id)
    with sqlite3.connect(directory / "dws_hosting.sqlite3") as db:
        db.execute("UPDATE batches SET state='completed'")
    state.set_enabled("binding", False, now_ms=7000)
    state.set_enabled("binding", True, now_ms=8000)
    incoming = message("new", created=9000, text="知道啦")
    state.ingest("binding", incoming, now_ms=9000)
    next_turn = state.claim_ready("binding", now_ms=14000)
    source.messages = (*source.messages, incoming)
    worker.dialogue = HostingDialogue(source, lambda: 14000)
    record = await worker.execute("binding", next_turn.turn_id)
    assert json.loads(record.invocation.message)["conversation_updates"] == []
    assert any(m.role == "owner" for m in record.invocation.dialogue_snapshot.messages)


async def test_unknown_dispatch_is_not_replayed_or_assumed_sent(tmp_path):
    from engine.community.kernel.dws_execution import ExecutionError

    state, ledger, turn, directory = build_execution(tmp_path)
    owner = message("owner", sender="self", text="晚点到", created=1500)
    source = History((owner, *turn.messages))
    engine = Engine(failure=ExecutionError("disconnected"))
    worker = HostingExecutor(
        state,
        ledger,
        engine,
        VerifiedContextFixture(),
        dialogue=HostingDialogue(source, lambda: 6000),
    )
    first = await worker.execute("binding", turn.turn_id)
    assert first.state == "unknown"
    assert await worker.execute("binding", turn.turn_id) is None
    assert len(engine.calls) == 1
    # Only after an independently confirmed end can the next turn proceed.
    ledger.require_manual(
        "binding", turn.turn_id, "ended_result_unavailable", ended=True
    )
    incoming = message("next", created=9000)
    state.ingest("binding", incoming, now_ms=9000)
    following = state.claim_ready("binding", now_ms=14000)
    source.messages = (*source.messages, incoming)
    engine.failure = None
    worker.dialogue = HostingDialogue(source, lambda: 14000)
    record = await worker.execute("binding", following.turn_id)
    data = json.loads(record.invocation.message)
    assert data["conversation_updates"] == []
    assert data["delivery_updates"][0]["status"] == "not_sent"
    assert not any(
        m.role == "assistant_sent" for m in record.invocation.dialogue_snapshot.messages
    )
