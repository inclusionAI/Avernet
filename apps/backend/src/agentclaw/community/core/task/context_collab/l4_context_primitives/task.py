"""L4 Task 状态词汇:G4 有限状态值(spec 全局约束状态词汇)。

当前三个北向 API 的 ``current_snapshot.status`` 只允许这六个值;
``ready`` / ``active`` 等 G4 State Machine 自定义状态名只存在于
状态机 Profile,不进入本词汇表。
"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    """Task 状态词汇;取值与 API 示例的 ``current_status`` 逐字一致。"""

    CREATED = "created"
    IN_PROGRESS = "in_progress"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"
