"""Persistence coverage for the root node's initial session identity."""

from __future__ import annotations

import json
from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    Status,
    TaskInfo,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.models import TaskNodeRunInfoModel
from agentclaw.community.core.task.repository.serializers import (
    _acceptance_from_dict,
    _acceptance_to_dict,
)
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)


def _task_spec(task_id: str) -> TaskSpec:
    return TaskSpec(
        context=Context(background="bg", title=task_id),
        goal=Goal(
            objective="objective",
            acceptances=[AcceptanceCriteria(id="ac1", description="done")],
        ),
    )


def _seed(db, task_id: str) -> None:
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={
                "context": {"background": "bg", "extend_props": {}},
                "goal": {
                    "objective": "objective",
                    "acceptances": [{"id": "ac1", "acceptance": "done"}],
                },
            },
            status=Status.PENDING,
        )
    )


def test_acceptance_result_serializes_done_and_reads_legacy_items():
    result = AcceptanceResult(
        verdict=AcceptanceVerdict.DONE,
        done_items=[{"id": "ac1", "passed": True, "summary": "done"}],
        gap_items=[],
    )

    assert _acceptance_to_dict(result) == {
        "verdict": "DONE",
        "done_items": [{"id": "ac1", "passed": True, "summary": "done"}],
        "gap_items": [],
    }

    legacy = _acceptance_from_dict(
        {
            "verdict": "DONE",
            "acceptances_metric": [
                {"id": "ac1", "passed": True, "summary": "done"},
                {"id": "ac2", "passed": False, "summary": "missing"},
            ],
            "gaps": ["ac2 missing"],
        }
    )
    assert legacy is not None
    assert legacy.done_items == [{"id": "ac1", "passed": True, "summary": "done"}]
    assert legacy.gap_items == ["ac2 missing"]


def test_acceptance_from_dict_does_not_read_undeployed_done_alias():
    result = _acceptance_from_dict(
        {"verdict": "DONE", "done": [{"id": "ac1"}], "gaps": []}
    )

    assert result is not None
    assert result.done_items == []
    assert result.gap_items == []


def test_initialize_graph_persists_root_main_session(db):
    task_id = "T-ROOT-SESSION"
    _seed(db, task_id)
    service = TaskGraphService(graph_repo=TaskGraphRepository(db))

    service.initialize_graph(
        TaskInfo(
            task_id=task_id,
            task_spec=_task_spec(task_id),
            source_type="bot",
            owner_bot_id="B-1",
            execution_config={"main_session_id": "session-initial"},
        )
    )

    service.update_task_node_info(
        TaskNodePatch(
            task_id=task_id,
            node_id=task_id,
            extend_props_patch={"session_id": "session-reported"},
        )
    )

    with db.orm_session() as session:
        row = (
            session.query(TaskNodeRunInfoModel)
            .filter(
                TaskNodeRunInfoModel.task_id == task_id,
                TaskNodeRunInfoModel.node_id == task_id,
            )
            .one()
        )
        assert row.session_id == "session-reported"


def test_task_context_survives_actual_goal_graph_hydration(db):
    task_id = "T-RELAY-ACTUAL-GOAL"
    _seed(db, task_id)
    repository = TaskGraphRepository(db)
    service = TaskGraphService(graph_repo=repository)

    service.initialize_graph(
        TaskInfo(
            task_id=task_id,
            task_spec=_task_spec(task_id),
            source_type="bot",
            owner_bot_id="B-1",
            execution_config={},
        )
    )
    service.update_task_node_info(
        TaskNodePatch(
            task_id=task_id,
            node_id=task_id,
            actual_goal=_task_spec(task_id).goal,
            local_acceptance_result=AcceptanceResult(
                verdict=AcceptanceVerdict.DONE,
                done_items=[{"id": "ac1", "passed": True}],
                gap_items=[],
            ),
            execution_decision="ACCEPTED",
            output_patch={"result": "relay-result"},
        )
    )

    with db.orm_session() as session:
        row = (
            session.query(TaskNodeRunInfoModel)
            .filter(
                TaskNodeRunInfoModel.task_id == task_id,
                TaskNodeRunInfoModel.node_id == task_id,
            )
            .one()
        )
        assert row.actual_goal == (
            '{"objective": "objective", "acceptances": '
            '[{"id": "ac1", "description": "done"}]}'
        )
        assert json.loads(row.acceptance_result) == {
            "verdict": "DONE",
            "done_items": [{"id": "ac1", "passed": True}],
            "gap_items": [],
        }

    hydrated = TaskGraphService(graph_repo=TaskGraphRepository(db))
    context = hydrated.get_task_context(task_id)
    assert [item.node_id for item in context.all_done_output] == [task_id]
    assert context.all_done_output[0].actual_goal.objective == "objective"
    assert context.all_done_output[0].output == {"result": "relay-result"}

    # Compatibility for graphs accepted before the actual_goal column existed.
    with db.orm_session() as session:
        row = (
            session.query(TaskNodeRunInfoModel)
            .filter(
                TaskNodeRunInfoModel.task_id == task_id,
                TaskNodeRunInfoModel.node_id == task_id,
            )
            .one()
        )
        row.actual_goal = None

    legacy_hydrated = TaskGraphService(graph_repo=TaskGraphRepository(db))
    legacy = legacy_hydrated.get_task_context(task_id)
    assert [item.node_id for item in legacy.all_done_output] == [task_id]
    assert legacy.all_done_output[0].actual_goal.objective == "objective"
