from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    NodeAction,
    NodeActionEvent,
    Status,
    TaskReportCommand,
    TaskReportEventType,
    TaskReportResult,
    TaskReportStatus,
)
from agentclaw.community.core.task.task_center.task_service_relay import (
    TaskServiceRelayMixin,
)


class _Graph:
    def __init__(self) -> None:
        self.events = []

    def append_action_event(self, task_id, node_id, action, payload, **kwargs):
        self.events.append((task_id, node_id, action, payload, kwargs))


class _Search:
    async def search_catalog(self, query):
        return {
            "candidates": [
                {
                    "bot_uuid": "bot-1",
                    "bot_name": "Risk Bot",
                    "bot_desc": "risk analysis",
                    "recommend": {"score": "0.91"},
                }
            ],
            "raw_item_count": 3,
            "failed_keywords": ["legacy"],
        }


class _Service(TaskServiceRelayMixin):
    def __init__(self) -> None:
        self._graph = _Graph()
        self._relay_adapter = SimpleNamespace(search=_Search())


@pytest.mark.asyncio
async def test_search_bots_records_relay_search_trace() -> None:
    service = _Service()

    candidates = await service.search_bots(
        query="payment risk",
        task_id="task-1",
        node_id="node-1",
    )

    assert [candidate.bot_id for candidate in candidates] == ["bot-1"]
    task_id, node_id, action, payload, _ = service._graph.events[-1]
    assert (task_id, node_id, action) == ("task-1", "node-1", NodeAction.DISPATCH)
    assert payload["phase"] == "search"
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["score"] == 0.91


# ===== report():上报即落轨迹(写时发射,读侧不再依赖投影重建) =====


class _ReportGraph:
    """Serve ``apply_relay_report`` with a scripted outcome triple."""

    def __init__(self, outcome) -> None:
        self._outcome = outcome
        self.commands = []

    def apply_relay_report(self, command):
        self.commands.append(command)
        return self._outcome


class _RecordingContextService:
    """Record every ``emit_trajectory_event`` call as a tuple."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def emit_trajectory_event(self, task_id, node_id, action_type, **kwargs):
        self.calls.append((task_id, node_id, action_type, kwargs))


class _ReportService(TaskServiceRelayMixin):
    def __init__(self, outcome, context_service) -> None:
        self._graph = _ReportGraph(outcome)
        self._task_context_service = context_service
        self._relay_adapter = SimpleNamespace(search=_Search())


def _report_command(event_id: str = "ev-1") -> TaskReportCommand:
    return TaskReportCommand(
        task_id="task-1",
        node_id="node-1",
        event_id=event_id,
        relay_token="tok",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=None,
    )


def _action_event(action: NodeAction, ts: int, payload: dict) -> NodeActionEvent:
    return NodeActionEvent(
        seq=1,
        ts=ts,
        action=action,
        status_from=Status.RUNNING,
        status_to=Status.RUNNING,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_report_emits_trajectory_rows_for_action_events() -> None:
    """上报即发射:apply_relay_report 返回的每个 action event 逐条转
    TrajectoryEvent kwargs 落表(gmt_create=动作 ts,action_result/error 与读时
    投影同源;ext_info 脱辅助键 + 带 holder_id/relay_event_id)。"""
    action_events = [
        _action_event(
            NodeAction.EXECUTE,
            1790000000123,
            {
                "__node_id": "node-1",
                "__holder_id": "bot-9",
                "success": True,
                "execution_decision": "ACCEPTED",
                "output": {"result": "done"},
            },
        ),
        _action_event(
            NodeAction.VERIFY,
            1790000000123,
            {
                "__node_id": "node-1",
                "__holder_id": "bot-9",
                "verdict": "DONE",
                "done_items": [],
                "gap_items": [],
            },
        ),
    ]
    outcome = (TaskReportResult(TaskReportStatus.APPLIED), action_events, True)
    ctx = _RecordingContextService()
    service = _ReportService(outcome, ctx)

    result = await service.report(_report_command())

    assert result.status == TaskReportStatus.APPLIED
    assert [(c[0], c[1], c[2]) for c in ctx.calls] == [
        ("task-1", "node-1", "execute"),
        ("task-1", "node-1", "verify"),
    ]
    execute_kwargs = ctx.calls[0][3]
    assert execute_kwargs["action_result"] == "success"
    assert execute_kwargs["now_ms"] == 1790000000123, "发射行 gmt_create = 动作 ts"
    assert execute_kwargs["ext_info"]["holder_id"] == "bot-9"
    assert execute_kwargs["ext_info"]["relay_event_id"] == "ev-1"
    assert "__node_id" not in execute_kwargs["ext_info"]
    assert ctx.calls[1][3]["action_result"] == "accept_pass"


@pytest.mark.asyncio
async def test_report_skips_trajectory_emission_on_replay() -> None:
    """幂等重放:apply 判定为 replay(action_events=None)→ 不发射,表里不添行。"""
    outcome = (TaskReportResult(TaskReportStatus.APPLIED), None, False)
    ctx = _RecordingContextService()
    service = _ReportService(outcome, ctx)

    result = await service.report(_report_command())

    assert result.status == TaskReportStatus.APPLIED
    assert ctx.calls == [], "重放不重复发射"


@pytest.mark.asyncio
async def test_report_without_context_service_is_noop() -> None:
    """``_task_context_service`` 未接线(轻量 DI)→ 上报正常,发射静默跳过。"""
    action_events = [
        _action_event(
            NodeAction.EXECUTE, 1790000000123,
            {"__node_id": "node-1", "success": True},
        )
    ]
    outcome = (TaskReportResult(TaskReportStatus.APPLIED), action_events, True)
    service = _ReportService(outcome, None)

    result = await service.report(_report_command("ev-none"))

    assert result.status == TaskReportStatus.APPLIED


@pytest.mark.asyncio
async def test_report_graph_failure_propagates_without_emission() -> None:
    """图应用失败(校验/token)→ 异常外抛,不发射任何轨迹行。"""

    class _FailingGraph:
        def apply_relay_report(self, command):
            raise TaskStateError("relay execution attempt has expired")

    class _Service(TaskServiceRelayMixin):
        def __init__(self) -> None:
            self._graph = _FailingGraph()
            self._task_context_service = _RecordingContextService()
            self._relay_adapter = SimpleNamespace(search=_Search())

    service = _Service()
    with pytest.raises(TaskStateError):
        await service.report(_report_command("ev-stale"))
    assert service._task_context_service.calls == []


@pytest.mark.asyncio
async def test_report_emits_trajectory_rows_for_trace_steps() -> None:
    """trace.steps 逐条落表:上报即转——每个 step 一次 ``emit_trajectory_event``,
    七字段(action_type/action_input/action_result/status_from/status_to/
    boost_reason/error_msg)对齐轨迹事件表;``now_ms`` 以高层动作 ts 为基准按
    step 序 +1 偏移(高层行 ts 被投影去重钉死,step 行排其后保时序);
    ext_info 带 ``trace_step`` 标识 + ``relay_event_id`` 可追溯;非法
    action_type(不在枚举内)跳过该条(读侧枚举强校验会丢行,写侧先拦)。
    """
    from agentclaw.community.core.task.domain.models import (
        TaskReportTrace,
        TaskReportTraceStep,
    )

    action_events = [
        _action_event(
            NodeAction.EXECUTE, 1790000000000,
            {"__node_id": "node-1", "success": True},
        ),
    ]
    outcome = (TaskReportResult(TaskReportStatus.APPLIED), action_events, True)
    ctx = _RecordingContextService()
    service = _ReportService(outcome, ctx)
    command = replace(
        _report_command("ev-trace"),
        trace=TaskReportTrace(
            steps=[
                TaskReportTraceStep(
                    action_type="execute",
                    action_result="tool_error_reported",
                    action_input="读取上下文并执行检索",
                    status_from="RUNNING",
                    status_to="RUNNING",
                    boost_reason="S1 上下文读取完成",
                    error_msg=None,
                ),
                TaskReportTraceStep(
                    action_type="verify",
                    action_result="accept_pass",
                ),
                TaskReportTraceStep(  # 非法 action_type → 写侧跳过(防读侧丢行)
                    action_type="S5C_SEARCH",
                    action_result="completed",
                ),
            ]
        ),
    )

    result = await service.report(command)

    assert result.status == TaskReportStatus.APPLIED
    # 1 条高层行 + 2 条 step 行(非法的跳过)
    assert [(c[1], c[2]) for c in ctx.calls] == [
        ("node-1", "execute"),
        ("node-1", "execute"),
        ("node-1", "verify"),
    ]
    step_1 = ctx.calls[1][3]
    assert step_1["action_result"] == "tool_error_reported"
    assert step_1["action_input"] == "读取上下文并执行检索"
    assert step_1["status_from"] == "RUNNING" and step_1["status_to"] == "RUNNING"
    assert step_1["boost_reason"] == "S1 上下文读取完成"
    assert step_1["error_msg"] is None
    assert step_1["now_ms"] == 1790000000001, "step 行 ts = 高层 ts + 序号偏移"
    assert step_1["ext_info"] == {
        "trace_step": True,
        "relay_event_id": "ev-trace",
    }
    step_2 = ctx.calls[2][3]
    assert step_2["action_result"] == "accept_pass"
    assert step_2["now_ms"] == 1790000000002
    assert step_2["status_from"] is None and step_2["status_to"] is None


@pytest.mark.asyncio
async def test_report_trace_steps_skipped_on_replay_or_without_context() -> None:
    """重放(action_events=None)不发射 step 行;context service 未接线同样静默。"""
    from agentclaw.community.core.task.domain.models import (
        TaskReportTrace,
        TaskReportTraceStep,
    )

    outcome = (TaskReportResult(TaskReportStatus.APPLIED), None, False)
    ctx = _RecordingContextService()
    service = _ReportService(outcome, ctx)
    command = replace(
        _report_command("ev-replay"),
        trace=TaskReportTrace(
            steps=[TaskReportTraceStep(action_type="plan", action_result="success")],
        ),
    )

    await service.report(command)
    assert ctx.calls == [], "重放不重复发射 step 行"
