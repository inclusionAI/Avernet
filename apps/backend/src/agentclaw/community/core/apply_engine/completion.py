"""Telling a state's producer that an apply of it has ended: publish, one subscriber per state kind.

The apply engine already picks a compiler by the apply's state kind
(``StateCompilers``); this is the second map of the same shape. When an apply
ends — by any path — the run service hands its outcome to the handler
registered for that kind, so the producer learns how its state went without
the engine knowing what the producer is::

    StateCompletionHandlers = {"genome": GenomeCompletionHandler(...)}   # the Manifest registers none

Every terminal path publishes: a finished run, a refused compile, an engine
exception, a task that could not be rebuilt, and the task queue giving up on
the apply task. A worker that dies mid-run publishes nothing itself; the
queue re-claims the task, and the re-run either applies again or — finding
the report already terminal — only publishes. A handler can therefore be
called more than once for one apply and must treat a repeat as a no-op.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Dict, Protocol, runtime_checkable

from agentclaw.community.core.apply_engine.compiler import StateRef
from agentclaw.community.core.apply_engine.outcomes import ApplyReport, ApplyStatus, EntryOutcome
from agentclaw.community.log import get_logger

__all__ = [
    "ApplyCompletionHandler",
    "ApplyOutcome",
    "CompletionPublisher",
    "StateCompletionHandlers",
    "outcome_of",
]

logger = get_logger()

#: How many undelivered entries a summary names before it counts the rest.
_NAMED = 3


@dataclass(frozen=True)
class ApplyOutcome:
    """What a producer learns when its apply ends: the apply, its terminal status, and one line on it."""

    apply_id: str
    status: ApplyStatus  #: ``SUCCEEDED``, ``PARTIAL`` or ``FAILED``; never ``RUNNING``
    summary: str  #: composed from the report's own fields; never raw exception text

    def __post_init__(self) -> None:
        if self.status is ApplyStatus.RUNNING:
            raise ValueError("an apply outcome is terminal")


def outcome_of(report: ApplyReport) -> ApplyOutcome:
    """The outcome a terminal report publishes::

    SUCCEEDED → "applied"
    PARTIAL   → "2 entries not delivered: skills/refund-policy (failed), mcp/jira (skipped)"
    FAILED    → the report's first note, else "the apply failed"
    """
    if report.status is ApplyStatus.SUCCEEDED:
        return ApplyOutcome(report.apply_id, report.status, "applied")
    if report.status is ApplyStatus.FAILED:
        reason = report.notes[0] if report.notes else "the apply failed"
        return ApplyOutcome(report.apply_id, report.status, reason)
    missed = [
        f"{category.construct.value}/{entry.identity} ({entry.outcome.value})"
        for category in report.categories
        for entry in category.entries
        if entry.outcome in (EntryOutcome.FAILED, EntryOutcome.SKIPPED)
    ]
    named = ", ".join(missed[:_NAMED]) + (f" and {len(missed) - _NAMED} more" if len(missed) > _NAMED else "")
    noun = "entry" if len(missed) == 1 else "entries"
    summary = f"{len(missed)} {noun} not delivered: {named}" if missed else "partially applied"
    return ApplyOutcome(report.apply_id, report.status, summary)


@runtime_checkable
class ApplyCompletionHandler(Protocol):
    """A producer's subscriber: called once an apply of its state kind has ended.

    Called on the apply worker after the report is terminal and before the
    apply lock is released, so whatever the producer records is in place
    before the bot's next apply can start. Must be idempotent; a raise is not
    lost — the run service keeps the lock and lets it reach the task queue,
    which runs the task again, and the re-run publishes the stored outcome
    again before unlocking. Only the queue's give-up unlocks unheard.
    """

    @abstractmethod
    def on_finished(self, *, env: str, entity_id: str, bot_id: str, state: StateRef, outcome: ApplyOutcome) -> None: ...


#: The run service's subscribers: one per state kind. A map multibinding, so each producer's module contributes its
#: own row; a kind with no row is told nothing.
StateCompletionHandlers = Dict[str, ApplyCompletionHandler]


class CompletionPublisher:
    """Tells the subscriber registered for an apply's state kind how that apply ended.

    The one place the run service and the unstartable-task path publish through. ``publish`` answers what
    the subscriber raised instead of raising it, because every caller has to decide about the apply lock
    first: it keeps the lock and re-raises, so the task queue runs the task again and the re-run publishes.
    """

    def __init__(self, handlers: StateCompletionHandlers) -> None:
        self._handlers = dict(handlers)

    def publish(
        self, *, env: str, entity_id: str, bot_id: str, state: StateRef, report: ApplyReport
    ) -> Exception | None:
        """Hand ``report``'s outcome to the kind's subscriber; ``None`` when it took it or the kind has none."""
        handler = self._handlers.get(state.kind)
        if handler is None:
            return None
        try:
            handler.on_finished(env=env, entity_id=entity_id, bot_id=bot_id, state=state, outcome=outcome_of(report))
        except Exception as exc:  # noqa: BLE001 - answered to the caller, which keeps the lock and re-raises
            logger.exception(
                "[manifest_apply] completion handler raised, apply_id=%s, kind=%s", report.apply_id, state.kind
            )
            return exc
        return None
