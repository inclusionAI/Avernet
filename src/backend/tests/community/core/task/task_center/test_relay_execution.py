from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
import logging
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskGraphPatch,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    TaskSourceType,
)
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance,
    RequestContext,
    RequestGoal,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.task_center.relay import RelayCoordinator
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_dispatch.claim_join_gate import RELAY_EXECUTION
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
)
import agentclaw.community.core.task.repository.models  # noqa: F401
from tests.community.core.task.task_trajectory._task_context_support import _tcs


class _InMemorySqliteDB:
    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _make_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return _InMemorySqliteDB(engine)


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
        self.calls: list[str] = []

    async def deliver(self, node) -> bool:
        self.calls.append(node.node_id)
        return self.succeeds


def _request() -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            context=RequestContext(title="调研", background="共享背景"),
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


def _accepted(output):
    if not isinstance(output, dict):
        output = {"result": output}
    return {
        "execution_decision": "ACCEPTED",
        "output": output,
        "acceptance_result": {
            "verdict": "DONE",
            "acceptances_metric": [],
            "gaps": [],
        },
    }


def _run(coro):
    return asyncio.run(coro)


def _service(*, discover=None, relay_enabled: bool = True, task_context_service=None):
    graph = TaskGraphService()
    return TaskService(
        graph,
        discover=discover or _Discover(),
        task_settings=_Settings(relay_enabled),
        task_id_provider=lambda: "relay-task",
        task_context_service=task_context_service,
    ), graph


def _service_with_traj():
    repo = TaskTrajectoryRepository(_make_db())
    service, graph = _service(task_context_service=_tcs(repo))
    return service, graph, repo


def _relay_records(repo):
    return [
        record
        for record in repo.list_events_by_task("relay-task")
        if str(record.action_type) == "relay"
    ]


def _plan_and_select(
    service: TaskService,
    *,
    origin_node_id: str,
    holder_id: str,
    turn: str,
    child_node_id: str,
    event_suffix: str,
) -> str:
    del child_node_id  # Graph owns target node identity in the new Relay contract.
    planned = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=origin_node_id,
            event_type="PLAN_RESULT",
            event_id=f"plan-{event_suffix}",
            holder_id=holder_id,
            relay_turn=turn,
            progress_reason="当前全局 gap 需要下一棒补齐",
            payload={
                "gaps": ["补齐市场研究 gap"],
                "next_task_spec": _child_spec(),
            },
        )
    )
    target_node_id = planned["target_node_id"]
    _run(service.search_task_candidates(query="补齐市场研究 gap"))
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=target_node_id,
            event_type="DISPATCH_RESULT",
            event_id=f"search-{event_suffix}",
            holder_id=holder_id,
            relay_turn=turn,
            progress_reason="候选 Bot 能力与下一节点目标匹配",
            payload={
                "outcome": "HIT_SINGLE",
                "run_mode": "single_bot",
                "driver_bot_id": (
                    "research-bot" if event_suffix == "1" else f"research-bot-{event_suffix}"
                ),
                "next_relay_bots": (
                    ["research-bot"]
                    if event_suffix == "1"
                    else [f"research-bot-{event_suffix}"]
                ),
            },
        )
    )
    return target_node_id


def test_relay_search_logs_empty_result_diagnostics(caplog) -> None:
    class _EmptyDiscover:
        def search_by_keyword(self, **kwargs):
            return {"total": 0, "items": []}

    service, _ = _service(discover=_EmptyDiscover())
    with caplog.at_level(logging.DEBUG, logger="task.relay.search"):
        result = _run(service.search_task_candidates(query="存储行业尽调"))

    assert result == {"candidates": [], "total": 0}
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "search_start" in message and "存储行业尽调" in message for message in messages
    )
    assert any(
        "search_complete" in message and "raw_item_count=0" in message
        for message in messages
    )
    assert any(
        "search_empty reason=no_matching_candidates" in message for message in messages
    )


def test_relay_search_logs_discover_failure(caplog) -> None:
    class _FailingDiscover:
        def search_by_keyword(self, **kwargs):
            raise RuntimeError("catalog unavailable")

    service, _ = _service(discover=_FailingDiscover())
    with caplog.at_level(logging.WARNING, logger="task.relay.search"):
        result = _run(service.search_task_candidates(query="存储行业尽调"))

    assert result == {"candidates": [], "total": 0}
    assert any(
        "search_failed" in record.getMessage()
        and "RuntimeError" in record.getMessage()
        and "catalog unavailable" in record.getMessage()
        for record in caplog.records
    )


def test_relay_exec_plan_search_dispatch_and_complete() -> None:
    service, graph_service = _service()
    submitted = _run(service.execute(_request()))
    assert submitted.success
    graph = graph_service.query_task_dashboard("relay-task")
    root = graph.tasks[0]
    assert root.status == Status.RUNNING
    assert root.run_info.assignee == "main-bot"
    assert graph.extend_props["execution_config"]["orchestration_mode"] == "relay"

    execution = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec-1",
            holder_id="main-bot",
            progress_reason="首棒形成分析，可以规划补充研究",
            payload=_accepted({"summary": "首轮结论"}),
        )
    )
    turn = execution["relay_turn"]

    research_step = _plan_and_select(
        service,
        origin_node_id="relay-task",
        holder_id="main-bot",
        turn=turn,
        child_node_id="research-step",
        event_suffix="1",
    )

    handed_off = graph_service.query_task_dashboard("relay-task")
    handed_off_root = next(n for n in handed_off.tasks if n.node_id == "relay-task")
    handed_off_child = next(n for n in handed_off.tasks if n.node_id == research_step)
    assert handed_off_root.status == Status.DONE
    assert handed_off_child.status == Status.PENDING
    assert handed_off.status == Status.RUNNING
    assert handed_off.effective_status == Status.RUNNING

    # PLAN_RESULT must be persistable as an idempotent HTTP fact. A live
    # TaskNode in the event record would break repository JSON persistence and
    # make a response-loss retry unable to recover target_node_id.
    event_records = graph_service.query_task_dashboard("relay-task").extend_props[
        "relay_event_records"
    ]
    plan_record = event_records["PLAN_RESULT:plan-1"]
    execution_record = event_records["EXECUTION_RESULT:exec-1"]
    assert plan_record["result"]["target_node_id"] == research_step
    assert "dispatch_turn" not in plan_record["result"]
    assert "relay_turn" not in execution_record["result"]
    serialized_events = json.dumps(event_records)
    assert turn not in serialized_events
    replayed_plan = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="plan-1",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="当前全局 gap 需要下一棒补齐",
            payload={
                "gaps": ["补齐市场研究 gap"],
                "next_task_spec": _child_spec(),
            },
        )
    )
    assert replayed_plan["idempotent"] is True
    assert replayed_plan["target_node_id"] == research_step

    dispatched = _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id="relay-task",
            target_node_id=research_step,
            holder_id="main-bot",
            relay_turn=turn,
            dispatch_id="dispatch-1",
        )
    )
    assert dispatched["assignee"] == "research-bot"
    running_baton = graph_service.query_task_dashboard("relay-task")
    running_child = next(
        n for n in running_baton.tasks if n.node_id == research_step
    )
    assert running_child.status == Status.RUNNING
    assert running_baton.status == Status.RUNNING
    assert running_baton.effective_status == Status.RUNNING

    second = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=research_step,
            event_type="EXECUTION_RESULT",
            event_id="exec-2",
            holder_id="research-bot",
            progress_reason="第二棒产出完成，继续计算全局 gap",
            payload=_accepted({"recommendation": "进入市场"}),
        )
    )
    final_step = _plan_and_select(
        service,
        origin_node_id=research_step,
        holder_id="research-bot",
        turn=second["relay_turn"],
        child_node_id="final-step",
        event_suffix="2",
    )
    _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id=research_step,
            target_node_id=final_step,
            holder_id="research-bot",
            relay_turn=second["relay_turn"],
            dispatch_id="dispatch-2",
        )
    )
    third = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=final_step,
            event_type="EXECUTION_RESULT",
            event_id="exec-3",
            holder_id="research-bot-2",
            progress_reason="最终一棒产出完成，检查根验收标准",
            payload=_accepted({"final": "complete"}),
        )
    )
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=final_step,
            event_type="PLAN_RESULT",
            event_id="plan-3",
            holder_id="research-bot-2",
            relay_turn=third["relay_turn"],
            progress_reason="根目标已全部满足",
            payload={"gaps": [], "next_task_spec": None},
        )
    )
    final = graph_service.query_task_dashboard("relay-task")
    assert [node.status for node in final.tasks] == [
        Status.DONE,
        Status.DONE,
        Status.SUCCESS,
    ]
    assert final.status == Status.DONE
    assert final.effective_status == Status.DONE
    assert final.output == {
        "relay-task": {"summary": "首轮结论"},
        research_step: {"recommendation": "进入市场"},
        final_step: {"final": "complete"},
    }
    for task_node in final.tasks:
        assert set(task_node.task_spec.to_dict()) == {"context", "goal"}


def test_relay_dispatch_rejects_current_holder_as_next_relay_bot() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec-self-dispatch",
            holder_id="main-bot:owner-1",
            progress_reason="首棒完成，规划下一棒",
            payload=_accepted({"summary": "首轮结论"}),
        )
    )["relay_turn"]
    planned = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="plan-self-dispatch",
            holder_id="main-bot:owner-1",
            relay_turn=turn,
            progress_reason="存在下一棒缺口",
            payload={"gaps": ["补齐市场研究 gap"], "next_task_spec": _child_spec()},
        )
    )
    with pytest.raises(TaskStateError, match="current holder"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=planned["target_node_id"],
                event_type="DISPATCH_RESULT",
                event_id="search-self-dispatch",
                holder_id="main-bot:owner-1",
                relay_turn=turn,
                progress_reason="候选 Bot 能力与下一节点目标匹配",
                payload={
                    "outcome": "HIT_SINGLE",
                    "run_mode": "single_bot",
                    "driver_bot_id": "main-bot",
                    "next_relay_bots": ["main-bot"],
                },
            )
        )


def test_relay_miss_publishes_bbs_and_claimant_continues_without_root_planning_reset() -> (
    None
):
    service, graph_service = _service()
    _run(service.execute(_request()))
    turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )["relay_turn"]
    planned = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="plan",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="需要 BBS 承接下一棒",
            payload={"gaps": ["补齐市场研究 gap"], "next_task_spec": _child_spec()},
        )
    )
    bbs_step = planned["target_node_id"]
    _run(service.search_task_candidates(query="补齐市场研究 gap"))
    published = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=bbs_step,
            event_type="DISPATCH_RESULT",
            event_id="miss",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="无直接候选，发布 BBS 广场",
            failure_reason="候选能力均不匹配",
            payload={
                "outcome": "MISS",
                "miss_reason": "no capability match",
            },
        )
    )
    assert published["published_bbs"]
    service.claim_bbs_task("relay-task", "bbs-bot", bbs_step)
    graph = graph_service.query_task_dashboard("relay-task")
    root = next(node for node in graph.tasks if node.node_id == "relay-task")
    bbs = next(node for node in graph.tasks if node.node_id == bbs_step)
    assert root.status == Status.DONE
    assert bbs.status == Status.RUNNING
    assert bbs.run_info.assignee == "bbs-bot"
    assert graph.status == Status.RUNNING
    assert graph.effective_status == Status.RUNNING
    with pytest.raises(TaskStateError):
        service.claim_bbs_task("relay-task", "other-bbs-bot", bbs_step)

    continued = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=bbs_step,
            event_type="EXECUTION_RESULT",
            event_id="bbs-exec",
            holder_id="bbs-bot",
            progress_reason="BBS 执行产出完成，继续计算全局 gap",
            payload=_accepted({"bbs_result": "补齐证据"}),
        )
    )
    assert (
        next(
            node
            for node in graph_service.query_task_dashboard("relay-task").tasks
            if node.node_id == "relay-task"
        ).status
        == Status.DONE
    )
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=bbs_step,
            event_type="PLAN_RESULT",
            event_id="bbs-plan",
            holder_id="bbs-bot",
            relay_turn=continued["relay_turn"],
            progress_reason="BBS 产出已满足根目标",
            payload={"gaps": [], "next_task_spec": None},
        )
    )
    assert graph_service.query_task_dashboard("relay-task").status == Status.DONE


def test_relay_bbs_result_only_completes_claimed_baton_node() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="bbs-root-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )["relay_turn"]
    planned = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="bbs-plan",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="下一棒转 BBS 广场",
            payload={"gaps": ["补齐市场研究 gap"], "next_task_spec": _child_spec()},
        )
    )
    bbs_step = planned["target_node_id"]
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=bbs_step,
            event_type="DISPATCH_RESULT",
            event_id="bbs-search",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="普通候选无法覆盖，发布 BBS",
            failure_reason="无匹配候选",
            payload={"outcome": "MISS", "miss_reason": "no_candidates"},
        )
    )
    service.claim_bbs_task("relay-task", "bbs-bot", bbs_step)
    before = graph_service.query_task_dashboard("relay-task")
    root_before = next(node for node in before.tasks if node.node_id == "relay-task")
    graph_status_before = before.status

    result = _run(
        service.report_bbs_result(
            "relay-task", bbs_step, "bbs-bot", output_patch={"bbs_result": "done"}
        )
    )

    after = graph_service.query_task_dashboard("relay-task")
    root_after = next(node for node in after.tasks if node.node_id == "relay-task")
    bbs_after = next(node for node in after.tasks if node.node_id == bbs_step)
    assert result.new_status == Status.DONE
    assert root_after.status == root_before.status
    assert after.status == graph_status_before
    assert root_after.run_info.extend_props.get("bbs_owner") is None
    assert bbs_after.status == Status.DONE
    assert bbs_after.run_info.output == {"bbs_result": "done"}


def test_relay_search_is_independent_from_task_context() -> None:
    service, _ = _service()
    result = _run(service.search_task_candidates(query="补齐市场研究 gap"))
    assert result["candidates"][0]["bot_uuid"] == "research-bot:owner-2"
    assert "catalog_id" not in result


def test_root_holder_accepts_frontend_composite_bot_identity() -> None:
    service, _ = _service()
    request = replace(_request(), owner_bot_id="main-bot:owner-1")
    _run(service.execute(request))
    result = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="composite-exec",
            holder_id="main-bot:owner-1",
            progress_reason="主 Bot 首棒完成",
            payload=_accepted("done"),
        )
    )
    assert result["relay_turn"]


def test_relay_rejects_missing_trajectory_reason() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    with pytest.raises(TaskStateError, match="progress_reason"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="no-reason",
                holder_id="main-bot",
                payload=_accepted("done"),
            )
        )
    root = graph_service.query_task_dashboard("relay-task").tasks[0]
    assert root.run_info.output == {}


def test_retried_execution_report_does_not_invalidate_first_turn_token() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    first = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="same-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )
    retried = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="same-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )
    assert retried["idempotent"] is True
    assert retried["relay_turn"] != first["relay_turn"]

    result = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="finish",
            holder_id="main-bot",
            relay_turn=first["relay_turn"],
            progress_reason="根目标已满足",
            payload={"gaps": [], "next_task_spec": None},
        )
    )
    assert result["completed"] is True

    delayed = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="same-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )
    assert delayed == {"ok": True, "idempotent": True, "turn_consumed": True}
    assert (
        graph_service.query_task_dashboard("relay-task").extend_props["relay_turn"][
            "status"
        ]
        == "CONSUMED"
    )


def test_expired_turn_is_recovered_by_idempotent_execution_retry() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    first = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="recover-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )
    graph = graph_service.query_task_dashboard("relay-task")
    expired = dict(graph.extend_props["relay_turn"])
    expired["expires_at_ms"] = 0
    graph_service.update_task_graph_info(
        "relay-task", TaskGraphPatch(extend_props_patch={"relay_turn": expired})
    )
    with pytest.raises(TaskStateError, match="invalid"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="PLAN_RESULT",
                event_id="stale-plan",
                holder_id="main-bot",
                relay_turn=first["relay_turn"],
                progress_reason="尝试使用已过期接力权",
                payload={"gaps": [], "next_task_spec": None},
            )
        )

    recovered = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="recover-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )
    assert recovered["idempotent"] is True
    assert recovered["relay_turn"] != first["relay_turn"]


def test_only_group_manager_can_report_and_continue() -> None:
    service, _ = _service(discover=_DiscoverTwo())
    _run(service.execute(_request()))
    turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="root-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )["relay_turn"]
    spec = _child_spec()
    planned = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="group-plan",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="下一棒需要多 Bot 协作",
            payload={"gaps": ["需要多 Bot 协作"], "next_task_spec": spec},
        )
    )
    group_step = planned["target_node_id"]
    _run(service.search_task_candidates(query="补齐市场研究 gap"))
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=group_step,
            event_type="DISPATCH_RESULT",
            event_id="group-search",
            holder_id="main-bot",
            relay_turn=turn,
            progress_reason="两个 Bot 能力互补，由 manager 汇总",
            payload={
                "outcome": "HIT_MULTI_BOTS",
                "bot_ids": ["manager-bot", "member-bot"],
                "collab_mode": "manager_worker",
            },
        )
    )
    _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id="relay-task",
            target_node_id=group_step,
            holder_id="main-bot",
            relay_turn=turn,
            dispatch_id="group-dispatch",
        )
    )
    with pytest.raises(TaskStateError, match="not assignee"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=group_step,
                event_type="EXECUTION_RESULT",
                event_id="member-exec",
                holder_id="member-bot",
                progress_reason="普通成员试图越权续棒",
                payload=_accepted("member"),
            )
        )
    manager = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id=group_step,
            event_type="EXECUTION_RESULT",
            event_id="manager-exec",
            holder_id="manager-bot",
            progress_reason="manager 已汇总协作群产出",
            payload=_accepted("group result"),
        )
    )
    assert manager["relay_turn"]


def test_default_centralized_mode_creates_no_relay_turn() -> None:
    async def scenario() -> None:
        service, graph_service = _service(relay_enabled=False)
        submitted = await service.execute(_request())
        graph = graph_service.query_task_dashboard(submitted.task_id)
        assert (
            graph.extend_props["execution_config"]["orchestration_mode"]
            == "centralized"
        )
        assert "relay_turn" not in graph.extend_props
        await service.drain_background()

    _run(scenario())


@pytest.mark.parametrize("task_type", ["workflow", "yaml", "static_plan", "bbs"])
def test_relay_setting_does_not_capture_non_dynamic_execution_adapters(
    task_type: str,
) -> None:
    service, _ = _service(relay_enabled=True)
    request = replace(_request(), execution_config={"task_type": task_type})

    stamped = service._apply_orchestration_mode(request)

    assert stamped.execution_config["orchestration_mode"] == "centralized"


def test_relay_plan_rejects_parallel_children() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )["relay_turn"]
    with pytest.raises(TaskStateError, match="requires next_task_spec"):
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="PLAN_RESULT",
                event_id="parallel-plan",
                holder_id="main-bot",
                relay_turn=turn,
                progress_reason="错误地产生多个并行下一棒",
                payload={
                    "has_gap": True,
                    "children": [
                        {"node_id": "one", "task_spec": _child_spec()},
                        {"node_id": "two", "task_spec": _child_spec()},
                    ],
                },
            )
        )
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
    service._runner.set_delivery("single_bot", delivery)
    _run(service.execute(_request()))
    turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted("done"),
        )
    )["relay_turn"]
    retry_step = _plan_and_select(
        service,
        origin_node_id="relay-task",
        holder_id="main-bot",
        turn=turn,
        child_node_id="retry-step",
        event_suffix="retry",
    )

    with pytest.raises(TaskStateError, match="dispatch failed"):
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id="relay-task",
                target_node_id=retry_step,
                holder_id="main-bot",
                relay_turn=turn,
                dispatch_id="dispatch-failed",
            )
        )
    failed = graph_service.query_task_nodes(
        "relay-task",
        TaskNodeQueryCriteria(node_ids=[retry_step]),
    )[0]
    assert failed.status == Status.PENDING
    assert failed.run_info.failure_reason == "下一棒 Runner 派发失败"

    delivery.succeeds = True
    retried = _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id="relay-task",
            target_node_id=retry_step,
            holder_id="main-bot",
            relay_turn=turn,
            dispatch_id="dispatch-failed",
        )
    )
    assert retried["ok"] is True
    # The same delivery id must retry a non-delivered attempt. It cannot report
    # early idempotency after only consuming the Relay ticket.
    assert delivery.calls.count(retry_step) == 2
    assert (
        graph_service.query_task_nodes(
            "relay-task",
            TaskNodeQueryCriteria(node_ids=[retry_step]),
        )[0].status
        == Status.RUNNING
    )
    delivered_again = _run(
        service.dispatch_task(
            task_id="relay-task",
            origin_node_id="relay-task",
            target_node_id=retry_step,
            holder_id="main-bot",
            relay_turn=turn,
            dispatch_id="dispatch-failed",
        )
    )
    assert delivered_again["ok"] is True
    assert delivered_again["idempotent"] is True
    assert delivery.calls.count(retry_step) == 2


def test_relay_coordinator_renews_only_expired_current_turn() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    coordinator = RelayCoordinator(graph_service, ttl_seconds=0)
    first = coordinator.grant("relay-task", "relay-task", "main-bot")
    assert first is not None

    renewed = coordinator.renew_expired("relay-task", "relay-task", "main-bot")

    assert renewed is not None
    assert renewed.token != first.token
    graph = graph_service.query_task_dashboard("relay-task")
    assert graph.extend_props["relay_turn"]["node_id"] == "relay-task"
    assert graph.extend_props["relay_turn"]["holder_id"] == "main-bot"
    assert graph.extend_props["relay_turn"]["status"] == "GRANTED"


def test_expired_relay_resume_only_mutates_current_baton_node() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    first_turn = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="resume-root-exec",
            holder_id="main-bot",
            progress_reason="首棒完成",
            payload=_accepted({"scope": "first"}),
        )
    )["relay_turn"]
    resume_step = _plan_and_select(
        service,
        origin_node_id="relay-task",
        holder_id="main-bot",
        turn=first_turn,
        child_node_id="resume-step",
        event_suffix="resume",
    )
    RelayCoordinator(graph_service).consume(
        "relay-task", resume_step, "main-bot", first_turn
    )
    graph_service.update_task_node_info(
        TaskNodePatch(
            task_id="relay-task",
            node_id=resume_step,
            status=Status.RUNNING,
            extend_props_patch={"relay_holder_id": "research-bot"},
        )
    )
    coordinator = RelayCoordinator(graph_service, ttl_seconds=0)
    assert coordinator.grant("relay-task", resume_step, "research-bot") is not None

    resumed: list[tuple[str, str]] = []

    async def _resume(node, turn):
        resumed.append((node.node_id, turn))
        return True

    service._runner.resume_relay_turn = _resume  # type: ignore[method-assign]
    parent_before = next(
        node
        for node in graph_service.query_task_dashboard("relay-task").tasks
        if node.node_id == "relay-task"
    )
    parent_status_before = parent_before.status
    parent_output_before = dict(parent_before.run_info.output)

    assert _run(service.resume_expired_relay_turn("relay-task")) is True

    graph = graph_service.query_task_dashboard("relay-task")
    parent_after = next(node for node in graph.tasks if node.node_id == "relay-task")
    baton_after = next(node for node in graph.tasks if node.node_id == resume_step)
    assert resumed and resumed[0][0] == resume_step
    assert parent_after.status == parent_status_before
    assert parent_after.run_info.output == parent_output_before
    assert baton_after.run_info.extend_props["relay_resume_count"] == 1
    assert graph.extend_props["relay_turn"]["node_id"] == resume_step


def test_task_context_projects_only_accepted_done_outputs_and_latest_gaps() -> None:
    service, graph_service = _service()
    _run(service.execute(_request()))
    execution = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="context-exec",
            holder_id="main-bot",
            progress_reason="完成本地覆盖范围",
            payload=_accepted({"evidence": "local-result"}),
        )
    )
    before_plan = service.get_task_context("relay-task")
    assert before_plan.spec.context.title == "调研"
    assert before_plan.gaps == []
    assert [item.node_id for item in before_plan.all_done_output] == ["relay-task"]
    assert before_plan.all_done_output[0].output == {"evidence": "local-result"}

    planned = _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="context-plan",
            holder_id="main-bot",
            relay_turn=execution["relay_turn"],
            progress_reason="仍缺最新证据",
            payload={
                "gaps": ["缺少最近三个月证据"],
                "next_task_spec": _child_spec(),
            },
        )
    )
    after_plan = service.get_task_context("relay-task")
    assert after_plan.gaps == ["缺少最近三个月证据"]
    target = graph_service.query_task_nodes(
        "relay-task",
        TaskNodeQueryCriteria(node_ids=[planned["target_node_id"]]),
    )[0]
    assert target.run_info.actual_goal is None
    assert target.run_info.output == {}
    assert target.run_info.acceptance_result is None


def test_declined_execution_is_not_exposed_as_done_output() -> None:
    service, _ = _service()
    _run(service.execute(_request()))
    _run(
        service.report_task_event(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="context-declined",
            holder_id="main-bot",
            progress_reason="职责不覆盖专业研究",
            failure_reason="capability_mismatch",
            payload={
                "execution_decision": "DECLINED",
                "actual_goal": None,
                "output": {},
                "acceptance_result": None,
            },
        )
    )
    assert service.get_task_context("relay-task").all_done_output == []


class TestRelayTrajectory:
    def test_full_cycle_emits_relay_timeline_in_order(self):
        service, _graph, repo = _service_with_traj()
        _run(service.execute(_request()))
        exec1 = _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec-1",
                holder_id="main-bot",
                progress_reason="首棒完成",
                payload=_accepted({"summary": "首轮"}),
            )
        )
        step1 = _plan_and_select(
            service,
            origin_node_id="relay-task",
            holder_id="main-bot",
            turn=exec1["relay_turn"],
            child_node_id="ignored",
            event_suffix="1",
        )
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id="relay-task",
                target_node_id=step1,
                holder_id="main-bot",
                relay_turn=exec1["relay_turn"],
                dispatch_id="dispatch-1",
            )
        )
        exec2 = _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=step1,
                event_type="EXECUTION_RESULT",
                event_id="exec-2",
                holder_id="research-bot",
                progress_reason="第二棒完成",
                payload=_accepted({"recommendation": "进入市场"}),
            )
        )
        step2 = _plan_and_select(
            service,
            origin_node_id=step1,
            holder_id="research-bot",
            turn=exec2["relay_turn"],
            child_node_id="ignored",
            event_suffix="2",
        )
        _run(
            service.dispatch_task(
                task_id="relay-task",
                origin_node_id=step1,
                target_node_id=step2,
                holder_id="research-bot",
                relay_turn=exec2["relay_turn"],
                dispatch_id="dispatch-2",
            )
        )
        exec3 = _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=step2,
                event_type="EXECUTION_RESULT",
                event_id="exec-3",
                holder_id="research-bot-2",
                progress_reason="最终完成",
                payload=_accepted({"final": "complete"}),
            )
        )
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=step2,
                event_type="PLAN_RESULT",
                event_id="plan-final",
                holder_id="research-bot-2",
                relay_turn=exec3["relay_turn"],
                progress_reason="根目标完成",
                payload={"gaps": []},
            )
        )
        records = _relay_records(repo)
        assert [(r.node_id, r.action_result) for r in records] == [
            ("relay-task", "bootstrap"),
            ("relay-task", "execution_result"),
            ("relay-task", "plan_result"),
            (step1, "hit_single"),
            (step1, "dispatch"),
            (step1, "execution_result"),
            (step1, "plan_result"),
            (step2, "hit_single"),
            (step2, "dispatch"),
            (step2, "execution_result"),
            (step2, "plan_result"),
        ]
        assert all(record.error_type is None for record in records)
        hit_records = [
            record for record in records if record.action_result == "hit_single"
        ]
        assert hit_records
        assert all(
            record.boost_reason == "候选 Bot 能力与下一节点目标匹配"
            for record in hit_records
        )
        plan_holders = [
            json.loads(record.ext_info).get("holder_id")
            for record in records
            if record.action_result == "plan_result"
        ]
        assert plan_holders == ["main-bot", "research-bot", "research-bot-2"]

    def test_callback_events_are_correlated_without_leaking_turn(self):
        service, _graph, repo = _service_with_traj()
        _run(service.execute(_request()))
        service.record_relay_callback_success(
            task_id="relay-task",
            node_id="relay-task",
            event_type="EXECUTION_RESULT",
            event_id="exec-http",
            holder_id="main-bot",
            relay_turn=None,
            payload={"success": True},
            result={"ok": True, "relay_turn": "secret-token"},
        )
        service.record_relay_callback_error(
            task_id="relay-task",
            node_id="relay-task",
            event_type="PLAN_RESULT",
            event_id="bad-plan",
            holder_id="main-bot",
            relay_turn="secret-token",
            progress_reason="规划",
            failure_reason=None,
            payload={"gaps": ["x"]},
            error_phase="processing",
            exception_type="TaskStateError",
            error_msg="bad plan",
        )
        records = _relay_records(repo)
        assert records[-2].action_result == "callback_reported"
        assert records[-1].action_result == "callback_report_failed"
        assert records[-1].error_type == ReasonCatalog.RELAY.value
        assert "secret-token" not in records[-1].ext_info
        assert json.loads(records[-1].ext_info)["relay_turn_prefix"] == "secret-t"

    def test_miss_claim_and_bbs_result_emit_relay_events(self):
        service, _graph, repo = _service_with_traj()
        _run(service.execute(_request()))
        execution = _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec",
                holder_id="main-bot",
                progress_reason="首棒完成",
                payload=_accepted({"summary": "done"}),
            )
        )
        planned = _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="PLAN_RESULT",
                event_id="plan",
                holder_id="main-bot",
                relay_turn=execution["relay_turn"],
                progress_reason="需要 BBS",
                payload={"gaps": ["专业缺口"], "next_task_spec": _child_spec()},
            )
        )
        target = planned["target_node_id"]
        _run(
            service.report_task_event(
                task_id="relay-task",
                node_id=target,
                event_type="DISPATCH_RESULT",
                event_id="miss",
                holder_id="main-bot",
                relay_turn=execution["relay_turn"],
                progress_reason="发布 BBS",
                failure_reason="无匹配",
                payload={"outcome": "MISS", "miss_reason": "no_candidates"},
            )
        )
        service.claim_bbs_task("relay-task", "bbs-bot", target)
        _run(
            service.report_bbs_result(
                "relay-task", target, "bbs-bot", output_patch={"bbs_result": "done"}
            )
        )
        pairs = [(r.node_id, r.action_result) for r in _relay_records(repo)]
        assert (target, "miss") in pairs
        assert (target, "bbs_claim") in pairs
        assert (target, "bbs_result") in pairs

    def test_dispatch_failure_and_resume_exhaustion_emit_errors(self):
        service, graph, repo = _service_with_traj()
        delivery = _ToggleDelivery()
        service._runner.set_delivery("single_bot", delivery)
        _run(service.execute(_request()))
        execution = _run(
            service.report_task_event(
                task_id="relay-task",
                node_id="relay-task",
                event_type="EXECUTION_RESULT",
                event_id="exec",
                holder_id="main-bot",
                progress_reason="首棒完成",
                payload=_accepted({"summary": "done"}),
            )
        )
        target = _plan_and_select(
            service,
            origin_node_id="relay-task",
            holder_id="main-bot",
            turn=execution["relay_turn"],
            child_node_id="ignored",
            event_suffix="failure",
        )
        with pytest.raises(TaskStateError, match="dispatch failed"):
            _run(
                service.dispatch_task(
                    task_id="relay-task",
                    origin_node_id="relay-task",
                    target_node_id=target,
                    holder_id="main-bot",
                    relay_turn=execution["relay_turn"],
                    dispatch_id="dispatch-failed",
                )
            )
        g = graph.query_task_dashboard("relay-task")
        g.extend_props["relay_turn"]["expires_at_ms"] = 0
        graph.update_task_node_info(
            TaskNodePatch(
                task_id="relay-task",
                node_id="relay-task",
                extend_props_patch={"relay_resume_count": 2},
            )
        )
        assert _run(service.resume_expired_relay_turn("relay-task")) is False
        g = graph.query_task_dashboard("relay-task")
        assert next(
            n for n in g.tasks if n.node_id == "relay-task"
        ).status == Status.DONE
        assert g.status == Status.HUNG
        errors = {r.action_result: r for r in _relay_records(repo)}
        assert errors["dispatch_failed_reopen"].error_type == ReasonCatalog.RELAY.value
        assert errors["resume_exhausted_hung"].error_type == ReasonCatalog.RELAY.value
