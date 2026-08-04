"""Engine-runtime groups — the public wrap of a bot's engine adapter.

Five routers, all mounted under ``/openapi/v1/bots/{bot_id}/…``. See
``docs/openapi-v1/engine-surface.md`` for which engine routes are wrapped and
which are deliberately not.
"""

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    ApprovalMode,
    MessageRole,
    SocketKind,
)

__all__ = ["ApprovalMode", "MessageRole", "SocketKind"]
