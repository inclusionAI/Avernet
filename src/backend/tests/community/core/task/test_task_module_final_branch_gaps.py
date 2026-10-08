from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.models import (
    NodeOpResult,
    PlanResult,
    Relation,
    RelationType,
    Status,
    TaskNodePatch,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_context.task_graph_support import (
    _relay_output_value,
)
from agentclaw.community.core.task.task_harness.harness import TaskHarness
from agentclaw.community.core.task.task_plan.planner import TaskPlanner
from agentclaw.community.core.task.task_plan.static_plan import (
    StaticPlanDefinition,
    StaticPlanNodeDefinition,
    StaticPlanRuntime,
)
from tests.community.core.task.test_harness_runner_cov_gaps import (
    _child,
    _dispatch_running,
    _graph_with,
    _make_engine,
    _patch,
)


def _run(coro):
    return asyncio.run(coro)


def _static_definition(*nodes: StaticPlanNodeDefinition) -> StaticPlanDefinition:
    return StaticPlanDefinition(
        template_id="branch-plan",
        entry_bot_id="entry",
        input_schema={},
        nodes=tuple(nodes),
    )


def _static_node(
    node_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    output: dict[str, str] | None = None,
    node_type: str = "bot",
    bot_ids: tuple[str, ...] = (),
    bot_names: tuple[str, ...] = (),
) -> StaticPlanNodeDefinition:
    return StaticPlanNodeDefinition(
        node_id=node_id,
        name=node_id,
        node_type=node_type,
        depends_on=depends_on,
        bot_id="bot" if node_type == "bot" else None,
        bot_ids=bot_ids,
        bot_names=bot_names,
        input={},
        output=output or {},
    )


def test_graph_support_returns_multi_key_relay_output_copy():
    output = {"first": 1, "second": 2}
    result = _relay_output_value(output)
    assert result == output
    assert result is not output


def test_relay_recovery_without_trajectory_callback_still_succeeds():
    service = TaskGraphService()
    graph = _graph_with(service, {"orchestration_mode": "relay"})
    _dispatch_running(service, graph, "c1")
    harness = TaskHarness(service)

    node = service._get_node(graph, "c1")
    assert harness._recover_relay_without_execution_result("t1", node) is True
    assert service._get_node(graph, "c1").status is Status.PENDING
    assert graph.extend_props["bbs_mode"] is True


def test_on_miss_without_miss_events_logs_empty_reason(monkeypatch):
    service = TaskGraphService()
    _graph_with(service)
    engine = _make_engine(service)
    captured = []

    class StopAfterLog(RuntimeError):
        pass

    def stop_after_log(*args, **kwargs):
        captured.append((args, kwargs))
        raise StopAfterLog

    monkeypatch.setattr(engine, "_log_action", stop_after_log)
    with pytest.raises(StopAfterLog):
        _run(engine.on_miss(_patch("t1", "t1", extend_props_patch={})))

    assert captured[0][0][3]["miss_reason"] == ""


def test_hung_propagation_unknown_root_candidate_is_noop(monkeypatch):
    service = TaskGraphService()
    _graph_with(service)
    engine = _make_engine(service)
    monkeypatch.setattr(service, "get_parent_task", lambda *_args: None)

    engine._maybe_propagate_hung("t1", "unknown", "branch-test")
    assert service.query_task_dashboard("t1").status is Status.RUNNING


def test_planner_parent_lookup_skips_nonmatching_relation():
    graph = SimpleNamespace(
        relations=[
            Relation(src_id="x", dst_id="other", type=RelationType.DEPENDENCY),
            Relation(src_id="parent", dst_id="child", type=RelationType.DEPENDENCY),
        ]
    )
    assert TaskPlanner(object())._get_parent_id(graph, "child") == "parent"


def test_plan_with_retry_without_resolvable_root_skips_action_log(monkeypatch):
    service = TaskGraphService()
    graph = _graph_with(service)
    engine = _make_engine(service)

    class Planner:
        async def plan(self, graph, target_node_id=None):
            return PlanResult(children=[], has_gap=False, gap_detail="done")

    engine._planner = Planner()
    monkeypatch.setattr(engine, "_root", lambda _task_id: None)
    logged = []
    monkeypatch.setattr(engine, "_log_action", lambda *args, **kwargs: logged.append(args))

    result = _run(engine._plan_with_retry("t1", graph))
    assert result.gap_detail == "done"
    assert logged == []


def test_propagate_terminal_without_hung_sibling_is_noop(monkeypatch):
    service = TaskGraphService()
    _graph_with(service)
    engine = _make_engine(service)
    propagated = []
    monkeypatch.setattr(
        engine,
        "_maybe_propagate_hung",
        lambda *args: propagated.append(args),
    )

    engine._propagate_terminal(
        "t1",
        _child("parent"),
        [SimpleNamespace(node_id="done", status=Status.SUCCESS)],
        [],
    )
    assert propagated == []


def test_static_next_wave_without_dependency_edges_adds_node():
    service = TaskGraphService()
    _graph_with(service)
    engine = _make_engine(service)
    definition = _static_definition(_static_node("wave-zero"))
    runtime = StaticPlanRuntime(definition, {})

    assert engine._static_next_wave("t1", runtime) == 1
    graph = service.query_task_dashboard("t1")
    assert any(node.node_id == "wave-zero" for node in graph.tasks)
    assert graph.relations == []


def _isolate_static_report(monkeypatch, engine):
    monkeypatch.setattr(engine, "_static_next_wave", lambda *_args: 0)

    async def no_prepare(*_args):
        return None

    async def no_drain(*_args):
        return None

    monkeypatch.setattr(engine, "_prepare_static", no_prepare)
    monkeypatch.setattr(engine, "_drain", no_drain)


def test_static_report_skips_mapping_when_reported_node_is_absent(monkeypatch):
    service = TaskGraphService()
    _graph_with(service)
    engine = _make_engine(service)
    definition = _static_definition(_static_node("known"))
    runtime = StaticPlanRuntime(definition, {})
    monkeypatch.setattr(engine, "_static_runtime", lambda _task_id: runtime)
    _isolate_static_report(monkeypatch, engine)

    _run(engine._on_static_report("t1", "missing"))
    assert service.query_task_dashboard("t1").status is Status.RUNNING


def test_static_report_ignores_unrecognized_output_expression(monkeypatch):
    service = TaskGraphService()
    graph = _graph_with(service)
    service.add_task_nodes([_child("mapped")], parent_node_id="t1")
    mapped = service._get_node(graph, "mapped")
    mapped.run_info.output = {"result": {"answer": 42}}
    engine = _make_engine(service)
    definition = _static_definition(
        _static_node(
            "mapped",
            output={"ignored": "literal", "accepted": "$.result"},
        ),
        _static_node("not-materialized"),
    )
    runtime = StaticPlanRuntime(definition, {})
    monkeypatch.setattr(engine, "_static_runtime", lambda _task_id: runtime)
    _isolate_static_report(monkeypatch, engine)

    _run(engine._on_static_report("t1", "mapped"))
    output = service._get_node(graph, "mapped").run_info.output
    assert output["accepted"] == {"answer": 42}
    assert "ignored" not in output


def test_static_terminal_report_without_root_node_only_syncs(monkeypatch):
    service = TaskGraphService()
    _graph_with(service)
    engine = _make_engine(service)
    node = _child("only")
    node.status = Status.SUCCESS
    definition = _static_definition(_static_node("only"))
    runtime = StaticPlanRuntime(definition, {})
    dashboard = SimpleNamespace(tasks=[node])
    monkeypatch.setattr(engine, "_static_runtime", lambda _task_id: runtime)
    monkeypatch.setattr(engine._graph, "query_task_dashboard", lambda _task_id: dashboard)
    _isolate_static_report(monkeypatch, engine)
    synced = []
    monkeypatch.setattr(engine, "_sync_graph_status_to_root", lambda task_id: synced.append(task_id))

    _run(engine._on_static_report("t1", "only"))
    assert synced == ["t1"]


def test_static_upstream_identity_without_trigger_uses_entry():
    definition = StaticPlanDefinition(
        template_id="merchant-operations-goal-to-plan",
        entry_bot_id="entry-bot",
        entry_name="入口Bot",
        input_schema={},
        nodes=(_static_node("first"),),
    )
    runtime = StaticPlanRuntime(definition, {})
    graph = SimpleNamespace(extend_props={})

    assert runtime._upstream_identity(runtime.by_id["first"], graph) == "入口Bot(entry-bot)"


def test_collaboration_roster_without_ids_keeps_header_only():
    node = _static_node(
        "collab",
        node_type="collaboration",
        bot_names=("Driver",),
        bot_ids=(),
    )
    runtime = StaticPlanRuntime(_static_definition(node), {})
    prompt = runtime._relay_instruction(node, {}, SimpleNamespace(extend_props={}))

    assert "## 群组成" in prompt
    assert "driver / 总结者" not in prompt


def test_static_fold_only_report_skips_execute_and_verify(monkeypatch):
    service = TaskGraphService()
    _graph_with(service)
    service.add_task_nodes([_child("c1")], parent_node_id="t1")
    engine = _make_engine(service)
    monkeypatch.setattr(engine, "_static_runtime", lambda _task_id: object())
    reconciled = []
    monkeypatch.setattr(
        engine,
        "_reconcile_root_hung_if_blocked",
        lambda task_id: reconciled.append(task_id),
    )

    result = _run(engine.on_report(TaskNodePatch(task_id="t1", node_id="c1")))
    assert result.success is True
    assert reconciled == ["t1"]


def test_bbs_report_running_snapshot_skips_pass_and_fail(monkeypatch):
    service = TaskGraphService()
    graph = _graph_with(service)
    service.add_task_nodes([_child("c1")], parent_node_id="t1")
    service.update_task_node_info(
        _patch("t1", "t1", extend_props_patch={"bbs_owner": "bbs-bot"})
    )
    service._get_node(graph, "c1").status = Status.RUNNING
    engine = _make_engine(service)
    monkeypatch.setattr(
        engine,
        "_report_node_patch",
        lambda patch: NodeOpResult(
            task_id=patch.task_id,
            node_id=patch.node_id,
            success=True,
        ),
    )
    passed = []
    failed = []

    async def on_pass(*args):
        passed.append(args)

    async def on_fail(*args):
        failed.append(args)

    monkeypatch.setattr(engine, "_on_pass_collect", on_pass)
    monkeypatch.setattr(engine, "_on_fail_collect", on_fail)

    result = _run(
        engine.on_bbs_report(
            TaskNodePatch(task_id="t1", node_id="c1", assignee="bbs-bot")
        )
    )
    assert result.success is True
    assert passed == []
    assert failed == []


def test_drain_ignores_unknown_side_effect_kind():
    service = TaskGraphService()
    _graph_with(service)
    engine = _make_engine(service)

    _run(engine._drain("t1", [("unknown",)]))
    assert service.query_task_dashboard("t1").status is Status.RUNNING


def test_static_auto_report_without_definition_uses_base_mock(monkeypatch):
    service = TaskGraphService()
    graph = _graph_with(service, {"static_plan_id": "plan", "static_auto_report_delay": 0})
    _dispatch_running(service, graph, "c1")
    engine = _make_engine(service)
    runtime = SimpleNamespace(by_id={})
    monkeypatch.setattr(engine, "_static_runtime", lambda _task_id: runtime)
    reports = []

    async def report(patch):
        reports.append(patch)

    monkeypatch.setattr(engine, "on_report", report)
    _run(engine._static_auto_report("t1", "c1"))

    result = reports[0].output_patch["result"]
    assert "summary" in result and "random" in result
    assert "approved" not in result
    assert "unhandled_tasks" not in result
