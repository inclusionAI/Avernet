"""TaskDispatcher 派发编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan.md §3.3 + §3.4。构造期注入策略池(``pool=``),内置默认
[DirectDispatchStrategy, SearchBasedDispatchStrategy];``set_strategies`` 仅供测试覆写。
不持 runner(HIT_MULTI_BOTS 拉群归编排核+runner);不写图、不起 run。
"""
from __future__ import annotations

import logging

from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode, effective_run_mode
from agentclaw.community.core.task.task_dispatch.strategies import (
    DirectDispatchStrategy,
    DispatchStrategy,
    SearchBasedDispatchStrategy,
    SearchOutcome,
)

logger = logging.getLogger("task.dispatcher")


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
        self._strategies: list[DispatchStrategy] = list(pool) if pool is not None else [
            DirectDispatchStrategy(),
            SearchBasedDispatchStrategy(),
        ]

    def set_strategies(self, strategies: list[DispatchStrategy]) -> None:
        """(测试覆写用)替换策略池。prod 经构造器 ``pool=`` 注入。"""
        self._strategies = list(strategies)

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        """入参=待派发节点;返回=填充执行者信息后的 list[TaskNode](对齐派发文档签名)。
        不写图、不起 run;per node first-match 策略 await apply SearchResult → 填 node.run_info:
        HIT_SINGLE→single_bot/bot_id;HIT_GROUP→coop_group/group_id;
        HIT_MULTI_BOTS→coop_group/pending_group_formation(assignee 留空,编排核拉群填);MISS→不填+标 miss_events。
        有效执行模态为 "bbs" 的节点→ 退化直接维持。协程化:catalog 搜推是耗时 IO,await 不阻塞编排核。"""
        graph = self._graph.query_task_dashboard(
            toDoTaskList[0].task_id if toDoTaskList else ""
        ) if toDoTaskList else None
        import asyncio as _aio
        logger.info("[task][dispatch] dispatch 入口 nodes=%s", [n.node_id for n in toDoTaskList])
        # v4:并发搜推(gather,无并发限流;catalog IO 耗时,串行是瓶颈)。BBS 节点跳过策略直接维持。
        async def _one(node: "TaskNode"):
            # 容错:搜推异常(无响应/推理失败/端口错)不崩整批,留 PENDING 标 dispatch_error 交 harness 重试搜推
            try:
                if effective_run_mode(node) == "bbs":
                    logger.info("[task][dispatch] node=%s run_mode=bbs 退化维持", node.node_id)
                    return node  # BBS 节点退化维持
                result = await self._select_and_apply(node, graph)
                if result.outcome == SearchOutcome.HIT_SINGLE:
                    node.run_info.run_mode = "single_bot"
                    node.run_info.assignee = result.bot_id
                    if result.bot_name is not None:
                        node.run_info.extend_props["assignee_name"] = result.bot_name
                    if result.owner_id is not None:
                        node.run_info.extend_props["assignee_owner_id"] = result.owner_id
                    if result.owner_name is not None:
                        node.run_info.extend_props["assignee_owner_name"] = result.owner_name
                elif result.outcome == SearchOutcome.HIT_GROUP:
                    node.run_info.run_mode = "coop_group"
                    node.run_info.assignee = result.group_id
                elif result.outcome == SearchOutcome.HIT_MULTI_BOTS:
                    node.run_info.run_mode = "coop_group"
                    node.run_info.extend_props["pending_group_formation"] = result.group_formation
                else:  # MISS
                    node.run_info.extend_props["miss_events"] = [result.miss_reason or "no_bot"]
                # JOIN 丢掉的候选透出到节点 unauthorized_bots(dashboard 暴露,引导 owner grant)
                if getattr(result, "unauthorized_bots", None):
                    node.run_info.extend_props["unauthorized_bots"] = result.unauthorized_bots
                group = getattr(result, "group_formation", None)
                logger.info(
                    "[task][dispatch] task=%s node=%s outcome=%s run_mode=%s assignee=%s "
                    "group_mode=%s group_bot_ids=%s unauthorized=%s",
                    node.task_id,
                    node.node_id,
                    result.outcome,
                    node.run_info.run_mode,
                    node.run_info.assignee or "<group pending/miss>",
                    group.collab_mode if group is not None else None,
                    list(group.bot_ids) if group is not None else None,
                    len(getattr(result, "unauthorized_bots", None) or []),
                )
                return node
            except Exception as ex:  # noqa: BLE001  搜推异常→吞掉,留 PENDING 交 harness 按超时重试
                logger.warning("[task][dispatch] node=%s 搜推异常→留 PENDING 交 harness: %s", node.node_id, ex)
                node.run_info.extend_props["dispatch_error"] = f"dispatch_exception:{type(ex).__name__}"
                return node
        out = list(await _aio.gather(*[_one(n) for n in toDoTaskList]))
        return out

    async def _select_and_apply(self, node: TaskNode, graph: TaskExecutionGraph | None):
        """first-match-wins 选策略 await apply。graph 为 None 时走兜底 MISS。"""
        import agentclaw.community.core.task.task_dispatch.strategies as _s
        if graph is None:
            return _s.SearchResult(outcome=_s.SearchOutcome.MISS, miss_reason="no_graph")
        for strategy in sorted(self._strategies, key=lambda r: r.priority):
            if await strategy.matches(node, graph):
                return await strategy.apply(node, graph)
        return _s.SearchResult(outcome=_s.SearchOutcome.MISS, miss_reason="no_strategy")
