"""TDD for TaskService intake/定义组 (Phase 2.1, plan §2.1).

Covers the intake推进闭环 INTAKE → DISCUSSING → PLANNED, the state_machine
guard on非法 moves, and the FR-OBS-11 create-time副屏 panel popup (publishes a
``PanelMessage`` with component ``taskPanel.TaskWorkflowView`` + task_id param).
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.protocols import PanelMessage
from agentclaw.community.core.task.domain.events import EventKind
from agentclaw.community.core.task.domain.models import (
    Plan,
    SubTaskSpec,
    TaskStatus,
)
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _service() -> tuple[TaskService, RecordingPanelPublisher]:
    pub = RecordingPanelPublisher()
    svc = TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), pub)
    return svc, pub


def _plan(node_id: str = "n1") -> Plan:
    return Plan(sub_tasks=[SubTaskSpec(node_id=node_id, spec="do x")], confidence=0.8)


# --- create + panel popup (FR-OBS-11) --------------------------------------


def test_create_yields_task_at_intake_with_root_phase():
    svc, _ = _service()
    task = svc.create(title="wire goal-driven canvas", source="api", background="bg")
    assert task.status is TaskStatus.INTAKE
    assert task.execution_graph is not None
    assert task.execution_graph.root_phase is TaskStatus.INTAKE
    assert task.id.startswith("task-")
    assert task.spec.metadata.title == "wire goal-driven canvas"
    assert task.spec.context.background == "bg"


def test_create_publishes_panel_popup_with_task_id():
    """FR-OBS-11: task creation triggers the副屏 dynamic-DAG canvas popup."""
    svc, pub = _service()
    task = svc.create(title="t", source="api")
    assert len(pub.published) == 1
    msg = pub.published[0]
    assert isinstance(msg, PanelMessage)
    assert msg.component == "taskPanel.TaskWorkflowView"
    assert msg.params.get("task_id") == task.id


def test_create_emits_task_created_event_with_monotonic_seq():
    svc, _ = _service()
    task = svc.create(title="t", source="api")
    events = svc._event_repo.load_events(task.id)  # noqa: SLF001
    assert len(events) == 1
    assert events[0].kind is EventKind.TASK_CREATED
    assert events[0].seq == 1


# --- intake推进 INTAKE → DISCUSSING → PLANNED ------------------------------


def test_amend_advances_intake_to_discussing():
    svc, _ = _service()
    task = svc.create(title="t")
    amended = svc.amend(task.id, {"summary": "refined goal"})
    assert amended.status is TaskStatus.DISCUSSING
    assert amended.spec.metadata.summary == "refined goal"


def test_finalize_plan_advances_discussing_to_planned():
    svc, _ = _service()
    task = svc.create(title="t")
    svc.amend(task.id, {"summary": "x"})
    planned = svc.finalize_plan(task.id, _plan())
    assert planned.status is TaskStatus.PLANNED
    assert planned.plan is not None
    assert len(planned.plan.sub_tasks) == 1


def test_finalize_plan_illegal_before_any_amend():
    svc, _ = _service()
    task = svc.create(title="t")
    # INTAKE → PLANNED is not a legal direct edge (must go via DISCUSSING).
    from agentclaw.community.core.task.domain.state_machine import (
        IllegalTransitionError,
    )

    with pytest.raises(IllegalTransitionError):
        svc.finalize_plan(task.id, _plan())


def test_intake_loop_P3_close_to_planned():
    """P3 = plan–propose–approve闭环: create → amend → finalize_plan."""
    svc, _ = _service()
    t = svc.create(title="goal")
    t = svc.amend(t.id, {"summary": "s", "tags": ["x"]})
    t = svc.finalize_plan(t.id, _plan("n1"))
    assert t.status is TaskStatus.PLANNED
    assert t.spec.metadata.tags == ["x"]


def test_cancel_from_planned_legal():
    svc, _ = _service()
    t = svc.create(title="t")
    svc.amend(t.id, {"summary": "s"})
    svc.finalize_plan(t.id, _plan())
    cancelled = svc.cancel(t.id, reason="user abort")
    assert cancelled.status is TaskStatus.CANCELLED


def test_amend_unknown_task_returns_none():
    svc, _ = _service()
    assert svc.amend("nope", {"x": 1}) is None
    assert svc.finalize_plan("nope", _plan()) is None