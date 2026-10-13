"""TaskPlanner 规划编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan.md §3.2 + §3.4。构造期注入策略池(``pool=``),内置默认 [WorkflowPlanningStrategy, GapBasedPlanningStrategy];
``set_strategies`` 仅供测试覆写,不对外暴露自定义。
Step2 改造:plan 接**显式 target_node_id**(on_fail/on_miss→失败/miss 叶,on_pass→父,on_execute 传 None→自发现根)。
返 ``PlanResult(children, has_gap, gap_detail)`` 四象限驱动编排。
"""

from __future__ import annotations

from agentclaw.community.core.task.task_runner.centralized_support import (
    AcceptanceResult,
    AcceptanceVerdict,
    NodeAction,
    TaskGraphPatch,
    TaskNodePatch,
    effective_run_mode,
)


import logging
from typing import Any
from agentclaw.community.core.task.domain.models import (
    PlanResult,
    RelationType,
    Status,
    TaskExecutionGraph,
    TaskNode,
)
from agentclaw.community.core.task.task_plan.strategies import (
    GapBasedPlanningStrategy,
    PlanningStrategy,
    WorkflowPlanningStrategy,
)


logger = logging.getLogger("task.planner")


class TaskPlanner:
    """规划编排壳:解析 target → first-match-wins 选策略(graph 级 config 匹配)→ apply(graph,target) 产 PlanResult → 去重。

    分层:TaskPlanner(编排壳,框架固定,零 case 知识)↔ PlanningStrategy(引擎内置策略,
    Avernet 默认实现 gap/workflow)。策略池构造期注入(``pool=``);``set_strategies`` 仅供测试覆写。"""

    def __init__(self, graph, *, pool: list[PlanningStrategy] | None = None) -> None:
        """graph: TaskGraphService(派生查询用;策略 apply 收显式 target 经 graph 快照);
        pool: 策略池(构造期注入;省略=内置默认 [WorkflowPlanning, GapBased])。"""
        self._graph = graph
        self._strategies: list[PlanningStrategy] = (
            list(pool)
            if pool is not None
            else [
                WorkflowPlanningStrategy(),
                GapBasedPlanningStrategy(),
            ]
        )

    def set_strategies(self, strategies: list[PlanningStrategy]) -> None:
        """(测试覆写用)替换策略池。prod 经构造器 ``pool=`` 注入。"""
        self._strategies = list(strategies)

    async def plan(
        self, graph: TaskExecutionGraph, target_node_id: str | None = None
    ) -> PlanResult:
        """解析 target → first-match-wins 选策略 → await apply(graph,target) 产 PlanResult → 去重(children)。

        target 解析:
        - ``target_node_id`` 非空 → 取该节点(由调用方保证可规划:on_fail=FAILED 叶/on_miss=PENDING miss 叶/on_pass=RUNNING 父);
        - ``target_node_id``=None → 自发现根 PENDING(初始规划;on_execute 唯一 None 调用方)。
        零 case 知识:不依赖节点名。协程化:策略 apply 在 corp 是 LLM 耗时 IO,await 不阻塞(锁内 await,同 task 串行是设计意图)。
        """
        target = self._resolve_target(graph, target_node_id)
        logger.info(
            "[task][planning] plan 入口 target=%s strategies=%s",
            target.node_id if target else "None",
            [type(s_).__name__ for s_ in self._strategies],
        )
        if target is None:
            logger.info("[task][planning] plan 无可规划 target → no_target")
            return PlanResult(children=[], has_gap=False, gap_detail="no_target")
        profile = graph.extend_props.get("runtime_profile") or {}
        requested_strategy = str(profile.get("planner_strategy") or "default").strip()
        strategies = sorted(self._strategies, key=lambda r: r.priority)
        if requested_strategy != "default":
            strategies = [
                strategy
                for strategy in strategies
                if str(getattr(strategy, "rule_id", "")).strip() == requested_strategy
            ]
            if not strategies:
                raise ValueError(
                    f"unknown frozen planner_strategy={requested_strategy!r}"
                )
        for strategy in strategies:
            if await strategy.matches(graph):
                logger.info(
                    "[task][planning] plan 命中策略 %s → apply…",
                    type(strategy).__name__,
                )
                pr = await strategy.apply(graph, target)
                existing_ids = {n.node_id for n in graph.tasks}
                produced = len(pr.children)
                strategy_had_children = produced > 0
                pr.children = [n for n in pr.children if n.node_id not in existing_ids]
                # 仅「策略产了子但全被去重掉(=已存在)」才视 gap 闭(无新增 actionable);
                # 策略本身返空 + has_gap=True(有 gap 拆不出 / 无规划端口)→ 保留,编排核走深度闸门 HUNG(不假 done)。
                if not pr.children and strategy_had_children:
                    pr.has_gap = False
                # REQ-3 PLAN 轨迹溯源:planned_children = 去重后真正落图的子 node_id
                # 列表(post-dedup,post-`has_gap` flip);planner 此处统一回填(对
                # workflow / gap_based 两策略对称),engine 的 PLAN 轨迹事件据此填
                # ext_info.children。strategy_name/prompt_digest/raw_response_digest
                # 已由策略 apply 填好(additive;字段缺 → 默认 None,采集层防御读取)。
                pr.planned_children = [n.node_id for n in pr.children]
                logger.info(
                    "[task][planning] plan 策略=%s 产子=%d 去重后=%d has_gap=%s gap_detail=%s",
                    type(strategy).__name__,
                    produced,
                    len(pr.children),
                    pr.has_gap,
                    pr.gap_detail,
                )
                return pr
        logger.warning(
            "[task][planning] plan 无策略命中 → no_strategy_hit(不应发生,GapBased 兜底)"
        )
        return PlanResult(
            children=[], has_gap=False, gap_detail="no_strategy_hit"
        )  # 兜底(不应发生:GapBased 兜底)

    def _resolve_target(
        self, graph: TaskExecutionGraph, target_node_id: str | None
    ) -> TaskNode | None:
        """解析显式 target_node_id → 节点;None → 自发现可规划根(PENDING 初始 / PLANNING 已进入规划态,
        无父)。on_execute 唯一 None 调用方;_mark_planning 会将根先翻 PENDING→PLANNING 再 plan,
        故自发现须接受 PLANNING 根。零 case 知识。"""
        if target_node_id is not None:
            for n in graph.tasks:
                if n.node_id == target_node_id:
                    return n
            return None
        # None(on_execute):根(无结构父),处于可规划态 PENDING(尚未规划)/ PLANNING(已进入规划)
        for n in graph.tasks:
            if (
                n.status in {Status.PENDING, Status.PLANNING}
                and not self._has_child(graph, n.node_id)
                and self._get_parent_id(graph, n.node_id) is None
            ):
                return n
        return None

    def _has_child(self, graph: TaskExecutionGraph, node_id: str) -> bool:
        return any(
            r.src_id == node_id and r.type == RelationType.DEPENDENCY
            for r in graph.relations
        )

    def _get_parent_id(self, graph: TaskExecutionGraph, node_id: str) -> str | None:
        for r in graph.relations:
            if r.dst_id == node_id and r.type == RelationType.DEPENDENCY:
                return r.src_id
        return None

    async def _plan_with_retry(
        self, task_id: str, graph, target_node_id: str | None = None
    ):
        """plan 容错重试:planning 调用失败(parse/not_completed/empty 等,gap_detail 以 ``plan_`` 前缀)
        → 重试最多 MAX_HARNESS 次;耗尽后返回最后结果(has_gap=True → 编排核走深度闸门/HUNG)。
        非 ``plan_`` 前缀的空结果(gap 闭 has_gap=F / 真拆不出 has_gap=T)不经重试直接返回。
        planning 是 owner bot 的耗时工作,失败同 exec_error 应重试而非静默 DONE/立即 HUNG。

        REQ-3 PLAN 轨迹闸门:每次尝试(retry attempt)发射一条 ``action_type=plan``
        轨迹事件,与既有 post-loop ``_log_action(NodeAction.PLAN, ...)`` 旁路并存(
        additive,既有调用零改动)。``attempt`` 即 ``range(max_h)`` 重试序号(0-based);
        失败 mid-row(``plan_call_fail``/``plan_parse_fail`` 等 gap_detail 以 ``plan_`` 开
        头)带 ``error_type=PLAN_FAILURE`` + ``error_msg`` + ``ext_info={strategy_name,
        gap_detail, raw_response_digest}``;成功条带 ``ext_info={strategy_name, has_gap,
        gap_detail, children, raw_response_digest}``、``error_*=None``。``action_input``
        = ``prompt_digest``(workflow 策略 → None)。溯源在策略/gap-based 作用域内计算
        (见 ``strategies.py::GapBasedPlanningStrategy.apply``),engine 仅读取。零侵入:
        采集层 try/except + emitter swallow(决策 #14),失败不影响规划。"""
        max_h = self._max_harness(task_id)
        # 解析轨迹/动作日志锚定节点 ``target_id``(retry 全程不变 —— ``planner.plan``
        # 不增节点,loop 内图态不变,故提前一次性解析;既有 post-loop ``_log_action``
        # 仍用同一 ``target_id``,逻辑等价)。NOTE:此处**不走** 决策 #14 swallow ——
        # ``_root`` 是 engine 自身的图查询(非 fire-and-forget 轨迹发射),异常须按
        # 既有行为向上传播(pre-change 652beb210 即未 guard);legitimate None(真无
        # 根)由下游 ``if target_id is not None`` / ``_emit_plan_trajectory`` 兜住。
        target_id = target_node_id
        if target_id is None:
            root = self._root(task_id)
            target_id = root.node_id if root else None
        pr = None
        for attempt in range(max_h):
            failure_msg: str | None = None
            try:
                pr = await self._planner.plan(graph, target_node_id=target_node_id)
            except Exception as exc:  # 传输/HTTP 异常(sofa_tracer httpx send hook 等)->plan_call_fail 重试,不 abort on_execute
                logger.warning(
                    "[task][plan-retry] task=%s attempt=%d/%d plan() 抛异常(将重试): %r",
                    task_id,
                    attempt + 1,
                    max_h,
                    exc,
                )
                pr = PlanResult(children=[], has_gap=True, gap_detail="plan_call_fail")
                failure_msg = f"{type(exc).__name__}: {exc}"
            # REQ-3: 每次尝试发射一条 PLAN 轨迹事件(失败 mid-row + 成功条),
            # additive 旁路 —— 与 post-loop ``_log_action(NodeAction.PLAN, ...)``
            # 互不相干(emitter 独立 direct-INSERT,不动 task_action_log)。
            self._emit_plan_trajectory(task_id, target_id, pr, attempt, failure_msg)
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
        self._report_graph_patch(
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
        # ``target_id`` 由上方 retry 前(REQ-3)一次性解析;既有 ``_log_action`` 调用字节不变。
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
        if node is None or node.status not in {Status.PENDING, Status.HUNG}:
            return  # 已 PLANNING / 其他终态 → 幂等不翻
        self._report_node_patch(
            TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PLANNING)
        )

    def _rollup_done_children_output(
        self, task_id: str, parent_node_id: str
    ) -> dict | None:
        """结构父(非执行)gap 闭翻 DONE 时,把直接已 DONE 子交付物滚入父 ``run_info.output``,
        使祖父一跳 ``done_children`` 看到交付物(否则空 output → gap_no_progress 死循环)。

        存储 output 恒为 dict;单子透传子存储 output(保 ``{"output":<c>}`` 单键形,dashboard 仍展平);
        多子按 node_id 聚合(``{<node_id>: <child.output>}``)。"""
        children = self._graph.get_child_tasks(task_id, parent_node_id)
        done = [c for c in children if c.status == Status.SUCCESS and c.run_info.output]
        if not done:
            return None
        if len(done) == 1:
            return dict(done[0].run_info.output)
        return {c.node_id: dict(c.run_info.output) for c in done}

    def _build_parent_acceptance_result(
        self, parent: TaskNode, pr: PlanResult | None
    ) -> AcceptanceResult:
        """结构父/根 gap 闭(自身验收通过)翻 DONE 时的父自身验收结果(验收执行者=owner)。

        调用上下文已 ``not pr.has_gap`` → verdict 恒 DONE。``pr.acceptance_result``(owner bot plan
        自评,对齐 common_task 协议)非空 → 直接用(done_items 透传);空 → 回退合成逐条"验收通过"。"""
        if pr is not None and pr.acceptance_result is not None:
            ar = pr.acceptance_result
            return AcceptanceResult(
                verdict=AcceptanceVerdict.DONE,  # gap 闭语境恒 DONE(防御 owner 自评 FAILED)
                done_items=list(ar.done_items or []),
                gap_items=[],
            )
        ac_ids = [a.id for a in parent.task_spec.goal.acceptances]
        metrics = [{ac_id: "验收通过(子节点交付达成)"} for ac_id in ac_ids]
        if not metrics:
            metrics = [{"all": "验收通过"}]
        return AcceptanceResult(
            verdict=AcceptanceVerdict.DONE, done_items=metrics, gap_items=[]
        )

    async def plan_requested(self, task_id: str) -> list[str]:
        """Handle centralized PLAN_REQUESTED and return dispatchable node ids.

        This is the first extracted event handler from the former lifecycle
        entrypoint. It owns only planning and graph materialization; delivery is
        triggered by the subsequent DISPATCH_REQUESTED event.
        """
        if self._is_external_managed_task(task_id) or self._is_graph_terminal(task_id):
            return []
        if self._static_runtime(task_id) is not None:
            await self._on_static_execute(task_id)
            return []
        with self._lock_for(task_id):
            root = self._root(task_id)
            if root is None or root.status != Status.PENDING:
                return []
            graph = self._graph.query_task_dashboard(task_id)
            self._mark_planning(task_id, root.node_id)
            pr = await self._plan_with_retry(task_id, graph)
            if pr.children:
                self._report_add_nodes(pr.children, root.node_id)
                return [node.node_id for node in pr.children]
            if not pr.has_gap:
                self._maybe_finish_graph(task_id, pr)
            else:
                self._hung_and_escalate(task_id, root.node_id, "root_gap_no_decompose")
            return []

    async def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph 后,条件 a(根 PENDING)→ plan(None 自发现根)→add→dispatch→start_run。"""
        if self._is_external_managed_task(task_id):
            logger.info(
                "[task][on_execute] task=%s external-managed, skip Avernet orchestration",
                task_id,
            )
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
                self._report_add_nodes(pr.children, root.node_id)
                await self._prepare_into(task_id, side)
            elif not pr.has_gap:
                self._maybe_finish_graph(task_id, pr)  # 根 gap 初始即闭(罕见)
            else:
                self._hung_and_escalate(
                    task_id, root.node_id, "root_gap_no_decompose"
                )  # 有 gap 拆不出 → HUNG 升 BBS
        await self._drain(task_id, side)

    async def _on_pass_collect(
        self, task_id: str, node_id: str, side: list[tuple]
    ) -> None:
        """PASS→SUCCESS 后:查结构父 P。v4 父恒 PLANNING(委托态),无需翻态:
        兄弟仍有未终态(RUNNING/PLANNING/PENDING)→等待;兄弟全 SUCCESS(plan-ready)→ plan(target=parent):
          有子→节点级 plan_round++(达 MAX_PLAN_ROUND→父 HUNG)+add+dispatch;
          空+has_gap=F→gap 闭:非根传播 DONE 上行/根→图 DONE;空+has_gap=T→HUNG 升 BBS。
        兄弟全终态含 HUNG/FAILED→终态传播。
        v5:重规划产子由**节点级 plan_round** 闸(根+中间父统一计数);loop_round 收敛为只数升 BBS。"""
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            side.append(("finish", task_id))
            return
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        triggering = next(
            (n for n in siblings if n.node_id == node_id),
            None,
        )
        root = self._root(task_id)
        is_root_parent = parent.node_id == (root.node_id if root else None)
        is_bbs_recovery = (
            is_root_parent
            and triggering is not None
            and effective_run_mode(triggering) == "bbs"
        )
        logger.info(
            "[task][on_pass] task=%s node=%s 父=%s 父态=%s 兄弟=%s bbs_recovery=%s",
            task_id,
            node_id,
            parent.node_id,
            parent.status,
            [(s2.node_id, s2.status.value) for s2 in siblings],
            is_bbs_recovery,
        )
        # BBS scoped 节点是对根下 HUNG 占位节点的恢复交付。原 HUNG 节点
        # 仍保留用于审计，不能阻断本次 BBS 成功后的根重新规划。
        if not is_bbs_recovery:
            if any(
                st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING}
                for st in siblings
            ):
                logger.info("[task][on_pass] task=%s 兄弟未全终态,等待", task_id)
                return
            if not all(st.status == Status.SUCCESS for st in siblings):
                self._propagate_terminal(task_id, parent, siblings, side)
                return
        # BBS 可恢复态守卫:图已升 BBS(bbs_mode=true)且根未被 BBS 接力持有(bbs_owner=None)。
        # 走到此守卫前 step①②已保证兄弟全终态且全 DONE;"停手等 BBS 接力"此时是死锁(无在途接力,
        # root 非 HUNG 不重升 BBS → 无人收口)。故无论触发叶是否 bbs scoped,一律放行 owner 复核根 gap:
        # gap 闭→_maybe_finish_graph(根 mode① HUNG→DONE);未闭→重 plan / HUNG 重升 BBS。
        if is_root_parent:
            g_ext = self._graph.query_task_dashboard(task_id).extend_props
            if g_ext.get("bbs_mode") and not g_ext.get("bbs_owner"):
                if is_bbs_recovery:
                    logger.info(
                        "[task][on_pass] task=%s bbs scoped 节点 SUCCESS→放行 owner 复核根 gap 收口",
                        task_id,
                    )
                else:
                    # step①② 已保证兄弟全 SUCCESS 且 bbs_owner=None(无在途接力):停手会死锁
                    # (无在途接力,root 非 HUNG 不重升 BBS → 无人收口)。普通叶最后 DONE 亦放行 owner 复核根 gap。
                    logger.info(
                        "[task][on_pass] task=%s 图 bbs_mode 未 claim,普通叶最后 SUCCESS→放行 owner 复核根 gap(避免死锁)",
                        task_id,
                    )
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
            plan_round = int(parent.run_info.extend_props.get("plan_round", 0))
            max_plan_round = self._max_plan_round(task_id)
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
            # 先判后+1:plan_round 现值为已产次数,0→产首子并+1,1→产第二子并+1,…,MAX-1→产第 MAX 子并+1=MAX,
            # 下次 plan_round=MAX>=MAX 撞顶不产(故 MAX_PLAN_ROUND=N 允许 N 次重规划产子)。
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=parent.node_id,
                    extend_props_patch={"plan_round": plan_round + 1},
                )
            )
            logger.info(
                "[task][on_pass] task=%s 父=%s plan_round=%d/%d 重规划产 %d 子",
                task_id,
                parent.node_id,
                plan_round,
                max_plan_round,
                len(pr.children),
            )
            self._report_add_nodes(pr.children, parent.node_id)
            await self._prepare_into(task_id, side)
        elif not pr.has_gap:
            if is_root_parent:
                self._maybe_finish_graph(task_id, pr)
                return
            # 结构父(非执行态)gap 闭翻 DONE 时补全 run_info:验收执行者=owner 落 run_mode/assignee,
            # 父自身 acceptance_result(owner 逐条验收结论)补全,output 滚直接已 SUCCESS 子交付物
            # (否则结构父 output 恒空 → 祖父一跳 done_children 看不到 → gap_no_progress 死循环)。
            if not parent.run_info.run_mode:
                _done_out = self._rollup_done_children_output(task_id, parent.node_id)
                _parent_acc = self._build_parent_acceptance_result(parent, pr)
                _owner = graph.extend_props.get("owner_bot_id") or ""
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=parent.node_id,
                        acceptance_result=_parent_acc,
                        output_patch=_done_out,
                        run_mode="single_bot" if _owner else None,
                        assignee=_owner or None,
                    )
                )
            else:
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id, node_id=parent.node_id, status=Status.SUCCESS
                    )
                )
            # 动作历史:TRANSITION(非根 gap 闭传播 DONE)
            self._log_action(
                task_id,
                parent.node_id,
                NodeAction.TRANSITION,
                {"reason": "gap_closed_propagate", "to": "DONE"},
                status_from=Status.PLANNING,
                status_to=Status.SUCCESS,
            )
            # 轨迹旁路:TRANSITION(非根 gap 闭传播 → 父 SUCCESS)—— 与既有
            # ``_log_action(NodeAction.TRANSITION, ...)`` 同闸门位置、独立直插
            # ``task_trajectory_events``(additive,非替换)。action_result=status_to
            # 派生(SUCCESS→"success");action_input=null(REQ-1:transition 触发原因
            # 由 action_result/ext_info 承载);ext_info 携带 reason。决策 #14:仅发射
            # 被吞(emitter 内 try/except+WARNING),上面的状态翻转不在 swallow 内。
            self._log_trajectory(
                task_id,
                parent.node_id,
                "transition",  # TrajectoryActionType.TRANSITION.value
                action_result=self._transition_action_result(Status.SUCCESS),
                action_input=None,
                ext_info={"reason": "gap_closed_propagate"},
                status_from=Status.PLANNING,
                status_to=Status.SUCCESS,
                attempt=0,
            )
            await self._on_pass_collect(task_id, parent.node_id, side)
        else:
            self._hung_and_escalate(task_id, parent.node_id, "gap_no_progress")

    async def _on_fail_collect(
        self, task_id: str, node_id: str, side: list[tuple]
    ) -> None:
        """兼容旧调用入口。验收失败已在 ``on_report`` 中原子折叠为 HUNG 并升级 BBS；
        此处不重复改变节点状态或安排重试。"""
        logger.info(
            "[task][on_fail] task=%s node=%s acceptance_not_passed already handled by on_report",
            task_id,
            node_id,
        )

    def _propagate_terminal(
        self, task_id: str, parent: TaskNode, siblings: list, side: list[tuple]
    ) -> None:
        """on_pass 时兄弟全终态但非全 DONE(含 HUNG):子含 HUNG→父 HUNG 冒泡(经 _maybe_propagate_hung)。
        FAILED 子由 harness 巡检补救,此处若仅 FAILED(无 HUNG)不在此处理(等 harness 补救/转 HUNG)。"""
        if any(st.status == Status.HUNG for st in siblings):
            self._maybe_propagate_hung(
                task_id, siblings[0].node_id if siblings else parent.node_id
            )

    def _maybe_finish_graph(self, task_id: str, pr: PlanResult | None = None) -> None:
        """根 gap 闭(终验通过)→ root 翻 SUCCESS(并补全 run_info)→ graph 终态镜像 SUCCESS。

        终态镜像:root 先 SUCCESS,再 _sync_graph_status_to_root 镜像 graph(保证 graph.status≡root.status,
        不再 graph 独立先写 status)。验收执行者=owner 落 run_mode/assignee,根自身 acceptance_result
        (owner 逐条验收结论),output 滚直接已 SUCCESS 子交付物。两写均经 SSOT 网关(锁内同步)。"""
        root = self._root(task_id)
        if root is not None and root.status not in {Status.DONE, Status.SUCCESS}:
            _rprev = root.status
            graph = self._graph.query_task_dashboard(task_id)
            _owner = graph.extend_props.get("owner_bot_id") or ""
            _root_out = self._rollup_done_children_output(task_id, root.node_id)
            _root_acc = self._build_parent_acceptance_result(root, pr)
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=root.node_id,
                    acceptance_result=_root_acc,
                    output_patch=_root_out,
                    run_mode="single_bot" if _owner else None,
                    assignee=_owner or None,
                )
            )
            # 动作历史:TRANSITION(根 gap 闭终验通过 → root SUCCESS)
            self._log_action(
                task_id,
                root.node_id,
                NodeAction.TRANSITION,
                {"reason": "root_gap_closed", "to": "SUCCESS"},
                status_from=_rprev,
                status_to=Status.SUCCESS,
            )
            # 轨迹旁路:TRANSITION(根 gap 闭终验通过 → root SUCCESS)—— additive
            # 独立直插。action_result=status_to 派生(SUCCESS→"success");action_input
            # =null(REQ-1);ext_info 携带 reason。决策 #14:仅发射被吞(emitter 内
            # try/except+WARNING),上面的 root run_info 补全与下方 _sync_graph_status
            # _to_root 镜像均不在 swallow 内。
            self._log_trajectory(
                task_id,
                root.node_id,
                "transition",  # TrajectoryActionType.TRANSITION.value
                action_result=self._transition_action_result(Status.SUCCESS),
                action_input=None,
                ext_info={"reason": "root_gap_closed"},
                status_from=_rprev,
                status_to=Status.SUCCESS,
                attempt=0,
            )
        # 终态镜像:root 已 DONE → graph 镜像 DONE(all_done 标记);不再 graph 独立先写 status
        self._sync_graph_status_to_root(task_id)
        self._report_graph_patch(
            task_id, TaskGraphPatch(output_patch={"result": "all_done"})
        )

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

        # 分波揭示:首波仅入图 depends_on 为空的节点(root 结构父);

        # 后续波由 _on_static_report -> _static_next_wave 按依赖完成度增量补入,

        # 避免 dashboard 一开始就暴露完整定制计划。

        all_nodes = runtime.nodes(task_id, root.task_spec)

        wave0_ids = {d.node_id for d in runtime.definition.nodes if not d.depends_on}

        nodes = [n for n in all_nodes if n.node_id in wave0_ids]

        if not nodes:
            logger.info(
                "[task][static-plan] execute task=%s no wave0 nodes, skip materialize",
                task_id,
            )

            return

        self._report_add_nodes(nodes, root.node_id)

        logger.info(
            "[task][static-plan] materialized wave0 task=%s node_count=%s nodes=%s",
            task_id,
            len(nodes),
            [n.node_id for n in nodes],
        )

        side: list[tuple] = []

        await self._prepare_static(task_id, runtime, side)

        logger.info(
            "[task][static-plan] execute task=%s prepared side_effects=%s",
            task_id,
            [item[0] for item in side],
        )

        await self._drain(task_id, side)

    def _static_next_wave(self, task_id: str, runtime) -> int:
        """分波揭示:补加可入图的下一波(defs 全 DONE 且未在图中)。



        结构父统一用 root(全程 PLANNING,可委托,复用 add_task_nodes 触发 cond_c);

        实体 DAG 依赖边(deps -> node)由 task_graph_service.add_relations 补写,

        使多入合并点(如 strategy_approval 依赖 risk/marketing/crowd/product 四路)

        在 dashboard 上可渲染为 DAG。返回本次新入图节点数。"""

        graph = self._graph.query_task_dashboard(task_id)

        done = {n.node_id for n in graph.tasks if n.status == Status.SUCCESS}

        in_graph = {n.node_id for n in graph.tasks}

        wave_defs = [
            d
            for d in runtime.definition.nodes
            if d.node_id not in in_graph and set(d.depends_on).issubset(done)
        ]

        if not wave_defs:
            return 0

        root = self._root(task_id)

        if root is None:
            return 0

        wave_ids = {d.node_id for d in wave_defs}

        all_nodes = runtime.nodes(task_id, root.task_spec)

        # attach_dependency=False:后续波节点入图不挂 root 锚定边,真依赖边由下面 add_relations

        # 补 deps→X(crowd/product 依赖 marketing、approval 四路合并、impl 依赖 approval 等),

        # 避免 dashboard 把 root(okr-implementation) 误连到所有后续节点。

        wave_nodes = [n for n in all_nodes if n.node_id in wave_ids]

        self._report_add_nodes(wave_nodes, root.node_id, attach_dependency=False)

        edges: list[tuple[str, str]] = []

        for d in wave_defs:
            edges.extend((dep, d.node_id) for dep in d.depends_on)

        if edges:
            self._report_add_relations(task_id, edges)

        logger.info(
            "[task][static-plan] wave added task=%s count=%s nodes=%s edges=%s",
            task_id,
            len(wave_defs),
            [d.node_id for d in wave_defs],
            len(edges),
        )

        return len(wave_defs)

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

                    for part in expression[len("$.result.") :].split("."):
                        current = (
                            current.get(part) if isinstance(current, dict) else None
                        )

                    mapped[key] = current

            if mapped:
                self._report_node_patch(
                    TaskNodePatch(task_id=task_id, node_id=node_id, output_patch=mapped)
                )

        # 分波揭示:上报后先补加可入图的新波节点(结构父=root,DAG 依赖边经 add_relations 补),

        # 再 prepare,使新波节点在本轮即可被 readiness 选中派发。

        wave_added = self._static_next_wave(task_id, runtime)

        side: list[tuple] = []

        await self._prepare_static(task_id, runtime, side)

        current = self._graph.query_task_dashboard(task_id)

        # terminal 必须要求"全部定义节点均已入图" + 已入图节点全部终态,

        # 否则分波会导致未入图节点被遗漏而误判提前 DONE。

        all_def_ids = set(runtime.by_id)

        materialized_ids = {n.node_id for n in current.tasks} & all_def_ids

        all_in_graph = materialized_ids == all_def_ids

        terminal = all_in_graph and all(
            n.status in {Status.DONE, Status.SUCCESS, Status.FAILED, Status.HUNG}
            for n in current.tasks
            if n.node_id in all_def_ids
        )

        logger.info(
            "[task][static-plan] report processed task=%s node=%s wave_added=%s next_side_effects=%s terminal=%s materialized=%s/%s node_states=%s",
            task_id,
            node_id,
            wave_added,
            [item[0] for item in side],
            terminal,
            len(materialized_ids),
            len(all_def_ids),
            {
                n.node_id: n.status.value
                for n in current.tasks
                if n.node_id in all_def_ids
            },
        )

        if terminal:
            # 终态镜像:root 先翻 SUCCESS(下方 if 块),再 _sync_graph_status_to_root 镜像 graph(不再 graph 独立先写 status)

            root_node = next((n for n in current.tasks if n.node_id == task_id), None)

            if root_node is not None and root_node.status not in {
                Status.DONE,
                Status.SUCCESS,
                Status.FAILED,
                Status.HUNG,
            }:
                try:
                    self._report_node_patch(
                        TaskNodePatch(
                            task_id=task_id, node_id=task_id, status=Status.SUCCESS
                        )
                    )

                except Exception as ex:  # noqa: BLE101 翻态非法不阻塞 graph DONE
                    logger.warning(
                        "[task][static-plan] root flip-to-DONE skipped task=%s status=%s: %s",
                        task_id,
                        root_node.status.value,
                        ex,
                    )

            self._sync_graph_status_to_root(task_id)

            logger.info(
                "[task][static-plan] completed task=%s template=%s",
                task_id,
                runtime.definition.template_id,
            )

            return

        await self._drain(task_id, side)
