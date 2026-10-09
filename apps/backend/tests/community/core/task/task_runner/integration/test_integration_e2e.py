import asyncio
import logging

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.client import build_integration


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _wire():
    """真实 TaskService(真 ExecutionEngine/TaskRunner)+ build_integration(double) 注入点。
    示范 _execution_backend 在真 TaskRunner 上生效。"""
    g = TaskGraphService()
    svc = TaskService(g)
    exe = build_integration(double=True, sink=svc.callback, runner=None, poller_thread=False)
    svc._engine._runner._execution_backend = exe
    return svc, exe


def test_bbs_dispatch_without_graph_fails_without_changing_node_status(caplog):
    exe = _wire()[1]
    n = TaskNode(node_id="b1", task_id="t1", status=Status.RUNNING,
                 task_spec=TaskSpec(Metadata("t1", "T", "do"), Context("bg"),
                                    Goal("O", [AcceptanceCriteria("a1", "d")])),
                 run_info=RuntimeInfo(run_mode="bbs", assignee="bbs_bot"),
                 node_run_graph=None)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO):
        ok = _run(exe.dispatch([n]))
    assert ok == [False]
    assert n.status == Status.RUNNING
    assert "dispatch failed: graph missing" in caplog.text
