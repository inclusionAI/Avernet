from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskGraphPatch,
    TaskNodeQueryCriteria,
    TaskSourceType,
)
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance,
    RequestContext,
    RequestGoal,
    RequestMetadata,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_dispatch.claim_join_gate import RELAY_EXECUTION


class _Settings:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self, setting_type: str) -> bool:
        return self.enabled and setting_type == RELAY_EXECUTION


class _Discover:
    def search_by_keyword(self, **kwargs):
        return {
            "items": [
                {
                    "bot_id": "research-bot",
                    "bot_uuid": "research-bot:owner-2",
                    "bot_name": "Research Bot",
                    "recommend": {"score": 0.91},
                }
            ]
        }


class _DiscoverTwo:
    def search_by_keyword(self, **kwargs):
        return {
            "items": [
                {"bot_id": "manager-bot", "recommend": {"score": 0.95}},
                {"bot_id": "member-bot", "recommend": {"score": 0.90}},
            ]
        }


class _ToggleDelivery:
    def __init__(self) -> None:
        self.succeeds = False

    async def deliver(self, node) -> bool:
        return self.succeeds


def _request() -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="调研", instruction="完成首轮分析"),
            context=RequestContext(background="共享背景"),
            goal=RequestGoal(
                objective="完成市场研究和结论",
                acceptances=[RequestAcceptance(id="a1", acceptance="结论可执行")],
            ),
        ),
        source_type=TaskSourceType.BOT,
        owner_user_id="owner-1",
        owner_bot_id="main-bot",
        execution_config={"task_type": "dynamic", "MAX_LOOP": 3},
    )


def _child_spec() -> dict:
    return {
        "metadata": {
            "task_id": "research-step",
            "title": "补充研究",
            "instruction": "基于上一棒产出补充市场数据并形成建议",
        },
        "context": {"background": "共享黑板中的首棒产出", "extend_props": {}},
        "goal": {
            "objective": "补齐市场研究 gap",
            "acceptances": [{"id": "a2", "description": "给出建议"}],
        },
    }


def _run(coro):
    return asyncio.run(coro)


def _service(*, discover=None, relay_enabled: bool = True):
    graph = TaskGraphService()
    return TaskService(
        graph,
        discover=discover or _Discover(),
        task_settings=_Settings(relay_enabled),
        task_id_provider=lambda: "relay-task",
    ), graph


def _plan_and_select(
    service: TaskService,
    *,
    origin_node_id: str,
    holder_id: str,
    turn: str,
    child_node_id: str,
    event_suffix: str,
) -> None:
    spec = _child_spec()
    spec["metadata"]["task_id"] = child_node_id
    _run(service.report_task_event(
        task_id="relay-task", node_id=origin_node_id,
        event_type="PLAN_RESULT", event_id=f"plan-{event_suffix}",
        holder_id=holder_id, relay_turn=turn,
        progress_reason="当前全局 gap 需要下一棒补齐",
        payload={"has_gap": True, "children": [{"node_id": child_node_id, "task_spec": spec}]},
    ))
    _run(service.search_task_candidates(query="补齐市场研究 gap"))
    _run(service.report_task_event(
        task_id="relay-task", node_id=child_node_id,
        event_type="SEARCH_RESULT", event_id=f"search-{event_suffix}",
        holder_id=holder_id, relay_turn=turn,
        progress_reason="候选 Bot 能力与下一节点目标匹配",
        payload={
            "outcome": "HIT_SINGLE",
            "assignee": "research-bot",
        },
    ))


def test_relay_exec_plan_search_dispatch_and_complete() -> None:
    service, graph_service = _service()
    submitted = _run(service.execute(_request()))
    assert submitted.success
    graph = graph_service.query_task_dashboard("relay-task")
    root = graph.tasks[0]
    assert root.status == Status.RUNNING
    assert root.run_info.assignee == "main-bot"
    assert graph.extend_props["execution_config"]["orchestration_mode"] == "relay"

    execution = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task",
        event_type="EXECUTION_RESULT", event_id="exec-1", holder_id="main-bot",
        progress_reason="首棒形成分析，可以规划补充研究",
        payload={"success": True, "output": {"summary": "首轮结论"}},
    ))
    turn = execution["relay_turn"]

    _plan_and_select(
        service, origin_node_id="relay-task", holder_id="main-bot",
        turn=turn, child_node_id="research-step", event_suffix="1",
    )
    dispatched = _run(service.dispatch_task(
        task_id="relay-task", node_id="research-step", holder_id="main-bot",
        relay_turn=turn, dispatch_id="dispatch-1",
    ))
    assert dispatched["assignee"] == "research-bot"
    assert graph_service.query_task_nodes(
        "relay-task", TaskNodeQueryCriteria(node_ids=["research-step"]),
    )[0].status == Status.RUNNING

    second = _run(service.report_task_event(
        task_id="relay-task", node_id="research-step",
        event_type="EXECUTION_RESULT", event_id="exec-2", holder_id="research-bot",
        progress_reason="第二棒产出完成，继续计算全局 gap",
        payload={"success": True, "output": {"recommendation": "进入市场"}},
    ))
    _plan_and_select(
        service, origin_node_id="research-step", holder_id="research-bot",
        turn=second["relay_turn"], child_node_id="final-step", event_suffix="2",
    )
    _run(service.dispatch_task(
        task_id="relay-task", node_id="final-step", holder_id="research-bot",
        relay_turn=second["relay_turn"], dispatch_id="dispatch-2",
    ))
    third = _run(service.report_task_event(
        task_id="relay-task", node_id="final-step",
        event_type="EXECUTION_RESULT", event_id="exec-3", holder_id="research-bot",
        progress_reason="最终一棒产出完成，检查根验收标准",
        payload={"success": True, "output": {"final": "complete"}},
    ))
    _run(service.report_task_event(
        task_id="relay-task", node_id="final-step",
        event_type="PLAN_RESULT", event_id="plan-3", holder_id="research-bot",
        relay_turn=third["relay_turn"], progress_reason="根目标已全部满足",
        payload={"has_gap": False, "children": []},
    ))
    final = graph_service.query_task_dashboard("relay-task")
    assert all(node.status == Status.SUCCESS for node in final.tasks)
    assert final.status == Status.DONE


def test_relay_miss_publishes_bbs_and_claimant_continues_without_root_planning_reset() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    turn = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))["relay_turn"]
    _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="PLAN_RESULT",
        event_id="plan", holder_id="main-bot", relay_turn=turn,
        progress_reason="需要 BBS 承接下一棒",
        payload={"has_gap": True, "children": [{"node_id": "bbs-step", "task_spec": _child_spec()}]},
    ))
    _run(service.search_task_candidates(query="补齐市场研究 gap"))
    published = _run(service.report_task_event(
        task_id="relay-task", node_id="bbs-step", event_type="SEARCH_RESULT",
        event_id="miss", holder_id="main-bot", relay_turn=turn,
        progress_reason="无直接候选，发布 BBS 广场",
        failure_reason="候选能力均不匹配", payload={
            "outcome": "MISS",
            "miss_reason": "no capability match",
        },
    ))
    assert published["published_bbs"]
    service.claim_bbs_task("relay-task", "bbs-bot", "bbs-step")
    graph = graph_service.query_task_dashboard("relay-task")
    root = next(node for node in graph.tasks if node.node_id == "relay-task")
    bbs = next(node for node in graph.tasks if node.node_id == "bbs-step")
    assert root.status == Status.PLANNING
    assert bbs.status == Status.RUNNING
    assert bbs.run_info.assignee == "bbs-bot"
    with pytest.raises(TaskStateError):
        service.claim_bbs_task("relay-task", "other-bbs-bot", "bbs-step")

    continued = _run(service.report_task_event(
        task_id="relay-task", node_id="bbs-step", event_type="EXECUTION_RESULT",
        event_id="bbs-exec", holder_id="bbs-bot",
        progress_reason="BBS 执行产出完成，继续计算全局 gap",
        payload={"success": True, "output": {"bbs_result": "补齐证据"}},
    ))
    assert next(
        node for node in graph_service.query_task_dashboard("relay-task").tasks
        if node.node_id == "relay-task"
    ).status == Status.PLANNING
    _run(service.report_task_event(
        task_id="relay-task", node_id="bbs-step", event_type="PLAN_RESULT",
        event_id="bbs-plan", holder_id="bbs-bot", relay_turn=continued["relay_turn"],
        progress_reason="BBS 产出已满足根目标",
        payload={"has_gap": False, "children": []},
    ))
    assert graph_service.query_task_dashboard("relay-task").status == Status.DONE


def test_relay_search_is_independent_from_task_context() -> None:
    service, _ = _service()
    result = _run(service.search_task_candidates(query="补齐市场研究 gap"))
    assert result["candidates"][0]["bot_uuid"] == "research-bot:owner-2"
    assert "catalog_id" not in result


def test_root_holder_accepts_frontend_composite_bot_identity() -> None:
    service, _ = _service()
    request = replace(_request(), owner_bot_id="main-bot:owner-1")
    _run(service.execute(request))
    result = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="composite-exec", holder_id="main-bot:owner-1",
        progress_reason="主 Bot 首棒完成", payload={"output": "done"},
    ))
    assert result["relay_turn"]


def test_relay_rejects_missing_trajectory_reason() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    with pytest.raises(TaskStateError, match="progress_reason"):
        _run(service.report_task_event(
            task_id="relay-task", node_id="relay-task",
            event_type="EXECUTION_RESULT", event_id="no-reason",
            holder_id="main-bot", payload={"output": "done"},
        ))
    root = graph_service.query_task_dashboard("relay-task").tasks[0]
    assert root.run_info.output == {}


def test_retried_execution_report_does_not_invalidate_first_turn_token() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    first = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="same-exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))
    retried = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="same-exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))
    assert retried["idempotent"] is True
    assert retried["relay_turn"] != first["relay_turn"]

    result = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="PLAN_RESULT",
        event_id="finish", holder_id="main-bot", relay_turn=first["relay_turn"],
        progress_reason="根目标已满足",
        payload={"has_gap": False, "children": []},
    ))
    assert result["completed"] is True

    delayed = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="same-exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))
    assert delayed == {"ok": True, "idempotent": True, "turn_consumed": True}
    assert graph_service.query_task_dashboard("relay-task").extend_props[
        "relay_turn"
    ]["status"] == "CONSUMED"


def test_expired_turn_is_recovered_by_idempotent_execution_retry() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    first = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="recover-exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))
    graph = graph_service.query_task_dashboard("relay-task")
    expired = dict(graph.extend_props["relay_turn"])
    expired["expires_at_ms"] = 0
    graph_service.update_task_graph_info(
        "relay-task", TaskGraphPatch(extend_props_patch={"relay_turn": expired})
    )
    with pytest.raises(TaskStateError, match="invalid"):
        _run(service.report_task_event(
            task_id="relay-task", node_id="relay-task", event_type="PLAN_RESULT",
            event_id="stale-plan", holder_id="main-bot", relay_turn=first["relay_turn"],
            progress_reason="尝试使用已过期接力权",
            payload={"has_gap": False, "children": []},
        ))

    recovered = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="recover-exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))
    assert recovered["idempotent"] is True
    assert recovered["relay_turn"] != first["relay_turn"]


def test_only_group_manager_can_report_and_continue() -> None:
    service, _ = _service(discover=_DiscoverTwo())
    _run(service.execute(_request()))
    turn = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="root-exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))["relay_turn"]
    spec = _child_spec()
    _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="PLAN_RESULT",
        event_id="group-plan", holder_id="main-bot", relay_turn=turn,
        progress_reason="下一棒需要多 Bot 协作",
        payload={"has_gap": True, "children": [{"node_id": "group-step", "task_spec": spec}]},
    ))
    _run(service.search_task_candidates(query="补齐市场研究 gap"))
    _run(service.report_task_event(
        task_id="relay-task", node_id="group-step", event_type="SEARCH_RESULT",
        event_id="group-search", holder_id="main-bot", relay_turn=turn,
        progress_reason="两个 Bot 能力互补，由 manager 汇总",
        payload={
            "outcome": "HIT_MULTI_BOTS",
            "bot_ids": ["manager-bot", "member-bot"],
            "collab_mode": "manager_worker",
        },
    ))
    _run(service.dispatch_task(
        task_id="relay-task", node_id="group-step", holder_id="main-bot",
        relay_turn=turn, dispatch_id="group-dispatch",
    ))
    with pytest.raises(TaskStateError, match="not assignee"):
        _run(service.report_task_event(
            task_id="relay-task", node_id="group-step", event_type="EXECUTION_RESULT",
            event_id="member-exec", holder_id="member-bot",
            progress_reason="普通成员试图越权续棒", payload={"output": "member"},
        ))
    manager = _run(service.report_task_event(
        task_id="relay-task", node_id="group-step", event_type="EXECUTION_RESULT",
        event_id="manager-exec", holder_id="manager-bot",
        progress_reason="manager 已汇总协作群产出", payload={"output": "group result"},
    ))
    assert manager["relay_turn"]


def test_default_centralized_mode_creates_no_relay_turn() -> None:
    async def scenario() -> None:
        service, graph_service = _service(relay_enabled=False)
        submitted = await service.execute(_request())
        graph = graph_service.query_task_dashboard(submitted.task_id)
        assert graph.extend_props["execution_config"]["orchestration_mode"] == "centralized"
        assert "relay_turn" not in graph.extend_props
        await service.drain_background()

    _run(scenario())


@pytest.mark.parametrize("task_type", ["workflow", "yaml", "static_plan", "bbs"])
def test_relay_setting_does_not_capture_non_dynamic_execution_adapters(task_type: str) -> None:
    service, _ = _service(relay_enabled=True)
    request = replace(_request(), execution_config={"task_type": task_type})

    stamped = service._apply_orchestration_mode(request)

    assert stamped.execution_config["orchestration_mode"] == "centralized"


def test_relay_plan_rejects_parallel_children() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    turn = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))["relay_turn"]
    with pytest.raises(TaskStateError, match="exactly one"):
        _run(service.report_task_event(
            task_id="relay-task", node_id="relay-task", event_type="PLAN_RESULT",
            event_id="parallel-plan", holder_id="main-bot", relay_turn=turn,
            progress_reason="错误地产生多个并行下一棒",
            payload={
                "has_gap": True,
                "children": [
                    {"node_id": "one", "task_spec": _child_spec()},
                    {"node_id": "two", "task_spec": _child_spec()},
                ],
            },
        ))
    assert len(graph_service.query_task_dashboard("relay-task").tasks) == 1


def test_invalid_relay_bbs_claim_does_not_reserve_task_owner() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    graph_service.update_task_graph_info(
        "relay-task", TaskGraphPatch(extend_props_patch={"bbs_mode": True})
    )
    with pytest.raises(TaskStateError, match="not claimable"):
        service.claim_bbs_task("relay-task", "bbs-bot", "relay-task")
    root = graph_service.query_task_dashboard("relay-task").tasks[0]
    assert root.run_info.extend_props.get("bbs_owner") is None


def test_failed_dispatch_reopens_same_turn_for_retry() -> None:
    service, graph_service = _service()
    delivery = _ToggleDelivery()
    service._engine._runner.set_delivery("single_bot", delivery)
    _run(service.execute(_request()))
    turn = _run(service.report_task_event(
        task_id="relay-task", node_id="relay-task", event_type="EXECUTION_RESULT",
        event_id="exec", holder_id="main-bot", progress_reason="首棒完成",
        payload={"output": "done"},
    ))["relay_turn"]
    _plan_and_select(
        service, origin_node_id="relay-task", holder_id="main-bot",
        turn=turn, child_node_id="retry-step", event_suffix="retry",
    )

    with pytest.raises(TaskStateError, match="dispatch failed"):
        _run(service.dispatch_task(
            task_id="relay-task", node_id="retry-step", holder_id="main-bot",
            relay_turn=turn, dispatch_id="dispatch-failed",
        ))
    failed = graph_service.query_task_nodes(
        "relay-task", TaskNodeQueryCriteria(node_ids=["retry-step"]),
    )[0]
    assert failed.status == Status.PENDING
    assert failed.run_info.failure_reason == "下一棒 Runner 派发失败"

    delivery.succeeds = True
    retried = _run(service.dispatch_task(
        task_id="relay-task", node_id="retry-step", holder_id="main-bot",
        relay_turn=turn, dispatch_id="dispatch-retry",
    ))
    assert retried["ok"] is True
    assert graph_service.query_task_nodes(
        "relay-task", TaskNodeQueryCriteria(node_ids=["retry-step"]),
    )[0].status == Status.RUNNING
