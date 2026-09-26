import asyncio
import logging

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskSpec,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import TaskExecutor


def _node(node_id="c1", task_id="t1", run_mode="bbs", assignee="b1"):
    return TaskNode(node_id=node_id, task_id=task_id, status=Status.PENDING,
                    task_spec=TaskSpec(Metadata(task_id, "T", "do"), Context("bg"),
                                       Goal("O", [AcceptanceCriteria("a1", "d")])),
                    run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
                    node_run_graph=None)  # type: ignore[arg-type]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_bbs_dispatch_without_graph_returns_false(caplog):
    exe = TaskExecutor(bot=None, bcs=None, formatter=None, context=None, sink=None, poller=None)
    with caplog.at_level(logging.INFO):
        res = _run(exe.dispatch([_node(run_mode="bbs")]))
    assert res == [False]
    assert "dispatch failed: graph missing" in caplog.text


def test_unknown_mode_returns_false():
    exe = TaskExecutor(bot=None, bcs=None, formatter=None, context=None, sink=None, poller=None)
    res = _run(exe.dispatch([_node(run_mode="weird")]))
    assert res == [False]


def test_dispatch_returns_one_bool_per_node():
    exe = TaskExecutor(bot=None, bcs=None, formatter=None, context=None, sink=None, poller=None)
    res = _run(exe.dispatch([_node("a", run_mode="bbs"), _node("b", run_mode="bbs")]))
    assert res == [False, False]


def test_runner_falls_back_to_stub_without_backend():
    from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
    from agentclaw.community.core.task.task_runner.task_runner import TaskRunner
    g = TaskGraphService()
    r = TaskRunner(g)  # 无 execution_backend
    res = _run(r.start_run([_node()]))
    assert res == [True]  # stub fallback
    assert r._run_log  # 记了投递日志
