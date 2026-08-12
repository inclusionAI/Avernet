"""M1 TaskGraphService 单测(对齐 tasks.md T1.x)。

覆盖:initialize_graph 幂等、add_task_nodes a/b/c 触发+单层同构护栏、
update_task_node_info 双模式状态机+fold、relations 派生(child/parent/depth)、
PLANNING 语义、传播 status 直驱、remove_subtree、query 派生查询、.execution_config。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.domain.errors import (
    GraphAlreadyInitializedError,
    GraphIntegrityError,
    NodeNotFoundError,
    TaskNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
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
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


# ===== fixtures / helpers =====
def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do it"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_channel_type="bot",
        source_channel_id="b1",
    )


def _node(node_id: str, task_id: str = "t1") -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec,
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]  store 回填
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


@pytest.fixture
def svc() -> TaskGraphService:
    return TaskGraphService()


@pytest.fixture
def graph(svc: TaskGraphService):
    return svc.initialize_graph(_task_info())


# ===== initialize_graph =====
class TestInitializeGraph:
    def test_basic(self, svc: TaskGraphService):
        g = svc.initialize_graph(_task_info("tA"))
        assert g.run_id == 1
        assert g.status == Status.RUNNING
        assert g.loop_round == 0
        assert len(g.tasks) == 1
        root = g.tasks[0]
        assert root.node_id == "tA"
        assert root.status == Status.PENDING
        assert root.node_run_graph is g

    def test_run_id_monotonic(self, svc: TaskGraphService):
        a = svc.initialize_graph(_task_info("tA"))
        b = svc.initialize_graph(_task_info("tB"))
        assert b.run_id == a.run_id + 1

    def test_idempotent_conflict(self, svc: TaskGraphService, graph):
        with pytest.raises(GraphAlreadyInitializedError):
            svc.initialize_graph(_task_info("t1"))


# ===== add_task_nodes 触发条件 + 护栏 =====
class TestAddTaskNodes:
    def test_trigger_a_initial_plan(self, svc: TaskGraphService, graph):
        children = svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        # relations 写入(单入:每子 1 入边)
        assert len(children.relations) == 2
        assert all(r.src_id == "t1" for r in children.relations)
        assert {r.dst_id for r in children.relations} == {"c1", "c2"}
        # 父进 PLANNING
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        # 子回填 node_run_graph
        assert svc._get_node(graph, "c1").node_run_graph is graph

    def test_trigger_b_remedy(self, svc: TaskGraphService, graph):
        # 先 add(条件 a)→ dispatch(RUNNING)→ FAIL+gaps
        svc.add_task_nodes([_node("leaf")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "leaf", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(
            _patch("t1", "leaf", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["缺深度"]))
        )
        assert svc._get_node(graph, "leaf").status == Status.FAILED
        # 条件 b 成立:补救子挂 FAILED 叶子下
        svc.add_task_nodes([_node("remedy")], parent_node_id="leaf")
        assert svc._get_node(graph, "leaf").status == Status.PLANNING
        assert svc.get_parent_task("t1", "remedy").node_id == "leaf"

    def test_trigger_c_next_layer(self, svc: TaskGraphService, graph):
        # 条件 a add 一层 → 父 PLANNING(无 RUNNING)→ 条件 c add 下一层
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        # c1 已 PENDING(未 dispatch),t1 PLANNING,无 RUNNING → 条件 c
        svc.add_task_nodes([_node("c1a")], parent_node_id="c1")
        assert svc.get_parent_task("t1", "c1a").node_id == "c1"

    def test_no_trigger_raises(self, svc: TaskGraphService, graph):
        # 无 PENDING 根(根已 PLANNING 后无 FAILED/PLANNING 触发)→ 这里根仍 PENDING,先不 add
        # 构造:根 dispatch RUNNING 后,无 a/b/c
        svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        with pytest.raises(GraphIntegrityError):
            svc.add_task_nodes([_node("c1")], parent_node_id="t1")

    def test_dual_id_raises(self, svc: TaskGraphService, graph):
        with pytest.raises(GraphIntegrityError, match="重复"):
            svc.add_task_nodes([_node("c1"), _node("c1")], parent_node_id="t1")

    def test_existing_id_raises(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        with pytest.raises(GraphIntegrityError, match="已存在"):
            svc.add_task_nodes([_node("c1")], parent_node_id="t1")

    def test_parent_not_delegatable(self, svc: TaskGraphService, graph):
        # 触发 b(FAILED+gaps 叶),但传 RUNNING 父 → 不可委托
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c2", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["x"]))
        )
        # c1 FAILED+gaps → 条件 b 成立;parent=c2 RUNNING 不可委托
        with pytest.raises(GraphIntegrityError, match="不可委托"):
            svc.add_task_nodes([_node("remedy")], parent_node_id="c2")

    def test_parent_not_found(self, svc: TaskGraphService, graph):
        with pytest.raises(NodeNotFoundError):
            svc.add_task_nodes([_node("c1")], parent_node_id="nonexistent")

    def test_empty_tasks_raises(self, svc: TaskGraphService, graph):
        with pytest.raises(GraphIntegrityError, match="不能为空"):
            svc.add_task_nodes([], parent_node_id="t1")


# ===== update_task_node_info 状态机 =====
class TestUpdateTaskNodeInfo:
    def test_acceptance_pass_to_done(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        r = svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS, acceptances_metric=["ac1"]))
        )
        assert r.prev_status == Status.RUNNING
        assert r.new_status == Status.DONE
        assert svc._get_node(graph, "c1").status == Status.DONE

    def test_acceptance_fail_no_gaps_raises(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        with pytest.raises(TaskStateError, match="gaps"):
            svc.update_task_node_info(
                _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=[]))
            )

    def test_acceptance_fail_with_gaps(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAIL, gaps=["缺x"]))
        )
        assert svc._get_node(graph, "c1").status == Status.FAILED

    def test_status_direct_dispatch(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        r = svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="bot1"))
        assert r.new_status == Status.RUNNING
        node = svc._get_node(graph, "c1")
        assert node.run_info.run_mode == "single_bot"
        assert node.run_info.assignee == "bot1"

    def test_status_direct_reset_harness(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1", status=Status.PENDING))  # Harness 复位
        assert svc._get_node(graph, "c1").status == Status.PENDING

    def test_status_direct_propagate_done(self, svc: TaskGraphService, graph):
        # 建 PLANNING 父,模拟传播 PLANNING→DONE
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        r = svc.update_task_node_info(_patch("t1", "t1", status=Status.DONE))
        assert r.new_status == Status.DONE
        assert svc._get_node(graph, "t1").status == Status.DONE

    def test_illegal_transition_raises(self, svc: TaskGraphService, graph):
        # DONE 不可再翻(PASS 后)
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.PASS)))
        with pytest.raises(TaskStateError):
            svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING))

    def test_fold_output_only(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        before = svc._get_node(graph, "c1").status
        svc.update_task_node_info(_patch("t1", "c1", output_patch={"k": "v"}))
        node = svc._get_node(graph, "c1")
        assert node.run_info.output == {"k": "v"}
        assert node.status == before  # 无 acceptance/status 不翻态

    def test_fold_extend_props(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", extend_props_patch={"miss_events": ["no_bot"]}))
        assert svc._get_node(graph, "c1").run_info.extend_props.get("miss_events") == ["no_bot"]


# ===== relations 派生查询 =====
class TestDerivedQueries:
    def test_get_child_parent(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        svc.add_task_nodes([_node("c1a")], parent_node_id="c1")
        children = svc.get_child_tasks("t1", "t1")
        assert {n.node_id for n in children} == {"c1", "c2"}
        assert svc.get_parent_task("t1", "c1").node_id == "t1"
        assert svc.get_parent_task("t1", "c1a").node_id == "c1"
        assert svc.get_parent_task("t1", "t1") is None  # 根

    def test_node_depth(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.add_task_nodes([_node("c1a")], parent_node_id="c1")
        assert svc._node_depth("t1", "t1") == 0
        assert svc._node_depth("t1", "c1") == 1
        assert svc._node_depth("t1", "c1a") == 2

    def test_query_task_nodes_status(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        pending = svc.query_task_nodes("t1", TaskNodeQueryCriteria(status=Status.PENDING))
        # 根 t1 已 PLANNING,c1/c2 PENDING → 2 个
        assert {n.node_id for n in pending} == {"c1", "c2"}

    def test_query_task_nodes_has_child(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.add_task_nodes([_node("c1a")], parent_node_id="c1")
        leaves = svc.query_task_nodes("t1", TaskNodeQueryCriteria(has_child_tasks=True))
        assert {n.node_id for n in leaves} == {"c1a"}  # c1a 叶,c1 有子,t1 有子
        internals = svc.query_task_nodes("t1", TaskNodeQueryCriteria(has_child_tasks=False))
        assert {n.node_id for n in internals} == {"t1", "c1"}

    def test_query_task_nodes_node_ids(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        r = svc.query_task_nodes("t1", TaskNodeQueryCriteria(node_ids=["c1"]))
        assert [n.node_id for n in r] == ["c1"]

    def test_query_dashboard_subtree(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        svc.add_task_nodes([_node("c1a")], parent_node_id="c1")
        sub = svc.query_task_dashboard("t1", node_id="c1")
        assert {n.node_id for n in sub.tasks} == {"c1", "c1a"}
        assert {(r.src_id, r.dst_id) for r in sub.relations} == {("c1", "c1a")}

    def test_query_dashboard_full(self, svc: TaskGraphService, graph):
        g = svc.query_task_dashboard("t1")
        assert g is graph  # 整图返回引用(D3-A)


# ===== remove_subtree =====
class TestRemoveSubtree:
    def test_remove_subtree(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        svc.add_task_nodes([_node("c1a")], parent_node_id="c1")
        svc.remove_subtree("t1", "c1")
        ids = {n.node_id for n in graph.tasks}
        assert ids == {"t1", "c2"}  # c1 及子 c1a 删
        assert not any(r.dst_id == "c1" or r.src_id == "c1" for r in graph.relations)

    def test_remove_subtree_root(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.remove_subtree("t1", "t1")
        assert graph.tasks == []
        assert graph.relations == []


# ===== execution_config / not found =====
class TestMisc:
    def test_execution_config_default(self, svc: TaskGraphService, graph):
        cfg = svc._execution_config("t1")
        assert cfg["MAX_DEPTH"] == 3
        assert cfg["BBS_MAX_DEPTH"] == 3

    def test_execution_config_custom(self, svc: TaskGraphService):
        ti = _task_info("tC")
        ti.execution_config["MAX_DEPTH"] = 5
        svc.initialize_graph(ti)
        assert svc._execution_config("tC")["MAX_DEPTH"] == 5
        assert svc._execution_config("tC")["BBS_MAX_DEPTH"] == 3  # 默认

    def test_task_not_found(self, svc: TaskGraphService):
        with pytest.raises(TaskNotFoundError):
            svc.query_task_dashboard("nope")

    def test_node_not_found(self, svc: TaskGraphService, graph):
        with pytest.raises(NodeNotFoundError):
            svc.get_child_tasks("t1", "nonexistent")
