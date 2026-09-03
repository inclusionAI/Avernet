"""TaskExecutorResultPoller 旁路 sidecar(同 TaskHarness 风格):三模态回收 single_bot/session/run。

daemon 线程持自有 loop 跑 run_poll_loop;_poll_once 为 async(端口 async),测试直驱。
SLA 超时→FAIL sla_timeout;连续 5 次端口失败→FAIL poll_exhausted;终态→翻译→report_result→注销。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from agentclaw.community.core.task.domain.models import TaskCallbackData
from agentclaw.community.core.task.task_runner.client.translators import (
    BcsSessionTranslator, BcsStateMachineRunTranslator, SingleBotRunTranslator,
)

logger = logging.getLogger("task.poller")

_DEFAULT_INTERVAL = 3.0
_DEFAULT_SLA = 600.0  # 真实 LLM execute round-trip(单 bot 产出)可达 10 分钟;double 立即终态不受影响
_MAX_CONSEC_FAIL = 5

_TERMINAL_SINGLE = {"COMPLETED", "FAILED"}
_TERMINAL_SM = {"completed", "failed", "aborted"}


@dataclass
class SingleBotHandle:
    loop_task_id: str
    run_id: str
    bot_id: str
    registered_at: float
    fails: int = 0
    session_id: str | None = None


@dataclass
class BcsGroupHandle:
    loop_task_id: str
    group_id: str
    collab_mode: str
    registered_at: float
    session_id: str | None = None
    run_id: str | None = None
    since_cursor: str | None = None
    fails: int = 0


class TaskExecutorResultPoller:
    def __init__(self, *, bot, bcs,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 interval: float = _DEFAULT_INTERVAL,
                 default_sla: float = _DEFAULT_SLA) -> None:
        self._bot = bot
        self._bcs = bcs
        self._clock = clock
        self._sleep = sleep
        self._interval = interval
        self._default_sla = default_sla
        self._sink = None
        self._handles: list[Any] = []
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def set_on_result(self, sink) -> None: self._sink = sink

    def pending(self) -> int:
        with self._lock:
            return len(self._handles)

    def register(self, handle) -> None:
        with self._lock:
            self._handles.append(handle)

    def stop(self) -> None:
        self._stop.set()

    def _sla_for(self, handle) -> float:
        return self._default_sla  # T8 可扩展读 gf.extend_props["sla_timeout_ms"]

    async def _report(self, data: TaskCallbackData, handle) -> None:
        if self._sink is not None:
            await self._sink.report_result(data)
        with self._lock:
            if handle in self._handles:
                self._handles.remove(handle)

    async def _poll_one(self, handle) -> None:
        now = self._clock()
        if now - handle.registered_at > self._sla_for(handle):
            logger.warning("[task][poller] %s SLA 超时(%.0fs>%.0fs)→exec_error sla_timeout",
                           handle.loop_task_id, now - handle.registered_at, self._sla_for(handle))
            await self._cancel_handle(handle)
            await self._report(self._exec_error(handle, "sla_timeout"), handle)
            return
        try:
            data = await self._poll_terminal(handle)
        except Exception:  # noqa: BLE001 任意端口异常→累计;达上限 poll_exhausted
            handle.fails += 1
            if handle.fails >= _MAX_CONSEC_FAIL:
                await self._cancel_handle(handle)
                await self._report(self._exec_error(handle, "poll_exhausted"), handle)
            return
        if data is not None:
            handle.fails = 0
            _result = (data.data.get("result") if isinstance(data.data, dict) else None) or {}
            logger.info("[task][poller] %s 收终态 success=%s data=%s",
                        handle.loop_task_id, _result.get("success"),
                        str(_result.get("data"))[:80])
            await self._report(data, handle)

    async def _poll_terminal(self, handle) -> TaskCallbackData | None:
        if isinstance(handle, SingleBotHandle):
            run = await self._bot.get_run(handle.run_id)
            status = str(run.get("status") or "").upper()
            if status in _TERMINAL_SINGLE:
                return SingleBotRunTranslator.adapt(run, handle.loop_task_id)
            return None
        if isinstance(handle, BcsGroupHandle) and handle.run_id is not None:  # run 模
            run = await self._bcs.get_state_machine_run(handle.run_id)
            if str(run.get("status") or "").lower() in _TERMINAL_SM:
                return BcsStateMachineRunTranslator.adapt(run, handle.loop_task_id)
            return None
        if isinstance(handle, BcsGroupHandle):  # session 模
            group = await self._bcs.get_group(handle.group_id)
            sess = (group.get("session") or {})
            if str(sess.get("status") or "").lower() in _TERMINAL_SM:
                msgs = await self._bcs.get_session_messages(handle.session_id, since_msg_id=handle.since_cursor)
                return BcsSessionTranslator.adapt(group, msgs, handle.loop_task_id)
            return None
        return None

    async def _cancel_handle(self, handle) -> None:
        if not isinstance(handle, SingleBotHandle):
            return
        cancel = getattr(self._bot, "cancel_run", None)
        if cancel is None:
            return
        try:
            await cancel(handle.run_id)
        except Exception as exc:  # noqa: BLE001 取消是 best-effort,不能吞掉主回投
            logger.warning("[task][poller] %s cancel_run failed: %s", handle.loop_task_id, exc)

    def _exec_error(self, handle, reason: str) -> TaskCallbackData:
        return TaskCallbackData(data={
            "loop_task_id": handle.loop_task_id,
            "workflow_type": "single_bot" if isinstance(handle, SingleBotHandle) else "bcn_coop_group",
            "workflow_id": 0,
            "instance_id": 0,
            "result": {"success": False, "exec_error": reason},
        })

    async def _poll_once(self) -> list[TaskCallbackData]:
        await self._poll_all_once()
        return []  # 测试经 sink.reports / pending() 断言

    async def _poll_all_once(self) -> None:
        with self._lock:
            snapshot = list(self._handles)
        for h in snapshot:
            await self._poll_one(h)

    def run_poll_loop(self, stop_event: threading.Event | None = None) -> None:
        if stop_event is None:
            stop_event = self._stop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while not stop_event.is_set():
            loop.run_until_complete(self._poll_all_once())
            self._sleep(self._interval)
