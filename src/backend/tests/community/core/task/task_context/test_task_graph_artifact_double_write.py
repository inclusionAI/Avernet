"""阶段一 Artifact 双写测试(语雀《BCN 产物领域对象设计》§12 兼容迁移):

- output fold 伴生发布不可变 Artifact(Text/Structured 分支启发式)并回填
  ``run_info.output_artifact_ids`` / ``primary_output_artifact_id``;
- 未装配 ArtifactService 时行为与既有路径完全一致(纯内核零影响);
- 产物写入失败 log-and-continue,不中断既有回报链路;
- 外部 artifact_ids 通道 fold;
- 重试(attempt)变量隔离:不同 harness_retries 不覆盖上一 attempt 产物。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager

import pytest

from agentclaw.community.core.base import Base
import agentclaw.community.core.task.repository.models  # noqa: F401
from agentclaw.community.core.repository.implementations.task.artifact_repository import (
    TaskArtifactRepository,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    TaskSpec,
)
from agentclaw.community.core.task.task_artifact import ArtifactService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _node_of(svc: TaskGraphService, task_id: str, node_id: str) -> TaskNode:
    """读回节点引用(query_task_nodes 复用 D3 返回引用语义)。"""
    return svc.query_task_nodes(task_id, TaskNodeQueryCriteria(node_ids={node_id}))[0]


# ===== SQLite harness(mirror tests/community/repository/task/conftest.py)=====
class InMemorySqliteDB:
    def __init__(self, engine):
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


@pytest.fixture
def artifact_service():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    repo = TaskArtifactRepository(InMemorySqliteDB(eng))
    return ArtifactService(repository=repo, clock=lambda: 1780000000000)


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do it"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


def _graph_with_child(svc: TaskGraphService) -> TaskNode:
    svc.initialize_graph(_task_info())
    child = TaskNode(
        node_id="c1",
        task_id="t1",
        status=Status.PENDING,
        task_spec=_task_info().task_spec,
        run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-a"),
        node_run_graph=None,  # type: ignore[arg-type]
    )
    svc.add_task_nodes([child], parent_node_id="t1")
    node = _node_of(svc, "t1", "c1")
    node.run_info.extend_props["session_id"] = "s_1"
    node.run_info.extend_props["harness_retries"] = 0
    return node


def test_double_write_publishes_text_artifact_and_backfills_ids(svc_artifact):
    svc, artifact_service = svc_artifact
    node = _graph_with_child(svc)
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", status=Status.RUNNING)
    )
    # callback 归一形态:单值文本 → ArtifactContent::Text
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", status=Status.DONE, output_patch={"output": "存储行业尽调报告已完成"})
    )
    refreshed = _node_of(svc, "t1", "c1")
    assert refreshed.run_info.output == {"output": "存储行业尽调报告已完成"}  # 兼容投影无损
    assert len(refreshed.run_info.output_artifact_ids) == 1
    primary = refreshed.run_info.primary_output_artifact_id
    assert primary == refreshed.run_info.output_artifact_ids[0]
    artifact = artifact_service.get(primary)
    assert artifact.content.kind == "text"
    assert artifact.content.text == "存储行业尽调报告已完成"
    assert artifact.scope.task_id == "t1" and artifact.scope.node_id == "c1"
    assert artifact.scope.session_id == "s_1" and artifact.scope.attempt == 0
    assert artifact.created_by.to_dict() == {"actor_type": "bot", "actor_id": "bot-a"}


def test_double_write_structured_content_for_non_text_patch(svc_artifact):
    svc, artifact_service = svc_artifact
    node = _graph_with_child(svc)
    svc.update_task_node_info(
        TaskNodePatch(
            task_id="t1", node_id="c1", status=Status.DONE,
            output_patch={"result": "all_done", "score": 62},
        )
    )
    refreshed = _node_of(svc, "t1", "c1")
    artifact = artifact_service.get(refreshed.run_info.primary_output_artifact_id)
    assert artifact.content.kind == "structured"  # 非单值文本 → Structured(JSON)
    assert artifact.content.value == {"result": "all_done", "score": 62}


def test_double_write_is_idempotent_on_replayed_report(svc_artifact):
    svc, artifact_service = svc_artifact
    _graph_with_child(svc)
    patch = TaskNodePatch(task_id="t1", node_id="c1", status=Status.DONE, output_patch={"output": "正文"})
    svc.update_task_node_info(patch)
    svc.update_task_node_info(patch)  # 事件重放/重报
    refreshed = _node_of(svc, "t1", "c1")
    assert len(refreshed.run_info.output_artifact_ids) == 1  # 确定性 id:同内容不重复
    assert len(artifact_service.list_by_task("t1")) == 1


def test_unbound_artifact_service_keeps_legacy_behavior():
    svc = TaskGraphService()  # 未 bind_artifact_service(纯内核路径)
    _graph_with_child(svc)
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", status=Status.DONE, output_patch={"output": "正文"})
    )
    refreshed = _node_of(svc, "t1", "c1")
    assert refreshed.run_info.output == {"output": "正文"}
    assert refreshed.run_info.output_artifact_ids == []
    assert refreshed.run_info.primary_output_artifact_id is None


def test_artifact_failure_is_log_and_continue(svc_artifact):
    """产物发布抛错(含仓储异常)不得中断 output fold(阶段一兼容优先)。"""
    svc, artifact_service = svc_artifact

    class _Boom:
        def publish(self, **kw):
            raise RuntimeError("storage down")

    _graph_with_child(svc)
    svc.bind_artifact_service(_Boom())
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", status=Status.DONE, output_patch={"output": "正文"})
    )
    refreshed = _node_of(svc, "t1", "c1")
    assert refreshed.run_info.output == {"output": "正文"}      # fold 照常
    assert refreshed.run_info.output_artifact_ids == []          # 产物字段留空
    assert refreshed.run_info.primary_output_artifact_id is None


def test_external_artifact_ids_fold_with_primary_default(svc_artifact):
    svc, _ = svc_artifact
    _graph_with_child(svc)
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", artifact_ids=["art_ext_1", "art_ext_1"])
    )
    refreshed = _node_of(svc, "t1", "c1")
    assert refreshed.run_info.output_artifact_ids == ["art_ext_1"]  # 去重
    assert refreshed.run_info.primary_output_artifact_id == "art_ext_1"


def test_attempt_variance_keeps_retry_artifacts_distinct(svc_artifact):
    """scope.attempt 显式保存:harness 重试后新一轮产出不覆盖上一 attempt 的产物(文档 §4.3)。"""
    svc, artifact_service = svc_artifact
    _graph_with_child(svc)
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", status=Status.RUNNING)
    )
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", output_patch={"output": "首轮产出"})
    )
    # harness 复位重投:重试计数 bump → attempt=1,新内容
    node = _node_of(svc, "t1", "c1")
    node.run_info.extend_props["harness_retries"] = 1
    svc.update_task_node_info(
        TaskNodePatch(task_id="t1", node_id="c1", output_patch={"output": "重试产出"}, status=Status.RUNNING)
    )
    refreshed = _node_of(svc, "t1", "c1")
    assert len(refreshed.run_info.output_artifact_ids) == 2
    artifacts = artifact_service.list_by_task("t1")
    assert {a.scope.attempt for a in artifacts} == {0, 1}
    first, second = sorted(artifacts, key=lambda a: a.scope.attempt)
    assert first.content.text == "首轮产出" and second.content.text == "重试产出"
    # primary 语义:最新发布者(最终输出/UI 主展示)
    assert refreshed.run_info.primary_output_artifact_id == second.artifact_id


@pytest.fixture
def svc_artifact(artifact_service):
    """(graph service, artifact service) with the seam bound."""
    svc = TaskGraphService()
    svc.bind_artifact_service(artifact_service)
    return svc, artifact_service