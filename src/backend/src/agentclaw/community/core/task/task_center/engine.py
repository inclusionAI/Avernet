"""ExecutionEngine 内部编排核(事件驱动 + 状态条件触发)。对齐 plan.md §3.0。

非独立模块,TaskService 内部实现细节,对外不暴露。构造期收传输端口(bot/bcs/discover,由 DI 从配置注入),
``_build_*`` 内部 new 引擎自带策略(TaskPlanner/TaskDispatcher/TaskRunner)+ 接线 TaskExecutor(三模态投递+poller)。
引擎自身实现 ResultSink(poller 终态回投直接调 on_report)与 TaskContextBuilder(执行上下文派生),
消除"先建 stub 再外部注入真实 body/接线点"的后填,无引擎子类化、无 reach-in setter。验收 100% 走 on_report
回投(gap 计算即验收,无主动 verify dispatch);BBS 投递归 runner BBS 模态(无 BbsMarketPort,升 BBS 只翻图态 bbs_mode)。
零 case 知识:engine 不含任何节点名字面量。测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。

Step2 改造(状态机解耦 + PlanResult + 显式 target + harness 执行报错区分):
- 状态机:PLANNING=规划中(显式委托态;on_pass 翻父 RUNNING→PLANNING 后 plan),RUNNING=执行中(子执行/自身执行);
  add_task_nodes 翻父→RUNNING(委托)。终验=根 gap 闭(plan 返 []+has_gap=F)→翻根 DONE + 图 DONE。
- plan(graph, target_node_id):on_execute→None(自发现根)/on_pass→parent/on_fail→failed 叶/on_miss→miss 叶。
  返 PlanResult(children, has_gap, gap_detail) 四象限:children→add+dispatch;空+has_gap=F→gap 闭 DONE;
  空+has_gap=T→深度闸门(升 BBS/HUNG)。
- harness 执行报错(exec_error):bot 压根没跑通(run FAILED/SLA/poll 耗尽)≠ 验收不过(run COMPLETED+FAIL)。
  执行报错→on_harness 复位 RUNNING→PENDING 重投;计 harness_retries,达 MAX_HARNESS(默认 3)→HUNG 不再流转。
  验收不过(acceptance FAIL+gaps)→on_fail 补救重规划(深度闸门)。

协程化(CR 反馈:任务执行是耗时任务):全链路 ``async def``。锁内 await plan/dispatch(同 task 串行 IO,设计意图);
投递/拉群 IO 锁外 await,gather+Semaphore 下沉。副作用收集:on_* 锁内 async collect → 锁外 ``_drain`` await 执行。
注:``threading.RLock`` 跨线程正确串行;corp 单持久 loop 并发同 task 回投需切 ``asyncio.Lock``(ocb 接入时定)。
"""
from __future__ import annotations

import logging
import threading

from agentclaw.community.core.task.domain.errors import NodeNotFoundError, TaskStateError
from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    NodeOpResult,
    Status,
    TaskCallbackData,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
)


logger = logging.getLogger("task.engine")

_DEFAULT_MAX_HARNESS = 3  # 执行报错 harness 重投上限(达上限→HUNG)


class ExecutionEngine:
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution(协程化,全链路 async)。

    构造期收传输端口(bot/bcs/discover,DI 从配置注入),``_build_*`` 内部 new 引擎自带策略 + 接线 TaskExecutor。
    引擎自当 ResultSink(poller 终态回投→on_report)与 TaskContextBuilder(执行上下文派生),消除后填/back-reach-in。
    on_* 入参统一收口 TaskNodePatch。按事件 + 状态条件分段协调。同 task_id 串行(per-task RLock);
    跨 task 并行。投递/拉群 IO 锁外 await,gather+Semaphore 并发。loop_round 仅升 BBS 时 ++。
    测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。"""

    def __init__(self, graph, *, bot=None, bcs=None, discover=None) -> None:
        """graph: TaskGraphService;bot: OpenApiBotPort;bcs: BcsClientPort;discover: BotDiscoverServiceProtocol。
        端口由 DI 从配置注入(local/prod/double 只换端口实现,引擎代码不变)。prod 必传;测试子类覆写
        ``_build_*`` 注入 stub 策略/投递时可省略(走 super 路径默认 berth)。"""
        self._graph = graph
        self._bot = bot
        self._bcs = bcs
        self._discover = discover
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.RLock()
        from agentclaw.community.core.task.task_runner.callback_adapter import CallbackAdapter
        self._cb_adapter = CallbackAdapter()
        self._poller_thread = None
        self._executor = self._build_executor()
        self._planner = self._build_planner()
        self._dispatcher = self._build_dispatcher()
        self._runner = self._build_runner()

    # ===== protected 工厂方法(测试子类可覆写注入 stub 策略/投递;引擎自带默认接真实端口)=====
    def _build_executor(self):
        if self._bot is None or self._bcs is None:
            return None
        from agentclaw.community.core.task.task_runner.integration.task_executor import TaskExecutor
        from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
            TaskExecutorResultPoller,
        )
        from agentclaw.community.core.task.task_runner.integration.prompt_formatter import (
            PromptFormatterImpl,
        )
        poller = TaskExecutorResultPoller(bot=self._bot, bcs=self._bcs)
        poller.set_on_result(self)
        exe = TaskExecutor(
            bot=self._bot, bcs=self._bcs, formatter=PromptFormatterImpl(),
            context=self, sink=self, poller=poller,
        )
        import threading as _t
        self._poller_thread = _t.Thread(target=poller.run_poll_loop, daemon=True, name="task-exec-poller")
        self._poller_thread.start()
        return exe

    def _build_planner(self):
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        from agentclaw.community.core.task.task_plan.strategies import (
            GapBasedPlanningStrategy, WorkflowPlanningStrategy,
        )
        pool = [WorkflowPlanningStrategy()]
        if self._bot is not None:
            pool.append(GapBasedPlanningStrategy(self._bot))
        else:
            pool.append(GapBasedPlanningStrategy())
        return TaskPlanner(self._graph, pool=pool)

    def _build_dispatcher(self):
        from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
        from agentclaw.community.core.task.task_dispatch.strategies import (
            DirectDispatchStrategy, SearchBasedDispatchStrategy,
        )
        pool = [DirectDispatchStrategy()]
        if self._bot is not None and self._discover is not None:
            pool.append(SearchBasedDispatchStrategy(self._bot, self._discover))
        else:
            pool.append(SearchBasedDispatchStrategy())
        return TaskDispatcher(self._graph, pool=pool)

    def _build_runner(self):
        from agentclaw.community.core.task.task_runner.runner import TaskRunner
        return TaskRunner(self._graph, execution_backend=self._executor)

    # ===== ResultSink impl:poller 终态回投直接调 on_report =====
    async def report_result(self, data: "TaskCallbackData") -> None:
        """引擎自当 ResultSink:TaskExecutorResultPoller 终态→TaskCallbackData→TaskNodePatch→on_report。
        与外部 HTTP push 回投(TaskLoopCallback.report_result→on_report)收敛同一入口。"""
        patch = self._cb_adapter.adapt(data)
        await self.on_report(patch)

    # ===== TaskContextBuilder impl =====
    def build(self, task_id: str, node_id: str) -> dict:
        """引擎自当 TaskContextBuilder:派生 execute 模式上下文(叶子/聚合均 execute;gap 计算即验收,
        无 verify 模式 dispatch)。siblings_outputs 取本节点的兄弟(DONE 的 run_info.output)。"""
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            return {"mode": "execute", "parent_node_id": None, "parent_spec": None,
                    "sibling_outputs": {}, "node_spec": node.task_spec if node else None}
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        sibling_outputs = {
            s.node_id: s.run_info.output
            for s in siblings if s.status == Status.DONE and s.node_id != node_id
        }
        return {"mode": "execute", "parent_node_id": parent.node_id,
                "parent_spec": parent.task_spec, "sibling_outputs": sibling_outputs,
                "node_spec": node.task_spec if node else None}

    def _lock_for(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            lk = self._locks.get(task_id)
            if lk is None:
                lk = threading.RLock()
                self._locks[task_id] = lk
            return lk

    def _root(self, task_id: str) -> TaskNode | None:
        graph = self._graph.query_task_dashboard(task_id)
        for n in graph.tasks:
            if self._graph.get_parent_task(task_id, n.node_id) is None:
                return n
        return None

    def _max_harness(self, task_id: str) -> int:
        cfg = self._graph._execution_config(task_id)
        return int(cfg.get("MAX_HARNESS", _DEFAULT_MAX_HARNESS))

    def _mark_planning(self, task_id: str, node_id: str) -> None:
        """节点被 owner bot 规划时,落 run_mode="planning" + assignee=owner(source_channel_id)。
        规划本身就是 owner bot 的 bot 工作(组 prompt→owner bot 算 gap 产子),故节点应归属 owner,
        而非 run_mode/assignee 留空。叶子派发执行时由 _prepare_into 覆写为 single_bot/coop_group/bbs+worker。"""
        graph = self._graph.query_task_dashboard(task_id)
        owner = str(graph.extend_props.get("source_channel_id") or "")
        if not owner:
            return
        self._graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=node_id, run_mode="planning", assignee=owner)
        )

    # ===== on_execute =====
    async def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph 后,条件 a(根 PENDING)→ plan(None 自发现根)→add→dispatch→start_run。"""
        side: list[tuple] = []
        with self._lock_for(task_id):
            root = self._root(task_id)
            logger.info("[on_execute] task=%s root=%s status=%s", task_id,
                        root.node_id if root else None, root.status if root else None)
            if root is None or root.status != Status.PENDING:
                logger.info("[on_execute] task=%s 非条件 a(根非 PENDING),跳过", task_id)
                return
            graph = self._graph.query_task_dashboard(task_id)
            self._mark_planning(task_id, root.node_id)  # root 由 owner bot 规划
            pr = await self._planner.plan(graph)   # None → 自发现根
            logger.info("[on_execute] task=%s plan 产 %d 子节点: %s",
                        task_id, len(pr.children), [n.node_id for n in pr.children])
            if pr.children:
                self._graph.add_task_nodes(pr.children, root.node_id)
                await self._prepare_into(task_id, side)
            elif not pr.has_gap:
                self._maybe_finish_graph(task_id)  # 根 gap 初始即闭(罕见)
            else:
                self._escalate_bbs(task_id, root.node_id)  # 有 gap 拆不出 → 升 BBS
        await self._drain(task_id, side)

    # ===== on_start =====
    async def on_start(self, patch: TaskNodePatch) -> NodeOpResult:
        """入站 start 回调:PENDING→RUNNING(幂等)。纯节点态翻转,不触发传播/side-effect。"""
        with self._lock_for(patch.task_id):
            graph = self._graph.query_task_dashboard(patch.task_id)
            node = next((n for n in graph.tasks if n.node_id == patch.node_id), None)
            if node is None:
                raise NodeNotFoundError(
                    f"on_start: node not found {patch.task_id}::{patch.node_id}"
                )
            if node.status == Status.RUNNING:
                return NodeOpResult(
                    task_id=patch.task_id, node_id=patch.node_id, success=True,
                    prev_status=Status.RUNNING, new_status=Status.RUNNING,
                )
            if node.status in {Status.DONE, Status.FAILED, Status.HUNG, Status.PLANNING}:
                raise TaskStateError(
                    f"on_start: stale/illegal start on {node.status} node "
                    f"{patch.task_id}::{patch.node_id}"
                )
            return self._graph.update_task_node_info(patch)

    # ===== on_report:三路分流(exec_error→harness / PASS→on_pass / FAIL→on_fail)=====
    async def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """回投事件:patch 内含 (task_id,node_id)+终态翻转依据。
        三路分流(互斥):
        ① ``exec_error`` 非空 → 执行报错(bot 没跑通)→ on_harness 复位重投(计数,达上限 HUNG);
        ② ``acceptance_result`` PASS → on_pass(DONE 传播/前向 plan);
        ③ ``acceptance_result`` FAIL+gaps → on_fail(补救重规划,深度闸门);
        无两者 → 仅 fold,返回。验收 100% 来自回投,engine 不主动验。"""
        logger.info("[on_report] task=%s node=%s exec_error=%s verdict=%s",
                    patch.task_id, patch.node_id, patch.exec_error,
                    patch.acceptance_result.verdict if patch.acceptance_result else "fold-only")
        with self._lock_for(patch.task_id):
            result = self._graph.update_task_node_info(patch)
            if patch.exec_error is not None:
                side: list[tuple] = []
                await self._on_harness_collect(patch.task_id, patch.node_id, patch.exec_error, side)
                await self._drain(patch.task_id, side)
                return result
            if patch.acceptance_result is None:
                return result  # 仅 fold,无翻态
            side = []
            verdict = patch.acceptance_result.verdict
            if verdict == AcceptanceVerdict.PASS:
                await self._on_pass_collect(patch.task_id, patch.node_id, side)
            else:  # FAIL
                await self._on_fail_collect(patch.task_id, patch.node_id, side)
            await self._drain(patch.task_id, side)
            return result

    async def _on_pass_collect(self, task_id: str, node_id: str, side: list[tuple]) -> None:
        """PASS→DONE 后:查结构父 P;P==RUNNING 且本批兄弟全 DONE ∧ 无 RUNNING(决策C)
        → 翻父 RUNNING→PLANNING → plan(target=parent) → children:add+dispatch(plan 翻 PLANNING→RUNNING);
        []+has_gap=F:gap 闭→非根传播 DONE 上行 / 根→图终态;[]+has_gap=T→深度闸门升 BBS。
        async collect(锁内 await plan/dispatch)。"""
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            # 根 PASS(来自 owner bot 终验回投)→ 图完成
            side.append(("finish", task_id))
            return
        if parent.status != Status.RUNNING:
            return  # 父非委托执行态,不推进
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        logger.info("[on_pass] task=%s node=%s 父=%s 父态=%s 兄弟=%s",
                    task_id, node_id, parent.node_id, parent.status,
                    [(s.node_id, s.status.value) for s in siblings])
        if not all(st.status == Status.DONE for st in siblings):
            logger.info("[on_pass] task=%s 兄弟未齐,等待", task_id)
            return
        if any(st.status == Status.RUNNING for st in siblings):
            logger.info("[on_pass] task=%s 仍有 RUNNING,等待", task_id)
            return
        # 本批兄弟全 DONE → 翻父 RUNNING→PLANNING(显式委托态)+ 标记 owner 规划 → plan(target=parent)
        graph_snap = self._graph.query_task_dashboard(task_id)
        owner = str(graph_snap.extend_props.get("source_channel_id") or "")
        self._graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=parent.node_id, status=Status.PLANNING,
                          run_mode="planning", assignee=owner)
        )
        graph = self._graph.query_task_dashboard(task_id)
        pr = await self._planner.plan(graph, target_node_id=parent.node_id)
        logger.info("[on_pass] task=%s 父=%s 委托 plan 产 %d 子 has_gap=%s",
                    task_id, parent.node_id, len(pr.children), pr.has_gap)
        if pr.children:
            self._graph.add_task_nodes(pr.children, parent.node_id)  # PLANNING→RUNNING
            await self._prepare_into(task_id, side)
        elif not pr.has_gap:
            # gap 闭(验收通过)→ DONE 上行传播;根 gap 闭→图完成(终验即根 gap 闭)
            root = self._root(task_id)
            if parent.node_id == (root.node_id if root else None):
                self._maybe_finish_graph(task_id)
                return
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=parent.node_id, status=Status.DONE)
            )
            await self._on_pass_collect(task_id, parent.node_id, side)  # 上行传播
        else:
            # 有 gap 拆不出 → 升 BBS(loop_round++/BBS_MAX→HUNG)
            self._escalate_bbs(task_id, parent.node_id)

    async def _on_fail_collect(self, task_id: str, node_id: str, side: list[tuple]) -> None:
        """验收不过(FAIL+gaps)→FAILED 后:深度闸门 + plan(target=失败叶)补求子:
        depth>=MAX → 直接升 BBS(loop_round++/BBS_MAX→HUNG,不再拆);
        depth<MAX → plan:children→add(翻 FAILED→RUNNING)+dispatch;[]+has_gap=F→gap 闭翻 DONE 上行;
        []+has_gap=T→升 BBS。async collect(锁内 await plan/dispatch)。"""
        depth = self._graph._node_depth(task_id, node_id)
        cfg = self._graph._execution_config(task_id)
        max_depth = cfg["MAX_DEPTH"]
        _n = next((x for x in self._graph.query_task_dashboard(task_id).tasks if x.node_id == node_id), None)
        logger.info("[on_fail] task=%s node=%s depth=%d/%d gaps=%s",
                    task_id, node_id, depth, max_depth,
                    (_n.run_info.acceptance_result.gaps if _n and _n.run_info.acceptance_result else None))
        if depth >= max_depth:
            self._escalate_bbs(task_id, node_id)  # 达深度上限 → 升 BBS(不再拆)
            return
        self._mark_planning(task_id, node_id)  # 失败叶由 owner 重新规划补救
        graph = self._graph.query_task_dashboard(task_id)
        pr = await self._planner.plan(graph, target_node_id=node_id)
        logger.info("[on_fail] task=%s node=%s 补救 plan 产 %d 子 has_gap=%s",
                    task_id, node_id, len(pr.children), pr.has_gap)
        if pr.children:
            self._graph.add_task_nodes(pr.children, node_id)  # FAILED→RUNNING
            await self._prepare_into(task_id, side)
        elif not pr.has_gap:
            # gap 闭(验收实已满足,罕见)→翻 DONE 上行
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.DONE)
            )
            await self._on_pass_collect(task_id, node_id, side)
        else:
            # 有 gap 拆不出(<MAX)→ 升 BBS
            self._escalate_bbs(task_id, node_id)

    async def _on_harness_collect(self, task_id: str, node_id: str, exec_error: str,
                                  side: list[tuple]) -> None:
        """执行报错(bot 没跑通:run FAILED/SLA/poll 耗尽)→ harness 复位重投。
        计 harness_retries(存 node.extend_props):<MAX_HARNESS → 翻 RUNNING→PENDING + 重派发(RUNNING);
        ≥MAX_HARNESS → 翻 HUNG(不再流转)。注意:执行报错≠验收不过,不入补救重规划。"""
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None:
            return
        retries = int(node.run_info.extend_props.get("harness_retries", 0))
        retries += 1
        max_harness = self._max_harness(task_id)
        logger.info("[on_harness] task=%s node=%s exec_error=%s retries=%d/%d",
                    task_id, node_id, exec_error, retries, max_harness)
        # 更新 harness_retries 计数(经 exec_error 分支已 fold 不到 extend_props;此处显式 patch 落计数)
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id, node_id=node_id,
                extend_props_patch={"harness_retries": retries, "last_exec_error": exec_error},
            )
        )
        if retries >= max_harness:
            # 达上限 → HUNG(不再流转;需人介入)
            logger.warning("[on_harness] task=%s node=%s 达 MAX_HARNESS(%d)→HUNG", task_id, node_id, max_harness)
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id, node_id=node_id, status=Status.HUNG,
                    extend_props_patch={"hung_reason": "exec_stuck"},
                )
            )
            return
        # 未达上限 → 复位 RUNNING→PENDING + re-prepare(重新 dispatch→start_run)
        self._graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PENDING)
        )
        await self._prepare_into(task_id, side)

    # ===== on_miss =====
    async def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS → 节点(PENDING,miss_events 已填):深度闸门 + plan(target=miss 叶)拆细:
        depth>=MAX → 升 BBS;depth<MAX → plan:children→add+dispatch;[]+has_gap=T→升 BBS。"""
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            depth = self._graph._node_depth(patch.task_id, patch.node_id)
            cfg = self._graph._execution_config(patch.task_id)
            max_depth = cfg["MAX_DEPTH"]
            if depth >= max_depth:
                self._escalate_bbs(patch.task_id, patch.node_id)
                await self._drain(patch.task_id, side)
                return
            self._mark_planning(patch.task_id, patch.node_id)  # miss 叶由 owner 规划拆细
            graph = self._graph.query_task_dashboard(patch.task_id)
            pr = await self._planner.plan(graph, target_node_id=patch.node_id)
            logger.info("[on_miss] task=%s node=%s depth=%d/%d plan 产 %d 子 has_gap=%s",
                        patch.task_id, patch.node_id, depth, max_depth, len(pr.children), pr.has_gap)
            if pr.children:
                self._graph.add_task_nodes(pr.children, patch.node_id)
                await self._prepare_into(patch.task_id, side)
            elif not pr.has_gap:
                pass  # gap 闭(罕见,miss 节点本无验收)→不推进
            else:
                self._escalate_bbs(patch.task_id, patch.node_id)
        await self._drain(patch.task_id, side)

    # ===== on_harness(外部显式调用:超时/崩溃;复用 _on_harness_collect)=====
    async def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路入口(外部超时/崩溃显式触发):exec_error 语义 → 复用 _on_harness_collect。

        默认 exec_error="external_harness";调用方可经 patch.exec_error 带原因。复位 + 重投 / HUNG 上限同上。"""
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            await self._on_harness_collect(patch.task_id, patch.node_id, patch.exec_error or "external_harness", side)
        await self._drain(patch.task_id, side)

    # ===== 升 BBS =====
    def _escalate_bbs(self, task_id: str, node_id: str) -> None:
        """自动升 BBS(无人工挡板):remove_subtree(删 node+子树)+ 经图级写口 loop_round++ + 标 BBS。
        loop_round ≥ BBS_MAX_DEPTH → STUCK → graph HUNG(人介入)。纯同步(锁内)。"""
        self._graph.remove_subtree(task_id, node_id)
        graph = self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(loop_round_increment=1)
        )
        cfg = self._graph._execution_config(task_id)
        bbs_max = cfg["BBS_MAX_DEPTH"]
        if graph.loop_round >= bbs_max:
            self._graph.update_task_graph_info(
                task_id,
                TaskGraphPatch(
                    status=Status.HUNG,
                    extend_props_patch={"hung_reason": "stuck"},
                ),
            )
        else:
            self._graph.update_task_graph_info(
                task_id,
                TaskGraphPatch(extend_props_patch={"bbs_mode": True}),
            )

    # ===== 派发+执行(通用)=====
    async def _prepare_into(self, task_id: str, side: list[tuple]) -> None:
        """查 PENDING 可派发节点 → await dispatcher.dispatch 返填执行者 → 落库 RUNNING(side 'run');
        HIT_MULTI_BOTS 标 pending_group_formation(side 'group' 待锁外拉群);MISS(side 'miss')。
        async collect(锁内 await dispatch catalog IO);拉群/投递 IO 由 ``_drain`` 锁外 await。"""
        pending = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(status=Status.PENDING)
        )
        if not pending:
            return
        logger.info("[prepare] task=%s 待派发节点=%s", task_id, [n.node_id for n in pending])
        dispatched = await self._dispatcher.dispatch(pending)
        logger.info("[prepare] task=%s dispatch 完成 %d 节点", task_id, len(dispatched))
        to_run: list[TaskNode] = []
        for node in dispatched:
            miss = node.run_info.extend_props.get("miss_events")
            gf = node.run_info.extend_props.pop("pending_group_formation", None)
            if gf is not None:
                logger.info("[prepare] task=%s node=%s → group(HIT_MULTI_BOTS collab=%s bot_ids=%s)",
                            task_id, node.node_id, gf.collab_mode, gf.bot_ids)
                side.append(("group", node, gf))
                continue
            if node.run_info.run_mode and node.run_info.assignee:
                logger.info("[prepare] task=%s node=%s → run(mode=%s assignee=%s)",
                            task_id, node.node_id, node.run_info.run_mode, node.run_info.assignee)
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        status=Status.RUNNING,
                        run_mode=node.run_info.run_mode,
                        assignee=node.run_info.assignee,
                    )
                )
                to_run.append(node)
            elif miss:
                logger.info("[prepare] task=%s node=%s → miss(%s)", task_id, node.node_id, miss)
                side.append((
                    "miss",
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        extend_props_patch={"miss_events": miss},
                    ),
                ))
        if to_run:
            side.append(("run", to_run))

    async def _drain(self, task_id: str, side: list[tuple]) -> None:
        """锁外统一 await 执行 side effects:run(投递 gather+Semaphore)/ group(拉群+patch+投递)/
        miss(递归 on_miss collect+drain)/ finish(图级 DONE 写)。保证锁内永不 await。"""
        run_nodes: list[TaskNode] = []
        for kind, *payload in side:
            if kind == "run":
                run_nodes.extend(payload[0])
            elif kind == "group":
                node, gf = payload
                logger.info("[drain] task=%s node=%s 拉群(collab=%s)", task_id, node.node_id, gf.collab_mode)
                gid = await self._runner.form_coop_group(gf)
                node.run_info.assignee = gid
                with self._lock_for(task_id):
                    self._graph.update_task_node_info(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            status=Status.RUNNING,
                            run_mode=node.run_info.run_mode,
                            assignee=gid,
                        )
                    )
                run_nodes.append(node)
            elif kind == "miss":
                await self.on_miss(payload[0])  # 递归 collect+drain(锁外)
            elif kind == "finish":
                logger.info("[drain] task=%s finish(根 gap 闭→图 DONE)", task_id)
                self._maybe_finish_graph(payload[0])
        if run_nodes:
            logger.info("[drain] task=%s start_run %d 节点:%s",
                        task_id, len(run_nodes), [n.node_id for n in run_nodes])
            await self._runner.start_run(run_nodes)

    def _maybe_finish_graph(self, task_id: str) -> None:
        """根 gap 闭(终验通过)→ 全图 DONE。图级写收口 + 根节点翻 DONE。两写均经 SSOT 网关(锁内同步)。"""
        self._graph.update_task_graph_info(
            task_id,
            TaskGraphPatch(
                status=Status.DONE,
                output_patch={"result": "all_done"},
            ),
        )
        root = self._root(task_id)
        if root is not None and root.status != Status.DONE:
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=root.node_id, status=Status.DONE)
            )
