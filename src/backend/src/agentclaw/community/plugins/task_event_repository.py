"""Unified ORM ``TaskEventRepo`` impl — append-only event log (Phase 1.3).

Single writer of the monotonic ``seq``: ``append`` validates the supplied
``seq`` against ``latest_seq(task_id)`` (rejecting gaps / reuse / out-of-order)
then ``db.add + flush``. ``ac_task_event`` has no ``gmt_modified`` — events are
immutable. ``(env, task_id, seq)`` unique constraint is the DB-level guard.
``load_events`` deserializes rows back into the typed :class:`TaskEvent`
subclass for the kind so the fold sees the real shape.

Avernet rules: no bare SQL; ``Optional[T]``; ``@inject``.
"""
from __future__ import annotations

import json
from typing import Optional

from injector import inject
from sqlalchemy import and_, desc

from agentclaw.community.core.task.domain.events import (
    Cancelled,
    EventKind,
    ExecutionAttempted,
    GoalRejected,
    GoalVerified,
    Hung,
    IllegalEventError,
    LoopRerouted,
    NodeAccepted,
    NodeDispatched,
    NodeFailed,
    NodeRejected,
    NodeRunning,
    PlanFinalized,
    SpecAmended,
    TaskCreated,
    TaskEvent,
    is_reported_kind,
    next_seq,
)
from agentclaw.community.core.task.domain.models import (
    AttemptOutcome,
    AttemptTrigger,
    NodeStatus,
    RouteClass,
    RunMode,
)
from agentclaw.community.core.task.repository.models import AcTaskEventModel
from agentclaw.community.plugin_api.database import DatabasePlugin


class OrmTaskEventRepository:
    """ORM-backed append-only :class:`TaskEventRepo`."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def append(self, event: TaskEvent) -> TaskEvent:
        expected = next_seq(self.latest_seq(event.task_id))
        if event.seq != expected:
            raise IllegalEventError(
                f"seq out of order for task {event.task_id}: "
                f"expected {expected}, got {event.seq}"
            )
        with self._db.orm_session() as session:
            model = AcTaskEventModel(
                task_id=event.task_id,
                seq=event.seq,
                kind=event.kind.value,
                reported=1 if event.reported else 0,
                payload_json=json.dumps(event.payload, ensure_ascii=False, default=str),
            )
            session.add(model)
            session.flush()
        return event

    def load_events(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        with self._db.orm_session() as session:
            rows = (
                session.query(AcTaskEventModel)
                .filter(
                    and_(
                        AcTaskEventModel.task_id == task_id,
                        AcTaskEventModel.seq > after_seq,
                    )
                )
                .order_by(AcTaskEventModel.seq.asc())
                .all()
            )
            return [_row_to_event(r) for r in rows]

    def latest_seq(self, task_id: str) -> Optional[int]:
        with self._db.orm_session() as session:
            row = (
                session.query(AcTaskEventModel)
                .filter(AcTaskEventModel.task_id == task_id)
                .order_by(desc(AcTaskEventModel.seq))
                .first()
            )
            return row.seq if row is not None else None


def _row_to_event(row: AcTaskEventModel) -> TaskEvent:
    kind = EventKind(row.kind)
    payload = json.loads(row.payload_json) if row.payload_json else {}
    reported = bool(row.reported) or is_reported_kind(kind)
    common = dict(
        task_id=row.task_id,
        seq=row.seq,
        kind=kind,
        reported=reported,
    )
    if kind is EventKind.TASK_CREATED:
        return TaskCreated(title=payload.get("title", ""), source=payload.get("source", ""), **common)
    if kind is EventKind.SPEC_AMENDED:
        return SpecAmended(patch=payload.get("patch", {}), **common)
    if kind is EventKind.PLAN_FINALIZED:
        return PlanFinalized(
            node_count=int(payload.get("node_count", 0)),
            confidence=float(payload.get("confidence", 0.0)),
            **common,
        )
    if kind is EventKind.NODE_DISPATCHED:
        return NodeDispatched(
            node_id=payload.get("node_id", ""),
            route_class=RouteClass(payload["route_class"]) if payload.get("route_class") else None,
            run_mode=RunMode(payload["run_mode"]) if payload.get("run_mode") else None,
            **common,
        )
    if kind is EventKind.NODE_RUNNING:
        return NodeRunning(
            node_id=payload.get("node_id", ""),
            from_status=NodeStatus(payload.get("from_status", "pending")),
            **common,
        )
    if kind is EventKind.LOOP_REROUTED:
        return LoopRerouted(
            node_id=payload.get("node_id", ""),
            new_route=RouteClass(payload["new_route"]) if payload.get("new_route") else None,
            **common,
        )
    if kind is EventKind.EXECUTION_ATTEMPTED:
        return ExecutionAttempted(
            node_id=payload.get("node_id", ""),
            executor_id=payload.get("executor_id", ""),
            paradigm=RunMode(payload["paradigm"]) if payload.get("paradigm") else None,
            round=int(payload.get("round", 1)),
            route_class=RouteClass(payload["route_class"]) if payload.get("route_class") else None,
            trigger=AttemptTrigger(payload.get("trigger", "routed")),
            outcome=AttemptOutcome(payload["outcome"]) if payload.get("outcome") else None,
            **common,
        )
    if kind is EventKind.NODE_ACCEPTED:
        return NodeAccepted(node_id=payload.get("node_id", ""), verifier=payload.get("verifier", ""), **common)
    if kind is EventKind.NODE_REJECTED:
        return NodeRejected(
            node_id=payload.get("node_id", ""),
            verifier=payload.get("verifier", ""),
            reason=payload.get("reason", ""),
            **common,
        )
    if kind is EventKind.NODE_FAILED:
        return NodeFailed(
            node_id=payload.get("node_id", ""),
            verifier=payload.get("verifier", ""),
            reason=payload.get("reason", ""),
            **common,
        )
    if kind is EventKind.GOAL_VERIFIED:
        return GoalVerified(
            verifier=payload.get("verifier", ""),
            verdict=payload.get("verdict", ""),
            summary=payload.get("summary", ""),
            **common,
        )
    if kind is EventKind.GOAL_REJECTED:
        return GoalRejected(
            verifier=payload.get("verifier", ""),
            verdict=payload.get("verdict", ""),
            reason=payload.get("reason", ""),
            **common,
        )
    if kind is EventKind.CANCELLED:
        return Cancelled(by=payload.get("by", "user"), reason=payload.get("reason", ""), **common)
    if kind is EventKind.HUNG:
        return Hung(reason=payload.get("reason", ""), **common)
    # Unknown / forward-compat kind: return the base envelope.
    return TaskEvent(**common, payload=payload)


__all__ = ["OrmTaskEventRepository"]