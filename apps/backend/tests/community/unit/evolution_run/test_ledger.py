"""实验记录 H（05）。"""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_ledger import (
    EventType,
    LocalExperimentLedger,
    VerdictRecord,
    VerdictStatus,
)

from .conftest import BOT

SUBMISSION = dict(
    bot=BOT,
    run_id="run_1",
    binding_id="bind_01",
    iteration=1,
    candidate_id="cid_1",
    mechanism="acme/x@1.0.0",
    parent_revision="sha256:a",
    candidate_revision="sha256:b",
    evidence=["ep:1"],
    patch={"ops": 1},
    cost=None,
    actor="acme/x@1.0.0",
)


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
