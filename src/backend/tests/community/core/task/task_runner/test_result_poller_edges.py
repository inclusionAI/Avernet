"""TaskExecutorResultPoller 边角单测 —— SM/session 非终态轮询、取消分支与 double 客户端补充。

纯 stub 端口(不触网):非终态返回 None、非 SingleBotHandle 取消直接跳过、
cancel_run 异常 best-effort 吞掉不打断主回投。
"""
from __future__ import annotations

import asyncio

from types import SimpleNamespace

from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
    BcsGroupHandle,
    SingleBotHandle,
    TaskExecutorResultPoller,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Bcs:
    def __init__(self, run=None, group=None, session_msgs=None):
        self._run = run or {}
        self._group = group or {}
        self._session_msgs = session_msgs or []

    async def get_state_machine_run(self, run_id):
        return self._run

    async def get_group(self, group_id):
        return self._group

    async def get_session_messages(self, session_id, *, limit=50, since_msg_id=None):
        return self._session_msgs


class _Bot:
    def __init__(self, cancel=None):
        self._cancel = cancel
        self.cancelled = []

    async def cancel_run(self, run_id):
        self.cancelled.append(run_id)
        if self._cancel is not None:
            await self._cancel(run_id)


def _poller(bcs=None, bot=None):
    return TaskExecutorResultPoller(bot=bot or SimpleNamespace(), bcs=bcs or _Bcs())


def test_sm_mode_non_terminal_returns_none():
    poller = _poller(bcs=_Bcs(run={"status": "in_progress", "output": None}))
    handle = BcsGroupHandle(
        loop_task_id="t1::c1", group_id="g1", collab_mode="state_machine",
        registered_at=0.0, session_id="s1", run_id="run_1",
    )
    assert _run(poller._poll_terminal(handle)) is None   # run 模未达终态 → 继续等


def test_session_mode_non_terminal_returns_none():
    poller = _poller(bcs=_Bcs(group={"session": {"status": "in_progress", "output": None}}))
    handle = BcsGroupHandle(
        loop_task_id="t1::c1", group_id="g1", collab_mode="chat",
        registered_at=0.0, session_id="s1",
    )
    assert _run(poller._poll_terminal(handle)) is None   # session 模未达终态 → 继续等


def test_cancel_handle_skips_non_single_bot_handle():
    poller = _poller()
    group_handle = BcsGroupHandle(
        loop_task_id="t1::c1", group_id="g1", collab_mode="chat",
        registered_at=0.0, session_id="s1",
    )
    _run(poller._cancel_handle(group_handle))          # 非 SingleBotHandle → 取消 no-op


def test_unknown_handle_type_polls_to_none():
    poller = _poller()
    assert _run(poller._poll_terminal("not-a-handle")) is None   # 未知 handle → 轮询 no-op


def test_cancel_handle_swallows_cancel_exception():
    async def _boom(run_id):
        raise RuntimeError("transport down")

    bot = _Bot(cancel=_boom)
    poller = _poller(bot=bot)
    handle = SingleBotHandle(loop_task_id="t1::c1", run_id="run_1", bot_id="b1", registered_at=0.0)
    _run(poller._cancel_handle(handle))                  # best-effort:异常仅告警,不向调用方抛


# ===== double 客户端补充(get_state_machine_run 轮询 intermediate / validate_definition)=====
def test_double_bcs_state_machine_run_poll_and_validate():
    from agentclaw.community.core.task.task_runner.client.double.double_bcs_client import (
        _DoubleBcsClient,
    )

    client = _DoubleBcsClient(
        session_status="completed", session_output="ok",
        sm_status="completed", sm_output="ok",
        poll_once_then_terminal=True, terminal_after=2,
    )
    intermediate = _run(client.get_state_machine_run("run_1"))     # 轮次 < terminal_after → in_progress
    assert intermediate == {"status": "in_progress", "output": None, "error": None}
    terminal = _run(client.get_state_machine_run("run_1"))         # 达轮次阈值 → 终态
    assert terminal == {"status": "completed", "output": "ok", "error": None}
    assert _run(client.validate_definition("yaml: ...")) is None   # double 放行


def test_double_open_api_bot_cancel_run_marks_failed():
    from agentclaw.community.core.task.task_runner.client.double.double_open_api_bot import (
        _DoubleOpenApiBot,
    )

    bot = _DoubleOpenApiBot()
    assert _run(bot.cancel_run("run_x")) is None
    assert bot.cancelled == ["run_x"]
    run = _run(bot.get_run("run_x"))
    assert run["status"] == "FAILED" and run["error"] == "cancelled"


def test_double_context_provider_builds_execute_mode():
    from agentclaw.community.core.task.task_runner.client.double.double_context_provider import (
        _DoubleContextProvider,
    )

    assert _DoubleContextProvider().build("t1", "c1") == {"mode": "execute"}