"""M6 singlebox E2E:任务目标驱动执行框架端到端(权威剧本 gwqie46v7hzr1w6h 机制)。

全模块接(真实 TaskGraphService/ExecutionEngine/TaskPlanner/TaskDispatcher/TaskRunner),
CaseEngine 子类覆写 _build_* 注入 case 策略 adapter(CaseDecomposer/CaseBotDiscover)+ test runner stub。
覆盖三模态(single_bot / coop_group / bbs)+ FAIL 补救治愈 + MISS 升 BBS + BBS bot 认领 + STUCK→HUNG。
节点名为 stub 产出(非框架写死);框架零 case 知识。验收 100% 回投(无 verify port);BBS 投递归 runner(无 market)。
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceVerdict,
    Context,
    Goal,
    Metadata,
    PlanResult,
    RelationType,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
    TaskCallbackData,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_dispatch.strategies import (
    GroupFormation,
    SearchOutcome,
    SearchResult,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


# ===== domain helpers =====
def _task_info(task_id: str = "t_case", *, max_depth: int = 3) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="存储行业尽调", instruction="produce a DD report"),
            context=Context(background="存储行业"),
            goal=Goal(
                objective="产出一份尽调报告",
                acceptances=[AcceptanceCriteria(id=f"ac{i}", description=f"d{i}") for i in range(1, 6)],
            ),
        ),
        source_channel_type="bot",
        source_channel_id="owner_bot",
        execution_config={"MAX_DEPTH": max_depth},
    )


def _node(node_id: str, task_id: str, *, run_mode: str | None = None, assignee: str | None = None) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec,
        run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
        node_run_graph=None,  # type: ignore[arg-type]  store 回填
    )


def _cb(success: bool, loop_task_id: str, *, data="done", fail_detail=None) -> TaskCallbackData:
    result: dict = {"success": success}
    if data is not None:
        result["data"] = data
    if fail_detail is not None:
        result["fail_detail"] = fail_detail
    return TaskCallbackData(
        loop_task_id=loop_task_id,
        workflow_type="single_bot",
        workflow_id=1,
        instance_id=1,
        result=result,
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ===== case decomposer(三阶段 AC 拆解 + FAIL/MISS 叶补救)=====
class CaseDecomposer:
    """按权威剧本三阶段 AC 步进拆 root 批;FAILED+gaps 叶 / MISS 叶 → 产补救子挂其下。

    BBS 批(N_practice_bbs)只产一次(``_bbs_attempted``);升 BBS 后被 remove 或被 BBS 认领子
    (N_practice_* prefix)替代后,据"practice 已 fulfilled"直接进批4 N_report,不重复产 BBS 批。
    """

    FOUR = ("N_market", "N_tech", "N_compete", "N_customer")

    def __init__(self, task_id: str = "t_case"):
        self._task_id = task_id
        self._bbs_attempted = False

    def decompose(self, graph) -> list[TaskNode]:
        # 1) 先处理 FAIL/MISS 叶补救(优先于 root 批)
        for n in graph.tasks:
            if n.task_id != self._task_id:
                continue
            if self._has_child(graph, n.node_id):
                continue
            if n.status == Status.FAILED and n.run_info.acceptance_result and n.run_info.acceptance_result.gaps:
                return [self._node(f"{n.node_id}_remedy")]
            if n.status == Status.PENDING and n.run_info.extend_props.get("miss_events"):
                return [self._node(f"{n.node_id}_miss_remedy")]
        # 2) root 批步进
        root = self._root(graph)
        if root is None or root.task_id != self._task_id:
            return []
        children = self._children(graph, root.node_id)
        child_ids = {c.node_id for c in children}
        if not children and root.status == Status.PENDING:
            return [self._node("N_overview")]
        if child_ids == {"N_overview"} and self._is_done(graph, "N_overview"):
            return [self._node(nid) for nid in self.FOUR]
        four_done = all(self._is_done(graph, nid) for nid in self.FOUR) and set(self.FOUR).issubset(child_ids)
        # 批3 BBS:只产一次(未尝试且未存在 N_practice_bbs)
        if four_done and not self._bbs_attempted and "N_practice_bbs" not in child_ids:
            self._bbs_attempted = True
            return [self._node("N_practice_bbs")]
        # 批4 N_report:practice 已 fulfilled(N_practice_bbs 或 BBS 认领子 N_practice_* DONE)且无 N_report
        practice_fulfilled = any(
            self._is_done(graph, cid) and cid.startswith("N_practice") for cid in child_ids
        )
        if four_done and practice_fulfilled and "N_report" not in child_ids:
            return [self._node("N_report")]
        return []

    # graph 派生 helper(自持,不依赖 TaskGraphService)
    def _node(self, node_id: str) -> TaskNode:
        return _node(node_id, self._task_id)

    def _root(self, graph):
        for n in graph.tasks:
            if n.task_id == self._task_id and not any(
                r.dst_id == n.node_id and r.type == RelationType.DEPENDENCY for r in graph.relations
            ):
                return n
        return None

    def _has_child(self, graph, node_id):
        return any(r.src_id == node_id and r.type == RelationType.DEPENDENCY for r in graph.relations)

    def _children(self, graph, node_id):
        ids = [r.dst_id for r in graph.relations if r.src_id == node_id and r.type == RelationType.DEPENDENCY]
        return [n for n in graph.tasks if n.node_id in ids]

    def _is_done(self, graph, node_id):
        return any(n.node_id == node_id and n.status == Status.DONE for n in graph.tasks)


class CaseBotDiscover:
    """本地 catalog:按 node_id 决定搜推结果。happy 用(阶段三搜推 HIT);escalate 用 MISS。"""

    def __init__(self, miss_nodes: set[str] | None = None, task_id: str = "t_case"):
        self._miss = miss_nodes or set()
        self._task_id = task_id

    def search(self, node: TaskNode) -> SearchResult:
        nid = node.node_id
        if nid in self._miss:
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason=f"no_bot_for_{nid}")
        if nid in ("N_market", "N_tech", "N_customer"):
            bots = [f"bot_{nid}_a", f"bot_{nid}_b", f"bot_{nid}_c"]
            return SearchResult(
                outcome=SearchOutcome.HIT_MULTI_BOTS,
                group_formation=GroupFormation(bot_ids=bots[:2] if nid == "N_market" else bots,
                                               collab_mode="manager_worker"),
            )
        if nid == "N_compete":
            return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="供应链专家Bot")
        if nid == "N_overview":
            return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="行业信息抓取Bot")
        if nid == "N_report":
            return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="报告聚合Bot")
        if nid == "N_practice_bbs":
            return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="实践Bot")
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="bot_default")


# ===== adapter:包 CaseDecomposer/CaseBotDiscover 成 PlanningStrategy/DispatchStrategy =====
class _CasePlanningStrategy:
    rule_id = "case"
    priority = 5

    def __init__(self, decomposer: CaseDecomposer):
        self._d = decomposer

    async def matches(self, graph) -> bool:
        return True

    async def apply(self, graph, target) -> PlanResult:
        kids = self._d.decompose(graph)
        return PlanResult(children=kids, has_gap=bool(kids))


class _CaseDispatchStrategy:
    rule_id = "case"
    priority = 5

    def __init__(self, discover: CaseBotDiscover):
        self._d = discover

    async def matches(self, node, graph) -> bool:
        return True

    async def apply(self, node, graph) -> SearchResult:
        return self._d.search(node)


# ===== test runner stub:记投递日志 + form_coop_group 记群 =====
class _TestRunner:
    def __init__(self, graph):
        self._graph = graph
        self._groups: list[GroupFormation] = []
        self._run_log: list[dict] = []

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        for node in toDoTaskList:
            self._run_log.append({
                "task_id": node.task_id, "node_id": node.node_id,
                "run_mode": node.run_info.run_mode, "assignee": node.run_info.assignee,
                "loop_task_id": f"{node.task_id}::{node.node_id}",
            })
        return [True] * len(toDoTaskList)

    async def form_coop_group(self, gf: GroupFormation) -> str:
        import uuid
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        self._groups.append(gf)
        return gid

    def query_status(self, task_id): return self._graph.query_task_dashboard(task_id).status
    def query_detail(self, node): return node
    def query_result(self, node): return node
    def query_bot_tasks(self, bot_id): return []
    def set_delivery(self, mode, port): pass


# ===== CaseEngine:覆写 _build_* 注入 case 策略 + test runner =====
class _CaseEngine(ExecutionEngine):
    def __init__(self, graph, decomposer, discover, runner=None):
        self._case_decomposer = decomposer
        self._case_discover = discover
        self._case_runner = runner
        super().__init__(graph)

    def _build_planner(self):
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        p = TaskPlanner(self._graph)
        p.set_strategies([_CasePlanningStrategy(self._case_decomposer)])
        return p

    def _build_dispatcher(self):
        from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
        d = TaskDispatcher(self._graph)
        d.set_strategies([_CaseDispatchStrategy(self._case_discover)])
        return d

    def _build_runner(self):
        return self._case_runner if self._case_runner is not None else super()._build_runner()


class _CaseTaskService(TaskService):
    def __init__(self, graph, decomposer, discover, runner=None, harness=None):
        self._cd = decomposer
        self._cbd = discover
        self._cr = runner
        super().__init__(graph, harness=harness)

    def _build_engine(self, *, bot=None, bcs=None, discover=None) -> ExecutionEngine:
        # case 测试覆写:注入 stub 策略/投递的 _CaseEngine(忽略传入端口)
        return _CaseEngine(self._graph, self._cd, self._cbd, self._cr)


def _patch(task_id: str, node_id: str, **kw) -> "object":
    """构造 TaskNodePatch(测试 on_harness 直调用)。"""
    from agentclaw.community.core.task.domain.models import TaskNodePatch as _TNP
    return _TNP(task_id=task_id, node_id=node_id, **kw)


def _wire_facade(*, task_id="t_case", max_depth=3, miss_nodes=None) -> tuple:
    """接线 case facade:真实 TaskGraphService + ExecutionEngine(经 _CaseEngine 注入 case 策略 stub)。
    v4:bbs_max_depth 已废(图级总轮次由 MAX_LOOP=10 承载);MISS/FAIL 补救改为 harness 重新派发执行(不拆子)。"""
    svc = TaskGraphService()
    runner = _TestRunner(svc)
    decomposer = CaseDecomposer(task_id=task_id)
    discover = CaseBotDiscover(miss_nodes=miss_nodes, task_id=task_id)
    facade = _CaseTaskService(svc, decomposer, discover, runner=runner)
    return facade, svc, runner


# ===== Test 1: 三模态 happy(单+协作群)→ 根终验回投 → graph DONE =====
class TestThreeModesHappyToDone:
    def test_full_flow(self):
        facade, svc, runner = _wire_facade()
        root_id = "t_case"
        _run(facade.execute(_task_info(root_id)))
        g = svc.query_task_dashboard(root_id)
        ov = svc._get_node(g, "N_overview")
        assert ov.run_info.run_mode == "single_bot"
        assert ov.run_info.assignee == "行业信息抓取Bot"
        assert ov.status == Status.RUNNING
        # v4:规划出子的父恒为 PLANNING(委托/编排态),RUNNING 只给真正派发执行的叶子
        assert svc._get_node(g, root_id).status == Status.PLANNING
        assert svc._get_node(g, root_id).run_info.run_mode == "planning"
        assert len(runner._run_log) == 1

        # 回投 N_overview PASS → 批2 four专题
        _run(facade.callback.report_result(_cb(True, f"{root_id}::N_overview", data="行业全貌")))
        g = svc.query_task_dashboard(root_id)
        assert svc._get_node(g, "N_overview").status == Status.DONE
        for nid, expect_mode in (("N_market", "coop_group"), ("N_tech", "coop_group"),
                                 ("N_compete", "single_bot"), ("N_customer", "coop_group")):
            n = svc._get_node(g, nid)
            assert n.run_info.run_mode == expect_mode, f"{nid} mode={n.run_info.run_mode}"
            assert n.status == Status.RUNNING, f"{nid} status={n.status}"
        # 动态拉群 N_market/N_tech/N_customer
        assert len(runner._groups) == 3
        gids = {n.run_info.assignee for n in (svc._get_node(g, "N_market"), svc._get_node(g, "N_tech"),
                                              svc._get_node(g, "N_customer"))}
        assert all(str(gi).startswith("grp_") for gi in gids)
        assert len(runner._run_log) == 5
        for nid in ("N_market", "N_tech", "N_compete", "N_customer"):
            _run(facade.callback.report_result(_cb(True, f"{root_id}::{nid}", data=f"{nid}_out")))
        g = svc.query_task_dashboard(root_id)
        pb = svc._get_node(g, "N_practice_bbs")
        assert pb.run_info.run_mode == "single_bot"
        assert pb.status == Status.RUNNING
        for nid in ("N_market", "N_tech", "N_compete", "N_customer", "N_overview"):
            assert svc._get_node(g, nid).status == Status.DONE

        _run(facade.callback.report_result(_cb(True, f"{root_id}::N_practice_bbs", data="一手实践")))
        g = svc.query_task_dashboard(root_id)
        assert svc._get_node(g, "N_report").run_info.run_mode == "single_bot"
        assert svc._get_node(g, "N_report").status == Status.RUNNING

        # 语义A:回投 N_report PASS → 根 plan[]→ gap 闭=终验通过 → 翻根 DONE + graph DONE(无需回投根 PASS)
        _run(facade.callback.report_result(_cb(True, f"{root_id}::N_report", data="尽调报告")))
        g = svc.query_task_dashboard(root_id)
        assert svc._get_node(g, root_id).status == Status.DONE  # gap 闭=终验通过→翻根 DONE(语义A)
        assert g.status == Status.DONE
        assert all(n.status == Status.DONE for n in g.tasks)

    def test_relations_decomposition_tree_single_in(self):
        facade, svc, *_ = _wire_facade()
        _run(facade.execute(_task_info("t_case")))
        _run(facade.callback.report_result(_cb(True, "t_case::N_overview", data="x")))
        g = svc.query_task_dashboard("t_case")
        non_root = [n for n in g.tasks if n.node_id != "t_case"]
        for n in non_root:
            parents = [r.src_id for r in g.relations if r.dst_id == n.node_id]
            assert len(parents) == 1, f"{n.node_id} 入边数={len(parents)}"
            assert parents[0] == "t_case"


# ===== Test 2: FAIL→FAILED→harness 重新派发执行→PASS 治愈(v4:补救=重新派发,不拆子) =====
class TestFailRemedyCure:
    def test_fail_then_remedy_pass_cures_and_propagates(self):
        facade, svc, runner = _wire_facade(max_depth=3)
        _run(facade.execute(_task_info("t_case", max_depth=3)))
        _run(facade.callback.report_result(_cb(True, "t_case::N_overview", data="overview")))
        _run(facade.callback.report_result(_cb(True, "t_case::N_compete", data="compete")))
        # 验收不过 → FAILED(v4:不立即补救拆子,交 harness 重新派发执行重试)
        _run(facade.callback.report_result(
            _cb(False, "t_case::N_market", fail_detail="市场深度不足", data=None)
        ))
        g = svc.query_task_dashboard("t_case")
        n_market = svc._get_node(g, "N_market")
        assert n_market.status == Status.FAILED
        assert n_market.run_info.acceptance_result is not None
        assert n_market.run_info.acceptance_result.verdict == AcceptanceVerdict.FAIL
        # v4:harness 重新派发执行(不拆):FAILED→PENDING→dispatch→RUNNING
        _run(facade._engine.on_harness(_patch("t_case", "N_market", exec_error="acceptance_fail_retry")))
        g = svc.query_task_dashboard("t_case")
        assert svc._get_node(g, "N_market").status == Status.RUNNING  # 重新派发执行
        # 重新派发后回投 PASS → DONE
        _run(facade.callback.report_result(_cb(True, "t_case::N_market", data="市场深化")))
        g = svc.query_task_dashboard("t_case")
        assert svc._get_node(g, "N_market").status == Status.DONE
        _run(facade.callback.report_result(_cb(True, "t_case::N_tech", data="tech")))
        _run(facade.callback.report_result(_cb(True, "t_case::N_customer", data="cust")))
        g = svc.query_task_dashboard("t_case")
        assert svc._get_node(g, "N_practice_bbs").status == Status.RUNNING


# ===== Test 3: MISS at max → HUNG(节点保留)+升 BBS =====
class TestMissEscalateBbs:
    def test_miss_at_max_escalates_bbs(self):
        facade, svc, runner = _wire_facade(max_depth=1, miss_nodes={"N_practice_bbs"})
        _run(facade.execute(_task_info("t_case", max_depth=1)))
        _run(facade.callback.report_result(_cb(True, "t_case::N_overview", data="overview")))
        for nid in ("N_market", "N_tech", "N_compete", "N_customer"):
            _run(facade.callback.report_result(_cb(True, f"t_case::{nid}", data=nid)))
        g = svc.query_task_dashboard("t_case")
        # v4:MISS at max(depth>=MAX)→HUNG(节点保留不 remove)+升 BBS(bbs_mode,loop_round++)
        node = next((n for n in g.tasks if n.node_id == "N_practice_bbs"), None)
        assert node is not None  # 节点保留
        assert node.status == Status.HUNG
        assert g.extend_props.get("bbs_mode") is True
        assert g.loop_round >= 1  # 升 BBS 自增图级 loop_round

    def test_bbs_bot_claims_and_drives(self):
        """模拟"真 BBS 执行接管"(out-of-band;singlebox 无 BCS BBS 真执行经框架状态机不可达,测试直写复位模拟):
        BBS bot 认领任务 → 复位图态/根委托态/已 HUNG 的 N_practice_bbs 标 DONE(BBS 已 fulfilled)
        → 自规划子任务 N_practice_bbs_bbs 挂 root → 执行 → 回投 → 根重 plan → N_report → 终验 DONE。"""
        facade, svc, runner = _wire_facade(max_depth=1, miss_nodes={"N_practice_bbs"})
        _run(facade.execute(_task_info("t_case", max_depth=1)))
        _run(facade.callback.report_result(_cb(True, "t_case::N_overview", data="overview")))
        for nid in ("N_market", "N_tech", "N_compete", "N_customer"):
            _run(facade.callback.report_result(_cb(True, f"t_case::{nid}", data=nid)))
        g = svc.query_task_dashboard("t_case")
        # MISS at max→HUNG→升 BBS→终态传播冒泡→图 HUNG(singlebox 无 BBS 真执行)
        hung_node = next((n for n in g.tasks if n.node_id == "N_practice_bbs"), None)
        assert hung_node is not None and hung_node.status == Status.HUNG
        assert g.extend_props.get("bbs_mode") is True
        # 模拟 BBS 接管:out-of-band 复位(经状态机从 HUNG 不可恢复,直写模拟 BCS BBS 接管复位)
        g.status = Status.RUNNING
        root = svc._get_node(g, "t_case")
        root.status = Status.PLANNING
        hung_node.status = Status.DONE  # BBS bot 认领后 N_practice_bbs 被 fulfilled
        # BBS bot 自规划子任务(run_mode=bbs,挂 root 下)
        bbs_sub = _node("N_practice_bbs_bbs", "t_case", run_mode="bbs", assignee="bot_bbs_7")
        svc.add_task_nodes([bbs_sub], parent_node_id="t_case")
        # Task 9 守卫(FR-EXT-06):_prepare_into 跳过 run_mode=bbs 节点,框架不自动派发/翻态;
        # bbs 叶由 bot 经 attach_bbs_node 自驱(create+start 合一:PENDING→RUNNING),此处直翻模拟启动
        svc.update_task_node_info(
            TaskNodePatch(task_id="t_case", node_id="N_practice_bbs_bbs", status=Status.RUNNING))
        g = svc.query_task_dashboard("t_case")
        assert svc._get_node(g, "N_practice_bbs_bbs").status == Status.RUNNING
        assert svc._get_node(g, "N_practice_bbs_bbs").run_info.run_mode == "bbs"
        _run(facade.callback.report_result(_cb(True, "t_case::N_practice_bbs_bbs", data="bbs 一手实践")))
        g = svc.query_task_dashboard("t_case")
        assert svc._get_node(g, "N_practice_bbs_bbs").status == Status.DONE
        assert svc._get_node(g, "N_report").status == Status.RUNNING
        _run(facade.callback.report_result(_cb(True, "t_case::N_report", data="尽调报告聚合")))
        g = svc.query_task_dashboard("t_case")
        # 语义A:根 plan[]→ gap 闭=终验通过 → 翻根 DONE + graph DONE
        assert svc._get_node(g, "t_case").status == Status.DONE
        assert g.status == Status.DONE


# ===== Test 4: MISS at max → 终态传播冒泡 → 图 HUNG(v4:HUNG 节点保留) =====
class TestBbsStuckHung:
    def test_miss_at_max_graph_hung(self):
        facade, svc, runner = _wire_facade(max_depth=1, miss_nodes={"N_practice_bbs"})
        _run(facade.execute(_task_info("t_case", max_depth=1)))
        _run(facade.callback.report_result(_cb(True, "t_case::N_overview", data="overview")))
        for nid in ("N_market", "N_tech", "N_compete", "N_customer"):
            _run(facade.callback.report_result(_cb(True, f"t_case::{nid}", data=nid)))
        g = svc.query_task_dashboard("t_case")
        # v4:MISS at max→HUNG→升 BBS→终态传播冒泡(子含 HUNG→父 HUNG→根 HUNG)→图 HUNG
        assert g.status == Status.HUNG
        node = next((n for n in g.tasks if n.node_id == "N_practice_bbs"), None)
        assert node is not None  # HUNG 节点保留(不 remove)
        assert node.status == Status.HUNG
        assert g.extend_props.get("bbs_mode") is True
        assert g.extend_props.get("hung_reason") in ("root_stuck", "child_hung", "loop_exhausted")


# ===== Test 5: dashboard 事件可重放(view 终态断言) =====
class TestDashboardTerminal:
    def test_query_result_and_detail(self):
        facade, svc, *_ = _wire_facade()
        _run(facade.execute(_task_info("t_case")))
        _run(facade.callback.report_result(_cb(True, "t_case::N_overview", data="行业全貌")))
        from agentclaw.community.core.task.task_runner.runner import TaskRunner
        r = TaskRunner(svc)
        detail = r.query_detail(_node("N_overview", "t_case"))
        assert detail.run_info.output.get("data") == "行业全貌"
        assert r.query_result(_node("N_overview", "t_case")).run_info.output.get("data") == "行业全貌"
