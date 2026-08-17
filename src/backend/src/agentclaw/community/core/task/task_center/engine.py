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
    NodeAction,
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

    def __init__(self, graph, *, bot=None, bcs=None, discover=None, bcs_identity=None) -> None:
        """graph: TaskGraphService;bot: OpenApiBotPort;bcs: BcsClientPort;discover: BotDiscoverServiceProtocol。
        端口由 DI 从配置注入(local/prod/double 只换端口实现,引擎代码不变)。prod 必传;测试子类覆写
        ``_build_*`` 注入 stub 策略/投递时可省略(走 super 路径默认 berth)。"""
        self._graph = graph
        self._bot = bot
        self._bcs = bcs
        self._discover = discover
        self._bcs_identity = bcs_identity
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
            context=self, sink=self, poller=poller, identity_resolver=self._bcs_identity,
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

    def _is_graph_terminal(self, task_id: str) -> bool:
        """图级终态(DONE/HUNG)判定。终态后自动驱动(plan/dispatch/harness/回投推进)一律冻结:
        MAX_LOOP 达上限→图 HUNG 后,后续 on_pass/on_miss/on_harness 不再推进(避免 loop_round 失控飙升
        与节点无限增生);on_bbs_report(BBS 接力恢复)是唯一可从 HUNG 恢复的路径,不在本守卫范围。"""
        try:
            return self._graph.query_task_dashboard(task_id).status in {Status.DONE, Status.HUNG}
        except Exception:  # noqa: BLE001  图不存在等→视为非终态,让正常入口逻辑处理
            return False

    async def _plan_with_retry(self, task_id: str, graph, target_node_id: str | None = None):
        """plan 容错重试:planning 调用失败(parse/not_completed/empty 等,gap_detail 以 ``plan_`` 前缀)
        → 重试最多 MAX_HARNESS 次;耗尽后返回最后结果(has_gap=True → 编排核走深度闸门/HUNG)。
        非 ``plan_`` 前缀的空结果(gap 闭 has_gap=F / 真拆不出 has_gap=T)不经重试直接返回。
        planning 是 owner bot 的耗时工作,失败同 exec_error 应重试而非静默 DONE/立即 HUNG。"""
        max_h = self._max_harness(task_id)
        pr = None
        for attempt in range(max_h):
            pr = await self._planner.plan(graph, target_node_id=target_node_id)
            if pr.children or not (pr.gap_detail or "").startswith("plan_"):
                break  # 有子 / 真 gap 闭 / 真拆不出 → 不重试
            logger.warning("[plan-retry] task=%s attempt=%d/%d gap_detail=%s",
                           task_id, attempt + 1, max_h, pr.gap_detail)
        # 可观测:落最近一次 plan 结果到图 extend_props(dashboard 可见,便于诊断 plan 为何产 []/HUNG)
        self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(extend_props_patch={
                "last_plan_target": target_node_id or "<root>",
                "last_plan_children": len(pr.children),
                "last_plan_has_gap": pr.has_gap,
                "last_plan_detail": pr.gap_detail,
            }))
        # 动作历史:PLAN 事件(gap 计算 + 产子结果)挂到被规划目标节点(根 gap 反复计算的轨迹留痕)
        target_id = target_node_id
        if target_id is None:
            root = self._root(task_id)
            target_id = root.node_id if root else None
        if target_id is not None:
            self._log_action(
                task_id, target_id, NodeAction.PLAN,
                {
                    "target": target_node_id or "<root>",
                    "children": [c.node_id for c in pr.children],
                    "has_gap": pr.has_gap,
                    "gap_detail": pr.gap_detail,
                },
                status_from=Status.PLANNING, status_to=Status.PLANNING,
            )
        return pr

    def _mark_planning(self, task_id: str, node_id: str) -> None:
        """节点进入规划委托态:PENDING→PLANNING(幂等,已 PLANNING 不重翻)。
        规划是编排态(Status.PLANNING),不是执行模式:run_mode/assignee 保持 None。
        规划者(owner bot)隐式来自 graph.extend_props.source_channel_id,不落节点 run_info。
        叶子派发执行时由 _prepare_into 覆写为 single_bot/coop_group/bbs+worker。"""
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None or node.status != Status.PENDING:
            return  # 已 PLANNING / 已终态 → 幂等不翻
        self._graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PLANNING)
        )

    def _log_action(
        self, task_id: str, node_id: str, action: NodeAction, payload: dict,
        *, attempt: int | None = None, status_from: Status | None = None,
        status_to: Status | None = None,
    ) -> None:
        """追加节点动作历史快照(append-only;零侵入驱动逻辑)。

        供各逻辑动作(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/TRANSITION)完成时调用,
        纯可观测旁路:不翻态、不读回驱动。``attempt`` 省略时取节点 harness_retries 快照;
        ``status_from``/``status_to`` 省略时由调用方按动作前/后态传(未翻态可不传)。
        """
        if attempt is None:
            node = next((n for n in self._graph.query_task_dashboard(task_id).tasks
                         if n.node_id == node_id), None)
            attempt = int(node.run_info.extend_props.get("harness_retries", 0)) if node else 0
        try:
            self._graph.append_action_event(
                task_id, node_id, action, payload,
                attempt=attempt, status_from=status_from, status_to=status_to,
            )
        except Exception as ex:  # noqa: BLE001  历史快照写入失败不影响驱动
            logger.warning("[action-log] task=%s node=%s action=%s 追加失败:%s",
                           task_id, node_id, action.value, ex)

    # ===== on_execute =====
    async def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph 后,条件 a(根 PENDING)→ plan(None 自发现根)→add→dispatch→start_run。"""
        if self._is_graph_terminal(task_id):
            logger.info("[on_execute] task=%s 图已终态(%s),冻结驱动", task_id,
                        self._graph.query_task_dashboard(task_id).status.value)
            return
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
            pr = await self._plan_with_retry(task_id, graph)   # None → 自发现根(含 plan 容错重试)
            logger.info("[on_execute] task=%s plan 产 %d 子节点: %s",
                        task_id, len(pr.children), [n.node_id for n in pr.children])
            if pr.children:
                self._graph.add_task_nodes(pr.children, root.node_id)
                await self._prepare_into(task_id, side)
            elif not pr.has_gap:
                self._maybe_finish_graph(task_id)  # 根 gap 初始即闭(罕见)
            else:
                self._hung_and_escalate(task_id, root.node_id, 'root_gap_no_decompose')  # 有 gap 拆不出 → HUNG 升 BBS
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
            # 动作历史:EXECUTE(执行产出)+ VERIFY(验收结论)——回投即一个执行动作闭环
            _out = dict(patch.output_patch) if patch.output_patch else {}
            if patch.exec_error is not None:
                self._log_action(
                    patch.task_id, patch.node_id, NodeAction.EXECUTE,
                    {"success": False, "exec_error": patch.exec_error, "output": _out},
                    status_from=result.prev_status, status_to=result.new_status,
                )
            elif patch.acceptance_result is not None:
                _ar = patch.acceptance_result
                self._log_action(
                    patch.task_id, patch.node_id, NodeAction.EXECUTE,
                    {"success": _ar.verdict == AcceptanceVerdict.PASS, "output": _out},
                    status_from=result.prev_status, status_to=result.new_status,
                )
                self._log_action(
                    patch.task_id, patch.node_id, NodeAction.VERIFY,
                    {
                        "verdict": _ar.verdict.value,
                        "acceptances_metric": list(_ar.acceptances_metric),
                        "gaps": list(_ar.gaps),
                    },
                    status_from=result.prev_status, status_to=result.new_status,
                )
            if patch.exec_error is not None:
                side: list[tuple] = []
                await self._on_harness_collect(patch.task_id, patch.node_id, patch.exec_error, side)
                await self._drain(patch.task_id, side)
                return result
            if patch.acceptance_result is None:
                return result  # 仅 fold,无翻态
            if self._is_graph_terminal(patch.task_id):
                logger.info("[on_report] task=%s 图已终态,fold 已落但冻结驱动", patch.task_id)
                return result
            side = []
            verdict = patch.acceptance_result.verdict
            if verdict == AcceptanceVerdict.PASS:
                await self._on_pass_collect(patch.task_id, patch.node_id, side)
            else:  # FAIL
                await self._on_fail_collect(patch.task_id, patch.node_id, side)
            await self._drain(patch.task_id, side)
            return result

    # ===== on_bbs_report:BBS 接力步⑤回投(collector-free)=====
    async def on_bbs_report(self, patch: TaskNodePatch, root_verified: bool = False) -> NodeOpResult:
        """BBS 接力步⑤回投:collector-free——仅翻 scoped 节点终态(SSOT ``update_task_node_info``),
        不跑 ``_on_pass_collect``/``_on_fail_collect``/``_drain``(避免框架经 owner-bot 重规划抢占接力,
        对齐 spec §10.4)。``root_verified=True`` → 根 PLANNING→DONE + 图 DONE。最后清根 ``bbs_owner`` 释放 claim。

        持有者校验:``root.run_info.extend_props['bbs_owner']`` 须 == ``patch.assignee``(调用方
        ``report_bbs_result`` 设 ``patch.assignee=bot_id``);非持有者 → ``TaskStateError``(在校验抛,
        不清 claim)。

        释放安全:scoped 翻态 / root_verified 根翻态(HUNG→DONE 等非法翻会抛)全部收在 ``try`` 内,
        ``finally`` 无条件清根 ``bbs_owner`` —— 翻态抛错也释放 claim,避免持卡者死锁(他 bot claim 被
        CAS 拒、持卡者重报已 DONE 节点再翻 DONE 亦非法)。owner 校验在 ``try`` 之前,非持有者抛错不清他卡。"""
        with self._lock_for(patch.task_id):
            graph = self._graph.query_task_dashboard(patch.task_id)
            root = next((n for n in graph.tasks if n.node_id == patch.task_id), None)
            if root is None or root.run_info.extend_props.get("bbs_owner") != patch.assignee:
                raise TaskStateError(f"on_bbs_report: 非claim持有者 task={patch.task_id}")
            try:
                # scoped 节点终态翻转(acceptance→DONE/FAILED)或 fold(output_patch/exec_error);无 collector
                result = self._graph.update_task_node_info(patch)
                if root_verified:
                    # 根 PLANNING→DONE:走 status 直驱(_DIRECT_TRANSITIONS 允许 PLANNING→DONE/HUNG);
                    # acceptance 驱动仅允许 RUNNING→DONE/FAILED(_ACCEPTANCE_TRANSITIONS),根非 RUNNING 故不可走 acceptance
                    self._graph.update_task_node_info(
                        TaskNodePatch(task_id=patch.task_id, node_id=patch.task_id, status=Status.DONE))
                    self._graph.update_task_graph_info(
                        patch.task_id, TaskGraphPatch(status=Status.DONE))
                return result
            finally:
                # 无论翻态是否抛(如 root_verified 在根 HUNG 时 HUNG→DONE 非法),都清根 bbs_owner 释放 claim
                self._graph.update_task_node_info(
                    TaskNodePatch(task_id=patch.task_id, node_id=patch.task_id,
                                  extend_props_patch={"bbs_owner": None}))

    async def _on_pass_collect(self, task_id: str, node_id: str, side: list[tuple]) -> None:
        """PASS→DONE 后:查结构父 P。v4 父恒 PLANNING(委托态),无需翻态:
        兄弟仍有未终态(RUNNING/PLANNING/PENDING)→等待;兄弟全 DONE(plan-ready)→ plan(target=parent):
          有子→add(父维持 PLANNING)+dispatch;空+has_gap=F→gap 闭:非根传播 DONE 上行/根→图 DONE;
          空+has_gap=T→HUNG 升 BBS。兄弟全终态含 HUNG/FAILED→终态传播。
        根成功重 plan(gap 未闭)**计 loop_round**(口子 A);中间父重 plan 不计。"""
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            side.append(("finish", task_id))
            return
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        logger.info("[on_pass] task=%s node=%s 父=%s 父态=%s 兄弟=%s",
                    task_id, node_id, parent.node_id, parent.status,
                    [(s2.node_id, s2.status.value) for s2 in siblings])
        if any(st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING} for st in siblings):
            logger.info("[on_pass] task=%s 兄弟未全终态,等待", task_id)
            return
        if not all(st.status == Status.DONE for st in siblings):
            self._propagate_terminal(task_id, parent, siblings, side)
            return
        root = self._root(task_id)
        is_root_parent = parent.node_id == (root.node_id if root else None)
        self._mark_planning(task_id, parent.node_id)
        graph = self._graph.query_task_dashboard(task_id)
        pr = await self._plan_with_retry(task_id, graph, target_node_id=parent.node_id)
        logger.info("[on_pass] task=%s 父=%s 委托 plan 产 %d 子 has_gap=%s",
                    task_id, parent.node_id, len(pr.children), pr.has_gap)
        if pr.children:
            if is_root_parent:
                self._bump_loop_round(task_id)  # 根 gap 复发盘:口子 A 收敛
            self._graph.add_task_nodes(pr.children, parent.node_id)
            await self._prepare_into(task_id, side)
        elif not pr.has_gap:
            if is_root_parent:
                self._maybe_finish_graph(task_id)
                return
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=parent.node_id, status=Status.DONE)
            )
            # 动作历史:TRANSITION(非根 gap 闭传播 DONE)
            self._log_action(
                task_id, parent.node_id, NodeAction.TRANSITION,
                {"reason": "gap_closed_propagate", "to": "DONE"},
                status_from=Status.PLANNING, status_to=Status.DONE,
            )
            await self._on_pass_collect(task_id, parent.node_id, side)
        else:
            self._hung_and_escalate(task_id, parent.node_id, "gap_no_progress")

    async def _on_fail_collect(self, task_id: str, node_id: str, side: list[tuple]) -> None:
        """验收不过(FAIL+gaps)→FAILED。v4:不立即补救拆子,置 FAILED 后交由 harness 周期巡检"重新派发执行"
        重试(不拆);harness 重试达 MAX_HARNESS→HUNG→升 BBS。本方法仅落 FAILED + 记 gaps,不推进 plan。"""
        _n = next((x for x in self._graph.query_task_dashboard(task_id).tasks if x.node_id == node_id), None)
        logger.info("[on_fail] task=%s node=%s → FAILED(gaps=%s),交 harness 重试重新派发",
                    task_id, node_id,
                    (_n.run_info.acceptance_result.gaps if _n and _n.run_info.acceptance_result else None))
        # FAILED 已由 acceptance patch 落态。重试重新派发由 harness 巡检 FAILED 触发(见 TaskHarness._poll_once)。
        # 此处不 plan、不 add,直接返回。

    async def _on_harness_collect(self, task_id: str, node_id: str, exec_error: str,
                                  side: list[tuple]) -> None:
        """harness 重试:统一处理 FAILED(验收不过)与 exec_error(执行报错)。
        重试=重新派发执行(不拆):<MAX_HARNESS → 复位 FAILED/RUNNING→PENDING + re-prepare(重新 dispatch→start_run);
        >=MAX_HARNESS → HUNG(再 升 BBS,loop_round++/图 HUNG)。"""
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None:
            return
        retries = int(node.run_info.extend_props.get("harness_retries", 0))
        retries += 1
        max_harness = self._max_harness(task_id)
        logger.info("[on_harness] task=%s node=%s reason=%s retries=%d/%d",
                    task_id, node_id, exec_error, retries, max_harness)
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id, node_id=node_id,
                extend_props_patch={"harness_retries": retries, "last_exec_error": exec_error},
            )
        )
        if retries >= max_harness:
            logger.warning("[on_harness] task=%s node=%s 达 MAX_HARNESS(%d)→HUNG", task_id, node_id, max_harness)
            self._hung_and_escalate(task_id, node_id, "exec_stuck")
            return
        # 复位到 PENDING 重新派发执行:FAILED/RUNNING→PENDING;PENDING 派发卡住(搜推无响应/派发失败)清
        # dispatch_error 让 _prepare_into 重新搜推(harness owns 重试计数+HUNG 上限,正常 cycle 跳过 dispatch_error 节点)
        if node.status in {Status.FAILED, Status.RUNNING}:
            _prev = node.status
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PENDING)
            )
            # 动作历史:RESET(harness 重新派发执行重试)
            self._log_action(
                task_id, node_id, NodeAction.RESET,
                {"reason": exec_error or "failed_retry", "prev_status": _prev.value,
                 "harness_retries_after": retries},
                attempt=retries, status_from=_prev, status_to=Status.PENDING,
            )
        elif node.status == Status.PENDING and node.run_info.extend_props.get("dispatch_error"):
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=node_id, extend_props_patch={"dispatch_error": None})
            )
        await self._prepare_into(task_id, side)

    # ===== on_miss =====
    async def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS(搜推未匹配执行者)→深度闸门:
        depth>=MAX → HUNG 升 BBS(拆不动,无 bot);depth<MAX → mark_planning + plan(target=miss 叶)拆细:
        有子→add(父置 PLANNING)+dispatch;空+has_gap=F→gap 闭不推进(罕见);空+has_gap=T→HUNG 升 BBS。
        MISS 不进 harness(无 bot 无可重试执行体)。"""
        if self._is_graph_terminal(patch.task_id):
            logger.info("[on_miss] task=%s 图已终态,冻结 MISS 推进", patch.task_id)
            return
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            depth = self._graph._node_depth(patch.task_id, patch.node_id)
            cfg = self._graph._execution_config(patch.task_id)
            max_depth = cfg["MAX_DEPTH"]
            # 动作历史:DISPATCH(MISS 搜推未命中执行者)
            _miss_reason = ""
            _ep = patch.extend_props_patch or {}
            if isinstance(_ep.get("miss_events"), list) and _ep["miss_events"]:
                _miss_reason = str(_ep["miss_events"][0])
            self._log_action(
                patch.task_id, patch.node_id, NodeAction.DISPATCH,
                {"outcome": "MISS", "miss_reason": _miss_reason, "depth": depth, "max_depth": max_depth},
                status_from=Status.PENDING, status_to=Status.PENDING,
            )
            if depth >= max_depth:
                logger.info("[on_miss] task=%s node=%s depth=%d/%d 拆不动→HUNG", patch.task_id, patch.node_id, depth, max_depth)
                self._hung_and_escalate(patch.task_id, patch.node_id, "miss_depth_exhausted")
                await self._drain(patch.task_id, side)
                return
            self._mark_planning(patch.task_id, patch.node_id)
            graph = self._graph.query_task_dashboard(patch.task_id)
            pr = await self._plan_with_retry(patch.task_id, graph, target_node_id=patch.node_id)
            logger.info("[on_miss] task=%s node=%s depth=%d/%d plan 产 %d 子 has_gap=%s",
                        patch.task_id, patch.node_id, depth, max_depth, len(pr.children), pr.has_gap)
            if pr.children:
                self._graph.add_task_nodes(pr.children, patch.node_id)
                await self._prepare_into(patch.task_id, side)
            elif not pr.has_gap:
                pass
            else:
                self._hung_and_escalate(patch.task_id, patch.node_id, "miss_no_decompose")
        await self._drain(patch.task_id, side)

    # ===== on_harness(harness 旁路入口:超时/崩溃/FAILED 巡检;复用 _on_harness_collect)=====
    async def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路入口:exec_error 语义(超时/崩溃/FAILED 巡检)→ 复用 _on_harness_collect 重新派发重试/上限 HUNG。"""
        if self._is_graph_terminal(patch.task_id):
            logger.info("[on_harness] task=%s 图已终态,冻结 harness 推进", patch.task_id)
            return
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            await self._on_harness_collect(patch.task_id, patch.node_id, patch.exec_error or "external_harness", side)
        await self._drain(patch.task_id, side)

    # ===== HUNG + 升 BBS(loop_round++ / 图 HUNG) + 终态传播 =====
    def _bump_loop_round(self, task_id: str) -> None:
        """图级 loop_round += 1。v4 仅两处计:根 gap 未闭重 plan(口子 A) + HUNG 升 BBS。达 MAX_LOOP→图 HUNG。"""
        graph = self._graph.update_task_graph_info(task_id, TaskGraphPatch(loop_round_increment=1))
        max_loop = self._graph._execution_config(task_id)["MAX_LOOP"]
        if graph.loop_round >= max_loop:
            self._graph.update_task_graph_info(
                task_id, TaskGraphPatch(status=Status.HUNG, extend_props_patch={"hung_reason": "loop_exhausted"}))
            logger.warning("[loop_round] task=%s 达 MAX_LOOP(%d)→图 HUNG", task_id, max_loop)

    def _hung_and_escalate(self, task_id: str, node_id: str, hung_reason: str) -> None:
        """节点置 HUNG + 父终态传播检查 + 升 BBS(loop_round++,bbs_mode;节点保留不 remove)。纯同步(锁内)。"""
        _prev = next((n.status for n in self._graph.query_task_dashboard(task_id).tasks
                      if n.node_id == node_id), None)
        self._graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.HUNG,
                          extend_props_patch={"hung_reason": hung_reason}))
        # 动作历史:TRANSITION(节点 HUNG)
        self._log_action(
            task_id, node_id, NodeAction.TRANSITION,
            {"reason": hung_reason, "to": "HUNG"},
            status_from=_prev, status_to=Status.HUNG,
        )
        logger.info("[hung] task=%s node=%s reason=%s → 升 BBS(loop_round++)", task_id, node_id, hung_reason)
        self._graph.update_task_graph_info(task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
        self._bump_loop_round(task_id)
        # 终态传播:查父,若兄弟全终态且含 HUNG→父 HUNG(冒泡,递归)
        self._maybe_propagate_hung(task_id, node_id)

    def _maybe_propagate_hung(self, task_id: str, node_id: str) -> None:
        """自 node 往上:若父的子全终态且含 HUNG → 父 HUNG(不计额外 loop_round,纯冒泡)→ 继续上行。
        到根 → 图终态收口(HUNG)。若图已 HUNG(loop_exhausted 等已收口)→ 不覆盖 hung_reason。"""
        cur = node_id
        while True:
            parent = self._graph.get_parent_task(task_id, cur)
            if parent is None:
                # cur 是根 → 图级收口(根 HUNG → 图 HUNG);不覆盖已设的图级 hung_reason
                root = self._root(task_id)
                if root is not None and root.node_id == cur and root.status == Status.HUNG:
                    g = self._graph.query_task_dashboard(task_id)
                    if g.status != Status.HUNG:
                        self._graph.update_task_graph_info(
                            task_id, TaskGraphPatch(status=Status.HUNG, extend_props_patch={"hung_reason": "root_stuck"}))
                return
            siblings = self._graph.get_child_tasks(task_id, parent.node_id)
            if any(st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING} for st in siblings):
                return  # 还有活子,等
            if any(st.status == Status.HUNG for st in siblings):
                self._graph.update_task_node_info(
                    TaskNodePatch(task_id=task_id, node_id=parent.node_id, status=Status.HUNG,
                                  extend_props_patch={"hung_reason": "child_hung"}))
                logger.info("[hung-propagate] task=%s 父=%s 因子含 HUNG→HUNG", task_id, parent.node_id)
                cur = parent.node_id
                continue
            return

    def _propagate_terminal(self, task_id: str, parent: TaskNode, siblings: list, side: list[tuple]) -> None:
        """on_pass 时兄弟全终态但非全 DONE(含 HUNG):子含 HUNG→父 HUNG 冒泡(经 _maybe_propagate_hung)。
        FAILED 子由 harness 巡检补救,此处若仅 FAILED(无 HUNG)不在此处理(等 harness 补救/转 HUNG)。"""
        if any(st.status == Status.HUNG for st in siblings):
            self._maybe_propagate_hung(task_id, siblings[0].node_id if siblings else parent.node_id)

    # ===== 派发+执行(通用)=====
    async def _prepare_into(self, task_id: str, side: list[tuple]) -> None:
        """查「未派发」PENDING 节点 → await dispatcher.dispatch 返填执行者 → HIT 先落 run_mode/assignee
        + 飞行标记 ``dispatching``(保持 PENDING),start_run/form_coop_group 成功后由 _drain 翻 RUNNING(side 'run'/'group')
        并清 dispatching;MISS(side 'miss');派发异常(side 'dispatch_fail',留 PENDING 交 harness 按超时重试搜推)。

        状态机:RUNNING=真执行;派发命中只填执行者+置 dispatching,PENDING 维持到 start_run 成功后才翻。
        跳过:① dispatching=True 节点(已交付 _drain 待翻 RUNNING 的飞行态,防双派发);② dispatch_error 节点
        (搜推异常/派发失败,harness owns 重试+HUNG 上限,正常 cycle 不重复搜推防 bot 调用风暴);
        ③ run_mode=="bbs" 节点(FR-EXT-06:bbs 由 bot 经 bbs/attach 自驱,框架不自动派发/翻态)。
        reset 节点(FAILED/RUNNING→PENDING 复位,无 dispatching)不在跳过之列→重新派发执行。"""
        all_pending = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(status=Status.PENDING)
        )
        pending = [n for n in all_pending
                   if not n.run_info.extend_props.get("dispatching")
                   and not n.run_info.extend_props.get("dispatch_error")
                   and n.run_info.run_mode != "bbs"]
        if not pending:
            return
        logger.info("[prepare] task=%s 待派发节点=%s", task_id, [n.node_id for n in pending])
        dispatched = await self._dispatcher.dispatch(pending)
        to_run: list[TaskNode] = []
        for node in dispatched:
            miss = node.run_info.extend_props.get("miss_events")
            gf = node.run_info.extend_props.pop("pending_group_formation", None)
            if gf is not None:
                logger.info("[prepare] task=%s node=%s → group(HIT_MULTI_BOTS collab=%s bot_ids=%s)",
                            task_id, node.node_id, gf.collab_mode, gf.bot_ids)
                # 飞行标记:group 交付 _drain 拉群前置,防并发 cycle 双搜推双拉群
                self._graph.update_task_node_info(
                    TaskNodePatch(task_id=task_id, node_id=node.node_id, run_mode="coop_group",
                                  extend_props_patch={"dispatching": True}))
                side.append(("group", node, gf))
                continue
            if node.run_info.run_mode and node.run_info.assignee:
                logger.info("[prepare] task=%s node=%s → run(mode=%s assignee=%s)",
                            task_id, node.node_id, node.run_info.run_mode, node.run_info.assignee)
                # HIT:落执行者+飞行标记 dispatching(保持 PENDING);start_run 成功后 _drain 翻 RUNNING+清 dispatching
                self._graph.update_task_node_info(
                    TaskNodePatch(task_id=task_id, node_id=node.node_id,
                                  run_mode=node.run_info.run_mode, assignee=node.run_info.assignee,
                                  extend_props_patch={"dispatching": True}))
                to_run.append(node)
            elif miss:
                logger.info("[prepare] task=%s node=%s → miss(%s)", task_id, node.node_id, miss)
                side.append(("miss", TaskNodePatch(task_id=task_id, node_id=node.node_id,
                            extend_props_patch={"miss_events": miss})))
            else:
                # 派发未产出执行者也非 MISS(dispatcher 已容错吞异常):标 dispatch_error 留 PENDING,harness 按超时重试搜推
                derr = node.run_info.extend_props.get("dispatch_error") or "no_result"
                logger.warning("[prepare] task=%s node=%s 派发未产出(%s)→留 PENDING 待 harness", task_id, node.node_id, derr)
                side.append(("dispatch_fail", TaskNodePatch(task_id=task_id, node_id=node.node_id,
                            extend_props_patch={"dispatch_error": derr})))
        if to_run:
            side.append(("run", to_run))

    async def _drain(self, task_id: str, side: list[tuple]) -> None:
        """锁外统一执行 side effects。投递/拉群 IO 锁外 await;翻态(side effect)收口锁内。
        v4 状态机:run 经 start_run 投递,成功后才翻 RUNNING+清 dispatching(对齐"调执行方法后置 RUNNING");
        失败→清执行者+清 dispatching+标 dispatch_error 留 PENDING 交 harness 重试搜推。group 经 form_coop_group
        拉群后翻 RUNNING+清 dispatching。miss 递归推进与 run 投递不互相阻塞。"""
        run_nodes: list[TaskNode] = []
        miss_tasks: list[TaskNodePatch] = []
        dispatch_fail_patches: list[TaskNodePatch] = []
        for kind, *payload in side:
            if kind == "run":
                run_nodes.extend(payload[0])
            elif kind == "group":
                node, gf = payload
                logger.info("[drain] task=%s node=%s 拉群(collab=%s)", task_id, node.node_id, gf.collab_mode)
                try:
                    gid = await self._runner.form_coop_group(gf)
                except Exception as ex:  # noqa: BLE001  拉群异常→清 dispatching 留 PENDING 交 harness
                    logger.warning("[drain] task=%s node=%s 拉群异常:%s→留 PENDING 待 harness", task_id, node.node_id, ex)
                    with self._lock_for(task_id):
                        self._graph.update_task_node_info(
                            TaskNodePatch(task_id=task_id, node_id=node.node_id, run_mode="", assignee="",
                                          extend_props_patch={"dispatching": None, "dispatch_error": "form_group_failed"}))
                    continue
                node.run_info.assignee = gid
                with self._lock_for(task_id):
                    self._graph.update_task_node_info(
                        TaskNodePatch(task_id=task_id, node_id=node.node_id, status=Status.RUNNING,
                                      run_mode=node.run_info.run_mode, assignee=gid,
                                      extend_props_patch={"dispatching": None}))
                # 动作历史:DISPATCH(HIT_MULTI 协作群)
                self._log_action(
                    task_id, node.node_id, NodeAction.DISPATCH,
                    {
                        "outcome": "HIT_MULTI",
                        "run_mode": "coop_group",
                        "assignee": gid,
                        "collab_mode": getattr(gf, "collab_mode", None),
                        "bot_ids": list(getattr(gf, "bot_ids", []) or []),
                    },
                    status_from=Status.PENDING, status_to=Status.RUNNING,
                )
                run_nodes.append(node)
            elif kind == "miss":
                miss_tasks.append(payload[0])
            elif kind == "dispatch_fail":
                dispatch_fail_patches.append(payload[0])
            elif kind == "finish":
                logger.info("[drain] task=%s finish(根 gap 闭→图 DONE)", task_id)
                self._maybe_finish_graph(payload[0])
        # ① run:start_run 投递,成功后翻 RUNNING+清 dispatching;失败清执行者+清 dispatching+标 dispatch_error 留 PENDING
        if run_nodes:
            logger.info("[drain] task=%s start_run %d 节点:%s", task_id, len(run_nodes), [n.node_id for n in run_nodes])
            try:
                results = await self._runner.start_run(run_nodes)
            except Exception as ex:  # noqa: BLE001  start_run 异常→全部当失败,清 dispatching 留 PENDING 交 harness
                logger.warning("[drain] task=%s start_run 异常:%s→全部留 PENDING 待 harness", task_id, ex)
                results = [False] * len(run_nodes)
            with self._lock_for(task_id):
                cur_map = {x.node_id: x for x in self._graph.query_task_nodes(
                    task_id, TaskNodeQueryCriteria(node_ids=[n.node_id for n in run_nodes]))}
                for node, ok in zip(run_nodes, results):
                    if not ok:
                        logger.warning("[drain] task=%s node=%s start_run 失败→清执行者留 PENDING 待 harness", task_id, node.node_id)
                        # 清 run_mode/assignee(置空串)+清 dispatching 使其重新可搜推;标 dispatch_error
                        self._graph.update_task_node_info(
                            TaskNodePatch(task_id=task_id, node_id=node.node_id, run_mode="", assignee="",
                                          extend_props_patch={"dispatching": None, "dispatch_error": "start_run_failed"}))
                        continue
                    cur = cur_map.get(node.node_id)
                    if cur is not None and cur.status == Status.PENDING:
                        self._graph.update_task_node_info(
                            TaskNodePatch(task_id=task_id, node_id=node.node_id, status=Status.RUNNING,
                                          extend_props_patch={"dispatching": None}))
                        # 动作历史:DISPATCH(HIT_SINGLE 单 bot 派发执行)
                        self._log_action(
                            task_id, node.node_id, NodeAction.DISPATCH,
                            {
                                "outcome": "HIT_SINGLE",
                                "run_mode": cur.run_info.run_mode,
                                "assignee": cur.run_info.assignee,
                            },
                            status_from=Status.PENDING, status_to=Status.RUNNING,
                        )
        # ② dispatch_fail:落 dispatch_error(留 PENDING,harness 按超时重试搜推)
        for patch in dispatch_fail_patches:
            self._graph.update_task_node_info(patch)
        # ③ miss 推进(递归 collect+drain)
        for m in miss_tasks:
            await self.on_miss(m)

    def _maybe_finish_graph(self, task_id: str) -> None:
        """根 gap 闭(终验通过)→ 全图 DONE。图级写收口 + 根节点翻 DONE。两写均经 SSOT 网关(锁内同步)。"""
        self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(status=Status.DONE, output_patch={"result": "all_done"}))
        root = self._root(task_id)
        if root is not None and root.status != Status.DONE:
            _rprev = root.status
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=root.node_id, status=Status.DONE))
            # 动作历史:TRANSITION(根 gap 闭终验通过 → root DONE)
            self._log_action(
                task_id, root.node_id, NodeAction.TRANSITION,
                {"reason": "root_gap_closed", "to": "DONE"},
                status_from=_rprev, status_to=Status.DONE,
            )
