"""M4b TaskRunner 单测(对齐 tasks.md T4b.x)。

真实 TaskGraphService 构图场景;Runner 内聚(无额外 stub)。覆盖:
start_run 三 run_mode 分发(记投递日志/loop_task_id 格式/非法模式 False)、form_coop_group 生成 group_id+记 GroupFormation、
query_status/detail/result 回填、query_bot_tasks stub、_build_context 验收/执行双模式自动切换。
"""
from __future__ import annotations

import asyncio
import pytest

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
from agentclaw.community.core.task.task_dispatch.protocols import GroupFormation
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.task_runner import TaskRunner


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)

# ===== helpers =====
def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


def _child(node_id: str, task_id: str = "t1", run_mode: str | None = None, assignee: str | None = None) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec,
        run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


def _pass() -> AcceptanceResult:
    return AcceptanceResult(verdict=AcceptanceVerdict.DONE)


@pytest.fixture
def svc() -> TaskGraphService:
    return TaskGraphService()


@pytest.fixture
def graph(svc: TaskGraphService):
    return svc.initialize_graph(_task_info())


def _dispatch(svc: TaskGraphService, graph, node_ids: list[str], parent: str = "t1",
              run_mode: str = "single_bot", assignee: str = "bot1") -> list[TaskNode]:
    """辅助:add + dispatch 落 RUNNING,返回图内节点。"""
    svc.add_task_nodes([_child(n) for n in node_ids], parent_node_id=parent)
    nodes: list[TaskNode] = []
    for n in node_ids:
        svc.update_task_node_info(_patch("t1", n, status=Status.RUNNING, run_mode=run_mode, assignee=assignee))
        nodes.append(svc._get_node(graph, n))
    return nodes


def _node_output(svc: TaskGraphService, node_id: str) -> dict:
    return svc.query_task_nodes("t1", TaskNodeQueryCriteria(node_ids=[node_id]))[0].run_info.output


# ===== start_run 三模态 =====
class TestStartRun:
    def test_single_bot_dispatched(self, svc, graph):
        runner = TaskRunner(svc)
        _dispatch(svc, graph, ["c1"], run_mode="single_bot", assignee="bot_market")
        results = _run(runner.start_run([svc._get_node(graph, "c1")]))
        assert results == [True]
        log = runner._run_log[-1]
        assert log["run_mode"] == "single_bot"
        assert log["assignee"] == "bot_market"
        assert log["loop_task_id"] == "t1::c1"
        assert log["node_id"] == "c1"

    def test_coop_group_dispatched(self, svc, graph):
        runner = TaskRunner(svc)
        _dispatch(svc, graph, ["c1"], run_mode="coop_group", assignee="grp_tech")
        results = _run(runner.start_run([svc._get_node(graph, "c1")]))
        assert results == [True]
        assert runner._run_log[-1]["run_mode"] == "coop_group"
        assert runner._run_log[-1]["assignee"] == "grp_tech"

    def test_bbs_dispatched(self, svc, graph):
        runner = TaskRunner(svc)
        _dispatch(svc, graph, ["c1"], run_mode="bbs", assignee="bot_bbs_7")
        results = _run(runner.start_run([svc._get_node(graph, "c1")]))
        assert results == [True]
        assert runner._run_log[-1]["run_mode"] == "bbs"
        assert runner._run_log[-1]["assignee"] == "bot_bbs_7"

    def test_batch_mixed_modes(self, svc, graph):
        runner = TaskRunner(svc)
        # 一次性 add 三兄弟(满足条件 a/c:无 RUNNING),再分别标三种 run_mode 落 RUNNING
        svc.add_task_nodes([_child("c1"), _child("c2"), _child("c3")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b1"))
        svc.update_task_node_info(_patch("t1", "c2", status=Status.RUNNING, run_mode="coop_group", assignee="g1"))
        svc.update_task_node_info(_patch("t1", "c3", status=Status.RUNNING, run_mode="bbs", assignee="bbs1"))
        results = _run(runner.start_run([svc._get_node(graph, "c1"), svc._get_node(graph, "c2"), svc._get_node(graph, "c3")]))
        assert results == [True, True, True]
        assert len(runner._run_log) == 3
        assert {e["run_mode"] for e in runner._run_log} == {"single_bot", "coop_group", "bbs"}

    def test_invalid_mode_returns_false(self, svc, graph):
        runner = TaskRunner(svc)
        _dispatch(svc, graph, ["c1"])
        node = svc._get_node(graph, "c1")
        node.run_info.run_mode = None  # 强制非法
        results = _run(runner.start_run([node]))
        assert results == [False]
        assert runner._run_log == []  # 非法不记日志


# ===== form_coop_group =====
class TestFormCoopGroup:
    def test_generates_group_id_and_records(self, svc):
        runner = TaskRunner(svc)
        gf = GroupFormation(bot_ids=["bot_a", "bot_b"], collab_mode="manager_worker")
        gid = _run(runner.form_coop_group(gf))
        assert gid.startswith("grp_")
        assert len(gid) > len("grp_")
        assert runner._groups[gid] is gf

    def test_distinct_group_ids(self, svc):
        runner = TaskRunner(svc)
        gf = GroupFormation(bot_ids=["bot_a"], collab_mode="chat")
        g1 = _run(runner.form_coop_group(gf))
        g2 = _run(runner.form_coop_group(gf))
        assert g1 != g2
        assert len(runner._groups) == 2

    def test_records_collab_modes(self, svc):
        runner = TaskRunner(svc)
        for cm in ("chat", "manager_worker", "state_machine"):
            _run(runner.form_coop_group(GroupFormation(bot_ids=["b"], collab_mode=cm)))
        modes = {g.collab_mode for g in runner._groups.values()}
        assert modes == {"chat", "manager_worker", "state_machine"}


# ===== query_* =====
class TestQuery:
    def test_query_status_returns_graph_status(self, svc, graph):
        runner = TaskRunner(svc)
        assert runner.query_status("t1") == Status.RUNNING
        graph.status = Status.DONE
        assert runner.query_status("t1") == Status.DONE

    def test_query_detail_backfills_node(self, svc, graph):
        runner = TaskRunner(svc)
        _dispatch(svc, graph, ["c1"])
        shell = _child("c1")
        shell.run_info.assignee = None
        detail = runner.query_detail(shell)
        assert detail.run_info.run_mode == "single_bot"
        assert detail.run_info.assignee == "bot1"
        assert detail.status == Status.RUNNING

    def test_query_detail_unknown_node_returns_input(self, svc, graph):
        runner = TaskRunner(svc)
        shell = _child("nope")
        ret = runner.query_detail(shell)
        assert ret is shell  # 图内无 → 原样返回

    def test_query_result_backfills_output(self, svc, graph):
        runner = TaskRunner(svc)
        _dispatch(svc, graph, ["c1"])
        svc.update_task_node_info(_patch("t1", "c1", output_patch={"data": "行业全貌"}))
        ret = runner.query_result(_child("c1"))
        assert ret.run_info.output.get("data") == "行业全貌"

    def test_query_bot_tasks_stub_empty(self, svc):
        runner = TaskRunner(svc)
        assert runner.query_bot_tasks("bot_market") == []  # Avernet stub


# ===== _build_context 双模式 =====
class TestBuildContext:
    def test_verify_mode_when_has_children(self, svc, graph):
        runner = TaskRunner(svc)
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.add_task_nodes([_child("c1a"), _child("c1b")], parent_node_id="c1")
        svc.update_task_node_info(_patch("t1", "c1a", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1b", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1a", output_patch={"data": "d_a"}, acceptance_result=_pass()))
        ctx = runner._build_context("t1", "c1")
        assert ctx["mode"] == "verify"
        assert ctx["child_outputs"] == {"c1a": _node_output(svc, "c1a")}
        assert ctx["goal"] is not None  # node.task_spec.goal
        assert ctx["node_instruction"] == "do"

    def test_execute_mode_when_leaf_with_parent(self, svc, graph):
        runner = TaskRunner(svc)
        svc.add_task_nodes([_child("c1"), _child("c2")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1", acceptance_result=_pass()))
        ctx = runner._build_context("t1", "c2")
        assert ctx["mode"] == "execute"
        assert ctx["parent_node_id"] == "t1"
        assert ctx["parent_spec"] is not None
        assert ctx["sibling_outputs"] == {"c1": {}}  # c1 DONE 输出为空 dict
        assert ctx["node_spec"] is not None

    def test_execute_mode_includes_done_sibling_outputs(self, svc, graph):
        runner = TaskRunner(svc)
        svc.add_task_nodes([_child("c1"), _child("c2")], parent_node_id="t1")
        svc.update_task_node_info(_patch("t1", "c1", status=Status.RUNNING, run_mode="single_bot", assignee="b"))
        svc.update_task_node_info(_patch("t1", "c1", output_patch={"data": "行业全貌"}, acceptance_result=_pass()))
        ctx = runner._build_context("t1", "c2")
        assert ctx["mode"] == "execute"
        assert ctx["sibling_outputs"] == {"c1": _node_output(svc, "c1")}

    def test_execute_mode_root_leaf_no_parent(self, svc, graph):
        runner = TaskRunner(svc)
        ctx = runner._build_context("t1", "t1")  # 根节点无结构父(边界)
        assert ctx["mode"] == "execute"
        assert ctx["parent_node_id"] is None
