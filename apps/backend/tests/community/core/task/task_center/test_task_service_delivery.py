"""S6 relay 交付轨迹写时发射单测(dispatch_node → task_trajectory_events 行)。

原读时投影用例(在 action_log payload 投影 trace_steps)已随「上报即落表」的
统一决策改写:交付事实在 dispatch_node 内**写时**即转轨迹事件行,经
``_task_context_service.emit_trajectory_event`` 直插表;action_log 保持
service 侧事实快照,读侧投影按秒粒度签名去重兜底。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.models import (
    Status,
    TaskNode,
)
from agentclaw.community.core.task.task_center.task_service_delivery import (
    TaskServiceDeliveryMixin,
)


class _Intents:
    """Delivery intent fake:一次 CLAIMED,记录 delivered 标记。"""

    def __init__(self) -> None:
        self.marks: list[tuple] = []
        self.settled: list[tuple[str, str, str]] = []

    def settle_delivery_intent(self, task_id, node_id, relay_token):
        self.settled.append((task_id, node_id, relay_token))
        return True

    def claim_delivery_intent(self, task_id, node_id, relay_token, *, lease_ms=None):
        return ("CLAIMED", f"intent:{node_id}")

    def mark_delivery_intent(self, task_id, node_id, intent, *, delivered, error=None):
        self.marks.append((task_id, node_id, intent, delivered))


class _Graph:
    def __init__(self, node: TaskNode) -> None:
        self._node = node

    def query_task_dashboard(self, task_id, node_id=None):
        return SimpleNamespace(is_relay=True, tasks=[self._node])

    def append_action_event(self, task_id, node_id, action, payload, **kwargs):
        pass


class _RecordingContextService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def emit_trajectory_event(self, task_id, node_id, action_type, **kwargs):
        self.calls.append((task_id, node_id, action_type, kwargs))


class _DispatchError(RuntimeError):
    pass


def _make_node(status: Status = Status.RUNNING) -> TaskNode:
    return TaskNode(
        node_id="node-1",
        task_id="task-1",
        status=status,
        task_spec=None,
        run_info=SimpleNamespace(
            run_mode="single_bot",
            assignee="bot-1",
            extend_props={"relay_token": "gen-1", "relay_planned_by": "root"},
        ),
        node_run_graph=None,
    )


class _Service(TaskServiceDeliveryMixin):
    def __init__(self, node: TaskNode, *, dispatch_exc: Exception | None = None):
        self._delivery_intents = _Intents()
        self._graph = _Graph(node)
        self._task_context_service = _RecordingContextService()

        async def _dispatch(n):
            if dispatch_exc is not None:
                raise dispatch_exc
            return n

        self._relay_adapter = SimpleNamespace(
            dispatcher=SimpleNamespace(dispatch=_dispatch)
        )


@pytest.mark.asyncio
async def test_dispatch_node_settles_stale_terminal_single_bot_intent() -> None:
    node = _make_node(status=Status.HUNG)
    service = _Service(node)

    result = await service.dispatch_node("task-1", "node-1")

    assert result is node
    assert service._delivery_intents.settled == [
        ("task-1", "node-1", "gen-1"),
    ]
    assert not service._delivery_intents.marks


@pytest.mark.asyncio
async def test_dispatch_node_settles_invalid_single_bot_intent_and_fails() -> None:
    node = _make_node()
    node.run_info.assignee = "NOT_DECIDED"
    service = _Service(node)

    with pytest.raises(
        Exception, match="relay delivery target has an invalid assignee"
    ):
        await service.dispatch_node("task-1", "node-1")

    assert service._delivery_intents.settled == [
        ("task-1", "node-1", "gen-1"),
    ]
    assert not service._delivery_intents.marks


@pytest.mark.asyncio
async def test_dispatch_node_emits_s6_delivered_trajectory_row() -> None:
    """S6 写时发射:交付成功 → 一条 dispatch 轨迹行(action_result 为输出
    JSON,七字段对齐表列;ext_info 带 trace_step + S6_DISPATCH 标识)。"""
    node = _make_node()
    service = _Service(node)

    delivered = await service.dispatch_node("task-1", "node-1")

    assert delivered is node
    assert service._delivery_intents.marks[-1][-1] is True
    ctx = service._task_context_service
    assert len(ctx.calls) == 1
    task_id, node_id, action_type, kwargs = ctx.calls[0]
    assert (task_id, node_id, action_type) == ("task-1", "node-1", "dispatch")
    action_result = json.loads(kwargs["action_result"])
    assert action_result == {
        "status": "completed",
        "dispatched": True,
        "target_node_id": "node-1",
        "run_mode": "single_bot",
        "assignee": "bot-1",
        "actual_run_mode": None,
        "group_id": None,
        "session_id": None,
    }
    assert '"target_node_id": "node-1"' in kwargs["action_input"]
    assert kwargs["status_from"] == Status.RUNNING
    assert kwargs["ext_info"] == {"trace_step": True, "step": "S6_DISPATCH"}
    assert kwargs.get("error_msg") is None


@pytest.mark.asyncio
async def test_dispatch_node_emits_s6_failed_trajectory_row_and_reraises() -> None:
    """S6 失败:轨迹行 action_result 为失败输出 JSON + error_msg(原始异常照常外抛)。"""
    node = _make_node()
    service = _Service(node, dispatch_exc=_DispatchError("boom"))

    with pytest.raises(_DispatchError):
        await service.dispatch_node("task-1", "node-1")

    assert service._delivery_intents.marks[-1][-1] is False
    ctx = service._task_context_service
    assert len(ctx.calls) == 1
    _, _, _, kwargs = ctx.calls[0]
    assert json.loads(kwargs["action_result"]) == {
        "status": "failed",
        "dispatched": False,
    }
    assert "DispatchError" in str(kwargs["error_msg"])
    assert kwargs["ext_info"] == {"trace_step": True, "step": "S6_DISPATCH"}


@pytest.mark.asyncio
async def test_dispatch_node_without_context_service_is_noop() -> None:
    """``_task_context_service`` 未接线 → 交付照常,发射静默跳过。"""
    node = _make_node()
    service = _Service(node)
    service._task_context_service = None

    delivered = await service.dispatch_node("task-1", "node-1")

    assert delivered is node