"""Independent L4 Task contract, distinct from response projections."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskUpdate,
)


def task():
    return Task(
        task_id="task-1",
        collet_ref="collet-1",
        objective="产出可验收成果",
        status="created",
        version=1,
        principal_ref=PrincipalRef("human", "owner"),
        executor_ref=AgentRef("bot", "worker"),
        parent_task_ref=None,
        completion_conditions=("发布人验收成果",),
        created_event_ref="event-1",
        last_event_ref="event-1",
    )


def test_typed_task_roundtrip_and_progress_updates_preserve_identity():
    initial = task()
    assert Task.from_dict(initial.to_dict()) == initial
    progress = TaskUpdate(initial.task_id, 1, "in_progress").apply(
        initial,
        event_id="event-2",
        event_kind="task.progress.updated",
        payload={"percent": 100},
    )
    assert progress.task_id == initial.task_id
    assert progress.version == 2
    assert progress.status == "in_progress"
    assert progress.reported_percent == 100
    assert progress.progress_event_ref == progress.last_event_ref == "event-2"
    assert progress.created_event_ref == "event-1"
    assert Task.from_dict(progress.to_dict()) == progress


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_id": ""},
        {"collet_ref": ""},
        {"objective": ""},
        {"version": True},
        {"version": 0},
        {"status": "RUNNING"},
        {"principal_ref": "owner"},
        {"executor_ref": "worker"},
        {"parent_task_ref": "task-1"},
        {"completion_conditions": ["not a tuple"]},
        {"completion_conditions": ("",)},
        {"created_event_ref": None},
        {"last_event_ref": None},
        {"reported_percent": 60},
        {"reported_percent": True, "progress_event_ref": "event-2"},
        {"reported_percent": 101, "progress_event_ref": "event-2"},
        {"legacy_context_version": True},
    ],
)
def test_task_rejects_invalid_contract(overrides):
    with pytest.raises(ContextCollabValidationError):
        replace(task(), **overrides)


def test_import_baseline_is_explicit_not_a_fabricated_task_revision():
    imported = replace(
        task(), created_event_ref=None, last_event_ref=None, legacy_context_version=42
    )
    assert imported.version == 1
    assert Task.from_dict(imported.to_dict()) == imported
    with pytest.raises(ContextCollabValidationError, match="update evidence"):
        replace(imported, version=2)
    updated = TaskUpdate(imported.task_id, 1, "in_progress").apply(
        imported,
        event_id="new-event",
        event_kind="task.started",
        payload={},
    )
    assert updated.created_event_ref is None
    assert updated.last_event_ref == "new-event"
    assert updated.legacy_context_version == 42


@pytest.mark.parametrize(
    "values",
    [
        ("", 1, "created"),
        ("task-1", True, "created"),
        ("task-1", 0, "created"),
        ("task-1", 1, "unknown"),
    ],
)
def test_task_update_rejects_invalid_fence(values):
    with pytest.raises(ContextCollabValidationError):
        TaskUpdate(*values)
