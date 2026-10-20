"""relay 通道产物写点:apply_relay_report 两写点 capture、DECLINED 夹带整体拒绝(spec §5.1)。"""
from __future__ import annotations

import pytest

from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.errors import TaskStateError
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
from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.task_context.relay_report import apply_relay_report
from agentclaw.community.core.task.task_context.task_artifact import CodeContent, FileContent, StorageType
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import drain_published
from agentclaw.community.core.task.task_context.task_artifact.report_item import ArtifactReportItem
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

TASK_ID = "T-RELAY"
NODE_ID = "T-RELAY"


def _relay_graph_service(db) -> TaskGraphService:
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=TASK_ID,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": TASK_ID}},
            status=Status.PENDING,
        )
    )
    graph = TaskExecutionGraph(
        task_id=TASK_ID,
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        output={},
        extend_props={
            "execution_config": {"orchestration_mode": "relay", "MAX_LOOP": 3},
            "runtime_profile": {"allowed_run_modes": ["single_bot", "bbs"]},
        },
    )
    root = TaskNode(
        node_id=NODE_ID,
        task_id=TASK_ID,
        status=Status.RUNNING,
        task_spec=TaskSpec(Context("background", title="root"), Goal("finish", [])),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee="bot-1",
            extend_props={"relay_token": "attempt-1"},
        ),
        node_run_graph=graph,
    )
    graph.tasks.append(root)
    repo = TaskGraphRepository(db)
    repo.create_graph(graph, runtime_status=Status.RUNNING)
    service = TaskGraphService(graph_repo=repo)
    return service


def _file_item(attempt=0) -> ArtifactReportItem:
    return ArtifactReportItem(
        artifact_name=None,
        artifact_kind="file",
        session_id="sess-9",
        task_id=TASK_ID,
        node_id=NODE_ID,
        attempt=attempt,
        content=FileContent(
            storage_type=StorageType.BOT_WORKSPACE,
            storage_id="reports/analysis-report.md",
            file_type="text/markdown",
            file_name="analysis-report.md",
            file_id=None,
        ),
    )


def _code_item(attempt=0) -> ArtifactReportItem:
    return ArtifactReportItem(
        artifact_name=None,
        artifact_kind="code",
        session_id="sess-9",
        task_id=TASK_ID,
        node_id=NODE_ID,
        attempt=attempt,
        content=CodeContent(
            code_base_name="avernet",
            code_base_address="https://antcode.example.com/avernet",
            pr_link="https://antcode.example.com/pr/1",
            pr_desc="",
        ),
    )


def _execution_command(patch: TaskNodePatch, *, event_id="exec-1") -> TaskReportCommand:
    return TaskReportCommand(
        task_id=TASK_ID,
        node_id=NODE_ID,
        event_id=event_id,
        relay_token="attempt-1",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=patch,
    )


def _accepted_patch(artifact_patch=None) -> TaskNodePatch:
    return TaskNodePatch(
        task_id=TASK_ID,
        node_id=NODE_ID,
        execution_decision="ACCEPTED",
        actual_goal=Goal("cover part", []),
        local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        output_patch={"result": "evidence"},
        artifact_patch=artifact_patch,
    )


def _artifact_rows(db) -> list:
    with db.orm_session() as session:
        rows = (
            session.query(TaskArtifactModel)
            .filter(TaskArtifactModel.task_id == TASK_ID)
            .order_by(TaskArtifactModel.id)
            .all()
        )
        return [row.to_record() for row in rows]


@pytest.fixture(autouse=True)
def _clean_contextvar():
    drain_published()
    yield
    drain_published()


def test_accepted_execution_with_artifacts_persists_two_rows(db):
    service = _relay_graph_service(db)
    apply_relay_report(
        service,
        _execution_command(_accepted_patch([_file_item(), _code_item()])),
    )
    rows = _artifact_rows(db)
    kinds = sorted(r.kind for r in rows)
    # file + code + output({"result": "evidence"}) 派生 text
    assert kinds == ["code", "file", "text"]
    assert all(r.node_id == NODE_ID for r in rows)
    # 图未绑定会话:上报 item 采纳其 session_id(§3.3 兜底);派生 text 无上报来源 → None
    text_row = next(r for r in rows if r.kind == "text")
    assert text_row.scope_session_id is None
    assert {r.scope_session_id for r in rows if r.kind != "text"} == {"sess-9"}


def test_declined_execution_with_artifacts_rejected_wholesale(db):
    service = _relay_graph_service(db)
    command = _execution_command(
        TaskNodePatch(
            task_id=TASK_ID,
            node_id=NODE_ID,
            execution_decision="DECLINED",
            failure_reason="capability_mismatch",
            artifact_patch=[_file_item()],
        )
    )
    with pytest.raises(TaskStateError):
        apply_relay_report(service, command)
    assert _artifact_rows(db) == []
    assert drain_published() == []


def test_attempt_mismatch_rejects_whole_callback(db):
    service = _relay_graph_service(db)
    command = _execution_command(_accepted_patch([_file_item(attempt=7)]))
    with pytest.raises(Exception):
        apply_relay_report(service, command)
    assert _artifact_rows(db) == []


def test_completion_replay_does_not_duplicate(db):
    service = _relay_graph_service(db)
    apply_relay_report(service, _execution_command(_accepted_patch([_file_item(), _code_item()])))
    before = {r.artifact_id for r in _artifact_rows(db)}
    # completion 写点 capture 在同一份 fold 后 output 上 → select-first 吸收
    apply_relay_report(
        service,
        TaskReportCommand(
            task_id=TASK_ID,
            node_id=NODE_ID,
            event_id="plan-1",
            relay_token="attempt-1",
            event_type=TaskReportEventType.PLAN_RESULT,
            event=None,
            task_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
        ),
    )
    after = {r.artifact_id for r in _artifact_rows(db)}
    assert after == before


def test_event_without_artifacts_unchanged(db):
    """无 artifacts 的 relay 报文:行为与改前一致,仅 output 派生 text 一条(Review Focus #4)。"""

    service = _relay_graph_service(db)
    result = apply_relay_report(service, _execution_command(_accepted_patch()))
    assert result.status.value == "APPLIED"
    rows = _artifact_rows(db)
    assert len(rows) == 1
    assert rows[0].kind == "text"
    assert rows[0].content.text == "evidence"