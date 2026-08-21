from __future__ import annotations

import asyncio

import pytest

from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, Status, TaskInfo, TaskNodePatch, TaskSpec,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="O", acceptances=[AcceptanceCriteria(id="a1", description="done")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _engine_with_root(task_id="t1"):
    graph = TaskGraphService()
    graph.initialize_graph(_task_info(task_id))
    eng = ExecutionEngine(graph)
    # initialize_graph 建图首帧:全局图 status=RUNNING,只含根节点(node_id==task_id) PENDING。
    root = next(n for n in graph.query_task_dashboard(task_id).tasks if n.node_id)
    return eng, graph, root


class TestOnStart:
    def test_pending_to_running(self):
        eng, graph, root = _engine_with_root()
        assert root.status == Status.PENDING
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        res = _run(eng.on_start(patch))
        assert res.success is True
        assert res.new_status == Status.RUNNING
        assert root.status == Status.RUNNING

    def test_already_running_is_idempotent_noop(self):
        eng, graph, root = _engine_with_root()
        root.status = Status.RUNNING  # 直接置态(测试用)
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        res = _run(eng.on_start(patch))
        assert res.success is True
        assert res.prev_status == Status.RUNNING
        assert res.new_status == Status.RUNNING

    @pytest.mark.parametrize("term", [Status.DONE, Status.FAILED, Status.HUNG, Status.PLANNING])
    def test_terminal_or_planning_raises_stale(self, term):
        eng, graph, root = _engine_with_root()
        root.status = term
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        with pytest.raises(TaskStateError):
            _run(eng.on_start(patch))

    def test_unknown_node_raises_not_found(self):
        eng, graph, root = _engine_with_root()
        patch = TaskNodePatch(task_id=root.task_id, node_id="nope", status=Status.RUNNING)
        with pytest.raises(NodeNotFoundError):
            _run(eng.on_start(patch))

    def test_start_does_not_trigger_drain_or_propagation(self):
        eng, graph, root = _engine_with_root()
        before = graph.query_task_dashboard(root.task_id)
        before_graph_status = before.status
        before_node_count = len(before.tasks)
        patch = TaskNodePatch(task_id=root.task_id, node_id=root.node_id, status=Status.RUNNING)
        _run(eng.on_start(patch))
        after = graph.query_task_dashboard(root.task_id)
        # on_start 纯节点态翻转,不触发 _drain/finish/传播:图 status 不变(初始 RUNNING),节点数不变。
        assert after.status == before_graph_status
        assert len(after.tasks) == before_node_count
        assert root.status == Status.RUNNING