"""TDD for the generalized搜推 BotDiscoverService (Phase 4.1, plan §2.4).

"搜推 bot" 是泛化语义:不只是找单 bot,而是发现能 100% cover 子需求的
执行方——单 bot / 协作群(多 bot 拼合)/ 不可完成(拆解或 BBS)。非 bcsfuse
依赖,本地 BotCatalog + cover 计算。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    Node,
    RunMode,
    Task,
    TaskExecutionGraph,
    TaskSource,
    TaskSpec,
    TaskSpecMetadata,
    TaskStatus,
)
from agentclaw.community.core.task.protocols import RouteClass
from agentclaw.community.core.task.services.bot_catalog import BotProfile
from agentclaw.community.core.task.services.bot_discover_service import (
    BotDiscoverService,
)


def _catalog(bots: list[BotProfile]):
    class _StaticCatalog:
        def list_bots(self) -> list[BotProfile]:
            return list(bots)

    return _StaticCatalog()


def _service(bots: list[BotProfile]) -> BotDiscoverService:
    return BotDiscoverService(task_repo=None, bot_catalog=_catalog(bots))


# --- cover 计算 → route_class 四种结果 --------------------------------------


def test_single_bot_full_cover_routes_c1():
    bots = [
        BotProfile(bot_id="coder", summary="writes python", skills=["python", "test"]),
        BotProfile(bot_id="writer", summary="writes docs", skills=["doc"]),
    ]
    rec = _service(bots).recommend_for_spec("implement a python test")
    assert rec.route_class is RouteClass.C1
    assert rec.run_mode is RunMode.SINGLE_BOT
    assert len(rec.candidates) == 1
    assert rec.candidates[0].bot_id == "coder"
    assert rec.candidates[0].fit_score >= 1.0 - 1e-9
    assert rec.confidence >= 1.0 - 1e-9


def test_multi_bot_combine_full_cover_routes_c3_coop_group():
    # no single bot covers all keywords, but coder+tester union does
    bots = [
        BotProfile(bot_id="coder", summary="writes code", skills=["python"]),
        BotProfile(bot_id="tester", summary="writes tests", skills=["test"]),
        BotProfile(bot_id="ops", summary="deploys", skills=["deploy"]),
    ]
    rec = _service(bots).recommend_for_spec("python test")  # needs both
    assert rec.route_class is RouteClass.C3
    assert rec.run_mode is RunMode.COOP_GROUP
    assert {c.bot_id for c in rec.candidates} == {"coder", "tester"}
    assert rec.confidence >= 1.0 - 1e-9


def test_partial_cover_routes_c4_needs_decomposition():
    bots = [
        BotProfile(bot_id="coder", summary="writes code", skills=["python"]),
    ]
    # spec needs python + db + ui; no bot covers db/ui, union can't reach 1.0
    rec = _service(bots).recommend_for_spec("python db ui")
    assert rec.route_class is RouteClass.C4
    assert len(rec.candidates) == 1
    assert rec.candidates[0].bot_id == "coder"
    assert 0.0 < rec.confidence < 1.0


def test_zero_cover_routes_c5_escalate_bbs():
    bots = [
        BotProfile(bot_id="coder", summary="writes code", skills=["python"]),
    ]
    # nothing matches at all
    rec = _service(bots).recommend_for_spec("legal contract review")
    assert rec.route_class is RouteClass.C5
    assert rec.run_mode is RunMode.BBS
    assert rec.candidates == []
    assert rec.confidence == 0.0


def test_empty_spec_routes_c2_needs_clarification():
    bots = [BotProfile(bot_id="coder", summary="x", skills=["python"])]
    rec = _service(bots).recommend_for_spec("")
    assert rec.route_class is RouteClass.C2
    assert rec.confidence == 0.0


# --- 没有 bot / attempted 不排除 --------------------------------------------


def test_empty_catalog_routes_c5():
    rec = _service([]).recommend_for_spec("do something with python")
    assert rec.route_class is RouteClass.C5
    assert rec.confidence == 0.0


def test_attempted_executors_not_excluded_from_candidates():
    """recommend 不排除 attempted 执行方(降权在 Scheduler._route,非此处)。
    用真实 recommend(task_id,node_id) 路径,构造带 attempted 的 node。"""
    bots = [
        BotProfile(bot_id="coder", summary="writes python", skills=["python", "test"]),
    ]
    node = Node(
        node_id="n1",
        spec="implement a python test",
        attempted_executors=[],
    )
    # mark coder as attempted — recommend must still return it
    from agentclaw.community.core.task.domain.models import AttemptedRecord, RunMode as _RM

    node.attempted_executors = [
        AttemptedRecord(
            executor_id="coder",
            paradigm=_RM.SINGLE_BOT,
            round=1,
        )
    ]
    task = Task(
        id="t1",
        user_id="u1",
        source=TaskSource.IM,
        spec=TaskSpec(metadata=TaskSpecMetadata(id="t1", title="t")),
        status=TaskStatus.EXECUTING,
        execution_graph=TaskExecutionGraph(root_phase=TaskStatus.EXECUTING, nodes=[node]),
    )

    class _FakeRepo:
        def get_by_id(self, task_id: str) -> Task:
            return task

    svc = BotDiscoverService(task_repo=_FakeRepo(), bot_catalog=_catalog(bots))
    rec = svc.recommend("t1", "n1")
    assert rec.route_class is RouteClass.C1
    assert rec.candidates[0].bot_id == "coder"  # attempted 但未被排除


# --- recommend(task_id, node_id) 经 task_repo 加载 --------------------------


def test_recommend_loads_node_spec_from_repo():
    bots = [BotProfile(bot_id="coder", summary="python test", skills=["python", "test"])]
    node = Node(node_id="n2", spec="write a python test")
    task = Task(
        id="t2",
        user_id="u1",
        source=TaskSource.IM,
        spec=TaskSpec(metadata=TaskSpecMetadata(id="t2", title="t")),
        status=TaskStatus.EXECUTING,
        execution_graph=TaskExecutionGraph(root_phase=TaskStatus.EXECUTING, nodes=[node]),
    )

    class _FakeRepo:
        def get_by_id(self, task_id: str) -> Task:
            return task

    rec = BotDiscoverService(task_repo=_FakeRepo(), bot_catalog=_catalog(bots)).recommend(
        "t2", "n2"
    )
    assert rec.route_class is RouteClass.C1
    assert rec.candidates[0].bot_id == "coder"


def test_recommend_unknown_node_returns_c2():
    bots = [BotProfile(bot_id="coder", summary="python", skills=["python"])]
    task = Task(
        id="t3",
        user_id="u1",
        source=TaskSource.IM,
        spec=TaskSpec(metadata=TaskSpecMetadata(id="t3", title="t")),
        status=TaskStatus.EXECUTING,
        execution_graph=TaskExecutionGraph(root_phase=TaskStatus.EXECUTING, nodes=[]),
    )

    class _FakeRepo:
        def get_by_id(self, task_id: str) -> Task:
            return task

    rec = BotDiscoverService(task_repo=_FakeRepo(), bot_catalog=_catalog(bots)).recommend(
        "t3", "missing"
    )
    assert rec.route_class is RouteClass.C2