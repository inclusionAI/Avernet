"""TaskDispatch seam:BotDiscoverPort 搜推 Protocol + 搜推类型。对齐 plan.md §3.3 + tasks.md T4.1。

非领域实体,模块层接缝;Avernet stub(本地关键词 catalog)/ corp 真实搜推。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from agentclaw.community.core.task.domain.models import TaskNode


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


class BotDiscoverPort(Protocol):
    """搜推 seam(同步 in-process):据 node.task_spec 决定执行主体。search 入参只 node(不读 graph)。"""

    def search(self, node: TaskNode) -> SearchResult:
        """返回 4 态之一:HIT_SINGLE(bot_id)/HIT_GROUP(group_id)/HIT_MULTI_BOTS(group_formation)/MISS。"""
        ...
