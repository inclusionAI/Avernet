import asyncio

from agentclaw.community.core.task.domain.models import TaskCallbackData
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import BcsCreateGroupRequest
from agentclaw.community.core.task.task_runner.client.double.double_bcs_client import _DoubleBcsClient
from agentclaw.community.core.task.task_runner.client.double.double_context_provider import (
    _DoubleApiKeyProvider, _DoubleSink,
)
from agentclaw.community.core.task.task_runner.client.double.double_open_api_bot import _DoubleOpenApiBot


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_double_open_api_bot_grant_send_poll():
    bot = _DoubleOpenApiBot(final_status="COMPLETED", content="done")
    _run(bot.ensure_grant("b:1"))
    sent = _run(bot.send_message(bot_id="b:1", message="hi", metadata={}))
    run = _run(bot.get_run(sent.run_id))
    assert run["status"] == "COMPLETED"


def test_double_bcs_client_session_flow():
    c = _DoubleBcsClient(session_status="completed", session_output={"r": 1})
    res = _run(c.create_group(BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}])))
    _run(c.create_session(res.group_id, bootstrap_prompt="hi"))
    g = _run(c.get_group(res.group_id))
    assert g["session"]["status"] == "completed"


def test_double_bcs_client_state_machine_flow():
    c = _DoubleBcsClient(sm_status="completed", sm_output={"x": 1})
    rid = _run(c.start_state_machine_run("g1", definition_yaml=None,
                                         definition_ref={"id": "d1", "version": 1},
                                         session_id=None, input={"query": "q"}))
    run = _run(c.get_state_machine_run(rid))
    assert run["status"] == "completed"


def test_double_api_key_provider():
    k = _DoubleApiKeyProvider()
    assert k.api_key and k.api_key_prefix and k.base_url


def test_double_sink_collects():
    s = _DoubleSink()
    _run(s.report_result(TaskCallbackData(data={
        "loop_task_id": "t::n", "workflow_type": "single_bot",
        "workflow_id": 0, "instance_id": 0, "result": {"success": True},
    })))
    assert len(s.reports) == 1
