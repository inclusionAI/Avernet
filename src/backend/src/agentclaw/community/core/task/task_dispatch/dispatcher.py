"""TaskDispatcher 派发编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan.md §3.3 + §3.4。零参构造(持 graph 只读 config),内置默认策略池
[DirectDispatchStrategy, SearchBasedDispatchStrategy];``set_strategies`` 非公开。
不持 runner(HIT_MULTI_BOTS 拉群归编排核+runner);不写图、不起 run。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode
from agentclaw.community.core.task.task_dispatch.strategies import (
    DirectDispatchStrategy,
    DispatchStrategy,
    SearchBasedDispatchStrategy,
    SearchOutcome,
)


class TaskDispatcher:
    """派发编排壳:对每节点 first-match-wins 选策略(graph 级 config 匹配)→ apply 填 run_info 后返回。

    不写图、不起 run(编排核落库+起 run)。BBS 节点(run_mode 已 "bbs")退化为直接维持(不走策略)。
    HIT_MULTI_BOTS 时填 run_mode="coop_group"+extend_props["pending_group_formation"],assignee 留空
    (拉群归编排核调 runner.form_coop_group 后填 assignee)。
    """

    def __init__(self, graph) -> None:
        """graph: TaskGraphService(读图级 execution_config 匹配策略用,不写)。"""
        self._graph = graph
        self._strategies: list[DispatchStrategy] = [
            DirectDispatchStrategy(),
            SearchBasedDispatchStrategy(),
        ]

    def set_strategies(self, strategies: list[DispatchStrategy]) -> None:
        """(非公开)替换策略池。engine ``_build_dispatcher`` 工厂方法/corp 子类注入用。"""
        self._strategies = list(strategies)

    def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        """入参=待派发节点;返回=填充执行者信息后的 list[TaskNode](对齐派发文档签名)。
        不写图、不起 run;per node first-match 策略 apply SearchResult → 填 node.run_info:
        HIT_SINGLE→single_bot/bot_id;HIT_GROUP→coop_group/group_id;
        HIT_MULTI_BOTS→coop_group/pending_group_formation(assignee 留空,编排核拉群填);MISS→不填+标 miss_events。
        BBS 节点(run_mode 已 "bbs")→ 退化直接维持。"""
        graph = self._graph.query_task_dashboard(
            toDoTaskList[0].task_id if toDoTaskList else ""
        ) if toDoTaskList else None
        out: list[TaskNode] = []
        for node in toDoTaskList:
            if node.run_info.run_mode == "bbs":
                out.append(node)  # BBS 节点退化维持
                continue
            result = self._select_and_apply(node, graph)
            if result.outcome == SearchOutcome.HIT_SINGLE:
                node.run_info.run_mode = "single_bot"
                node.run_info.assignee = result.bot_id
            elif result.outcome == SearchOutcome.HIT_GROUP:
                node.run_info.run_mode = "coop_group"
                node.run_info.assignee = result.group_id
            elif result.outcome == SearchOutcome.HIT_MULTI_BOTS:
                node.run_info.run_mode = "coop_group"
                node.run_info.extend_props["pending_group_formation"] = result.group_formation
                # assignee 留空,编排核拉群后填
            else:  # MISS
                node.run_info.extend_props["miss_events"] = [result.miss_reason or "no_bot"]
            out.append(node)
        return out

    def _select_and_apply(self, node: TaskNode, graph: TaskExecutionGraph | None):
        """first-match-wins 选策略 apply。graph 为 None 时走兜底 MISS。"""
        import agentclaw.community.core.task.task_dispatch.strategies as _s
        if graph is None:
            return _s.SearchResult(outcome=_s.SearchOutcome.MISS, miss_reason="no_graph")
        for strategy in sorted(self._strategies, key=lambda r: r.priority):
            if strategy.matches(node, graph):
                return strategy.apply(node, graph)
        return _s.SearchResult(outcome=_s.SearchOutcome.MISS, miss_reason="no_strategy")
