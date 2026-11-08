"""Execution-generation guard for the network-admitted Relay HTTP surface."""

from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_context.relay_attempt import (
    verify_relay_attempt,
)


def require_relay_attempt(node: TaskNode, relay_token: str) -> None:
    """Reject stale executions after the Backend rotates a node generation.

    ``/api/v1/collaboration/tasks/**`` is admitted by the production network
    whitelist.  The relay token is therefore only a per-node generation fence;
    it is not an end-user or Bot authentication credential.

    实现委托 core 落点 ``core/task/task_context/relay_attempt.verify_relay_attempt``
    (spec §14.5:领域语义归属 core,产物写口与 relay 路由共用同一份 fence)。
    """
    verify_relay_attempt(node, relay_token)
