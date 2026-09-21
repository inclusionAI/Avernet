"""Centralized and Relay mode adapters.

Centralized behavior is implemented by the existing Planner, Dispatcher, Runner,
Graph, Harness, StaticPlanRuntime and TaskTrajectoryService classes.  This adapter
only composes ports and routes semantic events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.task.task_runner.centralized_support import (
    AcceptanceVerdict,
    BcnService,
    NodeAction,
    NodeOpResult,
    CoopGroupStart,
    GroupFormation,
    Status,
    TaskCallbackData,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    TaskStateError,
    _DEFAULT_MAX_HARNESS,
    asyncio,
    logger,
    threading,
)
from agentclaw.community.core.task.task_plan.planner import TaskPlanner
from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
from agentclaw.community.core.task.task_dispatch.search import TaskSearch
from agentclaw.community.core.task.task_runner.task_runner import TaskRunner
from agentclaw.community.core.task.task_harness.harness import TaskHarness
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
)
from agentclaw.community.core.task.task_runner.execution_events import TaskSemanticEvent

if TYPE_CHECKING:
    from agentclaw.community.core.task.task_context.task_context_service import (
        TaskContextServiceProtocol,
    )


class CentralizedExecutionAdapter:
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
        notify_messages_provider=None,
        bot_bindings=None,
        task_context_service: "TaskContextServiceProtocol | None" = None,
    ) -> None:
        """graph: TaskGraphService;bot: OpenApiBotPort;bcs: BcsClientPort;discover: BotDiscoverServiceProtocol。
        端口由 DI 从配置注入(local/prod/double 只换端口实现,引擎代码不变)。prod 必传;测试子类覆写
        ``_build_*`` 注入 stub 策略/投递时可省略(走 super 路径默认 berth)。

        BBS 任务模式候选通过 ``bcn.list_bots_by_task_modes``(注入的 BcnService,复用统一 provider 身份)查询。

        ``api_base_url``:任务后端 base url,经 _build_executor 透传给 TaskExecutor→bbs_runner.notify,
        拼成发给胜出 bot 的任务消息(spec §5:主动触发回投路径)。

        ``task_context_service``(可选):任务轨迹旁路采集的外部入口(REQ-11;spec 2026-09-18 重构)。
        引擎不再直接持 trajectory repo,只持 ``TaskContextServiceProtocol``;经 ``emit_trajectory_event``
        中转到内部 ``TaskTrajectoryService`` 落库。DI 在 prod 注入真实实现;测试/轻量 DI 取不到 → ``None``
        → ``_log_trajectory`` 静默 no-op,与 ``task_action_log`` 完全解耦(本 spec 不读不写 action log)。
        ``None`` 时引擎仍可正常运行,轨迹事件不落库但正向驱动不受影响。"""
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
        self._notify_provider = notify_messages_provider
        self._bot_bindings = bot_bindings
        # 轨迹旁路采集外部入口(可选):task_context_service;None 时 _log_trajectory 静默 no-op(emitter
        # 经内部 TaskTrajectoryService 独立 direct-INSERT,不走 task_action_log/append_action_event 链路)。
        self._task_context_service = task_context_service
        self._bg_tasks: set[object] = set()
        self._bbs_loop: asyncio.AbstractEventLoop | None = None
        self._bbs_loop_thread: threading.Thread | None = None
        self._bbs_loop_ready = threading.Event()
        self._bbs_loop_guard = threading.RLock()
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

    def _report_node_patch(self, patch: TaskNodePatch):
        return self._graph.report(
            TaskCallbackData(
                data={
                    "report_type": "NODE_PATCH",
                    "payload": {"patch": patch},
                }
            )
        )

    def _report_graph_patch(self, task_id: str, patch: TaskGraphPatch):
        return self._graph.report(
            TaskCallbackData(
                data={
                    "report_type": "GRAPH_PATCH",
                    "payload": {"task_id": task_id, "patch": patch},
                }
            )
        )

    def _report_add_nodes(
        self,
        nodes,
        parent_node_id,
        *,
        attach_dependency=True,
        mark_parent_planning=True,
    ):
        task_id = nodes[0].task_id if nodes else ""
        return self._graph.report(
            TaskCallbackData(
                data={
                    "report_type": "ADD_NODES",
                    "payload": {
                        "task_id": task_id,
                        "nodes": nodes,
                        "parent_node_id": parent_node_id,
                        "attach_dependency": attach_dependency,
                        "mark_parent_planning": mark_parent_planning,
                    },
                }
            )
        )

    def _report_add_relations(self, task_id: str, edges):
        return self._graph.report(
            TaskCallbackData(
                data={
                    "report_type": "ADD_RELATIONS",
                    "payload": {"task_id": task_id, "edges": edges},
                }
            )
        )

    @property
    def runner(self):
        """Shared delivery runtime exposed by the task_runner adapter."""
        return self._runner

    @property
    def discover(self):
        return self._discover

    async def start_coop_group(self, gf: GroupFormation) -> CoopGroupStart:
        """Create the BCN coop group and fetch its initial session_id by default."""
        group_id = await self._runner.form_coop_group(gf)
        session_id = await self._runner.get_group_session(group_id)
        return CoopGroupStart(group_id=group_id, session_id=session_id)

    def _build_executor(self):
        if self._bot is None or self._bcs is None:
            logger.warning(
                "[task][engine] execution_backend 不装配(bot=%s bcs=%s)→ form_coop_group/start_run/"
                "BBS start_run 全退 Avernet 桩(grp_<8hex>/stub_<8hex>/无 poller,任务卡 RUNNING 不收敛)。"
                "corp 排查: 确认 DEPLOY_PROFILE=corp + grep [task][corp-task] not configured 看哪个端口空。",
                "None" if self._bot is None else type(self._bot).__name__,
                "None" if self._bcs is None else type(self._bcs).__name__,
            )
            return None
        from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
            TaskExecutor,
        )
        from agentclaw.community.core.task.task_runner.modal_executor.task_executor_result_poller import (
            TaskExecutorResultPoller,
        )
        from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
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
            task_settings=self._task_settings,
            on_bbs_report=self.on_bbs_report,
            task_context_service=self._task_context_service,
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
                    use_search_skill=self._task_search_skill_enabled,
                    task_settings=self._task_settings,
                )
            )
        else:
            pool.append(SearchBasedDispatchStrategy())
        return TaskDispatcher(self._graph, pool=pool)

    def _build_runner(self):
        from agentclaw.community.core.task.task_runner.task_runner import TaskRunner

        return TaskRunner(self._graph, execution_backend=self._executor)

    async def report_result(self, data: "TaskCallbackData") -> None:
        """引擎自当 ResultSink:TaskExecutorResultPoller 终态→TaskCallbackData→TaskNodePatch→on_report。
        与外部 HTTP push 回投(TaskLoopCallback.report_result→on_report)收敛同一入口。"""
        patch = self._cb_adapter.adapt(data)
        await self.on_report(patch)

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
            if s.status == Status.SUCCESS and s.node_id != node_id
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
        """节点级重规划次数上限 MAX_PLAN_ROUND(default 3)。父节点子全 SUCCESS→gap 未闭→重 plan 产新子,
        每次该路径走一次 +1;达上限父节点 HUNG(不再产子)"""
        cfg = self._graph._execution_config(task_id)
        return int(cfg.get("MAX_PLAN_ROUND", 3))

    def _task_type(self, task_id: str) -> str:
        """Return the immutable execution type recorded on the task graph."""
        try:
            raw = self._graph._execution_config(task_id).get("task_type", "dynamic")
        except Exception:  # noqa: BLE001 - missing/legacy graph defaults to dynamic
            return "dynamic"
        return str(getattr(raw, "value", raw) or "dynamic").strip().lower()

    def _is_external_managed_task(self, task_id: str) -> bool:
        """Whether a third party owns execution and next-node transitions."""
        return (
            self._task_type(task_id) in {"workflow", "yaml"}
            or self._graph._execution_config(task_id).get("orchestration_mode")
            == "relay"
        )

    def _is_graph_terminal(self, task_id: str) -> bool:
        """图级终态(DONE/SUCCESS/HUNG)判定。终态后自动驱动(plan/dispatch/harness/回投推进)一律冻结:
        MAX_LOOP 达上限→图 HUNG 后,后续 on_pass/on_miss/on_harness 不再推进(避免 loop_round 失控飙升
        与节点无限增生);on_bbs_report(BBS 接力恢复)是唯一可从 HUNG 恢复的路径,不在本守卫范围。"""
        try:
            return self._graph.query_task_dashboard(task_id).status in {
                Status.DONE,
                Status.SUCCESS,
                Status.HUNG,
            }
        except Exception:  # noqa: BLE001  图不存在等→视为非终态,让正常入口逻辑处理
            return False

    _METHOD_OWNERS = {
        "_plan_with_retry": TaskPlanner,
        "_mark_planning": TaskPlanner,
        "_rollup_done_children_output": TaskPlanner,
        "_build_parent_acceptance_result": TaskPlanner,
        "plan_requested": TaskPlanner,
        "on_execute": TaskPlanner,
        "_on_pass_collect": TaskPlanner,
        "_on_fail_collect": TaskPlanner,
        "_propagate_terminal": TaskPlanner,
        "_maybe_finish_graph": TaskPlanner,
        "dispatch_requested": TaskDispatcher,
        "_prepare_into": TaskDispatcher,
        "_drain": TaskRunner,
        "redrive": TaskHarness,
        "_reset_action_result": TaskHarness,
        "_reset_sla_threshold_ms": TaskHarness,
        "_reset_elapsed_ms": TaskHarness,
        "_emit_reset_trajectory": TaskHarness,
        "_on_harness_collect": TaskHarness,
        "on_miss": TaskHarness,
        "on_harness": TaskHarness,
        "_sync_graph_status_to_root": TaskHarness,
        "_bump_loop_round": TaskHarness,
        "_escalate_hung": TaskHarness,
        "_hung_and_escalate": TaskHarness,
        "_on_bg_done": TaskHarness,
        "_ensure_bbs_loop": TaskHarness,
        "_on_auto_report_done": TaskHarness,
        "_reset_root_plan_round": TaskHarness,
        "_schedule_bbs_notify": TaskHarness,
        "_enter_root_bbs": TaskHarness,
        "_maybe_propagate_hung": TaskHarness,
        "_reconcile_root_hung_if_blocked": TaskHarness,
        "_emit_plan_trajectory": TaskTrajectoryService,
        "_log_action": TaskTrajectoryService,
        "_log_trajectory": TaskTrajectoryService,
        "_transition_action_result": TaskTrajectoryService,
        "_read_exec_error_origin": TaskTrajectoryService,
        "_read_interface_error_code": TaskTrajectoryService,
        "_read_exec_request_input_and_attempt": TaskTrajectoryService,
        "_emit_execute_trajectory": TaskTrajectoryService,
        "_static_runtime": TaskTrajectoryService,
        "_on_static_execute": TaskPlanner,
        "_static_next_wave": TaskPlanner,
        "_static_auto_report_on": TaskRunner,
        "_prepare_static": TaskDispatcher,
        "_prepare_static_into": TaskDispatcher,
        "_static_auto_report": TaskRunner,
        "_static_bbs_handoff_auto_report": TaskRunner,
        "_static_fallback_delay": TaskHarness,
        "_static_mock_fallback_delay": TaskHarness,
        "_static_auto_report_delay": TaskHarness,
        "_bbs_handoff_delay": TaskHarness,
        "_on_bbs_handoff_done": TaskHarness,
        "_bbs_handoff_claim": TaskHarness,
        "_on_static_report": TaskPlanner,
    }

    def __getattr__(self, name: str):
        owner = self._METHOD_OWNERS.get(name)
        if owner is None:
            raise AttributeError(name)
        descriptor = owner.__dict__.get(name)
        if isinstance(descriptor, staticmethod):
            return descriptor.__func__
        if isinstance(descriptor, classmethod):
            return descriptor.__get__(type(self), type(self))
        return getattr(owner, name).__get__(self, type(self))

    async def on_start(self, patch: TaskNodePatch) -> NodeOpResult:
        """Route executor start facts to the unified TaskGraphService."""
        return await self._graph.on_start(patch)

    async def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """回投事件:patch 内含 (task_id,node_id)+终态翻转依据。
        三路分流(互斥):
        ① ``exec_error`` 非空 → 执行报错(bot 没跑通)→ on_harness 复位重投(计数,达上限 HUNG);
        ② ``acceptance_result`` PASS → on_pass(DONE 传播/前向 plan);
        ③ ``acceptance_result`` FAIL+gaps → on_fail(补救重规划,深度闸门);
        无两者 → 仅 fold,返回。验收 100% 来自回投,engine 不主动验。"""
        logger.info(
            "[task_callback][on_report] task=%s node=%s exec_error=%s verdict=%s",
            patch.task_id,
            patch.node_id,
            patch.exec_error,
            patch.acceptance_result.verdict if patch.acceptance_result else "fold-only",
        )
        with self._lock_for(patch.task_id):
            logger.info(
                "[task_callback][on_report] begin update task node info, task=%s,",
                patch.task_id,
            )
            # 固定 plan 的"执行报错(exec_error)"不进 harness 重投/HUNG:V2 relay 下发给 bot 的是纯
            # 交接正文(无 {success,data,gaps} poller 协议),bot 常回自然语言 → poller 误判 exec_error
            # (terminal_result_invalid)。若仍 reset+重派×MAX_HARNESS→HUNG,会在 80s 兜底
            # (_static_auto_report)之前把节点挂死,整固定流程读不往下走(等同 on_harness 入口守卫
            # 39636835f 的意图,补在 poller→on_report 这条未守的路径上)。此处对固定 plan 的
            # exec_error:不翻 FAILED、不重派,保持节点 RUNNING,仅记 last_exec_error 留痕,让
            # 80s 兜底推进 DONE;真实 {success} 回投先到则走下方 acceptance DONE 自跳过。
            if (
                self._static_runtime(patch.task_id) is not None
                and patch.exec_error is not None
            ):
                logger.info(
                    "[task_callback][on_report] task=%s node=%s 固定 plan exec_error=%s 忽略翻态/重派,交给 static fallback 兜底",
                    patch.task_id,
                    patch.node_id,
                    patch.exec_error,
                )
                patch.status = None  # 保持 RUNNING,不落 FAILED
                patch.acceptance_result = None
                _ep = dict(patch.extend_props_patch) if patch.extend_props_patch else {}
                _ep.setdefault("last_exec_error", patch.exec_error)
                patch.extend_props_patch = _ep
                result = self._report_node_patch(patch)
                self._reconcile_root_hung_if_blocked(patch.task_id)
                return result
            # 先落验收结果，再处理状态。验收失败不作为普通 DONE 参与父节点成功
            # 聚合:保留 acceptance_result/gaps 作为诊断上下文,随后升级当前节点 HUNG,
            # 由 _maybe_propagate_hung 冒泡到根并进入 BBS。
            acceptance_failed = (
                patch.acceptance_result is not None
                and patch.acceptance_result.verdict == AcceptanceVerdict.FAILED
                and not self._is_external_managed_task(patch.task_id)
            )
            if acceptance_failed:
                # 在同一次 SSOT 写入中选择 HUNG,避免先落 DONE 再二次翻态。
                # acceptance_result/gaps 仍会保留在 run_info 中供 BBS 复核。
                patch.status = Status.HUNG
                patch.extend_props_patch = {
                    **(patch.extend_props_patch or {}),
                    "hung_reason": "acceptance_failed",
                }
            result = self._report_node_patch(patch)
            if self._static_runtime(patch.task_id) is not None:
                # Static plans use the same harness contract as dynamic tasks.
                if patch.exec_error is not None:
                    # Dead in practice — the line ~2050 guard already returned for
                    # static-plan + exec_error (保留 RUNNING 不重派/不 HUNG,让 80s
                    # 兜底 ``_static_auto_report`` 推进)。保留分支以备该守卫被收回;其
                    # 轨迹发射属于 2050 守卫的职责范围,这里不补(避免与守卫路径重复)。
                    side: list[tuple] = []
                    await self._on_harness_collect(
                        patch.task_id, patch.node_id, patch.exec_error, side
                    )
                    await self._drain(patch.task_id, side)
                elif patch.acceptance_result is not None:
                    # I-2: 固定 plan 上报(真实 bot 回投 / ``_static_auto_report``
                    # 80s 兜底 mock 同走此分支)与动态 plan 同一收口 — 在 early
                    # return 之前补齐 EXECUTE/VERIFY 轨迹行(REQ-5),与下方动态分支
                    # (L2096-2147)同构。决策 #14 emission-only:emitter 内部
                    # ``try/except`` 吞而不抛 + WARNING,主流程不受影响。
                    self._emit_execute_trajectory(
                        patch,
                        result,
                        action_type="execute",
                        action_result="success",
                        is_exec_error=False,
                    )
                    self._emit_execute_trajectory(
                        patch,
                        result,
                        action_type="verify",
                        action_result=(
                            "accept_pass"
                            if patch.acceptance_result.verdict == AcceptanceVerdict.DONE
                            else "accept_fail"
                        ),
                        is_exec_error=False,
                    )
                    await self._on_static_report(patch.task_id, patch.node_id)
                self._reconcile_root_hung_if_blocked(patch.task_id)
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
                # 轨迹旁路:EXECUTE(err) —— error_type 按 _exec_error_origin 分类映射(REQ-5)
                self._emit_execute_trajectory(
                    patch,
                    result,
                    action_type="execute",
                    action_result="failed",
                    is_exec_error=True,
                )
            elif patch.acceptance_result is not None:
                _ar = patch.acceptance_result
                self._log_action(
                    patch.task_id,
                    patch.node_id,
                    NodeAction.EXECUTE,
                    {"success": _ar.verdict == AcceptanceVerdict.DONE, "output": _out},
                    status_from=result.prev_status,
                    status_to=result.new_status,
                )
                # 轨迹旁路:EXECUTE(ok) —— 执行产出成功(无 error_origin;验收结论由 VERIFY 承载)
                self._emit_execute_trajectory(
                    patch,
                    result,
                    action_type="execute",
                    action_result="success",
                    is_exec_error=False,
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
                # 轨迹旁路:VERIFY —— 验收结论(error_origin 不适用;acceptance 另属
                # ACCEPTANCE_FAILED,取 action_result=accept_pass/accept_fail)
                self._emit_execute_trajectory(
                    patch,
                    result,
                    action_type="verify",
                    action_result=(
                        "accept_pass"
                        if _ar.verdict == AcceptanceVerdict.DONE
                        else "accept_fail"
                    ),
                    is_exec_error=False,
                )
            if self._is_external_managed_task(patch.task_id):
                # Third-party execution owns transitions. Graph status mirrors
                # root via the single terminal-sync point(不再独立写 status)。
                self._sync_graph_status_to_root(patch.task_id)
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
                self._reconcile_root_hung_if_blocked(patch.task_id)
                return result
            if patch.acceptance_result is None:
                self._reconcile_root_hung_if_blocked(patch.task_id)
                return result  # 仅 fold,无翻态
            if self._is_graph_terminal(patch.task_id):
                logger.info(
                    "[task][on_report] task=%s 图已终态,fold 已落但冻结驱动",
                    patch.task_id,
                )
                return result
            side = []
            verdict = patch.acceptance_result.verdict
            if verdict == AcceptanceVerdict.DONE:
                logger.info(
                    "[task_callback][on_report] accept_pass,task=%s", patch.task_id
                )
                await self._on_pass_collect(patch.task_id, patch.node_id, side)
            else:  # 验收未通过:节点已升级 HUNG,冒泡到根并进入 BBS
                logger.info(
                    "[task_callback][on_report] acceptance_not_passed -> HUNG/BBS,task=%s",
                    patch.task_id,
                )
                self._escalate_hung(patch.task_id, patch.node_id, "acceptance_failed")
            await self._drain(patch.task_id, side)
            self._reconcile_root_hung_if_blocked(patch.task_id)
            return result

    async def on_bbs_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """BBS 接力步⑤回投:翻 scoped 节点终态 + 释放 claim,**收口交给 engine 既有路径(非 bot 声明)**。

        不再有 ``root_verified``:根目标是否满足由框架经 owner 复核(``_on_pass_collect``→``plan(root)``→
        ``has_gap=False``→``_maybe_finish_graph``)判定,**不由接力 bot 自报**。BBS 回投表示本次接力执行与验收已完成,统一将 scoped 节点置为 SUCCESS。BBS 回投
        不删除节点、不根据回投内容判定 FAILED。
        最后清根 ``bbs_owner`` 释放 claim。

        持有者校验:``root.run_info.extend_props['bbs_owner']`` 须 == ``patch.assignee``(调用方
        ``report_bbs_result`` 设 ``patch.assignee=bot_id``);非持有者 → ``TaskStateError``(在校验抛,
        不清 claim)。

        释放安全:scoped 终态翻转(fold)收在 ``try`` 内,``finally`` 无条件清根 ``bbs_owner`` —— 翻态抛错也
        释放 claim,避免持卡者死锁(他 bot claim 被 CAS 拒)。owner 校验在 ``try`` 之前,非持有者抛错不清他卡。

        无 owner bot 时(单测)``plan(root)`` 返 ``has_gap=True``(no_planning_port)→ ``gap_no_progress`` → 父
        HUNG;故收口需 owner planner(live 有),单测只验 mechanics(scoped SUCCESS + claim 释放)。"""
        if self._is_external_managed_task(patch.task_id):
            logger.info(
                "[task][on_bbs_report] task=%s external-managed, graph update only",
                patch.task_id,
            )
            return self._report_node_patch(patch)
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
            # BBS 回投成功后将 scoped 节点置为 SUCCESS,使根节点进入正常
            # owner 复核/重新规划路径;不删除节点。
            completion_patch = TaskNodePatch(
                task_id=patch.task_id,
                node_id=patch.node_id,
                # A completed BBS relay is the successful execution/acceptance
                # handoff that unlocks the normal parent/root planning path.
                status=Status.SUCCESS,
                assignee=patch.assignee,
                output_patch=patch.output_patch,
                extend_props_patch=patch.extend_props_patch,
            )
            try:
                result = self._report_node_patch(completion_patch)
            finally:
                # 无论 scoped 节点翻态是否抛错,都清根 bbs_owner 释放 claim。
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=patch.task_id,
                        node_id=patch.task_id,
                        extend_props_patch={"bbs_owner": None},
                    )
                )
            # BBS scoped SUCCESS 进入统一通过收敛,由 owner 复核根 gap 并继续规划。
            # HUNG 是 BBS 可恢复态:attach 阶段保持根 HUNG,必须允许本次 SUCCESS
            # 回投进入 _on_pass_collect,再由其将根置为 PLANNING。
            if self._graph.query_task_dashboard(patch.task_id).status in {
                Status.DONE,
                Status.SUCCESS,
            }:
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
                if node is not None and node.status == Status.SUCCESS:
                    await self._on_pass_collect(patch.task_id, patch.node_id, side)
                elif node is not None and node.status == Status.FAILED:
                    await self._on_fail_collect(patch.task_id, patch.node_id, side)
        await self._drain(patch.task_id, side)
        return result

    async def start(self, task_id: str) -> Any:
        return await self.on_execute(task_id)

    async def handle_plan_requested(self, event: TaskSemanticEvent) -> Any:
        node_ids = await self.plan_requested(event.task_id)
        if node_ids:
            self._graph.emit_semantic_event(
                event.task_id,
                "DISPATCH_REQUESTED",
                payload={"node_ids": list(node_ids)},
            )
        return node_ids

    async def handle_dispatch_requested(self, event: TaskSemanticEvent) -> Any:
        side: list[tuple] = []
        with self._lock_for(event.task_id):
            await self._prepare_into(event.task_id, side)
        await self._drain(event.task_id, side)


class RelayExecutionAdapter:
    """Relay delivery/search boundary over stable TaskRunner APIs."""

    def __init__(self, *, runner: Any, discover: Any, user_id: str = "") -> None:
        self._runner = runner
        self._search = TaskSearch(discover, user_id=user_id)

    @property
    def runner(self) -> Any:
        return self._runner

    @property
    def search(self) -> TaskSearch:
        return self._search
