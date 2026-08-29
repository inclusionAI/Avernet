"""M1 TaskGraphService 单测(对齐 tasks.md T1.x)。

覆盖:initialize_graph 幂等、add_task_nodes a/b/c 触发+单层同构护栏、
update_task_node_info 双模式状态机+fold、relations 派生(child/parent/depth)、
PLANNING 语义、传播 status 直驱、v4 删 remove_subtree、query 派生查询、.execution_config。
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
    TaskGraphPatch,
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
        source_type="bot",
        owner_bot_id="b1",
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


def test_status_includes_cancelled():
    assert Status.CANCELLED.value == "CANCELLED"


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
        # v4:父进 PLANNING(委托/编排态)
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        # 子回填 node_run_graph
        assert svc._get_node(graph, "c1").node_run_graph is graph

    def test_trigger_b_remedy(self, svc: TaskGraphService, graph):
        # 先 add(条件 a)→ dispatch(RUNNING)→ FAIL+gaps
        svc.add_task_nodes([_node("leaf")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "leaf", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(
            _patch("t1", "leaf", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺深度"]))
        )
        assert svc._get_node(graph, "leaf").status == Status.FAILED
        # 条件 b 成立:补救子挂 FAILED 叶子下(v4 父→PLANNING)
        svc.add_task_nodes([_node("remedy")], parent_node_id="leaf")
        assert svc._get_node(graph, "leaf").status == Status.PLANNING
        assert svc.get_parent_task("t1", "remedy").node_id == "leaf"

    def test_trigger_c_next_layer(self, svc: TaskGraphService, graph):
        # v4:前向重规划,父恒 PLANNING,再 add(cond_c:存在 PLANNING 节点)
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")  # t1 PENDING→PLANNING
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        svc.add_task_nodes([_node("c2")], parent_node_id="t1")  # cond_c 命中(存在 PLANNING)
        assert svc.get_parent_task("t1", "c2").node_id == "t1"
        assert svc._get_node(graph, "t1").status == Status.PLANNING

    def test_no_trigger_raises(self, svc: TaskGraphService, graph):
        # 根 RUNNING(委托执行)后,a/b/c/d 均不满足(无 PENDING 根/无 FAILED 叶/无 PLANNING/无 miss 叶)→ raise
        svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        with pytest.raises(GraphIntegrityError):
            svc.add_task_nodes([_node("c1")], parent_node_id="t1")

    def test_dual_id_raises(self, svc: TaskGraphService, graph):
        with pytest.raises(GraphIntegrityError, match="重复"):
            svc.add_task_nodes([_node("c1"), _node("c1")], parent_node_id="t1")

    def test_existing_id_raises(self, svc: TaskGraphService, graph):
        # 首批 add 后根 t1 已 RUNNING(不可委托);在可委托 PENDING 叶 c2 下测重复 id
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        with pytest.raises(GraphIntegrityError, match="已存在"):
            svc.add_task_nodes([_node("c1")], parent_node_id="c2")

    def test_parent_not_delegatable(self, svc: TaskGraphService, graph):
        # 触发 b(FAILED+gaps 叶),但传 RUNNING 父 → 不可委托
        svc.add_task_nodes([_node("c1"), _node("c2")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c2", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["x"]))
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
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE, acceptances_metric=["ac1"]))
        )
        assert r.prev_status == Status.RUNNING
        assert r.new_status == Status.DONE
        assert svc._get_node(graph, "c1").status == Status.DONE

    def test_acceptance_fail_no_gaps_raises(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        with pytest.raises(TaskStateError, match="gaps"):
            svc.update_task_node_info(
                _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=[]))
            )

    def test_acceptance_fail_with_gaps(self, svc: TaskGraphService, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["缺x"]))
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
        # v4:add 后父 PLANNING;前向 gap 闭时翻 PLANNING→DONE(传播)
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        assert svc._get_node(graph, "t1").status == Status.PLANNING
        r = svc.update_task_node_info(_patch("t1", "t1", status=Status.DONE))  # gap 闭传播
        assert r.new_status == Status.DONE
        assert svc._get_node(graph, "t1").status == Status.DONE

    def test_illegal_transition_raises(self, svc: TaskGraphService, graph):
        # DONE 不可再翻(PASS 后)
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE)))
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

    # ---- v5: run_mode 空串归一 + 时间戳自动写 ----
    def test_run_mode_empty_string_normalized_to_none(self, svc, graph):
        # 空串(清执行者语义)归一为 None;run_mode 只有 single_bot/coop_group/bbs 三态
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        assert svc._get_node(graph, "c1").run_info.run_mode == "single_bot"
        svc.update_task_node_info(_patch("t1", "c1", run_mode="", assignee=""))
        node = svc._get_node(graph, "c1")
        assert node.run_info.run_mode is None
        assert node.run_info.assignee is None

    def test_pending_to_planning_allowed(self, svc, graph):
        # PENDING->PLANNING 合法(_mark_planning 初始根/MISS 叶进入规划)
        r = svc.update_task_node_info(_patch("t1", "t1", status=Status.PLANNING))
        assert r.new_status == Status.PLANNING
        assert svc._get_node(graph, "t1").status == Status.PLANNING

    def test_enter_running_writes_start_time(self, svc, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        node = svc._get_node(graph, "c1")
        assert node.run_info.start_time is not None
        assert node.run_info.end_time is None

    def test_pass_done_writes_end_time(self, svc, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        t0 = svc._get_node(graph, "c1").run_info.start_time
        svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE, acceptances_metric=["ac1"]))
        )
        node = svc._get_node(graph, "c1")
        assert node.status == Status.DONE
        assert node.run_info.end_time is not None
        assert node.run_info.start_time == t0

    def test_fail_writes_end_time(self, svc, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(
            _patch("t1", "c1", acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.FAILED, gaps=["x"]))
        )
        assert svc._get_node(graph, "c1").run_info.end_time is not None

    def test_reset_to_pending_clears_timestamps(self, svc, graph):
        svc.add_task_nodes([_node("c1")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        assert svc._get_node(graph, "c1").run_info.start_time is not None
        svc.update_task_node_info(_patch("t1", "c1", status=Status.PENDING))
        node = svc._get_node(graph, "c1")
        assert node.status == Status.PENDING
        assert node.run_info.start_time is None
        assert node.run_info.end_time is None

    def test_planning_to_hung_writes_end_time_only(self, svc, graph):
        # 纯规划节点从未执行(无 start)->直 HUNG:写 end_time,start 保持 None
        svc.update_task_node_info(_patch("t1", "t1", status=Status.PLANNING))
        assert svc._get_node(graph, "t1").run_info.start_time is None
        svc.update_task_node_info(_patch("t1", "t1", status=Status.HUNG))
        node = svc._get_node(graph, "t1")
        assert node.status == Status.HUNG
        assert node.run_info.end_time is not None
        assert node.run_info.start_time is None


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
        # 根 t1 已 RUNNING(委托),c1/c2 PENDING → 2 个
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


# v4:remove_subtree 已删除,TestRemoveSubtree 已移除

# ===== execution_config / not found =====
class TestUpdateTaskGraphInfo:
    """图级原子写口 update_task_graph_info(收口图级终态)。"""

    def test_loop_round_increment(self, svc: TaskGraphService, graph):
        g = svc.update_task_graph_info("t1", TaskGraphPatch(loop_round_increment=1))
        assert g.loop_round == 1
        svc.update_task_graph_info("t1", TaskGraphPatch(loop_round_increment=2))
        assert svc.query_task_dashboard("t1").loop_round == 3  # 原子加 2

    def test_status_done(self, svc: TaskGraphService, graph):
        g = svc.update_task_graph_info("t1", TaskGraphPatch(status=Status.DONE))
        assert g.status == Status.DONE
        assert svc.query_task_dashboard("t1").status == Status.DONE

    def test_status_hung(self, svc: TaskGraphService, graph):
        g = svc.update_task_graph_info("t1", TaskGraphPatch(status=Status.HUNG))
        assert g.status == Status.HUNG

    def test_output_patch_merge(self, svc: TaskGraphService, graph):
        assert graph.output == {}
        svc.update_task_graph_info("t1", TaskGraphPatch(output_patch={"result": "all_done"}))
        g = svc.update_task_graph_info("t1", TaskGraphPatch(output_patch={"extra": "x"}))
        assert g.output == {"result": "all_done", "extra": "x"}  # 浅合并累积

    def test_extend_props_patch_merge(self, svc: TaskGraphService, graph):
        svc.update_task_graph_info("t1", TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
        svc.update_task_graph_info("t1", TaskGraphPatch(extend_props_patch={"hung_reason": "stuck"}))
        g = svc.query_task_dashboard("t1")
        assert g.extend_props.get("bbs_mode") is True
        assert g.extend_props.get("hung_reason") == "stuck"  # 浅合并累积

    def test_combined_atomic_write(self, svc: TaskGraphService, graph):
        """一次 patch 多字段原子写(升 BBS 后 STUCK 场景)。"""
        # v4 升 BBS 不再 remove;直接新建图测多字段原子写
        svc2 = TaskGraphService()
        svc2.initialize_graph(_task_info("tX"))
        svc2.update_task_graph_info(
            "tX",
            TaskGraphPatch(
                loop_round_increment=1,
                status=Status.HUNG,
                extend_props_patch={"hung_reason": "stuck"},
                output_patch={"result": "stuck"},
            ),
        )
        g = svc2.query_task_dashboard("tX")
        assert g.loop_round == 1
        assert g.status == Status.HUNG
        assert g.extend_props["hung_reason"] == "stuck"
        assert g.output["result"] == "stuck"

    def test_omitted_fields_untouched(self, svc: TaskGraphService, graph):
        """未给字段不动(增量 patch)。"""
        assert graph.loop_round == 0
        assert graph.output == {}
        svc.update_task_graph_info("t1", TaskGraphPatch(status=Status.DONE))
        g = svc.query_task_dashboard("t1")
        assert g.loop_round == 0  # 未给 loop_round_increment,不动
        assert g.output == {}     # 未给 output_patch,不动

    def test_task_not_found(self, svc: TaskGraphService):
        with pytest.raises(TaskNotFoundError):
            svc.update_task_graph_info("nope", TaskGraphPatch(status=Status.DONE))

    def test_concurrent_safe(self, svc: TaskGraphService, graph):
        """加锁原子加并发安全(简化:顺序多次加不丢)。"""
        for _ in range(100):
            svc.update_task_graph_info("t1", TaskGraphPatch(loop_round_increment=1))
        assert svc.query_task_dashboard("t1").loop_round == 100


class TestMisc:
    def test_execution_config_default(self, svc: TaskGraphService, graph):
        cfg = svc._execution_config("t1")
        assert cfg["MAX_DEPTH"] == 2  # v4 默认
        assert cfg["MAX_LOOP"] == 10
        assert cfg["MAX_HARNESS"] == 3

    def test_execution_config_custom(self, svc: TaskGraphService):
        ti = _task_info("tC")
        ti.execution_config["MAX_DEPTH"] = 5
        svc.initialize_graph(ti)
        assert svc._execution_config("tC")["MAX_DEPTH"] == 5
        assert svc._execution_config("tC")["MAX_LOOP"] == 10  # v4 默认
        assert svc._execution_config("tC")["MAX_HARNESS"] == 3  # v4 默认

    def test_task_not_found(self, svc: TaskGraphService):
        with pytest.raises(TaskNotFoundError):
            svc.query_task_dashboard("nope")

    def test_node_not_found(self, svc: TaskGraphService, graph):
        with pytest.raises(NodeNotFoundError):
            svc.get_child_tasks("t1", "nonexistent")
