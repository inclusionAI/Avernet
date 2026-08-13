"""TaskDispatch 类型 re-export shim(向后兼容)。策略契约+类型已迁 ``strategies.py``。

BotDiscoverPort 已删(引擎内置策略库,不开放 Port 注入);搜推由 ``SearchBasedDispatchStrategy``
内置实现(corp 替换策略实现,非 Port 注入)。
"""
from __future__ import annotations

from agentclaw.community.core.task.task_dispatch.strategies import (  # noqa: F401
    GroupFormation,
    SearchOutcome,
    SearchResult,
)
