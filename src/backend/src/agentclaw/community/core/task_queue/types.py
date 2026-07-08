"""Value types for the task queue.

- :class:`TaskStatus` — the status **enum** stored in the DB. It is an
  ``enum.Enum`` (not loose string constants) on purpose: introducing a new
  status must be a deliberate edit here, and callers reference members rather
  than magic strings.
- :class:`TaskRecord` — an immutable projection of a ``ac_task_queue`` row
  (``payload`` already deserialized, ``status`` already an enum).
- The handler outcomes (:class:`Complete` / :class:`Reschedule` /
  :class:`Retry` / :class:`Fail`) and the :data:`TaskOutcome` union.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Union


class TaskStatus(str, Enum):
    """Status of an ``ac_task_queue`` row.

    A ``str``-valued enum so members compare/serialize as their plain value
    ("PENDING", …) while still forcing every new status to be declared here.

    Lifecycle: ``PENDING`` --claim--> ``RUNNING`` --outcome--> back to
    ``PENDING`` (reschedule/retry) or a terminal state. ``RUNNING`` with an
    expired lease is treated as abandoned and may be re-claimed.

    Terminal states:
      - ``SUCCEEDED`` — handler reported done.
      - ``FAILED``    — handler gave up (``Fail``), or a wiring error.
      - ``TIMED_OUT`` — the deadline elapsed before completion (distinct from
        a real failure: the work didn't fail, it ran out of time).
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


#: Statuses that are terminal — never claimed or run again.
TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMED_OUT}
)


@dataclass(frozen=True)
class TaskRecord:
    """Immutable projection of a ``ac_task_queue`` row.

    ``payload`` and ``deadline_at`` are always present — a task without work to
    do or without a give-up horizon is not a valid task. Scheduling timestamps
    are produced and compared **DB-side** (see the repository); the values here
    are read back from the DB. ``gmt_create`` / ``gmt_modified`` are
    DB-managed audit columns.
    """

    id: int
    task_type: str
    payload: dict
    status: TaskStatus
    deadline_at: datetime
    run_at: Optional[datetime]
    claimed_by: Optional[str]
    lease_expires_at: Optional[datetime]
    attempts: int
    last_error: Optional[str]
    env: str
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


# ── Handler outcomes ────────────────────────────────────────────────────────
# What a handler returns. The worker maps each to a repository transition. The
# deadline is enforced DB-side by the repository, so handlers never have to
# watch the clock — a Reschedule/Retry that would land past the deadline is
# turned into TIMED_OUT by the repository.


@dataclass(frozen=True)
class Complete:
    """The work is done. The worker marks the task ``SUCCEEDED`` (terminal)."""


@dataclass(frozen=True)
class Reschedule:
    """Work isn't done yet — run again after ``delay_seconds`` (the healthy,
    non-error path, e.g. a poller that hasn't reached a terminal state).

    Leaves ``last_error`` untouched. The caller specifies the delay because it
    knows the polling cadence.
    """

    delay_seconds: float


@dataclass(frozen=True)
class Retry:
    """This attempt errored — run again later (the failure path).

    Records ``error`` in ``last_error``. The retry delay is **always** the
    worker's exponential backoff (bounded by min/max); a handler does not pick
    it, since unbounded retries are capped by the deadline, not an attempt
    count.
    """

    error: Optional[str] = None


@dataclass(frozen=True)
class Fail:
    """Give up now — the worker marks the task ``FAILED`` (terminal) with
    ``error`` recorded. Use when the work can never succeed (vs. ``Retry``,
    which tries again, or ``TIMED_OUT``, which is the deadline elapsing)."""

    error: str


#: A handler returns one of these to drive the task's next transition.
TaskOutcome = Union[Complete, Reschedule, Retry, Fail]
