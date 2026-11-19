"""Durable candidate reservations, migration and atomic counter reconciliation."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from avernet_evolution.core.evolution_run import (
    BudgetUsage,
    EvolutionStore,
    RunStatus,
)

from .test_runs_jobs import _submit


def test_old_candidate_schema_migrates_without_losing_sequence(tmp_path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE evolution_run_candidate (run_id TEXT NOT NULL, "
            "candidate_id TEXT NOT NULL, revision TEXT NOT NULL, seq INTEGER NOT NULL, "
            "verdict TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(run_id, candidate_id))"
        )
        conn.execute("INSERT INTO evolution_run_candidate VALUES ('r', 'old', 'rev', 4, 'pending')")
    store = EvolutionStore(path)
    assert store.reserve_candidate("r", "old").seq == 4
    assert not store.submission_complete("r", "old")
    assert store.reserve_candidate("r", "next").seq == 5
    store.complete_submission("r", "old")
    store.conn.close()
    reopened = EvolutionStore(path)
    assert reopened.submission_complete("r", "old")
    assert reopened.reserve_candidate("r", "next").seq == 5
    reopened.conn.close()


@pytest.mark.asyncio
async def test_count_tracks_revisions_not_reservations_or_stale_run(services) -> None:
    run_id = await _submit(services)
    store = services.store
    assert store.reserve_candidate(run_id, "rejected").seq == 1
    store.upsert_candidate(run_id, "rejected", "", seq=1, verdict="reject")
    assert store.reserve_candidate(run_id, "candidate").seq == 2
    assert store.reserve_candidate(run_id, "candidate").seq == 2
    assert store.reserve_candidate(run_id, "unfinished").seq == 3
    run = store.get_run(run_id)
    store.update_run(replace(run, status=RunStatus.RUNNING, budget_used=BudgetUsage.of(usd="0.5")))
    store.record_candidate_revision(run_id, "candidate", "revision-1")
    store.record_candidate_revision(run_id, "candidate", "revision-1")
    run = store.get_run(run_id)
    assert run.candidate_count == 1 and run.status is RunStatus.RUNNING
    assert run.budget_used == BudgetUsage.of(usd="0.5")
    store.record_candidate_revision(run_id, "unfinished", "revision-2")
    assert store.get_run(run_id).candidate_count == 2


@pytest.mark.asyncio
async def test_revision_and_count_roll_back_together_on_write_failure(services) -> None:
    run_id = await _submit(services)
    store = services.store
    store.reserve_candidate(run_id, "candidate")
    store.conn.execute(
        "CREATE TRIGGER fail_counter BEFORE UPDATE OF candidate_count ON evolution_run "
        "BEGIN SELECT RAISE(ABORT, 'counter write failed'); END"
    )
    store.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="counter write failed"):
        store.record_candidate_revision(run_id, "candidate", "revision-1")
    assert store.candidates(run_id)[0].revision == ""
    assert store.get_run(run_id).candidate_count == 0
    assert not store.submission_complete(run_id, "candidate")
    store.conn.execute("DROP TRIGGER fail_counter")
    store.conn.commit()
    store.record_candidate_revision(run_id, "candidate", "revision-1")
    assert store.get_run(run_id).candidate_count == 1
