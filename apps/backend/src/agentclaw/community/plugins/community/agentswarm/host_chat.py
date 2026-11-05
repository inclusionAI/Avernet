"""Compatibility shim — the engine chat transport now lives in ``chat/engine_ws``.

Kept so every existing import path (``agentswarm.host_chat``,
``agentswarm.host``, the test seams) keeps resolving after the transport moved
behind the ``ChatTransport`` seam. No logic lives here; edit
``agentclaw.community.plugins.community.agentswarm.chat.engine_ws`` instead.

The tunables are *proxied* to ``engine_ws`` rather than copied. A copy would
fork the value the transport actually reads: a caller (or a test) that patches
``host_chat._CHAT_TIMEOUT_SECONDS`` would silently patch a number nothing reads,
and the leg would keep using the original deadline. The module below swaps its
own ``__class__`` for one that resolves those names against ``engine_ws``, which
is the only reliable way to intercept attribute assignment on a module.
"""
from __future__ import annotations

import sys
import types

from agentclaw.community.plugins.community.agentswarm.chat import engine_ws as _engine_ws
from agentclaw.community.plugins.community.agentswarm.chat.engine_ws import (
    ChatLegMixin,
    _CHAT_BRIDGE,
    chat_bridge_executor,
    set_chat_bridge_max_workers,
    shutdown_chat_bridge,
)
from agentclaw.community.plugins.community.agentswarm.chat.base import (
    ChatTimeoutError,
)

#: Tunables whose historical home was this module. Reads and writes resolve
#: against ``engine_ws`` so patching either name reaches the transport.
_PROXIED = frozenset({
    "_CHAT_TIMEOUT_SECONDS",
    "_DEFAULT_MAX_PLATFORM_WORKERS",
    "_DEFAULT_SESSION_KEY",
    "_ENGINE_WS_PATH",
    "_ENGINE_WS_PORT",
    "_RECV_SLICE_SECONDS",
    "_SESSION_RESET_SECONDS",
    "_ZERO_CHECK_OPEN_API_TOKEN",
})

_LOCAL = {
    "ChatLegMixin": ChatLegMixin,
    "ChatTimeoutError": ChatTimeoutError,
    "_CHAT_BRIDGE": _CHAT_BRIDGE,
    "chat_bridge_executor": chat_bridge_executor,
    "set_chat_bridge_max_workers": set_chat_bridge_max_workers,
    "shutdown_chat_bridge": shutdown_chat_bridge,
}

#: The proxied constants as ``engine_ws`` defines them, captured before any
#: caller can patch through this shim. A monkeypatch undo restores these.
_ORIGINAL = {name: getattr(_engine_ws, name) for name in _PROXIED}


class _ShimModule(types.ModuleType):
    """Module type that forwards the proxied tunables to ``engine_ws``."""

    def __getattribute__(self, name: str):
        if name in _PROXIED:
            return getattr(_engine_ws, name)
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value) -> None:
        if name in _PROXIED:
            setattr(_engine_ws, name, value)
            return
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in _PROXIED:
            # A monkeypatch undo runs delattr; deleting the transport's own
            # constant would break the leg. Restore the value captured at import
            # time instead.
            setattr(_engine_ws, name, _ORIGINAL[name])
            return
        super().__delattr__(name)


__all__ = sorted(set(_PROXIED) | set(_LOCAL))

sys.modules[__name__].__class__ = _ShimModule