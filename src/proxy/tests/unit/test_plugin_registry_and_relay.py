"""Unit tests for plugin_registry injection and relay cleanup loop."""

from __future__ import annotations

import pytest


class TestPluginRegistryInject:
    def test_inject_selector(self) -> None:
        from sandboxproxy.community import plugin_registry
        from sandboxproxy.community.plugins.resolver.stub import StubTargetResolver

        plugin_registry.register_plugin_option(
            "resolver", "extra_resolver", StubTargetResolver
        )

        class FakeSelector:
            def __init__(self) -> None:
                self.providers: dict = {}
                self.set_calls = 0

            def set_providers(self, **kwargs) -> None:
                self.providers.update(kwargs)
                self.set_calls += 1

        selector = FakeSelector()

        class FakePluginContainer:
            resolver = selector

        class FakeContainer:
            def plugins(self):
                return FakePluginContainer()

        plugin_registry.inject_into_plugin_container(FakeContainer())
        assert "extra_resolver" in selector.providers
        assert "stub" == StubTargetResolver.prefix


class TestRelayCleanupLoop:
    @pytest.mark.asyncio
    async def test_cleanup_loop_evicts_expired(self, monkeypatch) -> None:
        import asyncio

        from sandboxproxy.community.core.relay import RelayServer
        from sandboxproxy.community.plugins.relay_client.stub import StubRelayClient

        relay = RelayServer(StubRelayClient(), wait_timeout=0.01, cleanup_interval=0.01)
        await relay.start()
        await relay.register_mng("sess-expiring")
        await asyncio.sleep(0.05)
        await relay.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_cleanup(self) -> None:
        from sandboxproxy.community.core.relay import RelayServer
        from sandboxproxy.community.plugins.relay_client.stub import StubRelayClient

        relay = RelayServer(StubRelayClient(), wait_timeout=30.0)
        await relay.start()
        await relay.shutdown()
        await relay.shutdown()  # idempotent


class TestRelayServerWait:
    @pytest.mark.asyncio
    async def test_wait_for_client_and_close(self) -> None:
        from sandboxproxy.community.core.relay import RelayServer
        from sandboxproxy.community.plugins.relay_client.stub import StubRelayClient

        relay = RelayServer(StubRelayClient(), wait_timeout=30.0)
        await relay.start()
        await relay.register_mng("sess-1")
        fut = await relay.wait_for_client("sess-1")
        assert fut is not None
        await relay.close_session("sess-1")
        await relay.shutdown()
