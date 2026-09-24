"""PR4 读侧 Descriptor 测试:服务读方法 / DTO(artifact_to_dto + graph_to_dto 双轨)/
轨迹末位事件 artifacts 读时富化 / HTML 产物块 / analyzer bot 消息投影 / dashboard 路由透传。

spec:``src/backend/specs/2026-09-23-task-artifact-manifest/spec.md`` §4(读侧 Descriptor):
双轨并存(老 ``output`` 字段不动)、``is_primary``=latest_for_node、读时富化不落库、
无新端点。服务层 fakes 直构注入("no DI" 风格,样板 test_trajectory_service.py /
本目录 test_artifact_dualwrite.py);路由走 TestClient + injector 级 stub(样板
tests/community/adapters/http/task/test_dashboard_assignee_bot_info.py,该 harness 的
stub module 已同步补 TaskArtifactServiceProtocol 空数据绑定)。
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.task.router import (
    router as task_router,
)
from agentclaw.community.adapters.http.task.router import (
    router as task_internal_router,
)
from agentclaw.community.adapters.http.task.schemas import (
    TaskTrajectoryDTO,
    TrajectoryEventDTO,
    artifact_to_dto,
    graph_to_dto,
    task_context_to_dto,
    trajectory_to_dto,
)
from agentclaw.community.adapters.http.task.trajectory_html import (
    render_trajectory_html,
)
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactServiceProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    DoneOutput,
    Goal,
    RuntimeInfo,
    Status,
    TaskContext,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.serializers import (
    runtime_from_dict,
    runtime_to_dict,
)
from agentclaw.community.core.task.repository.types import (
    TaskArtifactRecord,
    TrajectoryEventRecord,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_service import (
    TaskArtifactService,
)
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    ArtifactLineage,
    ArtifactScope,
    FileArtifactContent,
    TaskArtifact,
    TextArtifactContent,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    epoch_ms_to_storage_datetime,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
    _timeline_fingerprint,
)


# ---------------------------------------------------------------------------
# fakes — 服务直构(no DI)
# ---------------------------------------------------------------------------


class _MemArtifactRepo:
    """TaskArtifactRepositoryProtocol 内存实现(排序契约复刻 dualwrite 测试)。"""

    def __init__(self):
        self.rows: list = []
        self._next_id = 0
        self.latest_calls: list[tuple] = []

    def create_or_get(self, record):
        for row in self.rows:
            if row.artifact_id == record.artifact_id:
                return row
        self._next_id += 1
        stored = replace(record, id=self._next_id)
        self.rows.append(stored)
        return stored

    @staticmethod
    def _sorted(rows):
        return sorted(rows, key=lambda r: (r.attempt, r.created_at, r.id), reverse=True)

    def get_by_artifact_id(self, artifact_id):
        return next((r for r in self.rows if r.artifact_id == artifact_id), None)

    def list_by_node(self, task_id, node_id, *, attempt=None):
        rows = [r for r in self.rows if r.task_id == task_id and r.node_id == node_id]
        if attempt is not None:
            rows = [r for r in rows if r.attempt == attempt]
        return self._sorted(rows)

    def list_by_task(self, task_id):
        return self._sorted([r for r in self.rows if r.task_id == task_id])

    def latest_for_node(self, task_id, node_id, *, kind=None):
        self.latest_calls.append((task_id, node_id, kind))
        rows = self.list_by_node(task_id, node_id)
        if kind is not None:
            rows = [r for r in rows if r.artifact_kind == kind]
        return rows[0] if rows else None


def _record(
    artifact_id,
    *,
    task_id="t1",
    node_id="n1",
    attempt=0,
    kind="node_result",
    content_kind="text",
    content=None,
    created_at=1000,
) -> TaskArtifactRecord:
    return TaskArtifactRecord(
        id=0,
        artifact_id=artifact_id,
        task_id=task_id,
        node_id=node_id,
        attempt=attempt,
        artifact_kind=kind,
        content_kind=content_kind,
        content=content if content is not None else {
            "kind": "text", "text": f"row-{artifact_id}", "media_type": "text/plain",
        },
        content_hash=f"sha256:{artifact_id}",
        created_at=created_at,
    )


def _text_art(artifact_id, *, node_id="n1", attempt=0, text=None, supersedes=None):
    return TaskArtifact(
        artifact_id=artifact_id,
        kind=ArtifactKind.NODE_RESULT,
        scope=ArtifactScope(task_id="t1", node_id=node_id, attempt=attempt),
        content=TextArtifactContent(
            text=text if text is not None else f"text-{artifact_id}",
            media_type="text/markdown",
        ),
        lineage=ArtifactLineage(derived_from=[], supersedes=supersedes),
        created_by="bot-a",
        created_at=1700,
    )


def _file_art(artifact_id, *, node_id="n1", attempt=0):
    return TaskArtifact(
        artifact_id=artifact_id,
        kind=ArtifactKind.GRAPH_ROLLUP,
        scope=ArtifactScope(task_id="t1", node_id=node_id, attempt=attempt),
        content=FileArtifactContent(
            resource_id=f"sr_{artifact_id.removeprefix('art_')}",
            file_name="report.pdf",
            media_type="application/pdf",
            size_bytes=183420,
            sha256="sha256:" + "f" * 64,
        ),
        created_by="system",
        created_at=1800,
    )


class _StubReadArtifactService:
    """读侧脚本 stub:按 task 过滤返回领域 TaskArtifact;publish no-op。"""

    def __init__(self, artifacts=None):
        self._artifacts = artifacts or []
        self.list_calls: list[str] = []

    def publish_output(self, *args, **kwargs):  # noqa: ANN002 读侧测试不触写面
        return 0

    def list_artifacts_for_task(self, task_id):
        self.list_calls.append(task_id)
        return [a for a in self._artifacts if a.scope.task_id == task_id]

    def primary_artifact_ids_by_node(self, task_id):
        primary: dict[str, str] = {}
        for a in self._artifacts:
            if a.scope.task_id != task_id:
                continue
            # 桶序即 latest_for_node 读序 → 每 node 首行为 primary
            primary.setdefault(a.scope.node_id, a.artifact_id)
        return primary


def _svc(repo=None) -> TaskArtifactService:
    return TaskArtifactService(repo=repo or _MemArtifactRepo())


# ---------------------------------------------------------------------------
# ① 服务读方法:list_artifacts_for_task / primary_artifact_ids_by_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_artifacts_for_task_projects_records_to_domain():
    repo = _MemArtifactRepo()
    repo.create_or_get(_record("art_a", node_id="n1", created_at=1000))
    repo.create_or_get(_record("art_b", node_id="n2", created_at=2000))
    arts = _svc(repo).list_artifacts_for_task("t1")
    assert [a.artifact_id for a in arts] == ["art_b", "art_a"]  # created_at DESC
    assert all(isinstance(a, TaskArtifact) for a in arts)
    assert arts[0].kind is ArtifactKind.NODE_RESULT
    assert arts[0].content.text == "row-art_b"


@pytest.mark.unit
def test_list_artifacts_for_task_skips_corrupt_row_without_swallowing_batch():
    """坏 content 行:WARNING 跳过该行,其余行照常投影(不吞整批,不静默进领域)。"""
    repo = _MemArtifactRepo()
    repo.create_or_get(_record("art_ok", created_at=1000))
    repo.create_or_get(_record(
        "art_bad", content={"kind": "bogus", "text": "!"}, content_kind="bogus",
        created_at=2000,
    ))
    arts = _svc(repo).list_artifacts_for_task("t1")
    assert [a.artifact_id for a in arts] == ["art_ok"]


@pytest.mark.unit
def test_primary_artifact_ids_by_node_latest_without_kind_filter():
    """primary = latest_for_node(kind 不传);attempt 大者胜;无产物节点不进 dict。"""
    repo = _MemArtifactRepo()
    repo.create_or_get(_record("art_v0", node_id="n1", attempt=0, created_at=1000))
    repo.create_or_get(_record("art_v1", node_id="n1", attempt=1, created_at=900))
    repo.create_or_get(_record(
        "art_ctrl", node_id="n2", kind="internal_control", created_at=3000,
    ))
    primary = _svc(repo).primary_artifact_ids_by_node("t1")
    assert primary == {"n1": "art_v1", "n2": "art_ctrl"}
    # latest_for_node 调用未传 kind(internal_control 行才可能人选)
    assert [call for call in repo.latest_calls] == [
        ("t1", "n1", None), ("t1", "n2", None),
    ]
    # 无产物(空仓/未知 task)→ 空 dict
    assert _svc(_MemArtifactRepo()).primary_artifact_ids_by_node("ghost") == {}


# ---------------------------------------------------------------------------
# ② DTO:artifact_to_dto(Text 截断 / File 元数据 / is_primary 双态)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_artifact_to_dto_text_projection_truncates_over_4096():
    dto = artifact_to_dto(_text_art("art_long", text="x" * 5000))
    assert dto.text is not None
    assert len(dto.text) == 4097  # 4096 字符 + 省略号
    assert dto.text.endswith("…")
    assert dto.content_kind == "text"
    assert dto.kind == "node_result"
    assert dto.media_type == "text/markdown"
    assert dto.file is None
    assert dto.attempt == 0 and dto.created_by == "bot-a" and dto.created_at == 1700


@pytest.mark.unit
def test_artifact_to_dto_text_under_limit_kept_verbatim():
    assert artifact_to_dto(_text_art("art_short", text="# 报告\n全文")).text == (
        "# 报告\n全文"
    )


@pytest.mark.unit
def test_artifact_to_dto_file_metadata_only_no_url_shape():
    dto = artifact_to_dto(_file_art("art_file"))
    assert dto.text is None
    assert dto.file == {
        "resource_id": "sr_file",
        "file_name": "report.pdf",
        "size_bytes": 183420,
        "sha256": "sha256:" + "f" * 64,
    }
    assert dto.content_kind == "file"
    assert dto.kind == "graph_rollup"
    assert dto.media_type == "application/pdf"
    # File 分支不携带任何 URL/handle 形态键
    assert not (set(dto.file or {}) & {"url", "download_url", "storage_key"})


@pytest.mark.unit
def test_artifact_to_dto_is_primary_dual_state():
    art = _text_art("art_p", supersedes="art_old")
    assert artifact_to_dto(art).is_primary is False
    assert artifact_to_dto(art, is_primary=True).is_primary is True
    dto = artifact_to_dto(art, is_primary=True)
    assert dto.supersedes == "art_old" and dto.derived_from == []


# ---------------------------------------------------------------------------
# ③ graph_to_dto 双轨:老 output 原样 + artifacts 分桶
# ---------------------------------------------------------------------------


def _graph_with_node(task_id="t1", node_id="n1", output=None):
    graph = TaskExecutionGraph(run_id=1, loop_round=0, status=Status.DONE)
    node = TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.DONE,
        task_spec=TaskSpec(
            context=Context(title="t", background=""),
            goal=Goal(objective="o", acceptances=[]),
        ),
        run_info=RuntimeInfo(output=dict(output) if output else {}, extend_props={}),
        node_run_graph=graph,
    )
    graph.tasks.append(node)
    return graph


@pytest.mark.unit
def test_graph_to_dto_dual_track_keeps_legacy_output_and_buckets_artifacts():
    """双轨:老 ``run_info.output``(单键展平)原样保留,artifacts 分桶新增。"""
    graph = _graph_with_node(output={"output": {"result": "老轨产出"}})
    dto = graph_to_dto(
        graph,
        artifacts_by_node={"n1": [_text_art("art_new"), _file_art("art_file")]},
        primary_artifact_ids={"art_new"},
    )
    node = dto.tasks[0]
    # 老轨不动:单键 output 展平照旧
    assert node.run_info.output == {"result": "老轨产出"}
    # 产物轨:分桶 + primary 集合标记
    assert [a.artifact_id for a in node.artifacts] == ["art_new", "art_file"]
    assert node.artifacts[0].is_primary is True
    assert node.artifacts[1].is_primary is False
    assert node.artifacts[1].file["resource_id"] == "sr_file"


@pytest.mark.unit
def test_graph_to_dto_without_artifacts_keeps_legacy_calls_unchanged():
    """artifacts_by_node=None(缺省)→ 各节点 artifacts=[](既有调用零变化)。"""
    dto = graph_to_dto(_graph_with_node(output={"k": "v"}))
    assert dto.tasks[0].artifacts == []
    assert dto.tasks[0].run_info.output == {"k": "v"}


@pytest.mark.unit
def test_graph_to_dto_primary_defaults_to_bucket_first():
    """primary 集合缺省 → 按分桶首行推导(桶序即 latest_for_node 仓储读序)。"""
    graph = _graph_with_node()
    dto = graph_to_dto(
        graph,
        artifacts_by_node={"n1": [
            _text_art("art_latest", attempt=1),
            _text_art("art_stale", attempt=0),
        ]},
    )
    assert dto.tasks[0].artifacts[0].is_primary is True
    assert dto.tasks[0].artifacts[1].is_primary is False
    assert dto.tasks[0].artifacts[0].attempt == 1


# ---------------------------------------------------------------------------
# ④ 轨迹读时富化:末位事件挂 artifacts / 早事件 None / 未接线 None / 指纹不变
# ---------------------------------------------------------------------------


class _FakeAssembler:
    def __init__(self, trajectory):
        self._trajectory = trajectory

    def assemble(self, task_id):
        return self._trajectory


class _FakeAnalyzer:
    async def analyze(self, *args, **kwargs):  # noqa: ANN002,ANN003 读路径不触
        raise AssertionError("read path must not call the analyzer")


class _FakeTrajRepo:
    def __init__(self, events=None):
        self._events = list(events or [])

    def list_events_by_task(self, task_id):
        return [r for r in self._events if r.task_id == task_id]

    def backfill_analysis(self, *args, **kwargs):  # noqa: ANN002,ANN003
        raise AssertionError("read path must not backfill")


def _traj_event(node_id, gmt_create, action=TrajectoryActionType.EXECUTE):
    return TrajectoryEvent(
        task_id="t1", node_id=node_id, action_type=action,
        action_result="success", attempt=0,
        gmt_create=gmt_create, gmt_modified=gmt_create,
    )


def _traj_record(node_id, gmt_create_ms, rec_id):
    dt = epoch_ms_to_storage_datetime(gmt_create_ms)
    return TrajectoryEventRecord(
        id=rec_id, task_id="t1", node_id=node_id, action_type="execute",
        attempt=0, action_result="success", ext_info=None,
        gmt_create=dt, gmt_modified=dt,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_attaches_artifacts_to_last_event_only():
    records = [
        _traj_record("n1", 1000, rec_id=1),
        _traj_record("n1", 2000, rec_id=2),
    ]
    traj = TaskTrajectory(
        task_id="t1", gmt_create=3000, gmt_modified=3000,
        timeline=[
            _traj_event("n1", 1000, action=TrajectoryActionType.PLAN),
            _traj_event("n1", 2000),
        ],
    )
    arts = [_text_art("art_t1", node_id="n1"), _file_art("art_f1", node_id="n1")]
    svc = TaskTrajectoryService(
        _FakeAssembler(traj), _FakeTrajRepo(records), _FakeAnalyzer(), None,
        artifact_service=_StubReadArtifactService(arts),
    )

    result = await svc.get_trajectory("t1", do_analysis=False)

    n1_events = [e for e in result.timeline if e.node_id == "n1"]
    assert n1_events[0].artifacts is None, "早事件不挂"
    assert [d["artifact_id"] for d in n1_events[-1].artifacts] == ["art_t1", "art_f1"]
    # descriptor = manifest dict 形状(content kind-tagged / scope / lineage 齐全)
    content = n1_events[-1].artifacts[0]["content"]
    assert content["kind"] == "text" and "text" in content
    file_content = n1_events[-1].artifacts[1]["content"]
    assert file_content["resource_id"] == "sr_f1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_unwired_artifacts_stay_none_and_fingerprint_stable():
    """未接线(缺省构造)→ 整条轨迹 artifacts=None;富化不进 timeline 指纹。"""
    records = [_traj_record("n1", 1000, rec_id=1)]
    fingerprint_before = _timeline_fingerprint(records)
    timeline = [_traj_event("n1", 1000)]
    traj = TaskTrajectory(task_id="t1", gmt_create=2000, gmt_modified=2000,
                          timeline=timeline)
    unwired = TaskTrajectoryService(
        _FakeAssembler(traj), _FakeTrajRepo(records), _FakeAnalyzer(), None,
    )
    result = await unwired.get_trajectory("t1", do_analysis=False)
    assert result.timeline[0].artifacts is None  # 未接线 → 不挂(缺字段=无信号)

    # 接线后富化成功,但指纹只敏感于事件行(records 未动 → 指纹不变)
    wired = TaskTrajectoryService(
        _FakeAssembler(traj), _FakeTrajRepo(records), _FakeAnalyzer(), None,
        artifact_service=_StubReadArtifactService([_text_art("art_x", node_id="n1")]),
    )
    enriched = await wired.get_trajectory("t1", do_analysis=False)
    assert enriched.timeline[0].artifacts is not None
    assert enriched.timeline[0].artifacts[0]["artifact_id"] == "art_x"
    assert _timeline_fingerprint(records) == fingerprint_before
    assert records[0].ext_info is None  # 读态不回写事件行


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_artifacts_read_failure_degrades():
    class _ExplodingArtifactService:
        def publish_output(self, *args, **kwargs):  # noqa: ANN002
            return 0

        def list_artifacts_for_task(self, task_id):
            raise RuntimeError("db down")

    records = [_traj_record("n1", 1000, rec_id=1)]
    traj = TaskTrajectory(task_id="t1", gmt_create=2000, gmt_modified=2000,
                          timeline=[_traj_event("n1", 1000)])
    svc = TaskTrajectoryService(
        _FakeAssembler(traj), _FakeTrajRepo(records), _FakeAnalyzer(), None,
        artifact_service=_ExplodingArtifactService(),
    )
    result = await svc.get_trajectory("t1", do_analysis=False)
    assert result is traj  # 本体照常返回(观测旁路,失败降级不抛)
    assert result.timeline[0].artifacts is None


@pytest.mark.unit
def test_trajectory_dto_passes_artifacts_through():
    traj = TaskTrajectory(task_id="t1", gmt_create=100, gmt_modified=100)
    ev = _traj_event("n1", 100)
    ev.artifacts = [_text_art("art_dto", node_id="n1").to_dict()]
    traj.timeline = [ev]
    assert trajectory_to_dto(traj).timeline[0].artifacts[0]["artifact_id"] == "art_dto"
    ev.artifacts = None  # 无富化 → DTO 空列表(缺字段=无信号)
    assert trajectory_to_dto(traj).timeline[0].artifacts == []


# ---------------------------------------------------------------------------
# ⑤ HTML:产物折叠块(Text/File 两态 / 无 artifacts 不渲染 / File 无 URL)
# ---------------------------------------------------------------------------


def _page(ev_dto) -> TaskTrajectoryDTO:
    return TaskTrajectoryDTO(
        task_id="t1", timeline=[ev_dto], analysis=None,
        gmt_create=1000, gmt_modified=1000,
    )


@pytest.mark.unit
def test_html_renders_artifacts_block_text_and_file():
    ev = TrajectoryEventDTO(
        task_id="t1", node_id="n1", action_type="execute", action_result="success",
        attempt=0, gmt_create=1000, gmt_modified=1000,
        artifacts=[
            _text_art("art_esc", text="<script>alert(1)</script>").to_dict(),
            _text_art("art_long", text="y" * 600).to_dict(),
            _file_art("art_htmlf").to_dict(),
        ],
    )
    page = render_trajectory_html(_page(ev))
    assert "产物 · artifacts" in page
    # Text:kind/media 徽章 + 全量 escape(<script> 不可注入)
    assert "node_result · text/markdown" in page
    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    # Text 截断:前 500 字符 + 省略号,不长篇撑页面
    assert "y" * 500 in page
    assert "y" * 501 not in page
    assert "…" in page
    # File:file_name + 稳定 resource_id,提示经会话文件接口访问
    assert "report.pdf" in page
    assert "sr_htmlf" in page
    assert "经既有会话文件接口访问" in page
    # spec 红线:不渲染任何 URL / 下载链接形态
    assert "http" not in page and "<a " not in page and "download" not in page


@pytest.mark.unit
def test_html_hides_artifacts_block_when_absent_or_empty():
    def _ev(artifacts):
        dto = TrajectoryEventDTO(
            task_id="t1", node_id="n1", action_type="execute", action_result="success",
            attempt=0, gmt_create=1000, gmt_modified=1000,
        )
        if artifacts is not None:
            dto.artifacts = artifacts
        return dto

    assert "产物 · artifacts" not in render_trajectory_html(_page(_ev([])))
    # artifacts 缺省(None/未富化 → DTO 默认空列表)同样不渲染
    assert "产物 · artifacts" not in render_trajectory_html(_page(_ev(None)))


# ---------------------------------------------------------------------------
# ⑥ analyzer 投影:bot 消息行带 artifacts 键(有/无两态)
# ---------------------------------------------------------------------------


def test_analyzer_bot_message_rows_carry_artifacts_summary():
    ev_with = _traj_event("n1", 1000)
    ev_with.artifacts = [
        _text_art("art_msg", text="z" * 400).to_dict(),
        _file_art("art_msgf").to_dict(),
        "not-a-dict",  # 防御:非 dict 形态跳过
    ]
    ev_without = _traj_event("n2", 2000)
    traj = TaskTrajectory(task_id="t1", gmt_create=3000, gmt_modified=3000,
                          timeline=[ev_with, ev_without])
    analyzer = TaskTrajectoryAnalyzer()
    payload = json.loads(
        analyzer._build_bot_message(traj, "events=2", lambda e: None)  # noqa: SLF001 白盒投影单测
    )
    row0, row1 = payload["timeline"]
    arts = row0["artifacts"]
    # Text → kind/media_type + text_preview(≤200 截断);File → resource_id 元数据
    assert arts[0]["kind"] == "node_result"
    assert arts[0]["media_type"] == "text/markdown"
    assert arts[0]["text_preview"] == "z" * 200 + "…"
    assert arts[1] == {
        "kind": "graph_rollup", "media_type": "application/pdf",
        "file_name": "report.pdf", "resource_id": "sr_msgf",
        "size_bytes": 183420,
    }
    assert len(arts) == 2  # 非 dict 防御跳过
    assert "artifacts" not in row1  # 无产物事件不加键(缺键=无信号)
    # 指令尾部只读说明(spec:事件行产物字段;File 引用稳定 resource_id)
    assert "artifacts" in payload["instruction"]
    assert "resource_id" in payload["instruction"]


# ---------------------------------------------------------------------------
# ⑦ DoneOutput.artifacts(get_task_context 组装后补挂 / 未接线保底)
# ---------------------------------------------------------------------------


def _spec_obj(task_id):
    return TaskSpec(
        context=Context(title=task_id, background=""),
        goal=Goal(objective=task_id, acceptances=[]),
    )


def _graph_with_accepted_child(task_id, node_id="n1"):
    svc = TaskGraphService()
    svc.initialize_graph(TaskInfo(
        task_id=task_id, task_spec=_spec_obj(task_id),
        source_type="bot", owner_bot_id="B",
    ))
    graph = svc._graphs[task_id]  # noqa: SLF001 白盒直改(样本 test_artifact_dualwrite)
    graph.tasks.append(TaskNode(
        node_id=node_id, task_id=task_id, status=Status.DONE,
        task_spec=_spec_obj(task_id), run_info=RuntimeInfo(),
        node_run_graph=graph,
    ))
    child = graph.tasks[-1]
    child.run_info.extend_props["execution_decision"] = "ACCEPTED"
    child.run_info.output = {"output": "done fact"}
    child.run_info.acceptance_result = AcceptanceResult(
        verdict=AcceptanceVerdict.DONE, done_items=[{"id": "a1", "passed": True}],
        gap_items=[],
    )
    return svc


@pytest.mark.unit
def test_get_task_context_attaches_done_output_artifacts():
    repo = _MemArtifactRepo()
    repo.create_or_get(_record(
        "art_ctx", task_id="t-ctx", node_id="n1",
        content={"kind": "text", "text": "manifest 全文",
                 "media_type": "text/markdown"},
    ))
    svc = _graph_with_accepted_child("t-ctx")
    svc.bind_artifact_service(TaskArtifactService(repo=repo))

    ctx = svc.get_task_context("t-ctx")

    assert len(ctx.all_done_output) == 1
    done = ctx.all_done_output[0]
    assert [d["artifact_id"] for d in done.artifacts] == ["art_ctx"]
    assert done.artifacts[0]["content"]["text"] == "manifest 全文"
    assert done.output == {"output": "done fact"}  # 老轨 output 原样(双轨)


@pytest.mark.unit
def test_get_task_context_unwired_keeps_default_empty_artifacts():
    svc = _graph_with_accepted_child("t-ctx2")
    ctx = svc.get_task_context("t-ctx2")
    assert ctx.all_done_output[0].artifacts == []  # 保底:默认空列表


@pytest.mark.unit
def test_task_context_dto_passes_done_output_artifacts():
    done = DoneOutput(
        node_id="n1", actual_goal=Goal(objective="o", acceptances=[]),
        output={"output": "x"}, acceptance_result=AcceptanceResult(
            verdict=AcceptanceVerdict.DONE, done_items=[], gap_items=[],
        ),
        artifacts=[{"artifact_id": "art_d"}],
    )
    ctx = TaskContext(spec=_spec_obj("t-dto"), all_done_output=[done], gaps=[])
    dto = task_context_to_dto(ctx)
    assert dto.all_done_output[0].artifacts == [{"artifact_id": "art_d"}]


# ---------------------------------------------------------------------------
# ⑧ RuntimeInfo 读时富化字段:不持久化(serializers 零感应)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_runtime_artifact_fields_are_not_persisted_by_serializers():
    runtime = RuntimeInfo(
        output_artifact_ids=["art_1", "art_2"],
        primary_output_artifact_id="art_1",
    )
    dumped = runtime_to_dict(runtime)
    assert "output_artifact_ids" not in dumped
    assert "primary_output_artifact_id" not in dumped
    restored = runtime_from_dict(dumped)
    assert restored.output_artifact_ids == []
    assert restored.primary_output_artifact_id is None


# ---------------------------------------------------------------------------
# ⑨ 路由透传:公开面 dashboard Injected 读协议,Envelope 出现 artifacts
# ---------------------------------------------------------------------------


class _StubRouteTaskService:
    """dashboard handler 只用 ``get_task_dashboard``(样本 test_router.py stub)。"""

    def get_task_dashboard(self, task_id, node_id=None, include_action_log=False):
        return _graph_with_node(
            task_id=task_id, node_id="n1", output={"output": "route-legacy"},
        )


class _StubRouteModule(Module):
    """TaskServiceProtocol + TaskArtifactServiceProtocol 的 injector 级 stub。"""

    def __init__(self, artifacts):
        super().__init__()
        self._artifacts = artifacts

    @singleton
    @provider
    def task_service(self) -> TaskServiceProtocol:
        return _StubRouteTaskService()  # type: ignore[return-value]

    @singleton
    @provider
    def artifact_service(self) -> TaskArtifactServiceProtocol:
        return _StubReadArtifactService(self._artifacts)  # type: ignore[return-value]


@pytest.fixture
def route_client():
    """公开面(dashboard 所在)+ 内部面镜像同挂载;断言只走公开面 dashboard 路由。"""
    arts = [
        _text_art("art_route", node_id="n1", text="产物文本"),
        _file_art("art_route_file", node_id="n1"),
    ]
    injector = Injector([_StubRouteModule(arts)])
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "route-owner"}
    attach_injector(app, injector)
    return TestClient(app), injector


@pytest.mark.unit
def test_dashboard_endpoint_enriches_artifacts_into_envelope(route_client):
    """handler 经 Injected 读协议组 artifacts_by_node + primary 集 → Envelope 透传。"""
    c, _ = route_client
    resp = c.get("/openapi/v1/collaboration/tasks/dashboard", params={"task_id": "t1"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    node = data["tasks"][0]
    # 双轨:老 output 字段原样保留
    assert node["run_info"]["output"] == "route-legacy"
    # 产物轨:stub 的两产物出现在 Envelope,primary 标记正确
    arts = node["artifacts"]
    assert [a["artifact_id"] for a in arts] == ["art_route", "art_route_file"]
    assert arts[0]["is_primary"] is True and arts[0]["text"] == "产物文本"
    assert arts[1]["file"]["resource_id"] == "sr_route_file"
    assert arts[1]["is_primary"] is False
    assert len(data["tasks"]) == 1