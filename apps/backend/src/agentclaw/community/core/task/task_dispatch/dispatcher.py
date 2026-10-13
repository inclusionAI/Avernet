"""TaskDispatcher 派发编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan.md §3.3 + §3.4。构造期注入策略池(``pool=``),内置默认
[DirectDispatchStrategy, SearchBasedDispatchStrategy];``set_strategies`` 仅供测试覆写。
不持 runner(HIT_MULTI_BOTS 拉群归编排核+runner);不写图、不起 run。
"""

from __future__ import annotations

from agentclaw.community.core.task.task_runner.centralized_support import (
    Any,
    Status,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    _UHT_MOCK,
    _now_ms,
    task_spec_instruction,
)


import dataclasses
import logging

from agentclaw.community.core.task.domain.models import (
    TaskExecutionGraph,
    TaskNode,
    effective_run_mode,
)
from agentclaw.community.core.task.task_dispatch.strategies import (
    DirectDispatchStrategy,
    GroupFormation,
    DispatchStrategy,
    SearchBasedDispatchStrategy,
    SearchOutcome,
)

logger = logging.getLogger("task.dispatcher")


def _is_exec_retry_replay(node: TaskNode) -> bool:
    """exec_error/SLA-timeout 重试且节点已有有效执行者 → 原样重跑,跳过搜推。

    命中条件:``harness_retries>0``(harness 三路巡检 SLA 超时/exec_error/FAILED 触发的重试计数)
    且有效执行模态 ∈ {single_bot,coop_group} 且 ``assignee`` 非空(曾真实派发执行,非 MISS/stale PENDING)。
    命中后 dispatcher 不重搜推、不覆写 run_mode/assignee,交编排核 ``_prepare_into._handle_node`` 的
    "run_mode+assignee" 分支走 ``start_run`` 原样重投(single_bot 重发同一 bot;coop_group 向既有
    group_id 重投,即首派 form_coop_group 之后的同条 start_run 路径)。避免重试被搜推
    翻转模态或换执行者;harness_retries 达 MAX_HARNESS→HUNG→升 BBS 的兜底不变。
    首次派发(harness_retries=0)/MISS 无 assignee/PENDING-stale 无 assignee/bbs → 不命中,走正常搜推/退化。
    """
    if int(node.run_info.extend_props.get("harness_retries", 0) or 0) <= 0:
        return False
    mode = effective_run_mode(node)
    if mode not in ("single_bot", "coop_group"):
        return False
    return bool(node.run_info.assignee)


class TaskDispatcher:
    """派发编排壳:对每节点 first-match-wins 选策略(graph 级 config 匹配)→ apply 填 run_info 后返回。

    不写图、不起 run(编排核落库+起 run)。BBS 节点的有效执行模态为 "bbs" 时退化为直接维持(不走策略)。
    HIT_MULTI_BOTS 时填 run_mode="coop_group"+extend_props["pending_group_formation"],assignee 留空
    (拉群归编排核调 runner.form_coop_group 后填 assignee)。
    """

    def __init__(self, graph, *, pool: list[DispatchStrategy] | None = None) -> None:
        """graph: TaskGraphService(读图级 execution_config 匹配策略用,不写);
        pool: 策略池(构造期注入;省略=内置默认 [DirectDispatch, SearchBased])。"""
        self._graph = graph
        self._strategies: list[DispatchStrategy] = (
            list(pool)
            if pool is not None
            else [
                DirectDispatchStrategy(),
                SearchBasedDispatchStrategy(),
            ]
        )

    def set_strategies(self, strategies: list[DispatchStrategy]) -> None:
        """(测试覆写用)替换策略池。prod 经构造器 ``pool=`` 注入。"""
        self._strategies = list(strategies)

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        """入参=待派发节点;返回=填充执行者信息后的 list[TaskNode](对齐派发文档签名)。
        不写图、不起 run;per node first-match 策略 await apply SearchResult → 填 node.run_info:
        HIT_SINGLE→single_bot/bot_id;HIT_GROUP→coop_group/group_id;
        HIT_MULTI_BOTS→coop_group/pending_group_formation(assignee 留空,编排核拉群填);MISS→不填+标 miss_events。
        有效执行模态为 "bbs" 的节点→ 退化直接维持。协程化:catalog 搜推是耗时 IO,await 不阻塞编排核。"""
        graph = (
            self._graph.query_task_dashboard(
                toDoTaskList[0].task_id if toDoTaskList else ""
            )
            if toDoTaskList
            else None
        )
        import asyncio as _aio

        logger.info(
            "[task][dispatch] dispatch 入口 nodes=%s", [n.node_id for n in toDoTaskList]
        )

        # v4:并发搜推(gather,无并发限流;catalog IO 耗时,串行是瓶颈)。BBS 节点跳过策略直接维持。
        async def _one(node: "TaskNode"):
            # 清上一轮残留的失败 carrier,防重投成功后把旧降级备注粘到本轮 hit/miss 事件
            node.run_info.extend_props.pop("_dispatch_failure", None)
            # 容错:搜推异常(无响应/推理失败/端口错)不崩整批,留 PENDING 标 dispatch_error 交 harness 重试搜推
            try:
                if effective_run_mode(node) == "bbs":
                    logger.info(
                        "[task][dispatch] node=%s run_mode=bbs 退化维持", node.node_id
                    )
                    return node  # BBS 节点退化维持
                if _is_exec_retry_replay(node):
                    logger.info(
                        "[task][dispatch] node=%s exec-retry 原样重跑(跳过搜推)mode=%s assignee=%s "
                        "harness_retries=%s",
                        node.node_id,
                        node.run_info.run_mode,
                        node.run_info.assignee,
                        node.run_info.extend_props.get("harness_retries", 0),
                    )
                    return node  # exec_error/超时重试:保留原模式+原执行者,不重搜推
                result = await self._select_and_apply(node, graph)
                if result.outcome == SearchOutcome.HIT_SINGLE:
                    node.run_info.run_mode = "single_bot"
                    node.run_info.assignee = result.bot_id
                    if result.bot_name is not None:
                        node.run_info.extend_props["assignee_name"] = result.bot_name
                    if result.owner_id is not None:
                        node.run_info.extend_props["assignee_owner_id"] = (
                            result.owner_id
                        )
                    if result.owner_name is not None:
                        node.run_info.extend_props["assignee_owner_name"] = (
                            result.owner_name
                        )
                elif result.outcome == SearchOutcome.HIT_GROUP:
                    node.run_info.run_mode = "coop_group"
                    node.run_info.assignee = result.group_id
                elif result.outcome == SearchOutcome.HIT_MULTI_BOTS:
                    node.run_info.run_mode = "coop_group"
                    node.run_info.extend_props["pending_group_formation"] = (
                        result.group_formation
                    )
                else:  # MISS
                    node.run_info.extend_props["miss_events"] = [
                        result.miss_reason or "no_bot"
                    ]
                # REQ-2 降级透传:rationale 装配抛错时 ``_build_search_rationale`` 已把原因回填到
                # ``result.assembly_error``;写入节点 ``_dispatch_failure`` carrier,引擎 hit/miss
                # DISPATCH 闸门据此在轨迹事件 ``ext_info`` 追加可见性备注(派发决策本身未失败,非 error_type)。
                _assy_err = getattr(result, "assembly_error", None)
                if _assy_err:
                    node.run_info.extend_props["_dispatch_failure"] = {
                        "error_type": "rationale_assembly_failed",
                        "error_msg": str(_assy_err)[:500],
                    }
                # REQ-2 DISPATCH rationale —— 策略 apply 填充,经 ``dataclasses.asdict``
                # 写入 ``node.run_info.extend_props["_dispatch_rationale"]`` 透给引擎
                # DISPATCH 闸门(此为唯一 carrier,无 contextvar)。``None``/异常 → 不写
                # (引擎 gate 防御读取 → ``ext_info=None``)。派发策略不再执行 claim-join
                # 后置过滤；这里仅透传仍保留的轨迹兼容字段。
                if getattr(result, "rationale", None) is not None:
                    try:
                        node.run_info.extend_props["_dispatch_rationale"] = (
                            dataclasses.asdict(result.rationale)
                        )
                    except Exception as ex:  # noqa: BLE001  序列化失败 → 留空不阻断派发
                        logger.warning(
                            "[task][dispatch] node=%s _dispatch_rationale 序列化失败: %s",
                            node.node_id,
                            ex,
                        )
                        node.run_info.extend_props["_dispatch_failure"] = {
                            "error_type": "rationale_serialize_failed",
                            "error_msg": f"{type(ex).__name__}: {ex}"[:500],
                        }
                group = getattr(result, "group_formation", None)
                logger.info(
                    "[task][dispatch] task=%s node=%s outcome=%s run_mode=%s assignee=%s "
                    "group_mode=%s group_bot_ids=%s",
                    node.task_id,
                    node.node_id,
                    result.outcome,
                    node.run_info.run_mode,
                    node.run_info.assignee or "<group pending/miss>",
                    group.collab_mode if group is not None else None,
                    list(group.bot_ids) if group is not None else None,
                )
                return node
            except Exception as ex:  # noqa: BLE001  搜推异常→吞掉,留 PENDING 交 harness 按超时重试
                logger.warning(
                    "[task][dispatch] node=%s 搜推异常→留 PENDING 交 harness: %s",
                    node.node_id,
                    ex,
                )
                node.run_info.extend_props["dispatch_error"] = (
                    f"dispatch_exception:{type(ex).__name__}"
                )
                # 失败 carrier(含异常消息):引擎 dispatch_fail 闸门据此发射带 ``error_type=DISPATCH_STUCK``
                # 的 DISPATCH 轨迹事件(短 ``dispatch_error`` 只够 harness 路由,这里补全诊断消息)。
                node.run_info.extend_props["_dispatch_failure"] = {
                    "error_type": "dispatch_exception",
                    "error_msg": f"{type(ex).__name__}: {ex}"[:500],
                }
                return node

        out = list(await _aio.gather(*[_one(n) for n in toDoTaskList]))
        return out

    async def _select_and_apply(self, node: TaskNode, graph: TaskExecutionGraph | None):
        """first-match-wins 选策略 await apply。graph 为 None 时走兜底 MISS。"""
        import agentclaw.community.core.task.task_dispatch.strategies as _s

        if graph is None:
            return _s.SearchResult(
                outcome=_s.SearchOutcome.MISS, miss_reason="no_graph"
            )
        profile = graph.extend_props.get("runtime_profile") or {}
        requested_strategy = str(
            profile.get("dispatcher_strategy") or "default"
        ).strip()
        strategies = sorted(self._strategies, key=lambda r: r.priority)
        if requested_strategy != "default":
            strategies = [
                strategy
                for strategy in strategies
                if str(getattr(strategy, "rule_id", "")).strip() == requested_strategy
            ]
            if not strategies:
                raise ValueError(
                    f"unknown frozen dispatcher_strategy={requested_strategy!r}"
                )
        for strategy in strategies:
            if await strategy.matches(node, graph):
                return await strategy.apply(node, graph)
        return _s.SearchResult(outcome=_s.SearchOutcome.MISS, miss_reason="no_strategy")

    async def dispatch_requested(
        self, task_id: str, node_ids: list[str] | None = None
    ) -> None:
        """Handle centralized DISPATCH_REQUESTED using existing Runner APIs.

        ``_prepare_into`` still contains the legacy dispatch preparation and
        delivery side-effect drain. Keeping that compatibility seam avoids any
        TaskRunner interface change while the remaining phases are extracted.
        """
        side: list[tuple] = []
        with self._lock_for(task_id):
            await self._prepare_into(task_id, side)
        await self._drain(task_id, side)

    async def _prepare_into(self, task_id: str, side: list[tuple]) -> None:
        """查「未派发」PENDING 节点 → await dispatcher.dispatch 返填执行者 → HIT 先落 run_mode/assignee
        + 飞行标记 ``dispatching``(保持 PENDING),start_run/form_coop_group 成功后由 _drain 翻 RUNNING(side 'run'/'group')
        并清 dispatching;MISS(side 'miss');派发异常(side 'dispatch_fail',留 PENDING 交 harness 按超时重试搜推)。

        状态机:RUNNING=真执行;派发命中只填执行者+置 dispatching,PENDING 维持到 start_run 成功后才翻。
        跳过:① dispatching=True 节点(已交付 _drain 待翻 RUNNING 的飞行态,防双派发);② dispatch_error 节点
        (搜推异常/派发失败,harness owns 重试+HUNG 上限,正常 cycle 不重复搜推防 bot 调用风暴);
        ③ effective_run_mode(node)=="bbs" 节点(FR-EXT-06:bbs 由 bot 经 bbs/attach 自驱,框架不自动派发/翻态)。
        reset 节点(FAILED/RUNNING→PENDING 复位,无 dispatching)不在跳过之列→重新派发执行。"""
        if self._is_external_managed_task(task_id):
            logger.info(
                "[task][prepare] task=%s external-managed, skip dynamic dispatch",
                task_id,
            )
            return
        all_pending = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(status=Status.PENDING)
        )
        pending = [
            n
            for n in all_pending
            if not n.run_info.extend_props.get("dispatching")
            and not n.run_info.extend_props.get("dispatch_error")
            and effective_run_mode(n) != "bbs"
        ]
        if not pending:
            return
        dispatch_started_at = _now_ms()
        for node in pending:
            # start_time is the first dispatch lifecycle timestamp, not the
            # beginning of each Harness retry attempt. Preserve it across
            # RUNNING→PENDING→RUNNING retries so elapsed time stays truthful.
            if node.run_info.start_time is None:
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        start_time=dispatch_started_at,
                    )
                )
        logger.info(
            "[task][prepare] task=%s 待派发节点=%s dispatch_started_at=%s",
            task_id,
            [n.node_id for n in pending],
            dispatch_started_at,
        )
        to_run: list[TaskNode] = []

        def _handle_node(node: TaskNode) -> None:
            miss = node.run_info.extend_props.get("miss_events")
            gf = node.run_info.extend_props.pop("pending_group_formation", None)
            if gf is not None:
                # ``pending_group_formation`` 只由动态 dispatcher 的 HIT_MULTI_BOTS
                # 产生；在动态编排核边界统一其协议和 BCS 协作方式，不能依赖任一
                # 搜推实现是否正确携带内部标记/默认 collab_mode。静态计划走
                # ``_prepare_static``，不会进入这里。
                inherited_protocol = gf.extend_props.get("dynamic_task_node_protocol")
                gf.extend_props["dynamic_task_node_protocol"] = True
                logger.info(
                    "[task][prepare] task=%s node=%s → group(HIT_MULTI_BOTS collab=%s "
                    "bot_ids=%s dynamic_task_node_protocol=%s inherited_protocol=%s)",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    gf.bot_ids,
                    gf.extend_props["dynamic_task_node_protocol"],
                    inherited_protocol,
                )
                # 群验收需要完整 goal/instruction，而不是只有一句 task_context。
                gf.extend_props.setdefault(
                    "task_objective", node.task_spec.goal.objective
                )
                gf.extend_props.setdefault(
                    "task_instruction", task_spec_instruction(node.task_spec)
                )
                gf.extend_props.setdefault(
                    "acceptances",
                    [
                        {"id": a.id, "description": a.description}
                        for a in node.task_spec.goal.acceptances
                    ],
                )
                # 飞行标记:group 交付 _drain 拉群前置,防并发 cycle 双搜推双拉群
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="coop_group",
                        extend_props_patch={
                            "dispatching": True,
                            "dispatching_at": _now_ms(),
                        },
                    )
                )
                side.append(("group", node, gf))
                return
            if node.run_info.run_mode and node.run_info.assignee:
                logger.info(
                    "[task][prepare] task=%s node=%s → run(mode=%s assignee=%s)",
                    task_id,
                    node.node_id,
                    node.run_info.run_mode,
                    node.run_info.assignee,
                )
                # HIT:落执行者+飞行标记 dispatching(保持 PENDING);start_run 成功后 _drain 翻 RUNNING+清 dispatching
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode=node.run_info.run_mode,
                        assignee=node.run_info.assignee,
                        extend_props_patch={
                            "dispatching": True,
                            "dispatching_at": _now_ms(),
                        },
                    )
                )
                to_run.append(node)
                return
            if miss:
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
                return
            # 派发未产出执行者也非 MISS(dispatcher 已容错吞异常):标 dispatch_error 留 PENDING,harness 按超时重试搜推
            derr = node.run_info.extend_props.get("dispatch_error") or "no_result"
            logger.warning(
                "[task][prepare] task=%s node=%s 派发未产出(%s)→留 PENDING 待 harness",
                task_id,
                node.node_id,
                derr,
            )
            _df = node.run_info.extend_props.get("_dispatch_failure")
            _fail_patch: dict[str, Any] = {"dispatch_error": derr}
            if isinstance(_df, dict):
                # 透传失败 carrier(dispatcher 顶层 except 写入,含异常消息)到 dispatch_fail
                # patch,供 _drain dispatch_fail 闸门发射带 error_msg 的 DISPATCH 轨迹事件。
                _fail_patch["_dispatch_failure"] = _df
            side.append(
                (
                    "dispatch_fail",
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        extend_props_patch=_fail_patch,
                    ),
                )
            )

        dispatched = await self._dispatcher.dispatch(pending)
        for node in dispatched:
            _handle_node(node)
        if to_run:
            side.append(("run", to_run))

    async def _prepare_static(self, task_id: str, runtime, side: list[tuple]) -> None:

        # cascade loop:enabled_when 未满足的节点(skip)被翻 DONE 后,会解锁依赖它的后续节点

        # (如 implementation skip 后 notify_done 的 depends_on={implementation} 满足),

        # 必须立即 _static_next_wave 揭示该后续波并继续 dispatch,否则后续节点(如 notify_done)

        # 永不入图,terminal 因 all_in_graph=False 永不翻 DONE,导致 graph 卡 EXECUTING、root 卡

        # "尚未开始"。max_rounds 兜底防依赖环导致的无限揭示。

        max_rounds = 8

        for round_idx in range(max_rounds):
            graph = self._graph.query_task_dashboard(task_id)

            readiness = runtime.ready(graph)

            logger.info(
                "[task][static-plan] prepare task=%s round=%s ready=%s skipped=%s",
                task_id,
                round_idx,
                [node.node_id for node in readiness.ready],
                [node.node_id for node in readiness.skipped],
            )

            for node in readiness.skipped:
                logger.info(
                    "[task][static-plan] skip node task=%s node=%s reason=enabled_when",
                    task_id,
                    node.node_id,
                )

                self._report_node_patch(
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
                    "[task][static-plan] dispatch ready nodes task=%s round=%s nodes=%s",
                    task_id,
                    round_idx,
                    [node.node_id for node in readiness.ready],
                )

                # Static nodes use the YAML-bound bot directly; skip catalog search

                # and claim-join so dependencies are never dispatched ahead of time

                # and the bound bot_id (e.g. strategy_approval/implementation) is

                # honored instead of being replaced by whatever catalog returns.

                await self._prepare_static_into(task_id, runtime, readiness.ready, side)

            # 本轮既无 skip 也无 ready:已达稳态,退出 cascade。

            if not readiness.skipped and not readiness.ready:
                break

            # 本轮有 skip:已把节点翻 DONE,揭示依赖它的后续波(notify_done 等),下一轮 ready 它并 dispatch。

            if readiness.skipped:
                self._static_next_wave(task_id, runtime)

        else:
            logger.warning(
                "[task][static-plan] prepare cascade hit max_rounds=%s task=%s,可能存在依赖环",
                max_rounds,
                task_id,
            )

    async def _prepare_static_into(
        self, task_id: str, runtime, ready_nodes, side: list[tuple]
    ) -> None:
        """Static DAG 节点跳过搜推,直接用 YAML 绑定的 bot 指派。



        type=bot → single_bot + assignee=definition.bot_id → start_run;

        type=collaboration → pending_group_formation → form_coop_group。

        不进 dispatcher.dispatch / 不查 catalog / 不做 claim_join,故未 ready 的依赖节点

        (strategy_approval/implementation)不会被提前搜推成 MISS/claim_mode_off,且 YAML 绑定

        的 bot_id 永远被尊重(不会被 catalog 命中的其他 bot 替换)。依赖顺序由 runtime.ready 保证。"""

        to_run: list[TaskNode] = []

        # 固定流程默认真实上报 + fallback 兜底:每个真实派发节点都额外调度一条延迟 mock 上报,

        # 真实回投先到则自跳过(mock 兜底延迟改为随机:单 bot 20-40s,协作群 40-80s,无固定超时),

        # 否则超时后由 mock 推进;auto=True 演示模式仍走短延迟(_static_auto_report 内按 mode 取 delay)。

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

            # notify 终端节点:任务实施完成通知触发用户(DingTalk account_id),不派发 bot;

            # 受信方=execution_config.owner_account_id(缺省 guoke.gk),凭证未配/投递失败→provider.send

            # 返 None,节点仍置 DONE 不阻塞 graph 终态(与 NullProvider 降级语义一致);不走 on_report 避免

            # acceptance PENDING+PASS 非法翻态,PENDING→DONE 经 status 直驱表允许。

            if getattr(definition, "node_type", "bot") == "notify":
                cfg = self._graph._execution_config(task_id)

                owner_acct = (
                    cfg.get("owner_account_id") if isinstance(cfg, dict) else None
                )

                recipient = owner_acct or "guoke.gk"

                logger.info(
                    "[task][static-plan] notify task=%s node=%s recipient=%s template=%s",
                    task_id,
                    node.node_id,
                    recipient,
                    runtime.definition.template_id,
                )

                ext_id = None

                send_err: str | None = None

                provider = self._notify_provider

                if provider is not None:
                    try:
                        from agentclaw.community.plugin_api.notify_sender import (
                            NotifyMessage,
                        )

                        title = f"OKR 实施完成：{runtime.definition.template_id}"

                        body = (
                            f"OKR 任务已实施完成。\n模板: {runtime.definition.template_id}\n"
                            f"通过 OKR实现Bot 完成风险评估 / 营销策略 / 审核 / 投放实施流程。"
                        )

                        ext_id = provider.send(
                            NotifyMessage(title=title, body=body, recipient=recipient),
                            channel="tc_card",
                        )

                    except Exception as ex:  # noqa: BLE101 provider never raise,防实现越界
                        send_err = f"{type(ex).__name__}: {ex}"

                        logger.warning(
                            "[task][static-plan] notify send 异常 task=%s node=%s: %s",
                            task_id,
                            node.node_id,
                            send_err,
                        )

                else:
                    logger.info(
                        "[task][static-plan] notify provider 未注入(NullProvider noop) task=%s node=%s",
                        task_id,
                        node.node_id,
                    )

                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        status=Status.DONE,
                        run_mode="notify",
                        assignee=f"dingtalk:{recipient}",
                        output_patch={
                            "notify_result": {
                                "recipient": recipient,
                                "sent": ext_id is not None,
                                "external_id": ext_id,
                                "error": send_err,
                                "channel": "tc_card",
                            }
                        },
                    )
                )

                logger.info(
                    "[task][static-plan] notify dispatched task=%s node=%s sent=%s ext_id=%s",
                    task_id,
                    node.node_id,
                    ext_id is not None,
                    ext_id,
                )

                continue

            # bbs_handoff 旁路:不可实现任务转 BBS 广场(与 approval 并行,不阻塞主实施线)。

            # 阶段① 入广场:写 assignee=安全架构师 + bbs_status=posted_in_square(dashboard 即可点开安全架构师

            # bot 主会话,不再空 assignee 致点不开);30s 后由 _bbs_handoff_claim 被接:真实 start_run 发

            # 安全架构师 并落 session_id + 翻 claimed。

            if getattr(definition, "node_type", "bot") == "bbs_handoff":
                bot_id = getattr(definition, "bot_id", None) or ""

                static_input = (
                    node.task_spec.context.extend_props.get("static_input") or {}
                )

                items = static_input.get("unhandled_tasks")

                if isinstance(items, list) and items:
                    pass  # 真实风险评估上报了结构化不可实现任务 → 原样用其内容

                else:
                    # 真实评估为自然语言、无结构化 unhandled_tasks → 兜底 mock 占位(与 _static_auto_report

                    # 的 _UHT_MOCK 同口径),安全架构师 不致收到空;真实检测到时优先用真实内容。

                    items = list(_UHT_MOCK)

                    logger.info(
                        "[task][static-plan] bbs_handoff 真实无结构化 unhandled_tasks,兜底 mock 占位 task=%s node=%s",
                        task_id,
                        node.node_id,
                    )

                logger.info(
                    "[task][static-plan] task=%s node=%s -> bbs_handoff posted items=%s rnd_bot=%s",
                    task_id,
                    node.node_id,
                    items if isinstance(items, list) else type(items).__name__,
                    bot_id,
                )

                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="bbs",
                        assignee=bot_id,
                        extend_props_patch={
                            "dispatching": True,
                            "dispatching_at": _now_ms(),
                            "bbs_status": "posted_in_square",
                            "bbs_owner": "",
                            "bbs_handed_to": "",
                            "bbs_task_items": items,
                        },
                    )
                )

                side.append(("bbs_handoff", node, bot_id, items))

                continue

            # auto 模式仍走真实派发(group/run),不跳过;仅额外调度延迟 mock 上报(见 _static_auto_report)

            gf = node.run_info.extend_props.get("pending_group_formation")
            static_group = node.run_info.extend_props.get("static_group")
            if gf is None and isinstance(static_group, dict):
                gf = GroupFormation(
                    bot_ids=list(static_group.get("bot_ids") or []),
                    collab_mode=str(static_group.get("collab_mode") or "chat"),
                    group_name=str(
                        static_group.get("group_name") or f"{task_id}-{node.node_id}"
                    ),
                    extend_props={
                        "static_input": dict(static_group.get("static_input") or {})
                    },
                )

            if gf is not None:
                gf.extend_props.setdefault(
                    "task_objective", node.task_spec.goal.objective
                )

                gf.extend_props.setdefault(
                    "task_instruction", task_spec_instruction(node.task_spec)
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

                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="coop_group",
                        extend_props_patch={
                            "dispatching": True,
                            "dispatching_at": _now_ms(),
                        },
                    )
                )

                side.append(("group", node, gf))

                auto_nodes.append(node)  # 拉群真实派发 + 调度兜底 mock 上报

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

                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        run_mode="single_bot",
                        assignee=bot_id,
                        extend_props_patch={
                            "dispatching": True,
                            "dispatching_at": _now_ms(),
                        },
                    )
                )

                to_run.append(node)

                auto_nodes.append(node)  # 单 bot 真实派发 + 调度兜底 mock 上报

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
