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

import asyncio
import logging
import os
import random
import threading
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
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
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult


logger = logging.getLogger("task.engine")

_DEFAULT_MAX_HARNESS = 3  # 执行报错 harness 重投上限(达上限→HUNG)


@dataclass(frozen=True)
class CoopGroupStart:
    """Result of starting a BCN coop group: the group id + its initial session_id."""

    group_id: str
    session_id: str | None


class ExecutionEngine:
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution(协程化,全链路 async)。

    构造期收传输端口(bot/bcs/discover,DI 从配置注入),``_build_*`` 内部 new 引擎自带策略 + 接线 TaskExecutor。
    引擎自当 ResultSink(poller 终态回投→on_report)与 TaskContextBuilder(执行上下文派生),消除后填/back-reach-in。
    on_* 入参统一收口 TaskNodePatch。按事件 + 状态条件分段协调。同 task_id 串行(per-task RLock);
    跨 task 并行。投递/拉群 IO 锁外 await,gather+Semaphore 并发。loop_round 仅升 BBS 时 ++。
    测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。"""

    def __init__(
        self,
        graph,
        *,
        bot=None,
        bcs=None,
        discover=None,
        bcn: BcnService | None = None,
        bcs_identity=None,
        auth_gate=None,
        task_search_skill_enabled: bool = False,
        task_settings=None,
        api_base_url: str = "",
        bot_token_provider=None,
    ) -> None:
        """graph: TaskGraphService;bot: OpenApiBotPort;bcs: BcsClientPort;discover: BotDiscoverServiceProtocol。
        端口由 DI 从配置注入(local/prod/double 只换端口实现,引擎代码不变)。prod 必传;测试子类覆写
        ``_build_*`` 注入 stub 策略/投递时可省略(走 super 路径默认 berth)。

        BBS 任务模式候选通过 ``bcn.list_bots_by_task_modes``(注入的 BcnService,复用统一 provider 身份)查询。

        ``api_base_url``:任务后端 base url,经 _build_executor 透传给 TaskExecutor→bbs_runner.notify,
        拼成发给胜出 bot 的任务消息(spec §5:主动触发回投路径)。"""
        self._graph = graph
        self._bot = bot
        self._bcs = bcs
        self._discover = discover
        self._bcn = bcn
        self._bcs_identity = bcs_identity
        self._auth_gate = auth_gate
        self._task_search_skill_enabled = task_search_skill_enabled
        self._task_settings = task_settings
        self._api_base_url = api_base_url
        self._bot_token_provider = bot_token_provider
        self._bg_tasks: set[asyncio.Task] = set()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.RLock()
        from agentclaw.community.core.task.task_runner.callback_adapter import (
            CallbackAdapter,
        )

        self._cb_adapter = CallbackAdapter()
        self._poller_thread = None
        self._executor = self._build_executor()
        self._planner = self._build_planner()
        self._dispatcher = self._build_dispatcher()
        self._runner = self._build_runner()
        logger.info(
            "[task][engine] 构造完成 bot=%s bcs=%s discover=%s bcn=%s executor=%s",
            type(bot).__name__ if bot is not None else "None",
            type(bcs).__name__ if bcs is not None else "None",
            type(discover).__name__ if discover is not None else "None",
            "BcnService" if bcn is not None else "None",
            type(self._executor).__name__
            if self._executor is not None
            else "None(退桩)",
        )

    # ===== 任务类型分流 seams(委托 self._runner;TaskService.execute 调用)=====
    async def trigger_single_bot_workflow(
        self, *, task_id: str, bot_id: str, message: str
    ) -> BotSendResult:
        """Single-bot workflow trigger; returns the conversation session_id."""
        return await self._runner.trigger_workflow(
            bot_id=bot_id, message=message, metadata={"biz_task_id": task_id}
        )

    async def start_coop_group(self, gf: GroupFormation) -> CoopGroupStart:
        """Create the BCN coop group and fetch its initial session_id by default."""
        group_id = await self._runner.form_coop_group(gf)
        session_id = await self._runner.get_group_session(group_id)
        return CoopGroupStart(group_id=group_id, session_id=session_id)

    # ===== protected 工厂方法(测试子类可覆写注入 stub 策略/投递;引擎自带默认接真实端口)=====
    def _build_executor(self):
        if self._bot is None or self._bcs is None:
            logger.warning(
                "[task][engine] execution_backend 不装配(bot=%s bcs=%s)→ form_coop_group/start_run/"
                "trigger_workflow/run_bbs 全退 Avernet 桩(grp_<8hex>/stub_<8hex>/无 poller,任务卡 RUNNING 不收敛)。"
                "corp 排查: 确认 DEPLOY_PROFILE=corp + grep [task][corp-task] not configured 看哪个端口空。",
                "None" if self._bot is None else type(self._bot).__name__,
                "None" if self._bcs is None else type(self._bcs).__name__,
            )
            return None
        from agentclaw.community.core.task.task_runner.integration.task_executor import (
            TaskExecutor,
        )
        from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
            TaskExecutorResultPoller,
        )
        from agentclaw.community.core.task.task_runner.integration.prompt_formatter import (
            PromptFormatterImpl,
        )

        poller = TaskExecutorResultPoller(bot=self._bot, bcs=self._bcs)
        poller.set_on_result(self)
        exe = TaskExecutor(
            bot=self._bot,
            bcs=self._bcs,
            formatter=PromptFormatterImpl(),
            context=self,
            sink=self,
            poller=poller,
            identity_resolver=self._bcs_identity,
            graph=self._graph,
            api_base_url=self._api_base_url,
            bcn=self._bcn,
            bot_token_provider=self._bot_token_provider,
        )
        import threading as _t

        self._poller_thread = _t.Thread(
            target=poller.run_poll_loop, daemon=True, name="task-exec-poller"
        )
        self._poller_thread.start()
        logger.info(
            "[task][engine] execution_backend 已装配 TaskExecutor + poller 启动 bot=%s bcs=%s",
            type(self._bot).__name__,
            type(self._bcs).__name__,
        )
        return exe

    def _build_planner(self):
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        from agentclaw.community.core.task.task_plan.strategies import (
            GapBasedPlanningStrategy,
            WorkflowPlanningStrategy,
        )

        pool = [WorkflowPlanningStrategy()]
        if self._bot is not None:
            pool.append(GapBasedPlanningStrategy(self._bot))
        else:
            pool.append(GapBasedPlanningStrategy())
        return TaskPlanner(self._graph, pool=pool)

    def _build_dispatcher(self):
        from agentclaw.community.core.task.task_dispatch.dispatcher import (
            TaskDispatcher,
        )
        from agentclaw.community.core.task.task_dispatch.strategies import (
            DirectDispatchStrategy,
            SearchBasedDispatchStrategy,
        )

        pool = [DirectDispatchStrategy()]
        if self._bot is not None and self._discover is not None:
            pool.append(
                SearchBasedDispatchStrategy(
                    self._bot,
                    self._discover,
                    bcn=self._bcn,
                    join_gate=self._auth_gate,
                    use_search_skill=self._task_search_skill_enabled,
                    task_settings=self._task_settings,
                )
            )
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
            return {
                "mode": "execute",
                "parent_node_id": None,
                "parent_spec": None,
                "sibling_outputs": {},
                "node_spec": node.task_spec if node else None,
            }
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        sibling_outputs = {
            s.node_id: s.run_info.output
            for s in siblings
            if s.status == Status.DONE and s.node_id != node_id
        }
        return {
            "mode": "execute",
            "parent_node_id": parent.node_id,
            "parent_spec": parent.task_spec,
            "sibling_outputs": sibling_outputs,
            "node_spec": node.task_spec if node else None,
        }

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

    def _max_plan_round(self, task_id: str) -> int:
        """节点级重规划次数上限 MAX_PLAN_ROUND(default 10)。父节点子全 DONE→gap 未闭→重 plan 产新子,
        每次该路径走一次 +1;达上限父节点 HUNG(不再产子)"""
        cfg = self._graph._execution_config(task_id)
        return int(cfg.get("MAX_PLAN_ROUND", 10))

    def _task_type(self, task_id: str) -> str:
        """Return the immutable execution type recorded on the task graph."""
        try:
            raw = self._graph._execution_config(task_id).get("task_type", "dynamic")
        except Exception:  # noqa: BLE001 - missing/legacy graph defaults to dynamic
            return "dynamic"
        return str(getattr(raw, "value", raw) or "dynamic").strip().lower()

    def _is_external_managed_task(self, task_id: str) -> bool:
        """Whether a third party owns execution and next-node transitions."""
        return self._task_type(task_id) in {"workflow", "yaml"}

    def _is_graph_terminal(self, task_id: str) -> bool:
        """图级终态(DONE/HUNG)判定。终态后自动驱动(plan/dispatch/harness/回投推进)一律冻结:
        MAX_LOOP 达上限→图 HUNG 后,后续 on_pass/on_miss/on_harness 不再推进(避免 loop_round 失控飙升
        与节点无限增生);on_bbs_report(BBS 接力恢复)是唯一可从 HUNG 恢复的路径,不在本守卫范围。"""
        try:
            return self._graph.query_task_dashboard(task_id).status in {
                Status.DONE,
                Status.HUNG,
            }
        except Exception:  # noqa: BLE001  图不存在等→视为非终态,让正常入口逻辑处理
            return False

    async def _plan_with_retry(
        self, task_id: str, graph, target_node_id: str | None = None
    ):
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
            logger.warning(
                "[task][plan-retry] task=%s attempt=%d/%d gap_detail=%s",
                task_id,
                attempt + 1,
                max_h,
                pr.gap_detail,
            )
        # 可观测:落最近一次 plan 结果到图 extend_props(dashboard 可见,便于诊断 plan 为何产 []/HUNG)
        self._graph.update_task_graph_info(
            task_id,
            TaskGraphPatch(
                extend_props_patch={
                    "last_plan_target": target_node_id or "<root>",
                    "last_plan_children": len(pr.children),
                    "last_plan_has_gap": pr.has_gap,
                    "last_plan_detail": pr.gap_detail,
                }
            ),
        )
        # 动作历史:PLAN 事件(gap 计算 + 产子结果)挂到被规划目标节点(根 gap 反复计算的轨迹留痕)
        target_id = target_node_id
        if target_id is None:
            root = self._root(task_id)
            target_id = root.node_id if root else None
        if target_id is not None:
            self._log_action(
                task_id,
                target_id,
                NodeAction.PLAN,
                {
                    "target": target_node_id or "<root>",
                    "children": [c.node_id for c in pr.children],
                    "has_gap": pr.has_gap,
                    "gap_detail": pr.gap_detail,
                },
                status_from=Status.PLANNING,
                status_to=Status.PLANNING,
            )
        return pr

    def _mark_planning(self, task_id: str, node_id: str) -> None:
        """节点进入规划委托态:PENDING→PLANNING(幂等,已 PLANNING 不重翻)。
        规划是编排态(Status.PLANNING),不是执行模式:run_mode/assignee 保持 None。
        规划者(owner bot)隐式来自 graph.extend_props.owner_bot_id,不落节点 run_info。
        叶子派发执行时由 _prepare_into 覆写为 single_bot/coop_group/bbs+worker。"""
        graph = self._graph.query_task_dashboard(task_id)
        node = next((n for n in graph.tasks if n.node_id == node_id), None)
        if node is None or node.status != Status.PENDING:
            return  # 已 PLANNING / 已终态 → 幂等不翻
        self._graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PLANNING)
        )

    def _log_action(
        self,
        task_id: str,
        node_id: str,
        action: NodeAction,
        payload: dict,
        *,
        attempt: int | None = None,
        status_from: Status | None = None,
        status_to: Status | None = None,
    ) -> None:
        """追加节点动作历史快照(append-only;零侵入驱动逻辑)。

        供各逻辑动作(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/TRANSITION)完成时调用,
        纯可观测旁路:不翻态、不读回驱动。``attempt`` 省略时取节点 harness_retries 快照;
        ``status_from``/``status_to`` 省略时由调用方按动作前/后态传(未翻态可不传)。
        """
        if attempt is None:
            node = next(
                (
                    n
                    for n in self._graph.query_task_dashboard(task_id).tasks
                    if n.node_id == node_id
                ),
                None,
            )
            attempt = (
                int(node.run_info.extend_props.get("harness_retries", 0)) if node else 0
            )
        try:
            self._graph.append_action_event(
                task_id,
                node_id,
                action,
                payload,
                attempt=attempt,
                status_from=status_from,
                status_to=status_to,
            )
        except Exception as ex:  # noqa: BLE001  历史快照写入失败不影响驱动
            logger.warning(
                "[task][action-log] task=%s node=%s action=%s 追加失败:%s",
                task_id,
                node_id,
                action.value,
                ex,
            )

    def _static_runtime(self, task_id: str):
        from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
        from agentclaw.community.core.task.task_plan.static_plan_runtime import StaticPlanRuntime
        cfg = self._graph._execution_config(task_id)
        if cfg.get("task_type") != "static_plan":
            return None
        template_id = cfg.get("static_plan_id")
        try:
            definition = StaticPlanDefinition.from_yaml(str(cfg["static_plan_yaml"]))
            runtime = StaticPlanRuntime(definition, dict(cfg.get("template_input") or {}))
        except Exception:
            logger.exception(
                "[task][static-plan] runtime init failed task=%s template=%s",
                task_id,
                template_id,
            )
            raise
        logger.debug(
            "[task][static-plan] runtime loaded task=%s template=%s",
            task_id,
            template_id or definition.template_id,
        )
        return runtime

    async def _on_static_execute(self, task_id: str) -> None:
        runtime = self._static_runtime(task_id)
        if runtime is None:
            return
        graph = self._graph.query_task_dashboard(task_id)
        root = self._root(task_id)
        logger.info(
            "[task][static-plan] execute task=%s template=%s graph_status=%s root=%s relation_count=%s",
            task_id,
            runtime.definition.template_id,
            graph.status.value,
            root.node_id if root else None,
            len(graph.relations),
        )
        if root is None or graph.relations:
            logger.info(
                "[task][static-plan] execute task=%s skip materialize root_exists=%s already_materialized=%s",
                task_id,
                root is not None,
                bool(graph.relations),
            )
            return
        nodes = runtime.nodes(task_id, root.task_spec)
        self._graph.add_task_nodes(nodes, root.node_id)
        logger.info(
            "[task][static-plan] materialized task=%s node_count=%s",
            task_id,
            len(nodes),
        )
        side: list[tuple] = []
        await self._prepare_static(task_id, runtime, side)
        logger.info(
            "[task][static-plan] execute task=%s prepared side_effects=%s",
            task_id,
            [item[0] for item in side],
        )
        await self._drain(task_id, side)

    def _static_auto_report_on(self, task_id: str) -> bool:
        """演示自驱开关:开启后静态 plan 节点不做真实派发/拉群,转为后台自回投 mock 结果,
        复用同一 on_report 通路推进图态,便于上报/skill 未就绪时也能跑通全链路。
        优先级:按任务 execution_config.static_auto_report(bool) → 服务端 env OCB_TASK_STATIC_AUTO_REPORT。"""
        cfg = self._graph._execution_config(task_id)
        if cfg.get("task_type") != "static_plan":
            return False
        flag = cfg.get("static_auto_report")
        if isinstance(flag, bool):
            return flag
        return os.environ.get("OCB_TASK_STATIC_AUTO_REPORT", "").lower() in {"1", "true", "yes", "on"}

    async def _prepare_static(self, task_id: str, runtime, side: list[tuple]) -> None:
        graph = self._graph.query_task_dashboard(task_id)
        readiness = runtime.ready(graph)
        logger.info(
            "[task][static-plan] prepare task=%s ready=%s skipped=%s",
            task_id,
            [node.node_id for node in readiness.ready],
            [node.node_id for node in readiness.skipped],
        )
        for node in readiness.skipped:
            logger.info(
                "[task][static-plan] skip node task=%s node=%s reason=enabled_when",
                task_id,
                node.node_id,
            )
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node.node_id,
                    status=Status.DONE,
                    output_patch={"skipped": True},
                    extend_props_patch={"static_blocked": None},
                )
            )
        if readiness.ready:
            logger.info(
                "[task][static-plan] dispatch ready nodes task=%s nodes=%s",
                task_id,
                [node.node_id for node in readiness.ready],
            )
            # Static nodes use the YAML-bound bot directly; skip catalog search
            # and claim-join so dependencies are never dispatched ahead of time
            # and the bound bot_id (e.g. strategy_approval/implementation) is
            # honored instead of being replaced by whatever catalog returns.
            await self._prepare_static_into(task_id, runtime, readiness.ready, side)

    async def _prepare_static_into(
        self, task_id: str, runtime, ready_nodes, side: list[tuple]
    ) -> None:
        """Static DAG 节点跳过搜推,直接用 YAML 绑定的 bot 指派。

        type=bot → single_bot + assignee=definition.bot_id → start_run;
        type=collaboration → pending_group_formation → form_coop_group。
        不进 dispatcher.dispatch / 不查 catalog / 不做 claim_join,故未 ready 的依赖节点
        (strategy_approval/implementation)不会被提前搜推成 MISS/claim_mode_off,且 YAML 绑定
        的 bot_id 永远被尊重(不会被 catalog 命中的其他 bot 替换)。依赖顺序由 runtime.ready 保证。"""
        auto = self._static_auto_report_on(task_id)
        to_run: list[TaskNode] = []
        auto_nodes: list[TaskNode] = []
        for node in ready_nodes:
            definition = runtime.by_id.get(node.node_id)
            if definition is None:
                logger.warning(
                    "[task][static-plan] task=%s node=%s 无定义,跳过",
                    task_id,
                    node.node_id,
                )
                continue
            if auto:
                node_type = getattr(definition, "node_type", "bot")
                run_mode = "coop_group" if node_type == "collaboration" else "single_bot"
                bot_id = getattr(definition, "bot_id", None) or ""
                logger.info(
                    "[task][static-plan] task=%s node=%s -> auto-mock skip dispatch type=%s run_mode=%s",
                    task_id, node.node_id, node_type, run_mode,
                )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode=run_mode,
                        assignee=bot_id,
                        extend_props_patch={"dispatching": True},
                    )
                )
                auto_nodes.append(node)
                continue
            gf = node.run_info.extend_props.get("pending_group_formation")
            if gf is not None:
                gf.extend_props.setdefault(
                    "task_objective", node.task_spec.goal.objective
                )
                gf.extend_props.setdefault(
                    "task_instruction", node.task_spec.metadata.instruction
                )
                gf.extend_props.setdefault(
                    "acceptances",
                    [
                        {"id": a.id, "description": a.description}
                        for a in node.task_spec.goal.acceptances
                    ],
                )
                logger.info(
                    "[task][static-plan] task=%s node=%s → group(collab=%s bot_ids=%s) 跳过搜推",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    list(gf.bot_ids),
                )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="coop_group",
                        extend_props_patch={"dispatching": True},
                    )
                )
                side.append(("group", node, gf))
                continue
            bot_id = getattr(definition, "bot_id", None)
            if bot_id:
                node.run_info.run_mode = "single_bot"
                node.run_info.assignee = bot_id
                logger.info(
                    "[task][static-plan] task=%s node=%s → run(assignee=%s) 跳过搜推",
                    task_id,
                    node.node_id,
                    bot_id,
                )
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="single_bot",
                        assignee=bot_id,
                        extend_props_patch={"dispatching": True},
                    )
                )
                to_run.append(node)
            else:
                logger.warning(
                    "[task][static-plan] task=%s node=%s 无 bot 绑定也无 group,跳过",
                    task_id,
                    node.node_id,
                )
        if to_run:
            side.append(("run", to_run))
        if auto_nodes:
            side.append(("auto", auto_nodes))

    async def _static_auto_report(self, task_id: str, node_id: str) -> None:
        """演示自驱:延迟后用 mock 结果走 on_report,复用静态推进通路。"""
        runtime = self._static_runtime(task_id)
        if runtime is None:
            return
        delay = random.uniform(0.8, 2.0)
        logger.info(
            "[task][static-plan] auto-report scheduled task=%s node=%s in %.2fs",
            task_id, node_id, delay,
        )
        await asyncio.sleep(delay)
        definition = runtime.by_id.get(node_id)
        mock_result: Any = {
            "summary": f"[auto-mock] node={node_id}",
            "random": f"{random.randrange(10 ** 6):06d}",
        }
        if definition is not None and any(
            isinstance(v, str) and v.startswith("$.result.approved")
            for v in definition.output.values()
        ):
            mock_result["approved"] = True
        logger.info(
            "[task][static-plan] auto-report fire task=%s node=%s mock=%s -> on_report",
            task_id, node_id, mock_result,
        )
        await self.on_report(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                acceptance_result=AcceptanceResult(
                    verdict=AcceptanceVerdict.PASS,
                    acceptances_metric=["auto_mock"],
                ),
                output_patch={"result": mock_result},
                extend_props_patch={"dispatching": None},
            )
        )

    async def _on_static_report(self, task_id: str, node_id: str) -> None:
        runtime = self._static_runtime(task_id)
        if runtime is None:
            return
        graph = self._graph.query_task_dashboard(task_id)
        reported = next((n for n in graph.tasks if n.node_id == node_id), None)
        definition = runtime.by_id.get(node_id)
        logger.info(
            "[task][static-plan] report task=%s node=%s node_found=%s definition_found=%s status=%s output_keys=%s",
            task_id,
            node_id,
            reported is not None,
            definition is not None,
            reported.status.value if reported is not None else None,
            sorted(reported.run_info.output) if reported is not None else [],
        )
        if reported is not None and definition is not None:
            raw = dict(reported.run_info.output)
            mapped: dict[str, Any] = {}
            for key, expression in definition.output.items():
                if expression in ("$.result", "$.report.result"):
                    mapped[key] = raw.get("result", raw)
                elif expression.startswith("$.result."):
                    current: Any = raw.get("result", raw)
                    for part in expression[len("$.result."):].split("."):
                        current = current.get(part) if isinstance(current, dict) else None
                    mapped[key] = current
            if mapped:
                self._graph.update_task_node_info(
                    TaskNodePatch(task_id=task_id, node_id=node_id, output_patch=mapped)
                )
        side: list[tuple] = []
        await self._prepare_static(task_id, runtime, side)
        current = self._graph.query_task_dashboard(task_id)
        terminal = all(
            n.status in {Status.DONE, Status.FAILED, Status.HUNG}
            for n in current.tasks
            if n.node_id in runtime.by_id
        )
        logger.info(
            "[task][static-plan] report processed task=%s node=%s next_side_effects=%s terminal=%s node_states=%s",
            task_id,
            node_id,
            [item[0] for item in side],
            terminal,
            {n.node_id: n.status.value for n in current.tasks if n.node_id in runtime.by_id},
        )
        if terminal:
            self._graph.update_task_graph_info(task_id, TaskGraphPatch(status=Status.DONE))
            logger.info("[task][static-plan] completed task=%s template=%s", task_id, runtime.definition.template_id)
            return
        await self._drain(task_id, side)

    # ===== on_execute =====
    async def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph 后,条件 a(根 PENDING)→ plan(None 自发现根)→add→dispatch→start_run。"""
        if self._is_external_managed_task(task_id):
            logger.info("[task][on_execute] task=%s external-managed, skip Avernet orchestration", task_id)
            return
        if self._is_graph_terminal(task_id):
            logger.info(
                "[task][on_execute] task=%s 图已终态(%s),冻结驱动",
                task_id,
                self._graph.query_task_dashboard(task_id).status.value,
            )
            return
        if self._static_runtime(task_id) is not None:
            await self._on_static_execute(task_id)
            return
        side: list[tuple] = []
        with self._lock_for(task_id):
            root = self._root(task_id)
            logger.info(
                "[task][on_execute] task=%s root=%s status=%s",
                task_id,
                root.node_id if root else None,
                root.status if root else None,
            )
            if root is None or root.status != Status.PENDING:
                logger.info(
                    "[task][on_execute] task=%s 非条件 a(根非 PENDING),跳过", task_id
                )
                return
            graph = self._graph.query_task_dashboard(task_id)
            self._mark_planning(task_id, root.node_id)  # root 由 owner bot 规划
            pr = await self._plan_with_retry(
                task_id, graph
            )  # None → 自发现根(含 plan 容错重试)
            logger.info(
                "[task][on_execute] task=%s plan 产 %d 子节点: %s",
                task_id,
                len(pr.children),
                [n.node_id for n in pr.children],
            )
            if pr.children:
                self._graph.add_task_nodes(pr.children, root.node_id)
                await self._prepare_into(task_id, side)
            elif not pr.has_gap:
                self._maybe_finish_graph(task_id)  # 根 gap 初始即闭(罕见)
            else:
                self._hung_and_escalate(
                    task_id, root.node_id, "root_gap_no_decompose"
                )  # 有 gap 拆不出 → HUNG 升 BBS
        await self._drain(task_id, side)

    async def redrive(self, task_id: str) -> None:
        """Recovery resume entrypoint: re-dispatch pending leaf nodes of a
        hydrated non-terminal task after an instance restart / rolling deploy.

        Mirrors the dispatch tail of ``on_execute`` but starts from the
        already-hydrated graph (``query_task_dashboard`` hydrates from the shared
        store on cache miss): collect未派发 PENDING 叶 → dispatch → start_run.
        Only non-terminal runtime statuses are recoverable (the worker filters),
        and terminal graphs freeze immediately. Idempotent: ``_prepare_into``
        skips nodes already ``dispatching`` and the status machine guards repeats.
        """
        if self._is_external_managed_task(task_id):
            logger.info("[task][redrive] task=%s external-managed, skip Avernet redrive", task_id)
            return
        if self._is_graph_terminal(task_id):
            logger.info("[task][redrive] task=%s 图已终态,冻结重投", task_id)
            return
        side: list[tuple] = []
        with self._lock_for(task_id):
            graph = self._graph.query_task_dashboard(task_id)
            logger.info(
                "[task][redrive] task=%s status=%s resume dispatch",
                task_id,
                graph.status.value,
            )
            await self._prepare_into(task_id, side)
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
                    task_id=patch.task_id,
                    node_id=patch.node_id,
                    success=True,
                    prev_status=Status.RUNNING,
                    new_status=Status.RUNNING,
                )
            if node.status in {
                Status.DONE,
                Status.FAILED,
                Status.HUNG,
                Status.PLANNING,
            }:
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
        logger.info(
            "[task][on_report] task=%s node=%s exec_error=%s verdict=%s",
            patch.task_id,
            patch.node_id,
            patch.exec_error,
            patch.acceptance_result.verdict if patch.acceptance_result else "fold-only",
        )
        with self._lock_for(patch.task_id):
            result = self._graph.update_task_node_info(patch)
            if self._static_runtime(patch.task_id) is not None:
                # Static plans use the same harness contract as dynamic tasks.
                if patch.exec_error is not None:
                    side: list[tuple] = []
                    await self._on_harness_collect(
                        patch.task_id, patch.node_id, patch.exec_error, side
                    )
                    await self._drain(patch.task_id, side)
                elif patch.acceptance_result is not None:
                    await self._on_static_report(patch.task_id, patch.node_id)
                return result
            # 动作历史:EXECUTE(执行产出)+ VERIFY(验收结论)——回投即一个执行动作闭环
            _out = dict(patch.output_patch) if patch.output_patch else {}
            if patch.exec_error is not None:
                self._log_action(
                    patch.task_id,
                    patch.node_id,
                    NodeAction.EXECUTE,
                    {"success": False, "exec_error": patch.exec_error, "output": _out},
                    status_from=result.prev_status,
                    status_to=result.new_status,
                )
            elif patch.acceptance_result is not None:
                _ar = patch.acceptance_result
                self._log_action(
                    patch.task_id,
                    patch.node_id,
                    NodeAction.EXECUTE,
                    {"success": _ar.verdict == AcceptanceVerdict.PASS, "output": _out},
                    status_from=result.prev_status,
                    status_to=result.new_status,
                )
                self._log_action(
                    patch.task_id,
                    patch.node_id,
                    NodeAction.VERIFY,
                    {
                        "verdict": _ar.verdict.value,
                        "acceptances_metric": list(_ar.acceptances_metric),
                        "gaps": list(_ar.gaps),
                    },
                    status_from=result.prev_status,
                    status_to=result.new_status,
                )
            if self._is_external_managed_task(patch.task_id):
                # Third-party execution owns transitions. Mirror a terminal
                # root status to the graph for dashboard visibility, but never
                # enter Avernet planner/dispatcher/retry orchestration.
                if (
                    patch.node_id == patch.task_id
                    and result.new_status in {
                        Status.DONE,
                        Status.FAILED,
                        Status.HUNG,
                        Status.CANCELLED,
                    }
                ):
                    self._graph.update_task_graph_info(
                        patch.task_id,
                        TaskGraphPatch(status=result.new_status),
                    )
                logger.info(
                    "[task][on_report] task=%s external-managed, graph update only",
                    patch.task_id,
                )
                return result
            if patch.exec_error is not None:
                side: list[tuple] = []
                await self._on_harness_collect(
                    patch.task_id, patch.node_id, patch.exec_error, side
                )
                await self._drain(patch.task_id, side)
                return result
            if patch.acceptance_result is None:
                return result  # 仅 fold,无翻态
            if self._is_graph_terminal(patch.task_id):
                logger.info(
                    "[task][on_report] task=%s 图已终态,fold 已落但冻结驱动",
                    patch.task_id,
                )
                return result
            side = []
            verdict = patch.acceptance_result.verdict
            if verdict == AcceptanceVerdict.PASS:
                await self._on_pass_collect(patch.task_id, patch.node_id, side)
            else:  # FAIL
                await self._on_fail_collect(patch.task_id, patch.node_id, side)
            await self._drain(patch.task_id, side)
            return result

    # ===== on_bbs_report:BBS 接力步⑤回投 =====
    async def on_bbs_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """BBS 接力步⑤回投:翻 scoped 节点终态 + 释放 claim,**收口交给 engine 既有路径(非 bot 声明)**。

        不再有 ``root_verified``:根目标是否满足由框架经 owner 复核(``_on_pass_collect``→``plan(root)``→
        ``has_gap=False``→``_maybe_finish_graph``)判定,**不由接力 bot 自报**。scoped 节点 PASS→DONE 走正常
        PASS 传播(parent=root;守卫对 ``run_mode=="bbs"`` 触发的 root 复核放行,见 §10.5 seam 对应处);
        FAIL+gaps→**删 scoped 节点**(丢弃本次接力尝试:不翻 FAILED、不 ``output_patch`` fold);
        图回到 root ``PLANNING``+``bbs_mode`` 可恢复态等下段重新 claim/attach,**不进 FAIL 传播**。
        最后清根 ``bbs_owner`` 释放 claim。

        持有者校验:``root.run_info.extend_props['bbs_owner']`` 须 == ``patch.assignee``(调用方
        ``report_bbs_result`` 设 ``patch.assignee=bot_id``);非持有者 → ``TaskStateError``(在校验抛,
        不清 claim)。

        释放安全:scoped 终态翻转(fold)收在 ``try`` 内,``finally`` 无条件清根 ``bbs_owner`` —— 翻态抛错也
        释放 claim,避免持卡者死锁(他 bot claim 被 CAS 拒)。owner 校验在 ``try`` 之前,非持有者抛错不清他卡。

        无 owner bot 时(单测)``plan(root)`` 返 ``has_gap=True``(no_planning_port)→ ``gap_no_progress`` → 父
        HUNG;故收口需 owner planner(live 有),单测只验 mechanics(scoped DONE + claim 释放)。"""
        if self._is_external_managed_task(patch.task_id):
            logger.info("[task][on_bbs_report] task=%s external-managed, graph update only", patch.task_id)
            return self._graph.update_task_node_info(patch)
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            graph = self._graph.query_task_dashboard(patch.task_id)
            root = next((n for n in graph.tasks if n.node_id == patch.task_id), None)
            if (
                root is None
                or root.run_info.extend_props.get("bbs_owner") != patch.assignee
            ):
                raise TaskStateError(
                    f"on_bbs_report: 非claim持有者 task={patch.task_id}"
                )
            # FAIL:丢弃本次接力尝试——删 scoped 节点(不翻 FAILED、不 fold output_patch/gaps 作 checkpoint);
            # 图回 root PLANNING+bbs_mode 可恢复态等下段重 claim/attach,不进 PASS/FAIL 传播。
            # PASS / fold-only(无 acceptance):scoped 终态翻转(PASS→DONE)或 fold(output_patch/exec_error)走原路径。
            is_fail = (
                patch.acceptance_result is not None
                and patch.acceptance_result.verdict == AcceptanceVerdict.FAIL
            )
            try:
                if is_fail:
                    if not patch.acceptance_result.gaps:
                        raise TaskStateError("on_bbs_report: FAIL 验收强制要求 gaps")
                    prev = next(
                        (n for n in graph.tasks if n.node_id == patch.node_id), None
                    )
                    self._graph.delete_task_node(patch.task_id, patch.node_id)
                    result = NodeOpResult(
                        task_id=patch.task_id,
                        node_id=patch.node_id,
                        success=True,
                        prev_status=(prev.status if prev else None),
                        new_status=None,
                    )
                    logger.info(
                        "[task][on_bbs_report] task=%s FAIL → 删 scoped 节点 %s(gaps=%s),claim 释放",
                        patch.task_id,
                        patch.node_id,
                        patch.acceptance_result.gaps,
                    )
                else:
                    # scoped 节点终态翻转(acceptance→DONE)或 fold(output_patch/exec_error)
                    result = self._graph.update_task_node_info(patch)
            finally:
                # 无论 FAIL 删节点 / PASS 翻态是否抛,都清根 bbs_owner 释放 claim
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=patch.task_id,
                        node_id=patch.task_id,
                        extend_props_patch={"bbs_owner": None},
                    )
                )
            # 收口:FAIL 已删节点(无 DONE/FAILED 可传播);PASS → scoped DONE→owner 复核根 gap 收口
            if is_fail:
                pass  # 图回可恢复态,等下段重 claim;无 PASS/FAIL 传播
            elif self._is_graph_terminal(patch.task_id):
                logger.info(
                    "[task][on_bbs_report] task=%s 图已终态,不再驱动", patch.task_id
                )
            else:
                node = next(
                    (
                        n
                        for n in self._graph.query_task_dashboard(patch.task_id).tasks
                        if n.node_id == patch.node_id
                    ),
                    None,
                )
                if node is not None and node.status == Status.DONE:
                    await self._on_pass_collect(patch.task_id, patch.node_id, side)
                elif node is not None and node.status == Status.FAILED:
                    await self._on_fail_collect(patch.task_id, patch.node_id, side)
        await self._drain(patch.task_id, side)
        return result

    async def _on_pass_collect(
        self, task_id: str, node_id: str, side: list[tuple]
    ) -> None:
        """PASS→DONE 后:查结构父 P。v4 父恒 PLANNING(委托态),无需翻态:
        兄弟仍有未终态(RUNNING/PLANNING/PENDING)→等待;兄弟全 DONE(plan-ready)→ plan(target=parent):
          有子→节点级 plan_round++(达 MAX_PLAN_ROUND→父 HUNG)+add+dispatch;
          空+has_gap=F→gap 闭:非根传播 DONE 上行/根→图 DONE;空+has_gap=T→HUNG 升 BBS。
        兄弟全终态含 HUNG/FAILED→终态传播。
        v5:重规划产子由**节点级 plan_round** 闸(根+中间父统一计数);loop_round 收敛为只数升 BBS。"""
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            side.append(("finish", task_id))
            return
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        logger.info(
            "[task][on_pass] task=%s node=%s 父=%s 父态=%s 兄弟=%s",
            task_id,
            node_id,
            parent.node_id,
            parent.status,
            [(s2.node_id, s2.status.value) for s2 in siblings],
        )
        if any(
            st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING}
            for st in siblings
        ):
            logger.info("[task][on_pass] task=%s 兄弟未全终态,等待", task_id)
            return
        if not all(st.status == Status.DONE for st in siblings):
            self._propagate_terminal(task_id, parent, siblings, side)
            return
        root = self._root(task_id)
        is_root_parent = parent.node_id == (root.node_id if root else None)
        # BBS 可恢复态守卫:图已升 BBS(bbs_mode=true)且根未被 BBS 接力持有(bbs_owner=None)→
        # 普通子节点 DONE 不触发根重规划(避免与 BBS 接力竞态,spec §10.4);由 BBS 接力收口。
        # **例外**:触发本轮 PASS 的是 ``run_mode=="bbs"`` scoped 节点(BBS 接力刚回投的进展)→ 放行 owner
        # 复核根 gap,满足则收口(``_maybe_finish_graph``),否则继续接力。无此例外则删去 ``root_verified`` 后
        # 收口会被守卫死锁(图 bbs_mode 且未 claim 时谁也收不了)。
        if is_root_parent:
            g_ext = self._graph.query_task_dashboard(task_id).extend_props
            if g_ext.get("bbs_mode") and not g_ext.get("bbs_owner"):
                triggering = next(
                    (
                        n
                        for n in self._graph.query_task_dashboard(task_id).tasks
                        if n.node_id == node_id
                    ),
                    None,
                )
                if (
                    triggering is not None
                    and (triggering.run_info.run_mode or "") == "bbs"
                ):
                    logger.info(
                        "[task][on_pass] task=%s bbs scoped 节点 DONE→放行 owner 复核根 gap 收口",
                        task_id,
                    )
                else:
                    logger.info(
                        "[task][on_pass] task=%s 图 bbs_mode 且未 claim,普通叶子→owner 停手等 BBS 接力",
                        task_id,
                    )
                    return
        self._mark_planning(task_id, parent.node_id)
        graph = self._graph.query_task_dashboard(task_id)
        pr = await self._plan_with_retry(task_id, graph, target_node_id=parent.node_id)
        logger.info(
            "[task][on_pass] task=%s 父=%s 委托 plan 产 %d 子 has_gap=%s",
            task_id,
            parent.node_id,
            len(pr.children),
            pr.has_gap,
        )
        if pr.children:
            # 节点级重规划次数闸 MAX_PLAN_ROUND(父节点"子全 DONE→gap 未闭→重 plan 产新子"计数):
            # 每个父节点各自计数(extend_props.plan_round);达上限 → 父 HUNG(gap_no_progress_plan_round)
            # + 冒泡终态传播,不再 add 新子。首帧 plan(on_execute)不计;on_miss 拆细不计。
            plan_round = int(parent.run_info.extend_props.get("plan_round", 0)) + 1
            max_plan_round = self._max_plan_round(task_id)
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=parent.node_id,
                    extend_props_patch={"plan_round": plan_round},
                )
            )
            if plan_round >= max_plan_round:
                logger.warning(
                    "[task][on_pass] task=%s 父=%s plan_round=%d/%d 达上限→HUNG(不再产子)",
                    task_id,
                    parent.node_id,
                    plan_round,
                    max_plan_round,
                )
                self._hung_and_escalate(task_id, parent.node_id, "plan_round_exhausted")
                return
            logger.info(
                "[task][on_pass] task=%s 父=%s plan_round=%d/%d 重规划产 %d 子",
                task_id,
                parent.node_id,
                plan_round,
                max_plan_round,
                len(pr.children),
            )
            self._graph.add_task_nodes(pr.children, parent.node_id)
            await self._prepare_into(task_id, side)
        elif not pr.has_gap:
            if is_root_parent:
                self._maybe_finish_graph(task_id)
                return
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id, node_id=parent.node_id, status=Status.DONE
                )
            )
            # 动作历史:TRANSITION(非根 gap 闭传播 DONE)
            self._log_action(
                task_id,
                parent.node_id,
                NodeAction.TRANSITION,
                {"reason": "gap_closed_propagate", "to": "DONE"},
                status_from=Status.PLANNING,
                status_to=Status.DONE,
            )
            await self._on_pass_collect(task_id, parent.node_id, side)
        else:
            self._hung_and_escalate(task_id, parent.node_id, "gap_no_progress")

    async def _on_fail_collect(
        self, task_id: str, node_id: str, side: list[tuple]
    ) -> None:
        """验收不过(FAIL+gaps)→FAILED。v4:不立即补救拆子,置 FAILED 后交由 harness 周期巡检"重新派发执行"
        重试(不拆);harness 重试达 MAX_HARNESS→HUNG→升 BBS。本方法仅落 FAILED + 记 gaps,不推进 plan。"""
        _n = next(
            (
                x
                for x in self._graph.query_task_dashboard(task_id).tasks
                if x.node_id == node_id
            ),
            None,
        )
        logger.info(
            "[task][on_fail] task=%s node=%s → FAILED(gaps=%s),交 harness 重试重新派发",
            task_id,
            node_id,
            (
                _n.run_info.acceptance_result.gaps
                if _n and _n.run_info.acceptance_result
                else None
            ),
        )
        # FAILED 已由 acceptance patch 落态。重试重新派发由 harness 巡检 FAILED 触发(见 TaskHarness._poll_once)。
        # 此处不 plan、不 add,直接返回。

    async def _on_harness_collect(
        self, task_id: str, node_id: str, exec_error: str, side: list[tuple]
    ) -> None:
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
        logger.info(
            "[task][on_harness] task=%s node=%s reason=%s retries=%d/%d",
            task_id,
            node_id,
            exec_error,
            retries,
            max_harness,
        )
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                extend_props_patch={
                    "harness_retries": retries,
                    "last_exec_error": exec_error,
                },
            )
        )
        if retries >= max_harness:
            logger.warning(
                "[task][on_harness] task=%s node=%s 达 MAX_HARNESS(%d)→HUNG",
                task_id,
                node_id,
                max_harness,
            )
            self._hung_and_escalate(task_id, node_id, "exec_stuck")
            return
        # 复位到 PENDING 重新派发执行:FAILED/RUNNING→PENDING;PENDING 派发卡住(搜推无响应/派发失败)清
        # dispatch_error 让 prepare 重新派发(harness owns 重试计数+HUNG 上限,正常 cycle 跳过 dispatch_error 节点)
        if node.status in {Status.FAILED, Status.RUNNING}:
            _prev = node.status
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PENDING)
            )
            # 动作历史:RESET(harness 重新派发执行重试)
            self._log_action(
                task_id,
                node_id,
                NodeAction.RESET,
                {
                    "reason": exec_error or "failed_retry",
                    "prev_status": _prev.value,
                    "harness_retries_after": retries,
                },
                attempt=retries,
                status_from=_prev,
                status_to=Status.PENDING,
            )
        elif node.status == Status.PENDING and node.run_info.extend_props.get(
            "dispatch_error"
        ):
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    extend_props_patch={"dispatch_error": None},
                )
            )
        # static plan:harness 重派走 static prepare(只派发 readiness.ready 的绑定 bot),
        # 不进搜推/claim_join,避免依赖未满足的节点(strategy_approval/implementation)被提前搜推
        # 派给 catalog 命中的错误 bot(如 default:35983)。
        _static_runtime = self._static_runtime(task_id)
        if _static_runtime is not None:
            await self._prepare_static(task_id, _static_runtime, side)
        else:
            await self._prepare_into(task_id, side)

    # ===== on_miss =====
    async def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS(搜推未匹配执行者)→深度闸门:
        depth>=MAX → HUNG 升 BBS(拆不动,无 bot);depth<MAX → mark_planning + plan(target=miss 叶)拆细:
        有子→add(父置 PLANNING)+dispatch;空+has_gap=F→gap 闭不推进(罕见);空+has_gap=T→HUNG 升 BBS。
        MISS 不进 harness(无 bot 无可重试执行体)。"""
        if self._is_external_managed_task(patch.task_id):
            logger.info("[task][on_miss] task=%s external-managed, skip dynamic planning", patch.task_id)
            return
        if self._is_graph_terminal(patch.task_id):
            logger.info(
                "[task][on_miss] task=%s 图已终态,冻结 MISS 推进", patch.task_id
            )
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
                patch.task_id,
                patch.node_id,
                NodeAction.DISPATCH,
                {
                    "outcome": "MISS",
                    "miss_reason": _miss_reason,
                    "depth": depth,
                    "max_depth": max_depth,
                },
                status_from=Status.PENDING,
                status_to=Status.PENDING,
            )
            if depth >= max_depth:
                logger.info(
                    "[task][on_miss] task=%s node=%s depth=%d/%d 拆不动→HUNG",
                    patch.task_id,
                    patch.node_id,
                    depth,
                    max_depth,
                )
                self._hung_and_escalate(
                    patch.task_id, patch.node_id, "miss_depth_exhausted"
                )
                await self._drain(patch.task_id, side)
                return
            self._mark_planning(patch.task_id, patch.node_id)
            graph = self._graph.query_task_dashboard(patch.task_id)
            pr = await self._plan_with_retry(
                patch.task_id, graph, target_node_id=patch.node_id
            )
            logger.info(
                "[task][on_miss] task=%s node=%s depth=%d/%d plan 产 %d 子 has_gap=%s",
                patch.task_id,
                patch.node_id,
                depth,
                max_depth,
                len(pr.children),
                pr.has_gap,
            )
            if pr.children:
                self._graph.add_task_nodes(pr.children, patch.node_id)
                await self._prepare_into(patch.task_id, side)
            elif not pr.has_gap:
                pass
            else:
                self._hung_and_escalate(
                    patch.task_id, patch.node_id, "miss_no_decompose"
                )
        await self._drain(patch.task_id, side)

    # ===== on_harness(harness 旁路入口:超时/崩溃/FAILED 巡检;复用 _on_harness_collect)=====
    async def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路入口:exec_error 语义(超时/崩溃/FAILED 巡检)→ 复用 _on_harness_collect 重新派发重试/上限 HUNG。"""
        if self._is_external_managed_task(patch.task_id):
            logger.info("[task][on_harness] task=%s external-managed, skip Avernet retry", patch.task_id)
            return
        if self._is_graph_terminal(patch.task_id):
            logger.info(
                "[task][on_harness] task=%s 图已终态,冻结 harness 推进", patch.task_id
            )
            return
        side: list[tuple] = []
        with self._lock_for(patch.task_id):
            await self._on_harness_collect(
                patch.task_id,
                patch.node_id,
                patch.exec_error or "external_harness",
                side,
            )
        await self._drain(patch.task_id, side)

    # ===== HUNG + 升 BBS(loop_round++ / 图 HUNG) + 终态传播 =====
    def _bump_loop_round(self, task_id: str) -> None:
        """图级 loop_round += 1。v4 仅两处计:根 gap 未闭重 plan(口子 A) + HUNG 升 BBS。达 MAX_LOOP→图 HUNG。"""
        graph = self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(loop_round_increment=1)
        )
        max_loop = self._graph._execution_config(task_id)["MAX_LOOP"]
        if graph.loop_round >= max_loop:
            self._graph.update_task_graph_info(
                task_id,
                TaskGraphPatch(
                    status=Status.HUNG,
                    extend_props_patch={"hung_reason": "loop_exhausted"},
                ),
            )
            logger.warning(
                "[task][loop_round] task=%s 达 MAX_LOOP(%d)→图 HUNG", task_id, max_loop
            )

    def _hung_and_escalate(self, task_id: str, node_id: str, hung_reason: str) -> None:
        """节点置 HUNG + 父终态传播检查 + 升 BBS(loop_round++,bbs_mode;节点保留不 remove)。纯同步(锁内)。"""
        _prev = next(
            (
                n.status
                for n in self._graph.query_task_dashboard(task_id).tasks
                if n.node_id == node_id
            ),
            None,
        )
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                status=Status.HUNG,
                extend_props_patch={"hung_reason": hung_reason},
            )
        )
        # 动作历史:TRANSITION(节点 HUNG)
        self._log_action(
            task_id,
            node_id,
            NodeAction.TRANSITION,
            {"reason": hung_reason, "to": "HUNG"},
            status_from=_prev,
            status_to=Status.HUNG,
        )
        logger.info(
            "[task][hung] task=%s node=%s reason=%s → 升 BBS(loop_round++)",
            task_id,
            node_id,
            hung_reason,
        )
        self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True})
        )
        self._bump_loop_round(task_id)
        # 终态传播:查父,若兄弟全终态且含 HUNG→父 HUNG(冒泡,递归)
        # 传 hung_reason:_maybe_propagate_hung 依据它判"可恢复 vs 硬死锁"(spec §10.5)
        self._maybe_propagate_hung(task_id, node_id, hung_reason)

    def _on_bg_done(self, bg: "asyncio.Task") -> None:
        """后台 run_bbs 完成:脱离跟踪集 + 异常可见(记 log,不抛,不阻塞 on_*)。"""
        self._bg_tasks.discard(bg)
        if bg.cancelled():
            return
        exc = bg.exception()
        if exc is not None:
            logger.error("[task][engine] run_bbs bg task 异常: %s", exc, exc_info=exc)

    def _schedule_bbs_notify(self, task_id: str, execution_graph) -> None:
        """可恢复拦截点(spec §5):fire-and-forget ``runner.run_bbs(execution_graph)``。

        命中根 BBS 可恢复态(miss_depth_exhausted + bbs_mode + 未 claim)时调用——主动 bid→select→claim→
        dispatch 给 dream-mode bot。不持锁、不阻塞 ``on_*``/``_maybe_propagate_hung`` 汇报路径:``asyncio.create_task``
        调度后台协程,异常经 ``_on_bg_done`` 记 log。端口不全(无 runner/bot/bcs,如单测 stub)→ 静默跳过。"""
        if self._runner is None or self._bot is None or self._bcs is None:
            return
        bg = asyncio.create_task(self._runner.run_bbs(execution_graph))
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._on_bg_done)
        logger.info(
            "[task][engine] task=%s 升BBS可恢复态→主动通知 dream-mode bot", task_id
        )

    def _maybe_propagate_hung(
        self, task_id: str, node_id: str, hung_reason: str = ""
    ) -> None:
        """自 node 往上:若父的子全终态且含 HUNG → 父 HUNG(不计额外 loop_round,纯冒泡)→ 继续上行。
        到根 → 图终态收口(HUNG)。若图已 HUNG(loop_exhausted 等已收口)→ 不覆盖 hung_reason。

        **BBS 可恢复态(spec §10.5)**:仅当根冒泡的来源 ``hung_reason``=``miss_depth_exhausted``
        (LAN 未匹配执行者,BBS 中继可接)且图 ``bbs_mode`` 已置、根未被 claim 时,拒不置图 HUNG,
        维持根原态(PLANNING 待接力)。其它 reason(``root_gap_no_decompose``/``gap_no_progress``/
        ``plan_round_exhausted``/``exec_stuck`` 等)是**硬死锁**(无规划端口 / 拆不出 / 重规划无进展 /
        执行卡死),即使 bbs_mode 也按硬 HUNG 收口(无规划端口可继 / 无进展可恢复;为避免"图一直 RUNNING 假活着"
        统一收口 HUNG)。"""
        # 仅 MISS 深度闸门升 BBS 视为可恢复;其它 reason 即便 bbs_mode 也走硬 HUNG 冒泡
        recoverable = hung_reason == "miss_depth_exhausted"
        cur = node_id
        while True:
            parent = self._graph.get_parent_task(task_id, cur)
            if parent is None:
                # cur 是根 → 图级收口(根 HUNG → 图 HUNG);不覆盖已设的图级 hung_reason。
                # 但若图已 bbs_mode=true 且根未被 BBS 持有 → 维持根原态(冒泡到此不置图 HUNG),
                # 留 BBS 接力可恢复(spec §10.5:升 BBS 落可恢复态,非图级硬 HUNG)。
                root = self._root(task_id)
                if (
                    root is not None
                    and root.node_id == cur
                    and root.status == Status.HUNG
                ):
                    g = self._graph.query_task_dashboard(task_id)
                    if g.status == Status.HUNG:
                        return
                    if (
                        recoverable
                        and g.extend_props.get("bbs_mode")
                        and not g.extend_props.get("bbs_owner")
                    ):
                        logger.info(
                            "[task][hung-propagate] task=%s 根冒泡被 BBS 可恢复态拦截(reason=%s),保持根原态",
                            task_id,
                            hung_reason,
                        )
                        self._schedule_bbs_notify(task_id, g)
                        return
                    self._graph.update_task_graph_info(
                        task_id,
                        TaskGraphPatch(
                            status=Status.HUNG,
                            extend_props_patch={"hung_reason": "root_stuck"},
                        ),
                    )
                return
            siblings = self._graph.get_child_tasks(task_id, parent.node_id)
            if any(
                st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING}
                for st in siblings
            ):
                return  # 还有活子,等
            if any(st.status == Status.HUNG for st in siblings):
                # BBS 可恢复态(spec §10.5):若父即根 + reason=miss_depth_exhausted + bbs_mode 已置且未 claim,
                # 不把根置 HUNG(保持 PLANNING 待 BBS 中继接管);否则正常冒泡。
                root = self._root(task_id)
                _g_now = self._graph.query_task_dashboard(task_id)
                if (
                    root is not None
                    and parent.node_id == root.node_id
                    and recoverable
                    and _g_now.extend_props.get("bbs_mode")
                    and not _g_now.extend_props.get("bbs_owner")
                ):
                    logger.info(
                        "[task][hung-propagate] task=%s 根可恢复态拦截(miss_depth_exhausted),根保持 PLANNING",
                        task_id,
                    )
                    self._schedule_bbs_notify(task_id, _g_now)
                    return
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=parent.node_id,
                        status=Status.HUNG,
                        extend_props_patch={"hung_reason": "child_hung"},
                    )
                )
                logger.info(
                    "[task][hung-propagate] task=%s 父=%s 因子含 HUNG→HUNG",
                    task_id,
                    parent.node_id,
                )
                cur = parent.node_id
                continue
            return

    def _propagate_terminal(
        self, task_id: str, parent: TaskNode, siblings: list, side: list[tuple]
    ) -> None:
        """on_pass 时兄弟全终态但非全 DONE(含 HUNG):子含 HUNG→父 HUNG 冒泡(经 _maybe_propagate_hung)。
        FAILED 子由 harness 巡检补救,此处若仅 FAILED(无 HUNG)不在此处理(等 harness 补救/转 HUNG)。"""
        if any(st.status == Status.HUNG for st in siblings):
            self._maybe_propagate_hung(
                task_id, siblings[0].node_id if siblings else parent.node_id
            )

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
        if self._is_external_managed_task(task_id):
            logger.info("[task][prepare] task=%s external-managed, skip dynamic dispatch", task_id)
            return
        all_pending = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(status=Status.PENDING)
        )
        pending = [
            n
            for n in all_pending
            if not n.run_info.extend_props.get("dispatching")
            and not n.run_info.extend_props.get("dispatch_error")
            and n.run_info.run_mode != "bbs"
        ]
        if not pending:
            return
        logger.info(
            "[task][prepare] task=%s 待派发节点=%s",
            task_id,
            [n.node_id for n in pending],
        )
        dispatched = await self._dispatcher.dispatch(pending)
        to_run: list[TaskNode] = []
        for node in dispatched:
            miss = node.run_info.extend_props.get("miss_events")
            gf = node.run_info.extend_props.pop("pending_group_formation", None)
            if gf is not None:
                logger.info(
                    "[task][prepare] task=%s node=%s → group(HIT_MULTI_BOTS collab=%s bot_ids=%s)",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    gf.bot_ids,
                )
                # 群验收需要完整 goal/instruction，而不是只有一句 task_context。
                gf.extend_props.setdefault(
                    "task_objective", node.task_spec.goal.objective
                )
                gf.extend_props.setdefault(
                    "task_instruction", node.task_spec.metadata.instruction
                )
                gf.extend_props.setdefault(
                    "acceptances",
                    [
                        {"id": a.id, "description": a.description}
                        for a in node.task_spec.goal.acceptances
                    ],
                )
                # 飞行标记:group 交付 _drain 拉群前置,防并发 cycle 双搜推双拉群
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="coop_group",
                        extend_props_patch={"dispatching": True},
                    )
                )
                side.append(("group", node, gf))
                continue
            if node.run_info.run_mode and node.run_info.assignee:
                logger.info(
                    "[task][prepare] task=%s node=%s → run(mode=%s assignee=%s)",
                    task_id,
                    node.node_id,
                    node.run_info.run_mode,
                    node.run_info.assignee,
                )
                # HIT:落执行者+飞行标记 dispatching(保持 PENDING);start_run 成功后 _drain 翻 RUNNING+清 dispatching
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode=node.run_info.run_mode,
                        assignee=node.run_info.assignee,
                        extend_props_patch={"dispatching": True},
                    )
                )
                to_run.append(node)
            elif miss:
                logger.info(
                    "[task][prepare] task=%s node=%s → miss(%s)",
                    task_id,
                    node.node_id,
                    miss,
                )
                side.append(
                    (
                        "miss",
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            extend_props_patch={"miss_events": miss},
                        ),
                    )
                )
            else:
                # 派发未产出执行者也非 MISS(dispatcher 已容错吞异常):标 dispatch_error 留 PENDING,harness 按超时重试搜推
                derr = node.run_info.extend_props.get("dispatch_error") or "no_result"
                logger.warning(
                    "[task][prepare] task=%s node=%s 派发未产出(%s)→留 PENDING 待 harness",
                    task_id,
                    node.node_id,
                    derr,
                )
                side.append(
                    (
                        "dispatch_fail",
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            extend_props_patch={"dispatch_error": derr},
                        ),
                    )
                )
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
        auto_nodes: list[TaskNode] = []
        for kind, *payload in side:
            if kind == "run":
                run_nodes.extend(payload[0])
            elif kind == "group":
                node, gf = payload
                logger.info(
                    "[task][drain] task=%s node=%s 拉群开始 collab=%s bot_ids=%s members=%s",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    list(getattr(gf, "bot_ids", []) or []),
                    list(getattr(gf, "members_info", []) or []),
                )
                # 协作群叶子:注入 loop_task_id 供 form_coop_group 写入群 context,
                # 供 driver/owner bot 验收后 push 回投 /callback/report 定位执行节点
                # (acceptance 段4;single_bot 走 poll,不经此拉群路径)。
                gf.extend_props.setdefault(
                    "loop_task_id", f"{node.task_id}::{node.node_id}"
                )
                try:
                    gid = await self._runner.form_coop_group(gf)
                    logger.info(
                        "[task][drain] task=%s node=%s 拉群成功 group_id=%s collab=%s",
                        task_id,
                        node.node_id,
                        gid,
                        gf.collab_mode,
                    )
                except Exception as ex:  # noqa: BLE001  拉群异常→清 dispatching 留 PENDING 交 harness
                    logger.exception(
                        "[task][drain] task=%s node=%s 拉群失败 exc_type=%s exc=%s collab=%s bot_ids=%s",
                        task_id,
                        node.node_id,
                        type(ex).__name__,
                        ex,
                        gf.collab_mode,
                        list(getattr(gf, "bot_ids", []) or []),
                    )
                    with self._lock_for(task_id):
                        self._graph.update_task_node_info(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                run_mode="",
                                assignee="",
                                extend_props_patch={
                                    "dispatching": None,
                                    "dispatch_error": "form_group_failed",
                                },
                            )
                        )
                    continue
                node.run_info.assignee = gid
                with self._lock_for(task_id):
                    self._graph.update_task_node_info(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            status=Status.RUNNING,
                            run_mode=node.run_info.run_mode,
                            assignee=gid,
                            extend_props_patch={"dispatching": None},
                        )
                    )
                # 动作历史:DISPATCH(HIT_MULTI 协作群)
                self._log_action(
                    task_id,
                    node.node_id,
                    NodeAction.DISPATCH,
                    {
                        "outcome": "HIT_MULTI",
                        "run_mode": "coop_group",
                        "assignee": gid,
                        "collab_mode": getattr(gf, "collab_mode", None),
                        "bot_ids": list(getattr(gf, "bot_ids", []) or []),
                    },
                    status_from=Status.PENDING,
                    status_to=Status.RUNNING,
                )
                run_nodes.append(node)
            elif kind == "auto":
                auto_nodes.extend(payload[0])
            elif kind == "miss":
                miss_tasks.append(payload[0])
            elif kind == "dispatch_fail":
                dispatch_fail_patches.append(payload[0])
            elif kind == "finish":
                logger.info("[task][drain] task=%s finish(根 gap 闭→图 DONE)", task_id)
                self._maybe_finish_graph(payload[0])
        # ① run:start_run 投递,成功后翻 RUNNING+清 dispatching;失败清执行者+清 dispatching+标 dispatch_error 留 PENDING
        if run_nodes:
            logger.info(
                "[task][drain] task=%s start_run %d 节点:%s",
                task_id,
                len(run_nodes),
                [n.node_id for n in run_nodes],
            )
            try:
                results = await self._runner.start_run(run_nodes)
            except Exception as ex:  # noqa: BLE001  start_run 异常→全部当失败,清 dispatching 留 PENDING 交 harness
                logger.warning(
                    "[task][drain] task=%s start_run 异常:%s→全部留 PENDING 待 harness",
                    task_id,
                    ex,
                )
                results = [False] * len(run_nodes)
            with self._lock_for(task_id):
                cur_map = {
                    x.node_id: x
                    for x in self._graph.query_task_nodes(
                        task_id,
                        TaskNodeQueryCriteria(node_ids=[n.node_id for n in run_nodes]),
                    )
                }
                for node, ok in zip(run_nodes, results):
                    if not ok:
                        logger.warning(
                            "[task][drain] task=%s node=%s start_run 失败→清执行者留 PENDING 待 harness",
                            task_id,
                            node.node_id,
                        )
                        # 清 run_mode/assignee(置空串)+清 dispatching 使其重新可搜推;标 dispatch_error
                        self._graph.update_task_node_info(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                run_mode="",
                                assignee="",
                                extend_props_patch={
                                    "dispatching": None,
                                    "dispatch_error": "start_run_failed",
                                },
                            )
                        )
                        continue
                    cur = cur_map.get(node.node_id)
                    if cur is not None and cur.status == Status.PENDING:
                        self._graph.update_task_node_info(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                status=Status.RUNNING,
                                extend_props_patch={"dispatching": None},
                            )
                        )
                        # 动作历史:DISPATCH(HIT_SINGLE 单 bot 派发执行)
                        self._log_action(
                            task_id,
                            node.node_id,
                            NodeAction.DISPATCH,
                            {
                                "outcome": "HIT_SINGLE",
                                "run_mode": cur.run_info.run_mode,
                                "assignee": cur.run_info.assignee,
                            },
                            status_from=Status.PENDING,
                            status_to=Status.RUNNING,
                        )
        # ④ auto(静态自驱 mock,OCB_TASK_STATIC_AUTO_REPORT):dispatching 守门防重派;后台 on_report
        #    自回投 PASS+mock 翻 DONE 推进图态,不占真实 bot/群。
        if auto_nodes:
            logger.info(
                "[task][drain] task=%s auto-mock scheduled %d nodes: %s",
                task_id, len(auto_nodes), [n.node_id for n in auto_nodes],
            )
            for n in auto_nodes:
                asyncio.create_task(self._static_auto_report(task_id, n.node_id))
        # ② dispatch_fail:落 dispatch_error(留 PENDING,harness 按超时重试搜推)
        for patch in dispatch_fail_patches:
            self._graph.update_task_node_info(patch)
        # ③ miss 推进(递归 collect+drain)
        for m in miss_tasks:
            await self.on_miss(m)

    def _maybe_finish_graph(self, task_id: str) -> None:
        """根 gap 闭(终验通过)→ 全图 DONE。图级写收口 + 根节点翻 DONE。两写均经 SSOT 网关(锁内同步)。"""
        self._graph.update_task_graph_info(
            task_id,
            TaskGraphPatch(status=Status.DONE, output_patch={"result": "all_done"}),
        )
        root = self._root(task_id)
        if root is not None and root.status != Status.DONE:
            _rprev = root.status
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=root.node_id, status=Status.DONE)
            )
            # 动作历史:TRANSITION(根 gap 闭终验通过 → root DONE)
            self._log_action(
                task_id,
                root.node_id,
                NodeAction.TRANSITION,
                {"reason": "root_gap_closed", "to": "DONE"},
                status_from=_rprev,
                status_to=Status.DONE,
            )
