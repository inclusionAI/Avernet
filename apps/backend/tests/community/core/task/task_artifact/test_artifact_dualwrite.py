"""集中通道产物双轨写点:update_task_node_info fold 后 capture,_persist_locked drain 随图提交(spec §5.1/§5.2)。"""
from __future__ import annotations

import pytest

from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Status,
    TaskInfo,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.models import TaskArtifactModel
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    drain_published,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _task_info(task_id: str) -> TaskInfo:
    return TaskInfo(
        task_id=task_id,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        source_type="bot",
        owner_bot_id="B-1",
        execution_config={},
    )


def _service_with_repo(db, task_id: str) -> TaskGraphService:
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0,
            task_id=task_id,
            source_type="bot",
            owner_user_id="U-1",
            owner_bot_id="B-1",
            execution_config={},
            task_spec={"metadata": {"task_id": task_id}},
            status=Status.PENDING,
        )
    )
    repo = TaskGraphRepository(db)
    service = TaskGraphService(graph_repo=repo)
    service.initialize_graph(_task_info(task_id))
    return service


def _artifact_rows(db, task_id: str, node_id: str | None = None) -> list:
    with db.orm_session() as session:
        query = session.query(TaskArtifactModel).filter(TaskArtifactModel.task_id == task_id)
        if node_id is not None:
            query = query.filter(TaskArtifactModel.node_id == node_id)
        rows = query.order_by(TaskArtifactModel.id).all()
        return [row.to_record() for row in rows]


@pytest.fixture(autouse=True)
def _clean_contextvar():
    drain_published()
    yield
    drain_published()


def test_central_text_patch_persists_one_text_artifact(db):
    task_id = "T-DUAL"
    service = _service_with_repo(db, task_id)
    service.update_task_node_info(
        TaskNodePatch(
            task_id=task_id,
            node_id=task_id,
            assignee="bot-7",
            output_patch={"output": "分析完成,共 3 条结论"},
        )
    )
    # output dict 原样与现状一致(非目标:双首领)
    graph = service.query_task_dashboard(task_id)
    assert graph.tasks[0].run_info.output == {"output": "分析完成,共 3 条结论"}
    rows = _artifact_rows(db, task_id)
    assert len(rows) == 1
    assert rows[0].kind == "text"
    assert rows[0].content.text == "分析完成,共 3 条结论"
    assert rows[0].creator == "bot-7"
    assert rows[0].attempt == 0
    assert rows[0].node_id == task_id


def test_retry_keeps_old_attempt_and_adds_new(db):
    task_id = "T-RETRY"
    service = _service_with_repo(db, task_id)
    service.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, output_patch={"output": "第一轮成果"}, assignee="bot-7")
    )
    # harness 复位:attempt +1 且清空上一轮 output(集中通道既有语义)
    service.update_task_node_info(
        TaskNodePatch(
            task_id=task_id,
            node_id=task_id,
            status=Status.PENDING,
            extend_props_patch={"harness_retries": 1},
        )
    )
    service.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, output_patch={"output": "第二轮成果"}, assignee="bot-7")
    )
    rows = _artifact_rows(db, task_id)
    by_attempt = {(r.attempt, r.content.text) for r in rows}
    assert by_attempt == {(0, "第一轮成果"), (1, "第二轮成果")}


def test_version_conflict_replay_does_not_duplicate(db):
    task_id = "T-REPLAY"
    service = _service_with_repo(db, task_id)
    # 先 hydrate 缓存,再在背后推进共享库版本 → service 下一笔必过期冲突重放
    service.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, output_patch={"output": "warmup"}, assignee="bot-7")
    )
    repo = service._graph_repo
    graph = repo.load_graph(task_id)
    repo.save_graph(graph, expected_version=repo.get_version(task_id), runtime_status=Status.RUNNING, action_events=[])
    # 同一逻辑产出的重放:fold 后 capture 重复暂存,select-first 吸收
    service.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, output_patch={"output": "warmup"}, assignee="bot-7")
    )
    rows = [r for r in _artifact_rows(db, task_id) if r.kind == "text"]
    texts = [r.content.text for r in rows]
    assert texts.count("warmup") == 1


def test_graph_repo_absent_no_publish_no_crash():
    task_id = "T-NOREPO"
    service = TaskGraphService()
    graph = service.initialize_graph(_task_info(task_id))
    root = graph.tasks[0]
    root.run_info.extend_props["session_id"] = None
    service.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, output_patch={"output": "done"}, assignee="bot-7")
    )
    # 缺席态:暂存被 _persist_locked 丢弃,不发布也不报错
    assert drain_published() == []


def test_node_output_business_dict_records_no_artifact(db):
    task_id = "T-DICT"
    service = _service_with_repo(db, task_id)
    service.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, output_patch={"data": {"rows": 1}}, assignee="bot-7")
    )
    assert _artifact_rows(db, task_id) == []