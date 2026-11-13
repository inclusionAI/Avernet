"""L5 Methods:当前产品实际采用的协作方法枚举。

BBS 公开讨论使用 ``Open + Group``,团队代办使用 ``Single``;
``Swarm`` 及 G3 演进对象暂不引入,未知 wire 值抛 ``ContextCollabValidationError``。
"""

from __future__ import annotations

from enum import Enum

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


class MethodKind(str, Enum):
    """L5 方法取值;wire 值与三个北向 API 的 ``method_kind`` 字段逐字一致。"""

    SINGLE = "Single"
    OPEN_GROUP = "Open + Group"

    @staticmethod
    def from_wire(value: str) -> "MethodKind":
        """从 API wire 字符串恢复枚举;未知值抛 ``ContextCollabValidationError``。"""
        try:
            return MethodKind(value)
        except ValueError:
            raise ContextCollabValidationError(
                f"unknown method_kind wire value: {value!r}"
            ) from None
