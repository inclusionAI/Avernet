"""Typed Task storage and explicit compatibility decoding; never writes on GET."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.views import (
    CurrentSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.utils.env_utils import get_current_env


def legacy_task(
    collet_id: str, snapshot: CurrentSnapshot, context_version: int
) -> Task:
    """Decode the historic single-work-item record into an explicit import baseline.

    Stable identity is a documented legacy mapping, NOT a claim that an old Task
    ID/version existed. The next accepted append materializes it transactionally;
    historic events are not rewritten and GET remains read-only.
    """
    return Task(
        task_id="task_legacy_"
        + uuid5(NAMESPACE_URL, f"accs:collet:{collet_id}:root").hex,
        collet_ref=collet_id,
        objective=snapshot.objective,
        status=snapshot.status,
        version=1,
        principal_ref=snapshot.principal_ref,
        executor_ref=snapshot.executor_ref,
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref=None,
        last_event_ref=None,
        legacy_context_version=context_version,
    )


def load_tasks(db, row) -> tuple[Task | None, tuple[Task, ...]]:
    if row.root_task_ref is None:
        orphan = (
            db.query(ContextCollabTaskModel.id)
            .filter(
                ContextCollabTaskModel.collab_context_id == row.collab_context_id,
                ContextCollabTaskModel.env == get_current_env(),
            )
            .first()
        )
        if orphan is not None:
            raise ContextCollabValidationError(
                "Task rows exist without a root Task reference"
            )
        if row.current_snapshot is None:
            return (
                None,
                (),
            )  # An explicitly partial, historical context has no Work state.
        task = legacy_task(
            row.collab_context_id,
            CurrentSnapshot.from_dict(json.loads(row.current_snapshot)),
            int(row.context_version),
        )
        return task, (task,)
    task_rows = (
        db.query(ContextCollabTaskModel)
        .filter(
            ContextCollabTaskModel.collab_context_id == row.collab_context_id,
            ContextCollabTaskModel.env == get_current_env(),
        )
        .all()
    )
    tasks = []
    for stored in task_rows:
        task = Task.from_dict(json.loads(stored.task_data))
        if (
            task.task_id != stored.task_id
            or task.collet_ref != row.collab_context_id
            or task.version != int(stored.task_version)
            or task.parent_task_ref != stored.parent_task_ref
        ):
            raise ContextCollabValidationError("Task storage identity/version mismatch")
        tasks.append(task)
    root = next((task for task in tasks if task.task_id == row.root_task_ref), None)
    if root is None or root.parent_task_ref is not None:
        raise ContextCollabValidationError("missing or invalid root Task")
    ids = {task.task_id for task in tasks}
    if any(
        task.parent_task_ref is not None and task.parent_task_ref not in ids
        for task in tasks
    ):
        raise ContextCollabValidationError("Task parent is outside the Collet")
    parents = {task.task_id: task.parent_task_ref for task in tasks}
    visited = set()
    for task in tasks:
        path = set()
        node = task.task_id
        while node is not None and node not in visited:
            if node in path:
                raise ContextCollabValidationError("Task parent graph contains a cycle")
            path.add(node)
            node = parents[node]
        visited.update(path)
    _validate_task_evidence(db, row, tasks)
    return root, tuple(tasks)


def legacy_progress(task: Task | None, events) -> Task | None:
    """Recover only explicitly reported, valid Task progress from legacy evidence."""
    if task is None or task.legacy_context_version is None:
        return task
    for event in events:
        percent = (event.payload or {}).get("percent")
        if (
            event.event_kind == "task.progress.updated"
            and event.primitive_type in {"Task", "Activity"}
            and event.task_ref in {None, task.task_id}
            and event.context_version_after is not None
            and 1 <= event.context_version_after <= task.legacy_context_version
            and type(percent) is int
            and 0 <= percent <= 100
        ):
            task = replace(
                task, reported_percent=percent, progress_event_ref=event.event_id
            )
    return task


def _validate_task_evidence(db, row, tasks) -> None:
    """Check stored authority against same-context events, not a view or label.

    Imported version-1 Tasks explicitly lack historical creation/update refs.
    Their old progress event can lack Task subject/revision, but must come from
    the documented legacy context baseline. New updates always carry both.
    """
    refs = {
        ref
        for task in tasks
        for ref in (
            task.created_event_ref,
            task.last_event_ref,
            task.progress_event_ref,
        )
        if ref is not None
    }
    events = {
        event.event_id: event
        for event in db.query(ContextCollabEventModel)
        .filter(
            ContextCollabEventModel.collab_context_id == row.collab_context_id,
            ContextCollabEventModel.env == get_current_env(),
            ContextCollabEventModel.event_id.in_(refs),
        )
        .all()
    }
    for task in tasks:
        for ref, kind in (
            (task.created_event_ref, "creation"),
            (task.last_event_ref, "update"),
            (task.progress_event_ref, "progress"),
        ):
            if ref is None:
                continue
            event = events.get(ref)
            if (
                event is None
                or event.primitive_type not in {"Task", "Activity"}
                or not event.event_kind.startswith("task.")
            ):
                raise ContextCollabValidationError(
                    "Task evidence is missing or is not Work"
                )
            historical_progress = (
                kind == "progress"
                and task.legacy_context_version is not None
                and event.task_ref is None
                and event.task_version_after is None
                and event.context_version_after is not None
                and event.context_version_after <= task.legacy_context_version
            )
            if not historical_progress and (
                event.task_ref != task.task_id
                or type(event.task_version_after) is not int
                or not 1 <= event.task_version_after <= task.version
            ):
                raise ContextCollabValidationError(
                    "Task evidence subject/revision mismatch"
                )
            if kind == "creation" and (
                event.event_kind != "task.created" or event.task_version_after != 1
            ):
                raise ContextCollabValidationError("Task creation evidence mismatch")
            if kind == "update" and event.task_version_after != task.version:
                raise ContextCollabValidationError(
                    "Task update evidence revision mismatch"
                )
            if kind == "progress":
                payload = json.loads(event.payload) if event.payload is not None else {}
                if (
                    event.event_kind != "task.progress.updated"
                    or type(payload.get("percent")) is not int
                    or payload["percent"] != task.reported_percent
                ):
                    raise ContextCollabValidationError(
                        "Task progress evidence mismatch"
                    )
