"""BBS 公开讨论读视图与投影(T3 落视图类型;投影函数随 T4 到)。

标题读源合同:初始事件 payload 的 ``objective``(spec §3.3 / 创建事实
spec §3 裁决 1);列表过渡期伴随读(裁决 2)。``T | None`` 只用于合法
领域可空:孤儿帖 ``body``(spec §6-D 裁决:发帖两步窗口态,详情容忍)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DiscussionSummaryView:
    """帖子列表项;``message_count`` 含首帖正文(纯回帖数减一)。"""

    collet_id: str
    title: str
    initiator_id: str
    message_count: int
    created_at: datetime


@dataclass(frozen=True)
class DiscussionDetailBody:
    """首帖正文事件投影。"""

    event_id: str
    content: str
    author_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class DiscussionReplyItem:
    """回帖投影;``parent_event_id`` 为 None 表示回复主题帖。"""

    event_id: str
    content: str
    author_id: str
    parent_event_id: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class DiscussionDetailView:
    """帖子详情:title 初始事件 payload 为新主,孤儿帖 ``body=None``。"""

    collet_id: str
    title: str
    state: str
    body: DiscussionDetailBody | None
    replies: list[DiscussionReplyItem]
