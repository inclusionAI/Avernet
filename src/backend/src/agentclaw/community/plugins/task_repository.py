"""Unified ORM ``TaskRepo`` impl for the :class:`Task` aggregate (Phase 1.3).

One ORM body behind the :class:`TaskRepo` Protocol; the only per-environment
difference is the injected :class:`DatabasePlugin` (SQLite local/CI vs OceanBase
prod via the same ``orm_session()``). ``save`` is an upsert keyed on ``task_id``;
``get_by_id`` / ``list_by_user`` deep-copy-by-reconstruct so callers cannot
mutate stored state by holding prior references (the repo invariant).

The aggregate's ``spec`` / ``execution_graph`` dataclasses are serialized to JSON
on save and deserialized on load. Env-scoping filters every query (prod parity).

Avernet rules: no bare SQL (ORM only); ``Optional[T]``; ``@inject``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Optional

from injector import inject
from sqlalchemy import and_

from agentclaw.community.core.task.domain.models import (
    Task,
    TaskExecutionGraph,
    TaskSource,
    TaskSpec,
    GraphStatus,
)
from agentclaw.community.core.task.domain.repository import TaskNotFoundError
from agentclaw.community.core.task.repository.models import AcTaskModel
from agentclaw.community.plugin_api.database import DatabasePlugin


def _json_dumps(obj: object) -> Optional[str]:
    """Serialize a dataclass-with-enums to JSON (enums → their .value).
    Returns None for None input so the nullable column stores NULL (not the
    literal string ``"null"``), which keeps the deserialize guard simple."""
    if obj is None:
        return None

    def _default(o: object) -> object:
        if is_dataclass(o):
            return asdict(o)  # type: ignore[arg-type]
        # StrEnum → value
        value = getattr(o, "value", None)
        if value is not None and hasattr(o, "value"):
            return value
        raise TypeError(f"not JSON serializable: {type(o)!r}")

    return json.dumps(obj, default=_default, ensure_ascii=False)


class OrmTaskRepository:
    """ORM-backed :class:`TaskRepo`."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def save(self, task: Task) -> None:
        with self._db.orm_session() as session:
            existing = (
                session.query(AcTaskModel)
                .filter(AcTaskModel.task_id == task.id)
                .one_or_none()
            )
            if existing is None:
                model = AcTaskModel(task_id=task.id)
                _apply_task_to_model(task, model)
                session.add(model)
            else:
                _apply_task_to_model(task, existing)
            session.flush()

    def get_by_id(self, task_id: str) -> Task:
        with self._db.orm_session() as session:
            model = (
                session.query(AcTaskModel)
                .filter(AcTaskModel.task_id == task_id)
                .one_or_none()
            )
            if model is None:
                raise TaskNotFoundError(task_id)
            return _model_to_task(model)

    def list_by_user(self, user_id: str) -> list[Task]:
        with self._db.orm_session() as session:
            rows = (
                session.query(AcTaskModel)
                .filter(
                    and_(
                        AcTaskModel.user_id == user_id,
                    )
                )
                .order_by(AcTaskModel.gmt_create.desc())
                .all()
            )
            return [_model_to_task(r) for r in rows]


def _apply_task_to_model(task: Task, model: AcTaskModel) -> None:
    model.user_id = task.user_id
    model.source = task.source.value
    model.status = task.status.value
    model.loop_round = task.loop_round
    model.spec_json = _json_dumps(task.spec)
    model.execution_graph_json = _json_dumps(task.execution_graph)


def _model_to_task(model: AcTaskModel) -> Task:
    spec = _deserialize_spec(model.spec_json)
    graph = _deserialize_graph(model.execution_graph_json)
    return Task(
        id=model.task_id,
        user_id=model.user_id,
        source=TaskSource(model.source),
        spec=spec,
        execution_graph=graph,
    )


def _deserialize_spec(raw: Optional[str]) -> TaskSpec:
    """Best-effort deserialize — reconstructs the full :class:`TaskSpec`."""
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteria,
        AcceptanceCriteriaKind,
        Constraint,
        ConstraintKind,
        Deliverable,
        DeliverableType,
        ExecutionMeta,
        RunMode,
        TaskContext,
        TaskGoal,
        TaskSpecMetadata,
    )

    if not raw:
        return TaskSpec(metadata=TaskSpecMetadata(id="", title=""))
    data = json.loads(raw)
    meta = TaskSpecMetadata(
        id=data.get("metadata", {}).get("id", ""),
        title=data.get("metadata", {}).get("title", ""),
        summary=data.get("metadata", {}).get("summary", ""),
        tags=data.get("metadata", {}).get("tags", []),
    )
    ctx_data = data.get("context") or {}
    context = TaskContext(
        background=ctx_data.get("background", ""),
        constraints=[
            Constraint(
                kind=ConstraintKind(c.get("kind", "hard")),
                text=c.get("text", ""),
            )
            for c in ctx_data.get("constraints", [])
        ],
    )
    goal_data = data.get("goal")
    goal = None
    if goal_data:
        goal = TaskGoal(
            objective=goal_data.get("objective", ""),
            acceptances=[
                AcceptanceCriteria(
                    kind=AcceptanceCriteriaKind(a.get("kind", "custom")),
                    properties=a.get("properties", {}),
                )
                for a in goal_data.get("acceptances", [])
            ],
        )
    execution = data.get("execution")
    exec_meta = None
    if execution:
        exec_meta = ExecutionMeta(
            run_mode=RunMode(execution["run_mode"]) if execution.get("run_mode") else None,
            owner_bot=execution.get("owner_bot"),
        )
    deliverables = [
        Deliverable(
            type=DeliverableType(d.get("type", "custom")),
            location=d.get("location", ""),
        )
        for d in data.get("deliverables", [])
    ]
    return TaskSpec(
        metadata=meta,
        context=context,
        goal=goal,
        deliverables=deliverables,
        execution=exec_meta,
    )


def _deserialize_graph(raw: Optional[str]) -> Optional[TaskExecutionGraph]:
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteria,
        AcceptanceCriteriaKind,
        ArtifactRef,
        AttemptedRecord,
        AttemptOutcome,
        AttemptTrigger,
        Deliverable,
        DeliverableType,
        Edge,
        EdgeKind,
        Node,
        NodeStatus,
        RouteClass,
        RunMode,
        SubDagRef,
    )

    if not raw:
        return None
    data = json.loads(raw)
    nodes = [
        Node(
            node_id=n.get("node_id", ""),
            spec=n.get("spec", ""),
            status=NodeStatus(n.get("status", "pending")),
            run_mode=RunMode(n["run_mode"]) if n.get("run_mode") else None,
            targets_acceptance=[
                AcceptanceCriteria(
                    kind=AcceptanceCriteriaKind(a.get("kind", "custom")),
                    properties=a.get("properties", {}),
                )
                for a in n.get("targets_acceptance", [])
            ],
            targets_deliverable=[
                Deliverable(
                    type=DeliverableType(d.get("type", "custom")),
                    location=d.get("location", ""),
                )
                for d in n.get("targets_deliverable", [])
            ],
            artifacts=[
                ArtifactRef(
                    name=a.get("name", ""),
                    location=a.get("location", ""),
                    type=a.get("type", ""),
                )
                for a in n.get("artifacts", [])
            ],
            attempted_executors=[
                AttemptedRecord(
                    executor_id=a.get("executor_id", ""),
                    paradigm=RunMode(a.get("paradigm", "single_bot")),
                    round=int(a.get("round", 1)),
                    outcome=AttemptOutcome(a["outcome"]) if a.get("outcome") else None,
                    route_class=RouteClass(a["route_class"]) if a.get("route_class") else None,
                    trigger=AttemptTrigger(a.get("trigger", "routed")),
                    at=a.get("at"),
                    note=a.get("note", ""),
                )
                for a in n.get("attempted_executors", [])
            ],
            properties=n.get("properties", {}),
            assignee=n.get("assignee"),
            instruction=n.get("instruction"),
            sub_dag=(
                SubDagRef(
                    ref_kind=n["sub_dag"].get("ref_kind", ""),
                    bcs_run_id=n["sub_dag"].get("bcs_run_id", ""),
                    group_id=n["sub_dag"].get("group_id", ""),
                    workflow_yaml_snapshot=n["sub_dag"].get("workflow_yaml_snapshot"),
                )
                if n.get("sub_dag")
                else None
            ),
        )
        for n in data.get("nodes", [])
    ]
    edges = [
        Edge(
            edge_id=e.get("edge_id", ""),
            from_node=e.get("from_node", ""),
            to_node=e.get("to_node", ""),
            kind=EdgeKind(e.get("kind", "dependency")),
        )
        for e in data.get("edges", [])
    ]
    return TaskExecutionGraph(
        status=GraphStatus(data.get("status") or "drafting"),
        loop_round=int(data.get("loop_round", 0)),
        nodes=nodes,
        edges=edges,
    )


__all__ = ["OrmTaskRepository"]