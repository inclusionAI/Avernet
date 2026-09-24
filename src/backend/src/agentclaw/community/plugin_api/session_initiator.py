"""SessionInitiator — engine session 创建 + 发现消息注入的 Plugin Protocol.

This is the selection seam under ``plugin_api/`` for task discovery: given a
list of discovered tasks, create an engine session and inject the discovery
prompt so the bot can present the tasks to the user.

Follows the ``DeviceSyncDispatcher`` pattern — the protocol references core
domain types (``DiscoveredTask`` / ``DiscoverySession``) only via postponed
annotations (``from __future__ import annotations``), so ``plugin_api/`` does
not import ``core/`` (layer rule test_architecture_compliance).

Rule 20 — every Plugin Protocol has ≥1 local + ≥1 prod impl:
- local   : ``CronRelaySessionInitiator`` (plugins/local) — relay + WebSocket.
- prod    : ``OpenApiBotSessionInitiator`` (corp/plugins/prod) — BaaS Open API.

Concrete implementations nominally inherit this Protocol and are decorated
``@plugin_impl`` so the Rule 20/21 registry recognizes the Protocol from the
direct base class.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin

# ``DiscoveredTask`` / ``DiscoverySession`` live in ``core.task.task_discovery.models``;
# referenced below only as postponed annotations so ``plugin_api/`` does not import core.


@runtime_checkable
class SessionInitiator(Plugin, Protocol):
    """Engine session 创建 + 消息注入接口。

    Implementations:
    - ``CronRelaySessionInitiator``  (local) — relay 通道创建 session + WebSocket 注入。
    - ``OpenApiBotSessionInitiator`` (prod)  — BaaS Open API 创建 session + 注入。
    """

    async def initiate_session(
        self,
        tasks: list[DiscoveredTask],  # noqa: F821  postponed annotation
        *,
        bot_id: str,
        owner_id: str,
        agent_id: str,
        model: str | None = None,
    ) -> DiscoverySession:  # noqa: F821  postponed annotation
        """为发现任务创建 engine session 并注入发现提示消息。"""
        ...
