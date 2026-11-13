"""``workflow_resolution`` 提升纯函数单测(task_commission spec §4.2/裁决 G)。

钉死与既有 ``services.context_collab_service._resolve_event_status`` 的
语义逐字等价(行为不变提升,通用服务改薄包装后以本模块 + append 套件
回归双证):
- 机器命中且守卫过 → 新 status;
- 无机器 / 无规则命中 → 原 status(事件仍接纳);
- 快照为 None 的外部部分行 → None(无可裁决);
- 守卫 FAIL → ``ContextCollabValidationError`` 响亮冒出;
``TASK_COMMISSION_PRECONDITIONS`` 为产品时序门(裁决 J)的唯一权威表。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.workflow_resolution import (
    TASK_COMMISSION_PRECONDITIONS,
    resolve_workflow_status,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task

PRINCIPAL = PrincipalRef(principal_type="human", principal_id="u_1001")
EXECUTOR = AgentRef(agent_type="bot", agent_id="bot_commission_001")


def _snapshot(status: str) -> Task:
    return Task(
        objective="整理任务代办 spec",
        status=status,
        principal_ref=PRINCIPAL,
        executor_ref=EXECUTOR,
        task_id="task_" + "collet_fixture",
        collet_ref="collet_fixture",
        version=1,
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_fixture",
        last_event_ref="evt_fixture",
    )


class TestEquivalenceWithLegacyResolver:
    def test_promotes_task_started_for_team_todo(self):
        result = resolve_workflow_status(
            workflow_kind="team_todo_execution",
            task=_snapshot("created"),
            event_kind="task.started",
            payload={},
            actor=PRINCIPAL,
        )
        assert result == "in_progress"

    def test_unknown_event_kind_keeps_current_status(self):
        result = resolve_workflow_status(
            workflow_kind="team_todo_execution",
            task=_snapshot("created"),
            event_kind="task.progress.updated",  # created 上无规则
            payload={},
            actor=PRINCIPAL,
        )
        assert result == "created"

    def test_unregisterd_workflow_kind_keeps_current_status(self):
        result = resolve_workflow_status(
            workflow_kind="bbs_public_discussion",  # 公开讨论无机器
            task=_snapshot("created"),
            event_kind="task.started",
            payload={},
            actor=PRINCIPAL,
        )
        assert result == "created"

    def test_null_snapshot_returns_none(self):
        result = resolve_workflow_status(
            workflow_kind="team_todo_execution",
            task=None,
            event_kind="task.started",
            payload={},
            actor=PRINCIPAL,
        )
        assert result is None

    def test_guard_failure_raises_loudly(self):
        """守卫 FAIL(陌生 actor 起任务)必须以校验错误冒出。"""
        with pytest.raises(ContextCollabValidationError):
            resolve_workflow_status(
                workflow_kind="team_todo_execution",
                task=_snapshot("reviewing"),
                event_kind="task.accepted",
                payload={},
                actor=PrincipalRef("human", "u_stranger"),
            )


class TestTaskCommissionPreconditions:
    def test_six_event_preconditions_pinned(self):
        """产品时序门唯一权威表(裁决 J);越轨即产品层拒。"""
        assert TASK_COMMISSION_PRECONDITIONS == {
            "task.started": {"created"},
            "task.progress.updated": {"in_progress"},
            "task.submitted": {"in_progress"},
            "task.accepted": {"reviewing"},
            "task.rejected": {"reviewing"},
            "task.cancelled": {
                "created",
                "in_progress",
                "reviewing",
                "blocked",
            },
        }
