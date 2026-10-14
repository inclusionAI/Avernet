"""Execution-generation guard for the network-admitted Relay HTTP surface."""

from __future__ import annotations

import hmac

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import TaskNode


def require_relay_attempt(node: TaskNode, relay_token: str) -> None:
    """Reject stale executions after the Backend rotates a node generation.

    ``/api/v1/collaboration/tasks/**`` is admitted by the production network
    whitelist.  The relay token is therefore only a per-node generation fence;
    it is not an end-user or Bot authentication credential.
    """
    current = str(node.run_info.extend_props.get("relay_token") or "")
    if not current or not relay_token or not hmac.compare_digest(current, relay_token):
        raise TaskStateError("relay execution attempt has expired")
