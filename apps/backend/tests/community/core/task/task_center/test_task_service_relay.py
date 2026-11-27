from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    BotCandidate,
    Context,
    Goal,
    NodeAction,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskReportCommand,
    TaskReportEventType,
    TaskReportResult,
    TaskReportStatus,
    TaskSpec,
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
    async def search_catalog(
        self,
        query,
        *,
        viewer_actor_type=None,
        viewer_actor_id=None,
        visibility=None,
    ):
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
        self._temporary_duty_candidates = ()


@pytest.mark.asyncio
async def test_search_bots_records_relay_search_trace() -> None:
    service = _Service()

    candidates = await service.search_bots(
        query="payment risk",
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
    )

    assert [candidate.bot_id for candidate in candidates] == ["bot-1"]
    task_id, node_id, action, payload, _ = service._graph.events[-1]
    assert (task_id, node_id, action) == ("task-1", "node-1", NodeAction.DISPATCH)
    assert payload["phase"] == "search"
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["score"] == 0.91


@pytest.mark.asyncio
async def test_search_bots_uses_configured_temporary_duty_fallback_without_overlap(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVER_ENV", "prod")
    service = _Service()
    service._temporary_duty_candidates = (
        BotCandidate(bot_id="bot-2", name="Fallback", description="duty", score=None),
        BotCandidate(bot_id="bot-3", name="Other", description="", score=None),
    )

    candidates = await service.search_bots(
        query="payment risk",
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
    )

    assert [candidate.bot_id for candidate in candidates] == ["bot-1", "bot-2", "bot-3"]
    _, _, _, payload, _ = service._graph.events[-1]
    assert payload["candidate_source"] == "search_plus_fallback"
    assert payload["fallback_candidate_count"] == 2
    assert payload["candidates"][-1]["score"] is None


@pytest.mark.asyncio
async def test_search_bots_does_not_add_fallback_candidates_when_search_overlaps(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVER_ENV", "prod")
    service = _Service()
    service._temporary_duty_candidates = (
        BotCandidate(bot_id="bot-1", name="Risk Bot", description="duty", score=None),
    )

    candidates = await service.search_bots(
        query="payment risk",
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
    )

    assert [candidate.bot_id for candidate in candidates] == ["bot-1"]
    _, _, _, payload, _ = service._graph.events[-1]
    assert payload["candidate_source"] == "search"
    assert payload["fallback_candidate_count"] == 0


@pytest.mark.asyncio
async def test_search_bots_uses_pre_temporary_duty_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVER_ENV", "pre")
    service = _Service()
    service._temporary_pre_duty_candidates = (
        BotCandidate(bot_id="bot-2", name="Pre Fallback", description="duty"),
    )

    candidates = await service.search_bots(
        query="payment risk",
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
    )

    assert [candidate.bot_id for candidate in candidates] == ["bot-1", "bot-2"]
    _, _, _, payload, _ = service._graph.events[-1]
    assert payload["candidate_source"] == "search_plus_fallback"
    assert payload["fallback_candidate_count"] == 1


@pytest.mark.asyncio
async def test_search_bots_skips_temporary_duty_fallback_outside_prod_and_pre(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVER_ENV", "dev")
    service = _Service()
    service._temporary_duty_candidates = (
        BotCandidate(bot_id="bot-2", name="Fallback", description="duty"),
    )
    service._temporary_pre_duty_candidates = (
        BotCandidate(bot_id="bot-3", name="Pre Fallback", description="duty"),
    )

    candidates = await service.search_bots(
        query="payment risk",
        task_id="task-1",
        node_id="node-1",
        relay_token="generation-1",
    )

    assert [candidate.bot_id for candidate in candidates] == ["bot-1"]
    _, _, _, payload, _ = service._graph.events[-1]
    assert payload["candidate_source"] == "search"
    assert payload["fallback_candidate_count"] == 0


def test_build_relay_root_instruction_uses_task_graph_source_channel() -> None:
    root = TaskNode(
        node_id="task-1",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(title="分析任务"),
            goal=Goal(
                objective="完成任务",
                acceptances=[AcceptanceCriteria(id="a1", description="输出结论")],
            ),
        ),
        run_info=RuntimeInfo(),
        node_run_graph=None,
    )
    root.run_info.extend_props["relay_token"] = "generation-1"
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        tasks=[root],
        task_id="task-1",
        extend_props={
            "execution_config": {
                "orchestration_mode": "relay",
                "source_channel": "dingtalk",
            },
            "runtime_profile": {"allowed_run_modes": ["single_bot", "coop_group"]},
        },
    )
    service = _Service()
    service._api_base_url = "https://backend.example"
    service._graph = SimpleNamespace(query_task_dashboard=lambda _: graph)

    instruction = service._build_relay_root_execution_instruction("task-1")

    assert "source_channel=dingtalk；" in instruction
    assert "relay_token=generation-1" in instruction


# ===== report():上报即落轨迹(写时发射,读侧不再依赖投影重建) =====


class _ReportGraph:
    """Serve ``apply_relay_report``/``query_task_dashboard`` with scripted facts."""

    def __init__(self, *, known_event_ids=None, apply_exc=None) -> None:
        self.known_event_ids = set(known_event_ids or [])
        self.apply_exc = apply_exc
        self.apply_commands = []

    def query_task_dashboard(self, task_id, node_id=None):
        return SimpleNamespace(
            extend_props={
                "_relay_report_event_ids": {k: {} for k in self.known_event_ids},
            },
        )

    def apply_relay_report(self, command):
        if self.apply_exc is not None:
            raise self.apply_exc
        self.apply_commands.append(command)
        return TaskReportResult(TaskReportStatus.APPLIED)


class _RecordingContextService:
    """Record every ``emit_trajectory_event`` call as a tuple."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def emit_trajectory_event(self, task_id, node_id, action_type, **kwargs):
        self.calls.append((task_id, node_id, action_type, kwargs))


class _ReportService(TaskServiceRelayMixin):
    def __init__(self, graph, context_service) -> None:
        self._graph = graph
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


def _trace_steps():
    from agentclaw.community.core.task.domain.models import (
        TaskReportTrace,
        TaskReportTraceStep,
    )

    return TaskReportTrace(
        steps=[
            TaskReportTraceStep(
                action_type="S1_CONTEXT",
                action_result="{\"status\":\"completed\",\"context_recovered\":true}",
                action_input="{\"context_endpoint\":\"/context\"}",
                status_from="RUNNING",
                status_to="RUNNING",
                boost_reason="S1 上下文读取完成",
                error_msg=None,
            ),
            TaskReportTraceStep(
                action_type="S4B_REPORT",
                action_result="{\"status\":\"completed\",\"data_status\":\"APPLIED\"}",
            ),
            TaskReportTraceStep(  # S 码即轨迹主事件 action_type,原样透传落表
                action_type="S5C_SEARCH_CANDIDATES",
                action_result="{\"status\":\"completed\",\"raw_item_count\":3}",
            ),
        ]
    )


@pytest.mark.asyncio
async def test_report_emits_only_trace_step_rows() -> None:
    """上报即发射 trace.step 行(**高层动作不落轨迹表**——2026-10-15 验收语义);
    ``action_type`` 写侧先验枚举,非法跳过;``now_ms`` 按序 +1 保时序;
    ext_info 带 trace_step/relay_event_id。"""
    ctx = _RecordingContextService()
    service = _ReportService(_ReportGraph(), ctx)
    command = replace(
        _report_command("ev-trace"), trace=_trace_steps(),
    )

    result = await service.report(command)

    assert result.status == TaskReportStatus.APPLIED
    assert [(c[1], c[2]) for c in ctx.calls] == [
        ("node-1", "S1_CONTEXT"),
        ("node-1", "S4B_REPORT"),
        ("node-1", "S5C_SEARCH_CANDIDATES"),
    ], "只发 step 行(S 码 action_type 原样透传);高层动作(apply 行)不落表"
    step_1 = ctx.calls[0][3]
    assert step_1["action_result"] == "{\"status\":\"completed\",\"context_recovered\":true}"
    assert step_1["action_input"] == "{\"context_endpoint\":\"/context\"}"
    assert step_1["status_from"] == "RUNNING" and step_1["status_to"] == "RUNNING"
    assert step_1["boost_reason"] == "S1 上下文读取完成"
    assert step_1["error_msg"] is None
    assert step_1["attempt"] == 0
    assert step_1["ext_info"] == {"trace_step": True, "relay_event_id": "ev-trace"}
    assert step_1["now_ms"] + 1 == ctx.calls[1][3]["now_ms"], "step 行按序 +1"


@pytest.mark.asyncio
async def test_report_skips_step_emission_on_replay() -> None:
    """重放上报(图里已有该 event_id 的回执)→ 不重复发射 step 行。"""
    ctx = _RecordingContextService()
    graph = _ReportGraph(known_event_ids={"ev-replay"})
    service = _ReportService(graph, ctx)
    command = replace(_report_command("ev-replay"), trace=_trace_steps())

    result = await service.report(command)

    assert result.status == TaskReportStatus.APPLIED
    assert ctx.calls == [], "重放(event_id 已有回执)不重复发射"


@pytest.mark.asyncio
async def test_report_without_context_service_is_noop() -> None:
    """``_task_context_service`` 未接线(轻量 DI)→ 上报正常,发射静默跳过。"""
    service = _ReportService(_ReportGraph(), None)
    command = replace(_report_command("ev-none"), trace=_trace_steps())

    result = await service.report(command)

    assert result.status == TaskReportStatus.APPLIED


@pytest.mark.asyncio
async def test_report_graph_failure_propagates_without_emission() -> None:
    """图应用失败(校验/token)→ 异常外抛,不发射任何轨迹行。"""
    ctx = _RecordingContextService()
    graph = _ReportGraph(apply_exc=TaskStateError("relay execution attempt has expired"))
    service = _ReportService(graph, ctx)

    with pytest.raises(TaskStateError):
        await service.report(_report_command("ev-stale"))
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_report_precheck_failure_degrades_to_emit() -> None:
    """预查读图失败 → best-effort 按未见过处理,step 行照发(宁多勿漏)。"""
    class _BrokenGraph:
        def query_task_dashboard(self, task_id, node_id=None):
            raise RuntimeError("graph unavailable")

        def apply_relay_report(self, command):
            return TaskReportResult(TaskReportStatus.APPLIED)

    ctx = _RecordingContextService()
    service = _ReportService(_BrokenGraph(), ctx)
    command = replace(_report_command("ev-degrade"), trace=_trace_steps())

    result = await service.report(command)

    assert result.status == TaskReportStatus.APPLIED
    assert len(ctx.calls) == 3


@pytest.mark.asyncio
async def test_no_trace_report_skips_observation_precheck_and_writes() -> None:
    class Graph(_ReportGraph):
        def query_task_dashboard(self, task_id, node_id=None):
            pytest.fail("execution report without trace needs no observation read")

    graph = Graph()
    ctx = _RecordingContextService()
    service = _ReportService(graph, ctx)
    command = _report_command("no-trace")
    assert (await service.report(command)).status == TaskReportStatus.APPLIED
    assert graph.apply_commands == [command]
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_no_trace_successor_report_still_hydrates_search_receipts() -> None:
    class Graph(_ReportGraph):
        def __init__(self):
            super().__init__()
            self.loaded = False

        def load_action_logs(self, graph):
            self.loaded = True

        def apply_relay_report(self, command):
            assert self.loaded, "report worker must load persisted search receipts"
            return super().apply_relay_report(command)

    graph = Graph()
    ctx = _RecordingContextService()
    service = _ReportService(graph, ctx)
    command = replace(
        _report_command("no-trace-plan"),
        event_type=TaskReportEventType.PLAN_RESULT,
        event=SimpleNamespace(),
    )
    assert (await service.report(command)).status == TaskReportStatus.APPLIED
    assert ctx.calls == []
