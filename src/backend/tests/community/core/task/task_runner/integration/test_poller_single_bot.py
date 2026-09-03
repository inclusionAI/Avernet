import asyncio
import time

from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
    SingleBotHandle, TaskExecutorResultPoller,
)


class _Bot:
    def __init__(self, runs):
        self._runs = runs
        self.calls = 0
        self.cancelled = []

    async def get_run(self, run_id):
        self.calls += 1
        return self._runs[run_id]

    async def cancel_run(self, run_id):
        self.cancelled.append(run_id)


class _Sink:
    def __init__(self):
        self.reports = []

    async def report_result(self, data):
        self.reports.append(data)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _poller(bot, sink, *, clock=None, sla=1000.0):
    return TaskExecutorResultPoller(bot=bot, bcs=None,
                                    clock=clock or time.monotonic, sleep=lambda s: None,
                                    interval=0.0, default_sla=sla)


def test_single_bot_terminal_reports_and_unregisters():
    bot = _Bot({"r1": {"status": "COMPLETED", "result": {"content": '{"success":true,"data":"done","gaps":[]}'}}})
    sink = _Sink()
    p = _poller(bot, sink)
    p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=time.monotonic()))
    _run(p._poll_once())
    assert sink.reports[0].data["result"]["success"] is True
    assert p.pending() == 0


def test_single_bot_not_terminal_no_report():
    bot = _Bot({"r1": {"status": "RUNNING"}})
    sink = _Sink()
    p = _poller(bot, sink)
    p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=time.monotonic()))
    _run(p._poll_once())
    assert sink.reports == []


def test_sla_timeout_reports_fail_and_unregisters():
    bot = _Bot({"r1": {"status": "RUNNING"}})
    sink = _Sink()
    t = [0.0]
    p = _poller(bot, sink, clock=lambda: t[0], sla=1.0)
    p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=0.0))
    t[0] = 100.0  # 远超 sla
    _run(p._poll_once())
    assert sink.reports[0].data["result"]["success"] is False
    assert sink.reports[0].data["result"]["exec_error"] == "sla_timeout"
    assert bot.cancelled == ["r1"]
    assert p.pending() == 0


def test_consecutive_failures_report_poll_exhausted():
    class _ErrBot:
        async def get_run(self, run_id):
            raise RuntimeError("boom")

    sink = _Sink()
    p = _poller(_ErrBot(), sink)
    p.set_on_result(sink)
    p.register(SingleBotHandle(loop_task_id="t1::c1", run_id="r1", bot_id="b1", registered_at=time.monotonic()))
    for _ in range(5):
        _run(p._poll_once())
    assert any(r.data["result"].get("exec_error") == "poll_exhausted" for r in sink.reports)
    assert p.pending() == 0
