"""Runtime reasons survive durable reads without leaking storage metadata."""

from agentclaw.community.adapters.http.task.schemas import graph_to_dto
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


def _create_graph(db, *, failure_reason=None, progress_reason=None):
    task_id = "T-RUNTIME-REASONS"
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="owner",
            owner_bot_id="bot",
            execution_config={},
            task_spec={},
            status=Status.HUNG,
        )
    )
    node = TaskNode(
        node_id=task_id,
        task_id=task_id,
        status=Status.HUNG,
        task_spec=TaskSpec(
            context=Context(title="reason", background=""),
            goal=Goal(objective="test", acceptances=[]),
        ),
        run_info=RuntimeInfo(
            failure_reason=failure_reason,
            progress_reason=progress_reason,
            extend_props={"session_id": "session-fixture"},
        ),
        node_run_graph=None,
    )
    graph = TaskExecutionGraph(
        task_id=task_id,
        run_id=1,
        loop_round=1,
        status=Status.HUNG,
        tasks=[node],
        extend_props={"hung_reason": "graph-level-reason"},
    )
    node.node_run_graph = graph
    repo = TaskGraphRepository(db)
    repo.create_graph(graph, runtime_status=Status.HUNG)
    return graph, repo


def test_runtime_reasons_survive_reload_and_dashboard_projection(db):
    graph, _ = _create_graph(
        db,
        failure_reason="bbs_disabled_and_no_candidate",
        progress_reason="无可用候选，规划失败",
    )
    # A fresh repository must read durable facts, not the original graph.
    restored = TaskGraphRepository(db).load_graph(graph.task_id)
    runtime = restored.tasks[0].run_info
    assert runtime.failure_reason == "bbs_disabled_and_no_candidate"
    assert runtime.progress_reason == "无可用候选，规划失败"
    assert (
        runtime.extend_props
        == graph.tasks[0].run_info.extend_props
        == {"session_id": "session-fixture"}
    )
    dashboard = graph_to_dto(restored).model_dump(mode="json")
    assert dashboard["status"] == "REVIEWING"
    info = dashboard["tasks"][0]["run_info"]
    assert info["failure_reason"] == "bbs_disabled_and_no_candidate"
    assert info["progress_reason"] == "无可用候选，规划失败"
    assert info["extend_props"] == {"session_id": "session-fixture"}


def test_runtime_reasons_are_updated_cleared_and_isolated_by_attempt(db):
    graph, repo = _create_graph(
        db, failure_reason="old failure", progress_reason="old progress"
    )
    runtime = graph.tasks[0].run_info
    runtime.failure_reason = "new failure"
    runtime.progress_reason = "new progress"
    repo.save_graph(
        graph, expected_version=1, runtime_status=Status.HUNG, action_events=[]
    )
    restored = TaskGraphRepository(db).load_graph(graph.task_id)
    assert restored.tasks[0].run_info.failure_reason == "new failure"
    assert restored.tasks[0].run_info.progress_reason == "new progress"

    runtime.extend_props["retry"] = 1
    runtime.failure_reason = None
    runtime.progress_reason = None
    repo.save_graph(
        graph, expected_version=2, runtime_status=Status.HUNG, action_events=[]
    )
    restored = TaskGraphRepository(db).load_graph(graph.task_id)
    assert restored.tasks[0].run_info.failure_reason is None
    assert restored.tasks[0].run_info.progress_reason is None
    assert restored.tasks[0].run_info.extend_props == {
        "session_id": "session-fixture",
        "retry": 1,
    }

    runtime.failure_reason = "temporary failure"
    runtime.progress_reason = "temporary progress"
    repo.save_graph(
        graph, expected_version=3, runtime_status=Status.HUNG, action_events=[]
    )
    runtime.failure_reason = None
    runtime.progress_reason = None
    repo.save_graph(
        graph, expected_version=4, runtime_status=Status.HUNG, action_events=[]
    )
    restored = TaskGraphRepository(db).load_graph(graph.task_id)
    assert restored.tasks[0].run_info.failure_reason is None
    assert restored.tasks[0].run_info.progress_reason is None


def test_historical_runtime_without_reason_metadata_does_not_guess_graph_reason(db):
    import json

    from agentclaw.community.core.task.repository.models import TaskNodeRunInfoModel

    graph, _ = _create_graph(db)
    # Seed the pre-fix storage shape: session metadata only, no runtime reasons.
    with db.orm_session() as session:
        row = session.query(TaskNodeRunInfoModel).filter_by(task_id=graph.task_id).one()
        row.extend_props = json.dumps({"session_id": "session-fixture"})
    restored = TaskGraphRepository(db).load_graph(graph.task_id)
    assert restored.extend_props["hung_reason"] == "graph-level-reason"
    assert restored.tasks[0].run_info.failure_reason is None
    assert restored.tasks[0].run_info.progress_reason is None
    assert restored.tasks[0].run_info.extend_props == {"session_id": "session-fixture"}
