"""任务代办读视图(T3):Query.project/search 的产品投影。

``status`` 读取 G4 推进后的独立 Task;``events`` 公开投影含 Task 控制事件
——推进历史是代办消费面的核心;``body`` 沿公开讨论 ``DiscussionDetailBody``
先例(孤儿帖 None 合法域可空)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentclaw.community.core.task.context_collab.products.bbs_public_discussion.views import (
    DiscussionDetailBody,
)


@dataclass(frozen=True)
class TaskCommissionSummaryView:
    """任务列表项;``message_count`` 含任务正文。"""

    collet_id: str
    title: str
    initiator_id: str
    message_count: int
    created_at: datetime


@dataclass(frozen=True)
class TaskCommissionDetailView:
    """任务详情:title 读源=初始事件 payload(回退伴随 spec)。"""

    collet_id: str
    title: str
    status: str
    body: DiscussionDetailBody | None
    events: list[dict]
