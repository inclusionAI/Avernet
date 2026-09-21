"""``build_integration`` 组合根单测 —— 生产分支(corp 覆写前默认 wiring)与 poller 线程分支。

真实适配器构造均为纯对象装配(httpx client 惰性建连,不发任何请求);corp 的
``_RealToken`` 未注入时以桩替身补齐契约属性。
"""
from __future__ import annotations

import threading

import agentclaw.community.core.task.task_runner.client as client_pkg
from agentclaw.community.core.task.task_runner.client import build_integration


class _Sink:
    async def report_result(self, data) -> None:  # pragma: no cover - 桩
        ...


class _Runner:
    def _build_context(self, task_id, node_id):
        return {"mode": "execute"}


class _FakeRealToken:
    """corp overlay 未注入时替身:满足 ``BcsTokenProvider`` 契约属性。"""

    token = "t"
    secret = "s"
    base_url = "http://localhost:21000"
    task_callback_url = ""


def test_double_branch_builds_doubles():
    exe = build_integration(double=True, sink=_Sink(), runner=None, poller_thread=False)
    kind = type(exe).__name__
    assert kind == "TaskExecutor"
    # double 分支:双端 + 双上下文(无 runner 不走 _RunnerContextBuilder)
    assert type(exe._bot).__name__ == "_DoubleOpenApiBot"
    assert type(exe._bcs).__name__ == "_DoubleBcsClient"
    assert type(exe._context).__name__ == "_DoubleContextProvider"
    assert exe._poller is not None and exe._poller._sink is not None


def test_real_branch_builds_production_adapters(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_runner.client.bcs_token_provider._RealToken",
        _FakeRealToken,
        raising=False,  # corp 未覆写时模块里不存在该名字,补齐待覆写点
    )
    exe = build_integration(double=False, sink=_Sink(), runner=_Runner(), poller_thread=False)
    # 生产 wiring:_DoubleApiKeyProvider 凭据 + OpenApiBotAdapter/BcsHttpAdapter + RunnerContextBuilder
    assert type(exe._bot).__name__ == "OpenApiBotAdapter"
    assert type(exe._bcs).__name__ == "BcsHttpAdapter"
    assert type(exe._context).__name__ == "_RunnerContextBuilder"
    assert type(exe._formatter).__name__ == "PromptFormatterImpl"


def test_real_branch_without_runner_falls_back_to_double_context(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_runner.client.bcs_token_provider._RealToken",
        _FakeRealToken,
        raising=False,
    )
    exe = build_integration(double=False, sink=_Sink(), runner=None, poller_thread=False)
    assert type(exe._context).__name__ == "_DoubleContextProvider"


def test_poller_thread_branch_starts_daemon(monkeypatch):
    from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
        TaskExecutorResultPoller,
    )

    created: list[threading.Thread] = []
    original_thread = threading.Thread

    def _recorder(*args, **kwargs):
        thread = original_thread(*args, **kwargs)
        created.append(thread)
        return thread

    monkeypatch.setattr(client_pkg.threading, "Thread", _recorder)
    monkeypatch.setattr(  # 桩掉循环体:只验证线程分支被真实启动 daemon=True
        TaskExecutorResultPoller, "run_poll_loop", lambda self: None
    )
    exe = build_integration(double=True, sink=_Sink(), poller_thread=True)
    assert len(created) == 1
    assert created[0].daemon is True
    created[0].join(timeout=2)  # 循环体被桩空 → 线程立即退出
    assert type(exe).__name__ == "TaskExecutor"