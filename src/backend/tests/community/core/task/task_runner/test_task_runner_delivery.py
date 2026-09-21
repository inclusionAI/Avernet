"""TaskRunner 投递 seam 单测 —— set_delivery 注入分支、port.deliver 真投递路径与
get_group_session 退桩回退(无 execution_backend)。

纯 stub:graph/端口均不触网,验证 Runner 的三模态分发与退桩行为契约。
"""
from __future__ import annotations

import asyncio

from types import SimpleNamespace

from agentclaw.community.core.task.task_runner.task_runner import TaskRunner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _node(node_id="c1", run_mode="single_bot"):
    return SimpleNamespace(
        node_id=node_id,
        task_id="t1",
        run_info=SimpleNamespace(run_mode=run_mode, assignee="b1"),
    )


class _Port:
    def __init__(self, ok=True):
        self.delivered = []
        self._ok = ok

    async def deliver(self, node) -> bool:
        self.delivered.append(node.node_id)
        return self._ok


def test_set_delivery_registers_port_and_start_run_uses_it():
    runner = TaskRunner(graph=None)
    port = _Port(ok=True)
    runner.set_delivery("single_bot", port)
    assert runner._deliveries["single_bot"] is port

    results = _run(runner.start_run([_node("c1"), _node("c2", run_mode="coop_group")]))
    # 注入端口的模态走真投递;未注入模态退桩记日志返 True
    assert results == [True, True]
    assert port.delivered == ["c1"]


def test_start_run_returns_false_for_unknown_mode():
    runner = TaskRunner(graph=None)
    results = _run(runner.start_run([_node(run_mode="nonsense")]))
    assert results == [False]               # 非法模态 → False(不投递)
    assert runner._run_log == []              # 未走投递,无 stub 记录


def test_get_group_session_falls_back_to_none_without_backend():
    runner = TaskRunner(graph=None)           # 无 execution_backend → 退桩 None
    assert _run(runner.get_group_session("grp_x")) is None