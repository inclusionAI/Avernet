from __future__ import annotations

from engine.community.plugin_api.openclaw import OpenClawPlugin
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl, __all__
from engine.community.plugins.openclaw.token_pool import TokenClientPool


def test_plugin_impl_exports_and_constructs_with_default_pool():
    plugin = OpenClawPluginImpl()

    assert isinstance(plugin, OpenClawPlugin)
    assert isinstance(plugin.pool, TokenClientPool)
    assert plugin._model_provider_map is None
    assert "OpenClawPluginImpl" in __all__


def test_plugin_impl_accepts_injected_client_and_pool():
    class Client:
        connected = True

    pool = TokenClientPool()
    client = Client()
    plugin = OpenClawPluginImpl(client=client, pool=pool)

    assert plugin.pool is pool
    assert plugin._client is client
