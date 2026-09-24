"""PR3 写侧双写测试:publish_output 分派/READY 闸/supersedes 链/失败策略,
以及 TaskGraphService fold fire 点(幂等/attempt 隔离/图级/未接线 no-op)。

服务层 fakes 直构注入("no DI" 风格,样板 test_trajectory_service.py);
图集成用内存 graph_service + 真 in-memory SQLite 仓储仓(repository/task conftest
同款 InMemorySqliteDB)。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from agentclaw.community.core.task.domain.errors import TaskArtifactPublishError
from agentclaw.community.core.task.domain.models import (
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
    Context,
    Goal,
    TaskGraphPatch,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_service import (
    TaskArtifactService,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _MemArtifactRepo:
    """TaskArtifactRepositoryProtocol 的内存实现(dedupe/排序契约复刻仓储测试)。"""

    def __init__(self):
        self.rows: list = []
        self._next_id = 0
        # 敌意开关:countdown 次调用后 INSERT 抛错
        self.fail_on_create: int | None = None

    def create_or_get(self, record):
        if self.fail_on_create is not None and self.fail_on_create > 0:
            self.fail_on_create -= 1
            raise RuntimeError("db down")
        for row in self.rows:
            same_key = (
                row["task_id"] == record.task_id
                and row["node_id"] == record.node_id
                and row["attempt"] == record.attempt
                and row["content_hash"] == record.content_hash
            )
            if same_key or row["artifact_id"] == record.artifact_id:
                return row["record"]
        self._next_id += 1
        stored = replace(record, id=self._next_id)
        self.rows.append({
            "task_id": record.task_id, "node_id": record.node_id,
            "attempt": record.attempt, "content_hash": record.content_hash,
            "artifact_id": record.artifact_id, "record": stored,
        })
        return stored

    def get_by_artifact_id(self, artifact_id):
        for row in self.rows:
            if row["artifact_id"] == artifact_id:
                return row["record"]
        return None

    def list_by_node(self, task_id, node_id, *, attempt=None):
        rows = [r["record"] for r in self.rows
                if r["task_id"] == task_id and r["node_id"] == node_id]
        if attempt is not None:
            rows = [r for r in rows if r.attempt == attempt]
        return sorted(
            rows,
            key=lambda r: (r.attempt, r.created_at, r.id),
            reverse=True,
        )

    def list_by_task(self, task_id):
        return sorted(
            [r["record"] for r in self.rows if r["task_id"] == task_id],
            key=lambda r: (r.attempt, r.created_at, r.id),
            reverse=True,
        )

    def latest_for_node(self, task_id, node_id, *, kind=None):
        rows = self.list_by_node(task_id, node_id)
        if kind is not None:
            rows = [r for r in rows if r.artifact_kind == kind]
        return rows[0] if rows else None


@dataclass
class _MemSessionFile:
    resource_id: str
    status: str = "ready"
    display_name: str = "report.pdf"
    size_bytes: int | None = 183420
    client_content_hash: str | None = "sha256:" + "f" * 64
    filename: str = "report.pdf"


class _MemSessionResources:
    def __init__(self, *records: _MemSessionFile):
        self.by_id = {r.resource_id: r for r in records}

    def get_by_resource_id(self, resource_id):
        return self.by_id.get(resource_id)


# ---------------------------------------------------------------------------
# publish_output:分派 / READY 闸 / 幂等 / supersedes / 失败策略
# ---------------------------------------------------------------------------


def _svc(repo=None, session_files=None) -> TaskArtifactService:
    return TaskArtifactService(
        repo=repo or _MemArtifactRepo(),
        session_resource_repo=_MemSessionResources(*session_files or ()),
    )


@pytest.mark.unit
def test_publish_output_text_single_row_with_derived_kind():
    repo = _MemArtifactRepo()
    n = _svc(repo).publish_output(
        "t1", "n1", 0, {"output": "# 报告\n全文"},
        created_by="bot-a",
    )
    assert n == 1
    row = repo.list_by_node("t1", "n1")[0]
    assert row.artifact_kind == "node_result"
    assert row.content_kind == "text"
    assert row.created_by == "bot-a"
    assert "全文" in row.content["text"]


@pytest.mark.unit
def test_publish_output_control_shapes_classified_internal_control():
    repo = _MemArtifactRepo()
    _svc(repo).publish_output("t1", "n1", 0, {"skipped": True})
    _svc(repo).publish_output("t1", "n2", 0,
                              {"notify_result": {"sent": 1}})
    kinds = [r.artifact_kind
             for r in repo.list_by_task("t1")]
    assert set(kinds) == {"internal_control"}


@pytest.mark.unit
def test_publish_output_file_ready_gate():
    """READY → File 分支落库;未就绪/缺失 → 拒该候选 + WARN,不吞整批。"""
    repo = _MemArtifactRepo()
    ready = _MemSessionFile("sr_ready")
    svc = _svc(repo, session_files=[ready, _MemSessionFile("sr_sync", status="device_syncing")])
    output = {
        "output": "文本产出",
        "file": {"resource_id": "sr_ready", "file_name": "report.pdf",
                 "size_bytes": 123},
        "not_ready": {"resource_id": "sr_sync", "file_name": "ghost.pdf"},
        "MISSING": {"resource_id": "sr_missing"},
    }
    n = svc.publish_output("t1", "n1", 0, output)
    # 1 Text + 1 File(仅 ready)
    assert n == 2
    rows = repo.list_by_node("t1", "n1")
    file_rows = [r for r in rows if r.content_kind == "file"]
    assert len(file_rows) == 1
    assert file_rows[0].content["resource_id"] == "sr_ready"
    # 元数据从 ref 优先、record 兜底
    assert file_rows[0].content["size_bytes"] == 123
    assert file_rows[0].content["sha256"].startswith("sha256:")


@pytest.mark.unit
def test_publish_output_idempotent_same_content():
    """同 (task,node,attempt) 同内容重复发布 → 一行(dedupe)。"""
    repo = _MemArtifactRepo()
    svc = _svc(repo)
    svc.publish_output("t1", "n1", 0, {"output": "x"})
    svc.publish_output("t1", "n1", 0, {"output": "x"})
    assert len(repo.list_by_node("t1", "n1")) == 1


@pytest.mark.unit
def test_publish_output_evolution_builds_supersedes_chain():
    """同 attempt 内容演进 → 新行 supersedes 旧行;旧行保留。"""
    repo = _MemArtifactRepo()
    svc = _svc(repo)
    v1 = svc.publish_output("t1", "n1", 0, {"output": "v1"})
    v2 = svc.publish_output("t1", "n1", 0, {"output": "v2"})
    assert v1 == v2 == 1
    rows = repo.list_by_node("t1", "n1")         # 新在前
    assert rows[0].supersedes == rows[1].artifact_id
    assert rows[1].supersedes is None


@pytest.mark.unit
def test_publish_output_attempt_isolation():
    """重试(新 attempt)→ 新系列,不覆盖、不 supersede 上一 attempt。"""
    repo = _MemArtifactRepo()
    svc = _svc(repo)
    svc.publish_output("t1", "n1", 0, {"output": "v0"})
    svc.publish_output("t1", "n1", 1, {"output": "v1"})
    rows = repo.list_by_node("t1", "n1")
    assert len(rows) == 2
    assert all(r.supersedes is None for r in rows)   # 跨 attempt 不成链
    assert {r.attempt for r in rows} == {0, 1}


@pytest.mark.unit
def test_publish_output_centralized_failure_raises():
    repo = _MemArtifactRepo()
    repo.fail_on_create = 1
    with pytest.raises(TaskArtifactPublishError):
        _svc(repo).publish_output("t1", "n1", 0, {"output": "x"}, relay_mode=False)


@pytest.mark.unit
def test_publish_output_relay_failure_degrades_to_warning():
    """spec 偏离记录:relay 模式持久化失败降 WARNING,不阻断收口。"""
    repo = _MemArtifactRepo()
    repo.fail_on_create = 1
    assert _svc(repo).publish_output("t1", "n1", 0, {"output": "x"},
                                     relay_mode=True) == 0


@pytest.mark.unit
def test_publish_output_empty_or_non_dict_is_noop():
    assert _svc().publish_output("t1", "n1", 0, {}) == 0
    assert _svc().publish_output("t1", "n1", 0, "x") == 0   # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TaskGraphService fold fire(集成)
# ---------------------------------------------------------------------------


def _spec(task_id):
    return TaskSpec(
        context=Context(title=task_id, background="bg"),
        goal=Goal(objective=task_id, acceptances=[]),
    )


def _graph_service(artifact_repo=None, bind_artifacts=True) -> TaskGraphService:
    svc = TaskGraphService()
    if bind_artifacts:
        svc.bind_artifact_service(TaskArtifactService(repo=artifact_repo or _MemArtifactRepo()))
    return svc


def _init_graph(svc: TaskGraphService, task_id="t-art"):
    svc.initialize_graph(TaskInfo(
        task_id=task_id, task_spec=_spec(task_id),
        source_type="bot", owner_bot_id="B",
    ))
    # 造一个 PENDING 子节点(target 供 fold)
    graph = svc._graphs[task_id]
    child = TaskNode(
        node_id="n1", task_id=task_id, status=Status.PENDING,
        task_spec=_spec(task_id), run_info=RuntimeInfo(),
        node_run_graph=graph,
    )
    graph.tasks.append(child)
    graph.relations.append(Relation(
        src_id=task_id, dst_id="n1", type=RelationType.DEPENDENCY))
    return svc


@pytest.mark.unit
def test_node_fold_fires_text_artifact_after_persist():
    repo = _MemArtifactRepo()
    svc = _init_graph(_graph_service(artifact_repo=repo), "t-fire")
    svc.update_task_node_info(TaskNodePatch(
        task_id="t-fire", node_id="n1", status=Status.DONE,
        output_patch={"output": "行业分析全文"},
        acceptance_result=None,
    ))
    rows = repo.list_by_node("t-fire", "n1")
    assert len(rows) == 1 and rows[0].content_kind == "text"
    assert rows[0].artifact_kind == "node_result"


@pytest.mark.unit
def test_node_fold_without_output_patch_fires_nothing():
    repo = _MemArtifactRepo()
    svc = _init_graph(_graph_service(artifact_repo=repo), "t-noop")
    svc.update_task_node_info(TaskNodePatch(
        task_id="t-noop", node_id="n1", status=Status.RUNNING,
    ))
    assert repo.rows == []


@pytest.mark.unit
def test_unwired_artifact_service_skips_with_info():
    """None → fire 点跳过,主流程照常(路径不为 None 才 fire)。"""
    svc = _init_graph(TaskGraphService(), "t-unwired")
    result = svc.update_task_node_info(TaskNodePatch(
        task_id="t-unwired", node_id="n1", status=Status.DONE,
        output_patch={"output": "x-无接线"},
    ))
    assert result.success


@pytest.mark.unit
def test_repeated_fold_same_patch_is_single_row():
    """fold×N(同 patch 重放/重投)→ dedupe 恰一行。"""
    repo = _MemArtifactRepo()
    svc = _init_graph(_graph_service(artifact_repo=repo), "t-idem")
    patch = TaskNodePatch(
        task_id="t-idem", node_id="n1", status=Status.DONE,
        output_patch={"output": "同容"},
    )
    for _ in range(3):
        svc.update_task_node_info(patch)
    assert len(repo.list_by_node("t-idem", "n1")) == 1


@pytest.mark.unit
def test_harness_retry_new_attempt_artifacts_isolated():
    repo = _MemArtifactRepo()
    svc = _init_graph(_graph_service(artifact_repo=repo), "t-retry")
    svc.update_task_node_info(TaskNodePatch(
        task_id="t-retry", node_id="n1", status=Status.RUNNING,
        output_patch={"output": "一次执行"},
        extend_props_patch={"harness_retries": 0},
    ))
    # harness 复位:attempt+1 后再产出
    svc.update_task_node_info(TaskNodePatch(
        task_id="t-retry", node_id="n1", status=Status.RUNNING,
        output_patch={"output": "二次执行"},
        extend_props_patch={"harness_retries": 1},
    ))
    rows = repo.list_by_task("t-retry")
    assert {r.attempt for r in rows} == {0, 1}
    assert len([r for r in rows if r.attempt == 1]) == 1
    # 均无 supersedes(跨 attempt 不成链)
    assert all(r.supersedes is None for r in rows)


@pytest.mark.unit
def test_graph_level_fold_fires_rollup_artifact_on_root():
    repo = _MemArtifactRepo()
    svc = _init_graph(_graph_service(artifact_repo=repo), "t-root")
    svc.update_task_graph_info("t-root", TaskGraphPatch(
        output_patch={"result": "all_done"},
        loop_round_increment=1,
    ))
    rows = repo.list_by_task("t-root")
    assert len(rows) == 1
    assert rows[0].artifact_kind == "graph_rollup"
    assert rows[0].node_id == "t-root"      # root(无入边)承接
    assert rows[0].attempt == 1             # loop_round 口径