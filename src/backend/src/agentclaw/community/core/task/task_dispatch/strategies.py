"""TaskDispatcher 内置派发优化策略库(引擎自带,不开放自定义)。

对齐 plan.md §3.4(first-match-wins by priority)。策略经 ``execution_config`` 动态匹配,
类 SQL optimizer:config 有 ``bot`` → DirectDispatchStrategy(跳过搜推直接填);
否则兜底 SearchBasedDispatchStrategy(搜推)。Avernet 默认 stub(search 恒 MISS);
corp 真实 catalog 搜推 + 多 bot 拉群在 ocb 仓替换策略实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode


class SearchOutcome(StrEnum):
    """搜推 4 态结果。"""

    HIT_SINGLE = "HIT_SINGLE"           # 单 bot 命中
    HIT_GROUP = "HIT_GROUP"             # 协作群命中(已有群)
    HIT_MULTI_BOTS = "HIT_MULTI_BOTS"   # 多 bot 命中,需动态拉协作群
    MISS = "MISS"                       # 未匹配执行者


@dataclass
class GroupFormation:
    """动态拉协作群参数(HIT_MULTI_BOTS 时 search 一并决出;内部参数,不持久 RuntimeInfo)。"""

    bot_ids: list[str]
    collab_mode: str                    # "chat"/"manager_worker"/"state_machine"(state_machine 注入 workflow yaml)
    extend_props: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """搜推结果。"""

    outcome: SearchOutcome
    bot_id: str | None = None                       # HIT_SINGLE
    group_id: str | None = None                     # HIT_GROUP
    group_formation: GroupFormation | None = None   # HIT_MULTI_BOTS
    miss_reason: str | None = None                  # MISS


class DispatchStrategy(Protocol):
    """派发优化策略契约(引擎内置,first-match-wins)。"""

    rule_id: str
    priority: int

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        """纯读:据图级 execution_config 判本策略是否适用(bot 信号)。协程化:corp catalog 查询可耗 IO。"""
        ...

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        """对单节点决出 SearchResult(4 态)。HIT_MULTI_BOTS 携 GroupFormation;拉群由编排核+runner。
        协程化:corp 真实 bot catalog 搜推是耗时 IO,await 不阻塞。"""
        ...


class DirectDispatchStrategy:
    """config 有 ``bot`` → 跳过搜推,直接返 HIT_SINGLE(bot=cfg["bot"])。"""

    rule_id = "direct"
    priority = 10

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        return cfg.get("bot") is not None

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=cfg.get("bot"))


class SearchBasedDispatchStrategy:
    """默认兜底:搜推匹配。Avernet stub:恒 MISS(触发升 BBS 链路可单测);
    corp 真实 catalog 搜推 + 多 bot 拉群推荐在 ocb 仓替换本类实现。"""

    rule_id = "search"
    priority = 99

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason="averent_stub_no_catalog")
