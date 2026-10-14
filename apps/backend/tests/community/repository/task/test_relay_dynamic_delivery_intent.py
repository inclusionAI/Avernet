"""Durable delivery intents cover an unformed Relay dynamic group node."""

from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TaskInfoRecord


TEST_RELAY_TOKEN = "0" * 16


def test_pending_dynamic_relay_group_creates_delivery_intent(db):
    task_id = "T-RELAY-DYNAMIC"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={"orchestration_mode": "relay"},
            task_spec={"metadata": {"task_id": task_id}},
            status=Status.RUNNING,
        )
    )
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=1,
        status=Status.RUNNING,
        tasks=[],
        relations=[],
        extend_props={
            "execution_config": {"orchestration_mode": "relay"},
            "runtime_profile": {
                "allowed_run_modes": ["single_bot", "coop_group", "bbs"],
            },
        },
        task_id=task_id,
    )
    root = TaskNode(
        node_id=task_id,
        task_id=task_id,
        status=Status.DONE,
        task_spec=TaskSpec(Context("root", title="root"), Goal("goal", [])),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="B-root",
            extend_props={"relay_root": True},
        ),
        node_run_graph=graph,
    )
    target = TaskNode(
        node_id="node-group",
        task_id=task_id,
        status=Status.PENDING,
        task_spec=TaskSpec(Context("group", title="group"), Goal("group", [])),
        run_info=RuntimeInfo(
            run_mode="coop_group",
            assignee=None,
            extend_props={
                "relay_token": TEST_RELAY_TOKEN,
                "pending_group_formation": {
                    "bot_ids": ["B-manager", "B-worker"],
                    "collab_mode": "manager_worker",
                    "extend_props": {"manager_bot_id": "B-manager"},
                },
            },
        ),
        node_run_graph=graph,
    )
    graph.tasks.extend([root, target])

    repo = TaskGraphRepository(db)
    assert repo.create_graph(graph, runtime_status=Status.RUNNING) == 1

    assert repo.list_pending_delivery_intents() == [(task_id, "node-group")]
