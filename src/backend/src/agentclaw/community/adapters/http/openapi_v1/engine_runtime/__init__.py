"""Engine-runtime groups — the public wrap of a bot's engine adapter.

Five routers — ``connection``, ``engine``, ``approvals``, ``sessions``,
``models`` — each mounted at ``/openapi/v1/bots/<component>/{bot_id}/…``, the
addressing rule the whole surface follows: the component's literal name first,
the bot as the first parameter beneath it. See
``docs/openapi-v1/engine-surface.md`` for which engine routes are wrapped and
which are deliberately not.
"""

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    ApprovalMode,
    MessageRole,
    SocketKind,
)

__all__ = ["ApprovalMode", "MessageRole", "SocketKind"]
