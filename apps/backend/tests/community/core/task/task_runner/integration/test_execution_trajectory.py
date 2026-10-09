import asyncio
import logging

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_context.task_context_service import (
    TaskContextService,
)
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiAuthError,
)
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    PromptFormatterImpl,
)
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)


def _node(mode: str, assignee: str) -> TaskNode:
    return TaskNode(
        node_id="n1",
        task_id="t1",
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context("background", title="title"),
            goal=Goal("objective", [AcceptanceCriteria("a1", "done")]),
        ),
        run_info=RuntimeInfo(
            run_mode=mode,
            assignee=assignee,
            extend_props={"harness_retries": 2},
        ),
        node_run_graph=None,
    )  # type: ignore[arg-type]


class _TrajectorySink:
    def __init__(self, error: Exception | None = None):
        self.events = []
        self._error = error

    def emit_trajectory_event(self, task_id, node_id, action_type, **kwargs):
        if self._error is not None:
            raise self._error
        self.events.append((task_id, node_id, action_type, kwargs))


class _Context:
    def build(self, task_id, node_id):
        return {"mode": "execute"}


class _Bot:
    def __init__(self, error: Exception | None = None):
        self._error = error

    async def send_message(self, **kwargs):
        if self._error is not None:
            raise self._error
        return BotSendResult(run_id="run-1", session_id="session-1")


class _Bcs:
    def __init__(self, error: Exception | None = None):
        self._error = error

    async def get_group(self, group_id):
        if self._error is not None:
            raise self._error
        return {"latest_running_session_id": "session-1"}


class _Poller:
    def register(self, handle):
        raise AssertionError("skill-report defaults on, so poller must not register")


def _executor(*, sink, bot=None, bcs=None):
    return TaskExecutor(
        bot=bot,
        bcs=bcs,
        formatter=PromptFormatterImpl(),
        context=_Context(),
        sink=None,
        poller=_Poller(),
        task_context_service=TaskContextService(trajectory_service=sink),
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_single_bot_success_records_execution_started():
    sink = _TrajectorySink()
    result = _run(
        _executor(sink=sink, bot=_Bot()).dispatch([_node("single_bot", "bot-1")])
    )

    assert result == [True]
    task_id, node_id, action_type, event = sink.events[0]
    assert (task_id, node_id, action_type) == ("t1", "n1", "execute")
    assert event["action_result"] == "single_bot_started"
    assert event["error_type"] is None
    assert event["error_msg"] is None
    assert event["ext_info"] == {
        "execution_mode": "single_bot", "assignee": "bot-1", "phase": "dispatch",
    }
    assert event["attempt"] == 2


def test_single_bot_openapi_error_records_details_and_keeps_false_result():
    sink = _TrajectorySink()
    result = _run(
        _executor(sink=sink, bot=_Bot(OpenApiAuthError("403 denied"))).dispatch(
            [_node("single_bot", "bot-1")]
        )
    )

    assert result == [False]
    event = sink.events[0][3]
    assert event["action_result"] == "single_bot_start_failed"
    assert event["error_type"] == "underlying_interface_error"
    assert event["error_msg"] == "403 denied"
    assert event["ext_info"]["exception_type"] == "OpenApiAuthError"


def test_coop_group_success_records_execution_started():
    sink = _TrajectorySink()
    result = _run(
        _executor(sink=sink, bcs=_Bcs()).dispatch([_node("coop_group", "group-1")])
    )

    assert result == [True]
    event = sink.events[0][3]
    assert event["action_result"] == "coop_group_started"
    assert event["error_type"] is None
    assert event["ext_info"]["execution_mode"] == "coop_group"


def test_coop_group_error_records_details_and_preserves_exception():
    sink = _TrajectorySink()
    failure = ConnectionError("BCS unavailable")

    with pytest.raises(ConnectionError, match="BCS unavailable"):
        _run(
            _executor(sink=sink, bcs=_Bcs(failure)).dispatch(
                [_node("coop_group", "group-1")]
            )
        )

    event = sink.events[0][3]
    assert event["action_result"] == "coop_group_start_failed"
    assert event["error_type"] == "transport_error"
    assert event["error_msg"] == "BCS unavailable"
    assert event["ext_info"]["exception_type"] == "ConnectionError"


def test_trajectory_write_failure_does_not_break_dispatch(caplog):
    sink = _TrajectorySink(RuntimeError("trajectory unavailable"))

    with caplog.at_level(logging.WARNING):
        result = _run(
            _executor(sink=sink, bot=_Bot()).dispatch(
                [_node("single_bot", "bot-1")]
            )
        )

    assert result == [True]
    assert "trajectory unavailable" in caplog.text
