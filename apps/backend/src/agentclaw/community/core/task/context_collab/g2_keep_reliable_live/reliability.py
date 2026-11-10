"""G2 可靠性规则:主干判定与乐观并发版本校验(spec 全局约束)。

两个纯函数,由仓储与用例在写入路径复用:``compute_is_trunk`` 在事件
接纳时计算 ``is_trunk`` 标记;``ensure_expected_version`` 做乐观并发
控制。本模块不做任何 IO,不持有状态。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabVersionConflict,
)


def compute_is_trunk(
    parent_event: str | None, previous_current_event_id: str | None
) -> bool:
    """计算新增事件是否属于主干。

    (全局约束)parent_event is None → True;
    previous_current_event_id is None → True;  # 初始事件
    parent_event == previous_current_event_id → True;  # 接在链头继续主干
    否则 False。
    """
    if parent_event is None:
        return True
    if previous_current_event_id is None:
        return True
    return parent_event == previous_current_event_id


def ensure_expected_version(expected: int, current: int) -> None:
    """校验乐观并发版本;不匹配抛 Conflict(带 current version)。"""
    if expected != current:
        raise ContextCollabVersionConflict(
            f"expected_version {expected} conflicts with "
            f"current context_version {current}"
        )
