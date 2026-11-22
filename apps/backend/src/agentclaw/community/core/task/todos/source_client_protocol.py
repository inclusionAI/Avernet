"""Todo-owned client boundary for reading external work-item sources."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.auth import AuthRequestContext


@runtime_checkable
class TodoSourceClientProtocol(Protocol):
    """Invoke one read-only source operation for the authenticated Human."""

    def call(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> object: ...
