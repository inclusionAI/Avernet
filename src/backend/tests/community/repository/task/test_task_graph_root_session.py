"""Persistence coverage for the root node's initial session identity."""

from __future__ import annotations

from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Status,
    TaskInfo,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.models import TaskNodeRunInfoModel
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
