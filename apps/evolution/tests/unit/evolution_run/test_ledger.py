"""实验记录 H（05）。"""

from __future__ import annotations

import sqlite3

import pytest

from avernet_evolution.core.evolution_ledger import (
    EventType,
    LocalExperimentLedger,
    VerdictRecord,
    VerdictStatus,
)

from .conftest import BOT

SUBMISSION = {
    "bot": BOT,
    "run_id": "run_1",
    "binding_id": "bind_01",
    "iteration": 1,
    "candidate_id": "cid_1",
    "mechanism": "acme/x@1.0.0",
    "parent_revision": "sha256:a",
    "candidate_revision": "sha256:b",
    "evidence": ["ep:1"],
    "patch": {"ops": 1},
    "cost": None,
    "actor": "acme/x@1.0.0",
}


@pytest.mark.asyncio
async def test_ledger_is_idempotent_on_submission() -> None:
    ledger = LocalExperimentLedger(":memory:")
    first = await ledger.record_submission(**SUBMISSION)
    second = await ledger.record_submission(**SUBMISSION)
    assert first == second
    assert len(await ledger.list(bot=BOT)) == 1


@pytest.mark.asyncio
async def test_ledger_records_verdict_and_promotion_events() -> None:
    ledger = LocalExperimentLedger(":memory:")
    entry_id = await ledger.record_submission(**SUBMISSION)
    await ledger.record_verdict(entry_id, VerdictRecord(status=VerdictStatus.ACCEPT))
    await ledger.record_promotion(entry_id, revision="sha256:b", actor="user_5")
    entry = await ledger.get(entry_id)
    assert entry.promoted is True
    assert entry.verdict is not None and entry.verdict.status is VerdictStatus.ACCEPT
    assert [event["type"] for event in entry.events] == [
        EventType.SUBMITTED.value,
        EventType.VERIFIED.value,
        EventType.PROMOTED.value,
    ]


@pytest.mark.asyncio
async def test_ledger_export_carries_promotion_state() -> None:
    ledger = LocalExperimentLedger(":memory:")
    entry_id = await ledger.record_submission(**SUBMISSION)
    await ledger.record_promotion(entry_id, revision="sha256:b", actor="user_5")
    exported = await ledger.export(bot=BOT)
    assert exported[0]["promoted"] is True


@pytest.mark.asyncio
async def test_failed_submission_event_rolls_back_entry_before_retry(tmp_path) -> None:
    path = tmp_path / "ledger.db"
    ledger = LocalExperimentLedger(path)
    ledger.conn.execute(
        "CREATE TRIGGER fail_event BEFORE INSERT ON ledger_events "
        "BEGIN SELECT RAISE(ABORT, 'event write failed'); END"
    )
    ledger.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="event write failed"):
        await ledger.record_submission(**SUBMISSION)
    # A retry on the same connection must not see a phantom 'existing' entry.
    assert await ledger.list(bot=BOT) == []
    ledger.conn.execute("DROP TRIGGER fail_event")
    ledger.conn.commit()
    entry_id = await ledger.record_submission(**SUBMISSION)
    assert await ledger.record_submission(**SUBMISSION) == entry_id
    ledger.conn.close()
    reopened = LocalExperimentLedger(path)
    entry = await reopened.get(entry_id)
    assert [event["type"] for event in entry.events] == [EventType.SUBMITTED.value]
    reopened.conn.close()
