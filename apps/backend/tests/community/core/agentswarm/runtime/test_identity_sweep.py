"""The backstop for runs that never reached their terminal write.

The normal path releases a run's identity when the run ends. This covers the runs
that do not end that way — and they are real, not hypothetical: a worker killed
mid-slice, a finalizer fenced out by a newer claim, a row left `RUNNING` after the
process that owned it died. Those identities and their workers' bindings live
forever without a sweeper.

Two properties matter and neither is obvious:

1. **It only touches old identities.** The cutoff must clear the longest legitimate
   run, because the sweeper cannot tell "slow" from "abandoned" — deleting a healthy
   run's identity mid-flight would break every worker it has.
2. **It never stops on one failure.** A sweeper that raises takes down its caller,
   which is worse than the leak it exists to fix.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from agentclaw.community.core.agentswarm.repository.identity.protocol import (
    SwarmIdentityRecord,
)
from agentclaw.community.core.agentswarm.runtime.identity_sweep import (
    SwarmIdentitySweeper,
)

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


class _Repo:
    def __init__(self, rows: list[SwarmIdentityRecord]) -> None:
        self._rows = rows
        self.asked: list[datetime] = []

    def list_stale(self, *, cutoff, limit=None):
        self.asked.append(cutoff)
        return self._rows[: limit or len(self._rows)]


class _Registry:
    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.destroyed: list[str] = []
        self._fail_on = fail_on or set()

    def destroy_identity(self, run_id: str, *, owner_id: str) -> bool:
        if run_id in self._fail_on:
            raise RuntimeError("passport service unavailable")
        self.destroyed.append(run_id)
        return True


def _row(run_id: str, *, status: str = "ISSUED", owner: str = "o-1"):
    return SwarmIdentityRecord(
        run_id=run_id,
        swarm_bot_id=f"agent_swarm_{run_id}",
        agent_code=f"ac-{run_id}",
        status=status,
        mcp_codes=(),
        cli_items=(),
        worker_count=0,
        owner_id=owner,
    )


def _sweeper(rows, registry, *, stale=3600):
    return SwarmIdentitySweeper(
        _Repo(rows), registry, stale_after_seconds=stale, clock=lambda: NOW
    )


@pytest.mark.unit
def test_it_destroys_identities_past_the_cutoff() -> None:
    registry = _Registry()
    outcome = _sweeper([_row("run-1")], registry).sweep()

    assert outcome.destroyed == ("run-1",)
    assert registry.destroyed == ["run-1"]


@pytest.mark.unit
def test_it_asks_the_store_for_the_right_cutoff() -> None:
    """The age window is what keeps it away from a healthy run."""
    repo = _Repo([])
    SwarmIdentitySweeper(
        repo, _Registry(), stale_after_seconds=7200, clock=lambda: NOW
    ).sweep()

    assert repo.asked == [NOW - timedelta(seconds=7200)]


@pytest.mark.unit
def test_a_dry_run_destroys_nothing() -> None:
    registry = _Registry()
    outcome = _sweeper([_row("run-1")], registry).sweep(dry_run=True)

    assert registry.destroyed == []
    assert outcome.skipped == ("run-1",)


@pytest.mark.unit
def test_an_already_destroyed_row_is_skipped() -> None:
    registry = _Registry()
    outcome = _sweeper([_row("run-1", status="DESTROYED")], registry).sweep()

    assert registry.destroyed == []
    assert outcome.skipped == ("run-1",)


@pytest.mark.unit
def test_one_failure_never_stops_the_sweep() -> None:
    registry = _Registry(fail_on={"run-bad"})
    outcome = _sweeper([_row("run-bad"), _row("run-good")], registry).sweep()

    assert registry.destroyed == ["run-good"], "a failure blocked a later run"
    assert outcome.errors and "run-bad" in outcome.errors[0]
    assert outcome.destroyed == ("run-good",)


@pytest.mark.unit
def test_batching_bounds_one_sweep() -> None:
    registry = _Registry()
    rows = [_row(f"run-{i}") for i in range(5)]

    outcome = _sweeper(rows, registry).sweep(max_batch=2)

    assert outcome.examined == 2
    assert len(registry.destroyed) == 2