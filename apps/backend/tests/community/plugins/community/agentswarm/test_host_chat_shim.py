"""The ``host_chat`` compatibility shim.

The engine leg moved to ``chat/engine_ws``; ``host_chat`` stays as a shim so
every historical import path keeps resolving. Two properties matter, and both
have already been broken once in review:

1. **Every name still resolves.** The shim's ``__all__`` is the old module's
   public surface, and callers (including tests) import through it.

2. **The tunables are live, not copies.** ``host_chat._CHAT_TIMEOUT_SECONDS``
   must be the same value ``engine_ws`` reads. A copy would let a caller patch a
   number nothing reads, and the leg would keep using the original deadline —
   which is exactly how a "bounded" chat silently stops being bounded.
"""

from __future__ import annotations

import pytest

import agentclaw.community.plugins.community.agentswarm.host_chat as host_chat_module
from agentclaw.community.plugins.community.agentswarm.chat import engine_ws


class TestExportedNames:
    """The historical surface still resolves through the shim."""

    @pytest.mark.parametrize(
        "name",
        [
            "ChatLegMixin",
            "ChatTimeoutError",
            "_CHAT_BRIDGE",
            "_CHAT_TIMEOUT_SECONDS",
            "_DEFAULT_MAX_PLATFORM_WORKERS",
            "_DEFAULT_SESSION_KEY",
            "_ENGINE_WS_PATH",
            "_ENGINE_WS_PORT",
            "_RECV_SLICE_SECONDS",
            "_SESSION_RESET_SECONDS",
            "_ZERO_CHECK_OPEN_API_TOKEN",
            "chat_bridge_executor",
            "set_chat_bridge_max_workers",
            "shutdown_chat_bridge",
        ],
    )
    def test_name_resolves(self, name):
        assert hasattr(host_chat_module, name)

    def test_all_lists_the_whole_surface(self):
        for name in host_chat_module.__all__:
            assert hasattr(host_chat_module, name), name

    def test_class_identity_is_the_engine_one(self):
        # Not merely equal: the very object, so isinstance checks and
        # monkeypatching behave as they always did.
        assert host_chat_module.ChatLegMixin is engine_ws.ChatLegMixin
        assert host_chat_module._CHAT_BRIDGE is engine_ws._CHAT_BRIDGE

    def test_unknown_attribute_raises(self):
        with pytest.raises(AttributeError, match="has no attribute"):
            _ = host_chat_module.this_name_never_existed


class TestPackageSurface:
    """The package's public exports, including the lazily-resolved transport.

    ``BaasGatewayChatTransport`` is imported on demand so the engine path never
    pulls ``httpx`` in. That laziness is only correct if the export actually
    resolves — hence this test.
    """

    def test_lazy_transport_resolves(self):
        import agentclaw.community.plugins.community.agentswarm.chat as chat_pkg
        from agentclaw.community.plugins.community.agentswarm.chat.base import (
            ChatTransport,
        )

        transport_cls = chat_pkg.BaasGatewayChatTransport

        assert issubclass(transport_cls, ChatTransport)

    def test_every_export_resolves(self):
        import agentclaw.community.plugins.community.agentswarm.chat as chat_pkg

        for name in chat_pkg.__all__:
            assert getattr(chat_pkg, name) is not None, name

    def test_unknown_attribute_raises(self):
        import agentclaw.community.plugins.community.agentswarm.chat as chat_pkg

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = chat_pkg.not_a_real_export


class TestTunablesAreProxied:
    """Reads and writes reach the module the transport actually reads."""

    @pytest.mark.parametrize(
        "name",
        [
            "_CHAT_TIMEOUT_SECONDS",
            "_RECV_SLICE_SECONDS",
            "_SESSION_RESET_SECONDS",
            "_DEFAULT_MAX_PLATFORM_WORKERS",
            "_ENGINE_WS_PORT",
            "_ENGINE_WS_PATH",
            "_DEFAULT_SESSION_KEY",
            "_ZERO_CHECK_OPEN_API_TOKEN",
            "_CHAT_TIMEOUT_SECONDS",
        ],
    )
    def test_read_matches_engine_ws(self, name):
        assert getattr(host_chat_module, name) == getattr(engine_ws, name)

    def test_write_reaches_engine_ws(self, monkeypatch):
        # monkeypatch is what the existing suite uses; it must land on the
        # transport's constant, not on a shim-local copy.
        monkeypatch.setattr(host_chat_module, "_CHAT_TIMEOUT_SECONDS", 0.05)

        assert engine_ws._CHAT_TIMEOUT_SECONDS == 0.05
        assert host_chat_module._CHAT_TIMEOUT_SECONDS == 0.05

    def test_monkeypatch_undo_restores_the_original(self, monkeypatch):
        original = engine_ws._CHAT_TIMEOUT_SECONDS
        with monkeypatch.context() as patch:
            patch.setattr(host_chat_module, "_RECV_SLICE_SECONDS", 0.01)
            assert engine_ws._RECV_SLICE_SECONDS == 0.01

        assert engine_ws._RECV_SLICE_SECONDS != 0.01
        assert engine_ws._CHAT_TIMEOUT_SECONDS == original

    def test_setattr_returns_to_engine_ws_on_rollback(self):
        original = host_chat_module._SESSION_RESET_SECONDS
        host_chat_module._SESSION_RESET_SECONDS = 0.25
        assert engine_ws._SESSION_RESET_SECONDS == 0.25
        try:
            assert True
        finally:
            # Assignment, not deletion: the shim restores from the captured
            # originals so the transport never loses a constant.
            del host_chat_module._SESSION_RESET_SECONDS
        assert engine_ws._SESSION_RESET_SECONDS == original

    def test_non_proxied_assignment_stays_local(self):
        # Anything outside the proxied set must not leak into the transport.
        host_chat_module._NEW_TEST_ATTRIBUTE = object()
        try:
            assert host_chat_module._NEW_TEST_ATTRIBUTE is not None
            assert not hasattr(engine_ws, "_NEW_TEST_ATTRIBUTE")
        finally:
            del host_chat_module._NEW_TEST_ATTRIBUTE