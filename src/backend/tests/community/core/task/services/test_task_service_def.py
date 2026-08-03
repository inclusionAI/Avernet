"""TDD for TaskService intake/定义组 (Phase 2.1, plan §2.1).

Covers the drafting推进闭环 (clarify keeps DRAFTING → finalize_plan → DEFINED),
the state_machine guard on非法 moves, and the FR-OBS-11 create-time副屏 panel
popup (publishes a ``PanelMessage`` with component ``taskPanel.TaskWorkflowView``
+ task_id param).
"""
from __future__ import annotations

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
    assert task.status is TaskStatus.DRAFTING
    assert task.execution_graph is not None
    assert task.execution_graph.root_phase is TaskStatus.DRAFTING
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


# --- drafting→defined 推进 (clarify keeps DRAFTING; finalize_plan → DEFINED) ---


def test_amend_keeps_drafting():
    svc, _ = _service()
    task = svc.create(title="t")
    amended = svc.clarify(task.id, {"summary": "refined goal"})
    assert amended.status is TaskStatus.DRAFTING
    assert amended.spec.metadata.summary == "refined goal"


def test_clarify_writes_full_task_spec_five_elements():
    """clarify 必须把 skill 识别+澄清产出的五要素(goal/acceptances/deliverables/
    constraints)写进 task.spec,不能只认 title/summary/tags/background 把其余丢弃。
    否则 spawn_build_dag 挂到规划节点上的 task_spec 永远是空数组/空串。"""
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteriaKind,
        ConstraintKind,
        DeliverableType,
    )

    svc, _ = _service()
    task = svc.create(title="修复 PR #1243 命名")
    svc.clarify(task.id, {
        "summary": "getUsrInfo 命名不符 PRD",
        "background": "PRD §3.2 要求 getUserInfo",
        "goal": {
            "objective": "修复命名 + 对齐 PRD §3.2",
            "acceptances": [
                {"kind": "invariant", "properties": {"rule": "无 getUsrInfo 残留"}},
                {"kind": "behavior", "properties": {"when": "调用", "then": "返回 userInfo"}},
            ],
        },
        "deliverables": [
            {"type": "code", "location": "src/foo.py"},
            {"type": "doc", "location": "docs/prd.md"},
        ],
        "constraints": [
            {"kind": "hard", "text": "不动接口签名"},
        ],
    })
    spec = svc.get(task.id).spec
    assert spec.metadata.summary == "getUsrInfo 命名不符 PRD"
    assert spec.context.background == "PRD §3.2 要求 getUserInfo"
    # goal
    assert spec.goal is not None
    assert spec.goal.objective == "修复命名 + 对齐 PRD §3.2"
    assert [a.kind for a in spec.goal.acceptances] == [
        AcceptanceCriteriaKind.INVARIANT, AcceptanceCriteriaKind.BEHAVIOR,
    ]
    assert spec.goal.acceptances[0].properties == {"rule": "无 getUsrInfo 残留"}
    # deliverables
    assert [d.type for d in spec.deliverables] == [DeliverableType.CODE, DeliverableType.DOC]
    assert spec.deliverables[0].location == "src/foo.py"
    # constraints
    assert [c.kind for c in spec.context.constraints] == [ConstraintKind.HARD]
    assert spec.context.constraints[0].text == "不动接口签名"


def test_clarify_falls_back_on_unknown_enum_kinds():
    """skill 传了非法 enum 字符串时不该 500,得回退到默认(CUSTOM/SOFT)保住文本。"""
    svc, _ = _service()
    task = svc.create(title="t")
    svc.clarify(task.id, {
        "goal": {"objective": "o", "acceptances": [{"kind": "bogus", "properties": {}}]},
        "deliverables": [{"type": "unknown", "location": "x"}],
        "constraints": [{"kind": "weird", "text": "c"}],
    })
    spec = svc.get(task.id).spec
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteriaKind, ConstraintKind, DeliverableType,
    )
    assert spec.goal.acceptances[0].kind is AcceptanceCriteriaKind.CUSTOM
    assert spec.deliverables[0].type is DeliverableType.CUSTOM
    assert spec.context.constraints[0].kind is ConstraintKind.SOFT


def test_clarify_accepts_nested_context_form_matching_skill_curl():
    """SKILL.md 的 clarify curl 把 background/constraints 嵌在 patch.context 下
    (与领域模型 spec.context.* 同形),_apply_spec_patch 必须认这个嵌套形式,不能
    只认顶层 background/constraints。"""
    from agentclaw.community.core.task.domain.models import ConstraintKind

    svc, _ = _service()
    task = svc.create(title="t")
    svc.clarify(task.id, {
        "context": {
            "background": "PRD §3.2 要求 getUserInfo",
            "constraints": [{"kind": "hard", "text": "不动接口签名"}],
        },
        "goal": {"objective": "修复命名"},
        "deliverables": [{"type": "code", "location": "src/foo.py"}],
    })
    spec = svc.get(task.id).spec
    assert spec.context.background == "PRD §3.2 要求 getUserInfo"
    assert spec.context.constraints[0].kind is ConstraintKind.HARD
    assert spec.context.constraints[0].text == "不动接口签名"


def test_finalize_plan_advances_drafting_to_defined():
    svc, _ = _service()
    task = svc.create(title="t")
    svc.clarify(task.id, {"summary": "x"})
    planned = svc.finalize_plan(task.id, _plan())
    assert planned.status is TaskStatus.DEFINED
    assert planned.plan is not None
    assert len(planned.plan.sub_tasks) == 1


def test_finalize_plan_legal_direct_from_drafting():
    """New SM: DRAFTING → DEFINED is a legal direct edge, so finalize_plan
    succeeds even without a prior clarify (clarify no longer transitions)."""
    svc, _ = _service()
    task = svc.create(title="t")
    planned = svc.finalize_plan(task.id, _plan())
    assert planned.status is TaskStatus.DEFINED
    assert planned.plan is not None


def test_intake_loop_P3_close_to_defined():
    """P3 = plan–propose–approve闭环: create → clarify → finalize_plan."""
    svc, _ = _service()
    t = svc.create(title="goal")
    t = svc.clarify(t.id, {"summary": "s", "tags": ["x"]})
    t = svc.finalize_plan(t.id, _plan("n1"))
    assert t.status is TaskStatus.DEFINED
    assert t.spec.metadata.tags == ["x"]


def test_cancel_from_defined_legal():
    svc, _ = _service()
    t = svc.create(title="t")
    svc.clarify(t.id, {"summary": "s"})
    svc.finalize_plan(t.id, _plan())
    cancelled = svc.cancel(t.id, reason="user abort")
    assert cancelled.status is TaskStatus.CANCELLED


def test_amend_unknown_task_returns_none():
    svc, _ = _service()
    assert svc.clarify("nope", {"x": 1}) is None
    assert svc.finalize_plan("nope", _plan()) is None