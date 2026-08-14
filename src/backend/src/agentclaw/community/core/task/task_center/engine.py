"""ExecutionEngine 内部编排核(事件驱动 + 状态条件触发)。对齐 plan.md §3.0。

非独立模块,TaskService 内部实现细节,对外不暴露。构造期收传输端口(bot/bcs/discover,由 DI 从配置注入),
``_build_*`` 内部 new 引擎自带策略(TaskPlanner/TaskDispatcher/TaskRunner)+ 接线 TaskExecutor(三模态投递+poller)。
引擎自身实现 ResultSink(poller 终态回投直接调 on_report)与 TaskContextBuilder(执行上下文派生),
消除"先建 stub 再外部注入真实 body/接线点"的后填,无引擎子类化、无 reach-in setter。验收 100% 走 on_report
回投(gap 计算即验收,无主动 verify dispatch);BBS 投递归 runner BBS 模态(无 BbsMarketPort,升 BBS 只翻图态 bbs_mode)。
零 case 知识:engine 不含任何节点名字面量。测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。

协程化(CR 反馈:任务执行是耗时任务):全链路 ``async def``。``plan``/``dispatch``(corp 为 LLM/catalog 耗时 IO)
亦 ``async``,在 per-task ``threading.RLock`` 锁内 await(同 task 串行推进的 IO,设计意图;不同 task 锁隔离不互阻塞)。
锁内不 await 的是高并发外部投递 IO(``start_run``/BCS 拉群 ``form_coop_group``/``deliver``)——这些 await 在锁外,
gather+Semaphore 并发下沉 ``TaskRunner.start_run`` 内部。
副作用收集模式:on_* 锁内 async collect(await plan/dispatch + 同步 add/patch,产出 side effects list)→
锁外 ``_drain`` 统一 await 执行 run/group/miss/finish(投递 IO)。
注:``threading.RLock`` 在本仓一次性事件循环/跨线程回调模型下跨线程正确串行;若 corp 采用单持久 loop 并发
处理同 task 多回投,需切 ``asyncio.Lock``(ocb 仓接入时定)。
"""
from __future__ import annotations

import threading

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


class ExecutionEngine:
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution(协程化,全链路 async)。

    构造期收传输端口(bot/bcs/discover,DI 从配置注入),``_build_*`` 内部 new 引擎自带策略 + 接线 TaskExecutor。
    引擎自当 ResultSink(poller 终态回投→on_report)与 TaskContextBuilder(执行上下文派生),消除后填/back-reach-in。
    on_* 入参统一收口 TaskNodePatch。按事件 + 状态条件(a/b/c + plan 三条件)分段协调。同 task_id 串行
    (per-task RLock,仅保护锁内同步编排写);跨 task 并行。投递/拉群 IO 锁外 await,gather+Semaphore 并发。
    loop_round 仅升 BBS 时 ++。测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。
    """

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
        # 回投适配:执行主体(经 poller 翻译终态/经 HTTP push)→ TaskCallbackData → TaskNodePatch → on_report
        from agentclaw.community.core.task.task_runner.callback_adapter import CallbackAdapter
        self._cb_adapter = CallbackAdapter()
        # TaskExecutor 接线(三模态投递+poller);poller sink = engine 自当(实现 report_result),
        # 执行上下文 = engine 自当(实现 build),消除"先建 stub 再接线"的后填。端口 None 时退化为默认 TaskRunner。
        self._poller_thread = None
        self._executor = self._build_executor()
        self._planner = self._build_planner()
        self._dispatcher = self._build_dispatcher()
        self._runner = self._build_runner()

    # ===== protected 工厂方法(测试子类可覆写注入 stub 策略/投递;引擎自带默认接真实端口)=====
    def _build_executor(self):
        """构造 TaskExecutor(三模态投递+poller);poller sink=engine 自当,context builder=engine 自当。
        端口 None(纯内核单测/stub 路径)→ 返回 None,runner 退化为默认 TaskRunner(stub 投递)。"""
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
        poller.set_on_result(self)  # engine 实现 report_result(poller 终态回投直接调 on_report)
        exe = TaskExecutor(
            bot=self._bot, bcs=self._bcs, formatter=PromptFormatterImpl(),
            context=self, sink=self, poller=poller,
        )
        # poller daemon 线程:异步回收 single_bot run / coop_group session / state_machine run,
        # 终态 → 翻译 → report_result → on_report(与外部 HTTP push 回投收敛同一入口)。
        # 同 build_integration(poller_thread=True) 语义;engine 自当 sink 时线程随 engine 生命周期(daemon)。
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
            pool.append(GapBasedPlanningStrategy())  # stub 路径(无端口;测试可覆写)
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
            pool.append(SearchBasedDispatchStrategy())  # stub 路径(无端口;测试可覆写)
        return TaskDispatcher(self._graph, pool=pool)

    def _build_runner(self):
        from agentclaw.community.core.task.task_runner.runner import TaskRunner
        return TaskRunner(self._graph, execution_backend=self._executor)

    # ===== ResultSink impl:poller 终态回投直接调 on_report(内部回投路径,绕过 HTTP)=====
    async def report_result(self, data: "TaskCallbackData") -> None:
        """引擎自当 ResultSink:TaskExecutorResultPoller 终态→TaskCallbackData→TaskNodePatch→on_report。
        与外部 HTTP push 回投(TaskLoopCallback.report_result→on_report)收敛同一入口。"""
        patch = self._cb_adapter.adapt(data)
        await self.on_report(patch)

    # ===== TaskContextBuilder impl:engine 自当执行上下文派生(消除 _RunnerContextBuilder 自循环)=====
    def build(self, task_id: str, node_id: str) -> dict:
        """引擎自当 TaskContextBuilder:派生 execute 模式上下文(叶子/聚合均 execute;gap 计算即验收,
        无 verify 模式 dispatch)。siblings_outputs 取本节点的兄弟(DONE 的 run_info.output);
        无结构父→根,无兄弟产出。"""
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

    # ===== on_execute =====
    async def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph 后,条件 a(根 PENDING)→ plan→add→dispatch→start_run。"""
        side: list[tuple] = []
        with self._lock_for(task_id):
            root = self._root(task_id)
            if root is None or root.status != Status.PENDING:
                return  # 非条件 a
            graph = self._graph.query_task_dashboard(task_id)
            nodes = await self._planner.plan(graph)   # 锁内 await plan(LLM IO,同 task 串行)
            if not nodes:
                return  # 无可规划
            self._graph.add_task_nodes(nodes, root.node_id)
            await self._prepare_into(task_id, side)   # 锁内 await dispatch(catalog IO)
        await self._drain(task_id, side)

    # ===== on_report =====
    async def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """回投事件:patch 内含 (task_id,node_id)+acceptance_result+output_patch。
        update_task_node_info 翻态(+fold)→ PASS 传播 / FAIL+gaps 补救。验收 100% 来自回投,engine 不主动验。"""
        with self._lock_for(patch.task_id):
            result = self._graph.update_task_node_info(patch)
            if patch.acceptance_result is None:
                return result  # 仅 fold,无翻态
            side: list[tuple] = []
            verdict = patch.acceptance_result.verdict
            if verdict == AcceptanceVerdict.PASS:
                await self._on_pass_collect(patch.task_id, patch.node_id, side)
            else:  # FAIL
                await self._on_fail_collect(patch.task_id, patch.node_id, side)
        await self._drain(patch.task_id, side)
        return result

    async def _on_pass_collect(self, task_id: str, node_id: str, side: list[tuple]) -> None:
        """PASS→DONE 后:查结构父 P;P=PLANNING 且本批兄弟全 DONE ∧ 无 RUNNING(决策C)
        → 委托 plan→prepare(产新子→add→dispatch side effects);gap 闭 → 非根传播 DONE 上行 / 根保持 PLANNING 等回投。
        根 PASS(来自 owner bot 回投)→ finish side effect。async collect(锁内 await plan/dispatch)。"""
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            # 根 PASS(来自 owner bot 终验回投)→ 图完成
            side.append(("finish", task_id))
            return
        if parent.status != Status.PLANNING:
            return  # 父非委托态,不推进
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        if not all(s.status == Status.DONE for s in siblings):
            return  # 兄弟未齐,等待
        if any(s.status == Status.RUNNING for s in siblings):
            return  # 仍有 RUNNING,等待
        # 本批兄弟全 DONE → 委托 plan(gap 计算;返 children=gap 未闭继续拆,返 []=gap 闭=验收通过)
        graph = self._graph.query_task_dashboard(task_id)
        new_children = await self._planner.plan(graph)
        if new_children:
            self._graph.add_task_nodes(new_children, parent.node_id)
            await self._prepare_into(task_id, side)
        else:
            # decompose 返 [](gap 闭=验收通过)→ DONE 上行传播;根 gap 闭→图完成(终验即根 gap 闭)
            root = self._root(task_id)
            if parent.node_id == (root.node_id if root else None):
                side.append(("finish", task_id))  # 根 gap 闭=终验通过→图 DONE
                return
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=parent.node_id, status=Status.DONE)
            )
            await self._on_pass_collect(task_id, parent.node_id, side)  # 上行传播

    async def _on_fail_collect(self, task_id: str, node_id: str, side: list[tuple]) -> None:
        """FAIL+gaps→FAILED 后:深度闸门 <MAX → plan→add(补救子挂该节点下,该节点进 PLANNING)→ prepare;
        ≥MAX → 自动升 BBS(remove_subtree+loop_round+++标 BBS)。async collect(锁内 await plan/dispatch)。"""
        depth = self._graph._node_depth(task_id, node_id)
        cfg = self._graph._execution_config(task_id)
        max_depth = cfg["MAX_DEPTH"]
        if depth < max_depth:
            graph = self._graph.query_task_dashboard(task_id)
            new_children = await self._planner.plan(graph)
            if new_children:
                self._graph.add_task_nodes(new_children, node_id)
                await self._prepare_into(task_id, side)
        else:
            self._escalate_bbs(task_id, node_id)

    # ===== on_miss =====
    async def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS → 节点仍 PENDING(miss_events 已填):
        <MAX → plan→add(拆细)→ 消费 miss_events → dispatch;≥MAX → 自动升 BBS。"""
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            depth = self._graph._node_depth(patch.task_id, patch.node_id)
            cfg = self._graph._execution_config(patch.task_id)
            max_depth = cfg["MAX_DEPTH"]
            if depth < max_depth:
                graph = self._graph.query_task_dashboard(patch.task_id)
                new_children = await self._planner.plan(graph)
                if new_children:
                    self._graph.add_task_nodes(new_children, patch.node_id)
                    await self._prepare_into(patch.task_id, side)
            else:
                self._escalate_bbs(patch.task_id, patch.node_id)
        await self._drain(patch.task_id, side)

    # ===== on_harness =====
    async def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路:RUNNING 超时/崩溃 → 复位回 PENDING(update_task_node_info)→ 正常 dispatch 重投。
        不抢正向驱动;不直接写 HUNG(STUCK 走 on_miss/on_fail 升 BBS 链路上限判)。"""
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            self._graph.update_task_node_info(patch)
            await self._prepare_into(patch.task_id, side)
        await self._drain(patch.task_id, side)

    # ===== 升 BBS =====
    def _escalate_bbs(self, task_id: str, node_id: str) -> None:
        """自动升 BBS(无人工挡板):remove_subtree(删 xx_node+子树)+ 经图级写口 loop_round++ + 标 BBS。
        loop_round ≥ BBS_MAX_DEPTH → STUCK → graph HUNG(人介入)。图级写收口 update_task_graph_info(SSOT)。
        BBS 实际投递归 runner BBS 模态(下次 dispatch→start_run;无独立 BbsMarketPort)。纯同步(锁内)。"""
        self._graph.remove_subtree(task_id, node_id)
        graph = self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(loop_round_increment=1)
        )
        cfg = self._graph._execution_config(task_id)
        bbs_max = cfg["BBS_MAX_DEPTH"]
        if graph.loop_round >= bbs_max:
            # STUCK → graph HUNG(人介入);node 已删,标 graph 级(经图级写口)
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
        """查 PENDING 可派发节点 → await dispatcher.dispatch 返填执行者 → HIT_MULTI_BOTS 标 pending_group_formation
        → 落库 RUNNING(side 'run');HIT_MULTI_BOTS 待拉群(side 'group');MISS(side 'miss')。
        async collect(锁内 await dispatch catalog IO);拉群/投递 IO 由 ``_drain`` 锁外 await。"""
        pending = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(status=Status.PENDING)
        )
        if not pending:
            return
        dispatched = await self._dispatcher.dispatch(pending)
        to_run: list[TaskNode] = []
        for node in dispatched:
            miss = node.run_info.extend_props.get("miss_events")
            gf = node.run_info.extend_props.pop("pending_group_formation", None)
            if gf is not None:
                # HIT_MULTI_BOTS:待锁外拉群填充 assignee 后投递
                side.append(("group", node, gf))
                continue
            if node.run_info.run_mode and node.run_info.assignee:
                # 有执行者 → 落库 RUNNING
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
                # MISS → on_miss(锁外 await)
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
                self._maybe_finish_graph(payload[0])
        if run_nodes:
            await self._runner.start_run(run_nodes)

    def _maybe_finish_graph(self, task_id: str) -> None:
        """根 PASS(终验回投通过)→ 全图 DONE。图级写收口 update_task_graph_info(SSOT 唯一网关)。"""
        self._graph.update_task_graph_info(
            task_id,
            TaskGraphPatch(
                status=Status.DONE,
                output_patch={"result": "all_done"},
            ),
        )
