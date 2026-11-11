"""G2 可靠性规则:乐观并发版本校验(spec 全局约束)。

``ensure_expected_version`` 做乐观并发控制,由仓储在写入路径复用。
本模块不做任何 IO,不持有状态。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabVersionConflict,
)


def ensure_expected_version(expected: int, current: int) -> None:
    """校验乐观并发版本;不匹配抛 Conflict(带 current version)。"""
    if expected != current:
        raise ContextCollabVersionConflict(
            f"expected_version {expected} conflicts with "
            f"current context_version {current}"
        )
