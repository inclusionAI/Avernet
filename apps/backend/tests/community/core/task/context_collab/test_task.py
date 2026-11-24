"""Independent L4 Task contract, distinct from response projections."""

import json
from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ResourceBinding,
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
        executor_refs=(AgentRef("bot", "worker"),),
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
        {"executor_refs": ("worker",)},
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


def test_task_artifacts_default_empty_and_read_older_records():
    initial = task()
    assert initial.artifacts == ()
    data = initial.to_dict()
    assert data.pop("artifacts") == []
    assert Task.from_dict(data) == initial


def test_task_artifacts_roundtrip_without_changing_work_reporting():
    artifacts = (
        Artifact(
            "requirements",
            ResourceBinding(
                ResourceRef("document", "requirements.md", "v1"), "grant-1"
            ),
        ),
        Artifact(
            "code",
            ResourceBinding(ResourceRef("file", "calculator.py", None), None),
        ),
    )
    initial = replace(task(), artifacts=artifacts)
    data = initial.to_dict()
    assert data["artifacts"] == [
        {
            "artifact_id": "requirements",
            "resource_binding": {
                "resource_ref": {
                    "resource_type": "document",
                    "resource_id": "requirements.md",
                    "revision": "v1",
                },
                "grant_ref": "grant-1",
            },
        },
        {
            "artifact_id": "code",
            "resource_binding": {
                "resource_ref": {
                    "resource_type": "file",
                    "resource_id": "calculator.py",
                    "revision": None,
                },
                "grant_ref": None,
            },
        },
    ]
    restored = Task.from_dict(json.loads(json.dumps(data)))
    assert restored == initial
    assert all(isinstance(artifact, Artifact) for artifact in restored.artifacts)
    assert isinstance(restored.artifacts[0].resource_binding, ResourceBinding)
    assert isinstance(restored.artifacts[0].resource_binding.resource_ref, ResourceRef)
    updated = TaskUpdate(initial.task_id, 1, "reviewing").apply(
        initial,
        event_id="event-2",
        event_kind="task.submitted",
        payload={"artifacts": ["artifact://new-report"]},
    )
    assert updated.artifacts == initial.artifacts
    assert initial.version == 1


@pytest.mark.parametrize(
    "artifacts", [None, "artifact://one", [], ("artifact://one",), ({},), (1,)]
)
def test_task_rejects_untyped_artifacts(artifacts):
    with pytest.raises(ContextCollabValidationError, match="artifacts"):
        replace(task(), artifacts=artifacts)


@pytest.mark.parametrize("artifacts", [None, "artifact://one", (), [""], [1]])
def test_task_rejects_invalid_serialized_artifacts(artifacts):
    data = task().to_dict()
    data["artifacts"] = artifacts
    with pytest.raises(ContextCollabValidationError, match="artifacts"):
        Task.from_dict(data)


@pytest.mark.parametrize(
    "artifact",
    [
        {},
        {"artifact_id": "", "resource_binding": {}},
        {"artifact_id": "output", "resource_binding": "resource://output"},
        {"artifact_id": "output", "resource_binding": {"resource_ref": {}}},
    ],
)
def test_task_decodes_artifacts_with_the_l4_artifact_contract(artifact):
    data = task().to_dict()
    data["artifacts"] = [artifact]
    with pytest.raises(ContextCollabValidationError):
        Task.from_dict(data)
