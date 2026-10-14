"""Dynamic manager-worker Relay handoff contract tests."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.task.schemas_relay_report import (
    TaskReportCommandDTO,
)
from agentclaw.community.core.task.domain.errors import DispatchError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskNodePatch,
    TaskReportCommand,
    TaskReportEventType,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_dispatch.dispatcher import (
    RelayHandoffDispatchModeStrategy,
    TaskDispatcher,
)


TEST_RELAY_TOKEN = "0" * 16

GROUP_FORMATION = {
    "bot_ids": ["bot-manager", "bot-worker"],
    "collab_mode": "manager_worker",
    "group_name": "relay-dynamic-group",
    "members_info": [
        {"bot_id": "bot-manager", "role": "manager"},
        {"bot_id": "bot-worker", "role": "worker"},
    ],
    "extend_props": {"manager_bot_id": "bot-manager"},
}


def _graph_service() -> TaskGraphService:
    graph = TaskExecutionGraph(
        task_id="task-1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        extend_props={
            "execution_config": {"orchestration_mode": "relay", "MAX_LOOP": 5},
            "runtime_profile": {
                "dispatcher_strategy": "relay_handoff",
                "runner_strategy": "relay_prompt",
                "allowed_run_modes": ["single_bot", "coop_group", "bbs"],
            },
        },
    )
    root = TaskNode(
        node_id="task-1",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=TaskSpec(Context("root", title="root"), Goal("finish", [])),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="bot-root",
            extend_props={"relay_token": "generation-1"},
        ),
        node_run_graph=graph,
    )
    graph.tasks.append(root)
    service = TaskGraphService()
    service._graphs[graph.task_id] = graph
    return service


def _accepted_execution() -> TaskReportCommand:
    return TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="execution-1",
        relay_token="generation-1",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1",
            node_id="task-1",
            execution_decision="ACCEPTED",
            actual_goal=Goal("cover part", []),
            output_patch={"result": "ROOT_FACT_DONE"},
            local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        ),
    )


def _group_plan(**run_info_kwargs) -> TaskReportCommand:
    runtime = RuntimeInfo(run_mode="coop_group", assignee=None)
    for key, value in run_info_kwargs.items():
        setattr(runtime, key, value)
    planned = TaskNode(
        node_id=None,
        task_id="task-1",
        status=None,
        task_spec=TaskSpec(
            Context("next", title="next"),
            Goal("complete group fact", []),
        ),
        run_info=runtime,
        node_run_graph=None,
    )
    return TaskReportCommand(
        task_id="task-1",
        node_id="task-1",
        event_id="plan-group-1",
        relay_token="generation-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=planned,
    )


def test_relay_plan_persists_pending_group_without_group_assignee() -> None:
    service = _graph_service()
    service.apply_relay_report(_accepted_execution())
    command = _group_plan(
        extend_props={"pending_group_formation": GROUP_FORMATION},
    )

    result = service.apply_relay_report(command)

    assert result.status.value == "APPLIED"
    target = service.query_task_dashboard("task-1").tasks[-1]
    assert target.run_info.run_mode == "coop_group"
    assert target.run_info.assignee is None
    assert target.run_info.extend_props["pending_group_formation"] == GROUP_FORMATION
    assert target.run_info.extend_props.get("group_id") is None
    origin = service.query_task_dashboard("task-1").tasks[0]
    plan_event = origin.run_info.action_log[-2]
    assert plan_event.payload["pending_group_formation"] == GROUP_FORMATION


def test_relay_rejects_existing_group_id_in_planned_group() -> None:
    service = _graph_service()
    service.apply_relay_report(_accepted_execution())
    command = _group_plan(
        assignee="grp-existing",
        extend_props={"pending_group_formation": GROUP_FORMATION},
    )

    with pytest.raises(TaskStateError, match="cannot preselect an existing group"):
        service.apply_relay_report(command)


@pytest.mark.parametrize(
    ("formation", "message"),
    [
        (
            {**GROUP_FORMATION, "collab_mode": "chat"},
            "manager_worker",
        ),
        (
            {**GROUP_FORMATION, "extend_props": {}},
            "manager bot",
        ),
        (
            {**GROUP_FORMATION, "bot_ids": ["only-bot"]},
            "at least two distinct bots",
        ),
        (
            {
                **GROUP_FORMATION,
                "extend_props": {
                    "manager_bot_id": "bot-manager",
                    "task_instruction": "injected",
                },
            },
            "supports only manager_bot_id",
        ),
    ],
)
def test_relay_rejects_invalid_dynamic_group_formations(
    formation: dict, message: str
) -> None:
    service = _graph_service()
    service.apply_relay_report(_accepted_execution())
    command = _group_plan(
        extend_props={"pending_group_formation": formation},
    )

    with pytest.raises(TaskStateError, match=message):
        service.apply_relay_report(command)


def test_relay_report_dto_carries_planned_runtime_fields() -> None:
    command = TaskReportCommandDTO.model_validate(
        {
            "task_id": "task-1",
            "node_id": "node-1",
            "event_id": "event-1",
            "relay_token": "generation-1",
            "event_type": "PLAN_RESULT",
            "event": {
                "task_spec": {
                    "context": {"title": "下一棒", "background": ""},
                    "goal": {"objective": "补齐验收", "acceptances": []},
                },
                "run_info": {
                    "run_mode": "bbs",
                    "assignee": None,
                    "failure_reason": "no candidate",
                    "extend_props": {},
                },
            },
        }
    ).to_domain()

    assert command.event.run_info.run_mode == "bbs"
    assert command.event.run_info.assignee is None
    assert command.event.run_info.failure_reason == "no candidate"
    assert command.event.run_info.extend_props == {}


class _DynamicGroupRunner:
    def __init__(self, start_results: list[bool] | None = None) -> None:
        self.formations: list[object] = []
        self.started: list[TaskNode] = []
        self.start_results = list(start_results or [True])

    async def form_coop_group(self, formation) -> str:
        self.formations.append(formation)
        return "grp-created"

    async def start_run(self, node: TaskNode) -> bool:
        self.started.append(node)
        if len(self.start_results) > 1:
            return self.start_results.pop(0)
        return self.start_results[0]


def test_relay_dispatch_forms_dynamic_group_and_keeps_generation() -> None:
    service = _graph_service()
    target = TaskNode(
        node_id="node-group",
        task_id="task-1",
        status=Status.RUNNING,
        task_spec=TaskSpec(
            Context("group", title="group"),
            Goal("complete group", []),
        ),
        run_info=RuntimeInfo(
            run_mode="coop_group",
            assignee=None,
            extend_props={
                "relay_token": TEST_RELAY_TOKEN,
                "pending_group_formation": GROUP_FORMATION,
            },
        ),
        node_run_graph=service.query_task_dashboard("task-1"),
    )
    service.query_task_dashboard("task-1").tasks.append(target)
    runner = _DynamicGroupRunner()
    dispatcher = TaskDispatcher(
        service,
        runner=runner,
        mode_strategies={"relay_handoff": RelayHandoffDispatchModeStrategy()},
    )

    delivered = _run(dispatcher._dispatch_relay_node(target))

    assert delivered.run_info.assignee == "grp-created"
    assert delivered.run_info.extend_props["group_id"] == "grp-created"
    assert delivered.run_info.extend_props["relay_token"] == TEST_RELAY_TOKEN
    assert len(runner.formations) == 1
    formation = runner.formations[0]
    assert formation.bot_ids == ["bot-manager", "bot-worker"]
    assert formation.extend_props["relay_execution"] is True
    assert formation.extend_props["loop_task_id"] == "task-1::node-group"
    assert formation.extend_props["relay_token"] == TEST_RELAY_TOKEN
    assert runner.started == [target]


class _DeliveryIntents:
    def __init__(self) -> None:
        self.claims: list[tuple[str, str, str]] = []
        self.marks: list[tuple[str, str, bool]] = []

    def claim_delivery_intent(
        self, task_id: str, node_id: str, relay_token: str, *, lease_ms: int = 60000
    ) -> tuple[str, str | None]:
        self.claims.append((task_id, node_id, relay_token))
        return "CLAIMED", "lease-1"

    def mark_delivery_intent(
        self,
        task_id: str,
        node_id: str,
        lease_token: str,
        *,
        delivered: bool,
        error: str | None = None,
    ) -> None:
        assert lease_token == "lease-1"
        self.marks.append((task_id, node_id, delivered))


def test_relay_dispatch_retry_reuses_created_group_without_reforming() -> None:
    service = _graph_service()
    target = TaskNode(
        node_id="node-group",
        task_id="task-1",
        status=Status.PENDING,
        task_spec=TaskSpec(
            Context("group", title="group"),
            Goal("complete group", []),
        ),
        run_info=RuntimeInfo(
            run_mode="coop_group",
            assignee=None,
            extend_props={
                "relay_token": TEST_RELAY_TOKEN,
                "pending_group_formation": GROUP_FORMATION,
            },
        ),
        node_run_graph=service.query_task_dashboard("task-1"),
    )
    service.query_task_dashboard("task-1").tasks.append(target)
    runner = _DynamicGroupRunner(start_results=[False, True])
    dispatcher = TaskDispatcher(
        service,
        runner=runner,
        mode_strategies={"relay_handoff": RelayHandoffDispatchModeStrategy()},
    )

    with pytest.raises(DispatchError, match="relay delivery was not accepted"):
        _run(dispatcher.dispatch(target))
    delivered = _run(dispatcher.dispatch(target))

    assert delivered.run_info.assignee == "grp-created"
    assert len(runner.formations) == 1
    assert len(runner.started) == 2


def test_task_service_dispatch_completes_dynamic_group_delivery_chain() -> None:
    service = _graph_service()
    target = TaskNode(
        node_id="node-group",
        task_id="task-1",
        status=Status.PENDING,
        task_spec=TaskSpec(
            Context("group", title="group"),
            Goal("complete group", []),
        ),
        run_info=RuntimeInfo(
            run_mode="coop_group",
            assignee=None,
            extend_props={
                "relay_token": TEST_RELAY_TOKEN,
                "relay_planned_by": "task-1",
                "pending_group_formation": GROUP_FORMATION,
            },
        ),
        node_run_graph=service.query_task_dashboard("task-1"),
    )
    service.query_task_dashboard("task-1").tasks.append(target)
    runner = _DynamicGroupRunner()
    dispatcher = TaskDispatcher(
        service,
        runner=runner,
        mode_strategies={"relay_handoff": RelayHandoffDispatchModeStrategy()},
    )
    task_service = TaskService(service, delivery_intents=_DeliveryIntents())
    task_service._relay_adapter._dispatcher = dispatcher
    intents = task_service._delivery_intents

    delivered = _run(task_service.dispatch_node("task-1", "node-group"))

    assert delivered.run_info.assignee == "grp-created"
    persisted = next(
        node
        for node in service.query_task_dashboard("task-1").tasks
        if node.node_id == "node-group"
    )
    assert persisted.status == Status.RUNNING
    assert persisted.run_info.assignee == "grp-created"
    assert persisted.run_info.extend_props["group_id"] == "grp-created"
    assert persisted.run_info.extend_props["relay_token"] == TEST_RELAY_TOKEN
    assert intents.claims == [("task-1", "node-group", TEST_RELAY_TOKEN)]
    assert intents.marks == [("task-1", "node-group", True)]
    dispatch_log = persisted.run_info.action_log[-1]
    assert dispatch_log.payload["action_result"] == "delivered"
    assert dispatch_log.payload["run_mode"] == "coop_group"
    assert dispatch_log.payload["assignee"] == "grp-created"


def _run(coroutine):
    import asyncio

    return asyncio.run(coroutine)
