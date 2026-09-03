import asyncio
import time

from agentclaw.community.core.task.task_runner.client.double.double_bcs_client import _DoubleBcsClient
from agentclaw.community.core.task.task_runner.client.double.double_context_provider import _DoubleSink
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
    BcsGroupHandle, TaskExecutorResultPoller,
)


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_poller_session_mode_reports_completed():
    bcs = _DoubleBcsClient(
        session_status="completed",
        session_output={"success": True, "data": {"r": 1}, "gaps": []},
    )
    sink = _DoubleSink()
    p = TaskExecutorResultPoller(bot=None, bcs=bcs, clock=time.monotonic, sleep=lambda s: None,
                                 interval=0.0, default_sla=1000.0)
    p.set_on_result(sink)
    p.register(BcsGroupHandle(loop_task_id="t::g", group_id="g1", collab_mode="chat",
                              registered_at=time.monotonic(), session_id="s1", run_id=None))
    _run(p._poll_once())
    assert sink.reports[0].data["result"]["success"] is True


def test_poller_run_mode_reports_completed():
    bcs = _DoubleBcsClient(
        sm_status="completed",
        sm_output={"success": True, "data": {"x": 1}, "gaps": []},
    )
    sink = _DoubleSink()
    p = TaskExecutorResultPoller(bot=None, bcs=bcs, clock=time.monotonic, sleep=lambda s: None,
                                 interval=0.0, default_sla=1000.0)
    p.set_on_result(sink)
    p.register(BcsGroupHandle(loop_task_id="t::g", group_id="g1", collab_mode="state_machine",
                              registered_at=time.monotonic(), session_id=None, run_id="run_9"))
    _run(p._poll_once())
    assert sink.reports[0].data["result"]["success"] is True
