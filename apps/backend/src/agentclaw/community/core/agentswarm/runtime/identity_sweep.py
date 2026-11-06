"""Releases run identities the terminal path never reached.

The normal end of a run releases its identity: `_destroy_run_bot_personas` runs after
the terminal write, and the factory releases the run's AgentPass once its workers are
gone.

That path does not cover every run. A run whose terminal write never lands — a worker
killed mid-slice, a fenced-out finalizer, a row left `RUNNING` after the process that
owned it died — keeps its identity and its workers' bindings indefinitely. This sweeper
is the backstop for exactly those: it ages out identities that are still `ISSUED` long
after any live run could plausibly be using them.

**Why an age cutoff rather than a status check.** The sweeper cannot tell "running and
slow" from "abandoned"; only time can. The cutoff therefore has to clear the longest
legitimate run, and it is a deployment setting, not a guess in code.

**It destroys, so it is bounded and boring.** Best-effort per run: one failure is
logged and the sweep continues, and nothing here raises — a sweeper that can take down
its caller would be worse than the leak it fixes.

**This sweeper has no caller yet.** The repository ships no scheduler: the artifact
retention sweeper this one is modelled on is in the same state, its docstring noting
that DI wiring and a periodic caller are "deliberately deferred". So the same is true
here — the sweep is built, bounded and tested, and a scheduler that does not exist
cannot call it. Until one does, a run that never reaches its terminal write keeps its
identity. That gap is recorded in ``tasks.md`` rather than papered over with a thread
started at import.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from agentclaw.community.log import get_logger

logger = get_logger("agentswarm")

#: How long an issued identity may sit untouched before the sweeper considers it
#: abandoned. Deliberately generous: a swarm run is bounded by its own slice budget
#: (an hour by default), and a cutoff near that would race healthy runs.
DEFAULT_IDENTITY_STALE_SECONDS = 12 * 3600


@dataclass(frozen=True)
class IdentitySweepOutcome:
    """What one sweep did, in the retention sweeper's vocabulary."""

    examined: int = 0
    destroyed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class SwarmIdentitySweeper:
    """Destroys identities of runs that ended without releasing them."""

    def __init__(
        self,
        repository: Any,
        registry: Any,
        *,
        stale_after_seconds: int = DEFAULT_IDENTITY_STALE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._stale_after = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def sweep(
        self, *, dry_run: bool = False, max_batch: int | None = None
    ) -> IdentitySweepOutcome:
        cutoff = self._clock() - timedelta(seconds=self._stale_after)
        examined = 0
        destroyed: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        list_stale = getattr(self._repository, "list_stale", None)
        if list_stale is None:
            return IdentitySweepOutcome()

        for record in list_stale(cutoff=cutoff, limit=max_batch):
            examined += 1
            if record.status != "ISSUED":
                skipped.append(record.run_id)
                continue
            if dry_run:
                skipped.append(record.run_id)
                continue
            try:
                released = self._registry.destroy_identity(
                    record.run_id, owner_id=record.owner_id
                )
            except Exception as exc:  # noqa: BLE001 - one run never stops the sweep
                errors.append(f"{record.run_id}: {exc}")
                logger.warning(
                    "[agentswarm][sweep] identity destroy failed for run=%s: %s",
                    record.run_id, exc,
                )
                continue
            (destroyed if released else skipped).append(record.run_id)

        if destroyed:
            logger.info(
                "[agentswarm][sweep] released %d abandoned run identity(ies): %s",
                len(destroyed), destroyed,
            )
        return IdentitySweepOutcome(
            examined=examined,
            destroyed=tuple(destroyed),
            skipped=tuple(skipped),
            errors=tuple(errors),
        )