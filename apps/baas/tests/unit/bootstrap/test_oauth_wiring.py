"""Wiring tests: PluginConfig accepts 'oauth' and selector builds OAuthPlugin."""

import pytest
from pydantic import ValidationError

from secbaas.community.bootstrap._configs import PluginConfig
from secbaas.community.plugins.auth.oauth import OAuthPlugin


def test_plugin_config_accepts_oauth():
    cfg = PluginConfig(auth="oauth")
    assert cfg.auth == "oauth"


def test_plugin_config_rejects_unknown_auth():
    with pytest.raises(ValidationError):
        PluginConfig(auth="nope")


def test_selector_builds_oauth_auth_plugin(monkeypatch):
    # OAuthPlugin() -> OAuthClient() reads ConfigLoader; stub it.
    import secbaas.community.plugins.auth.oauth._oauth_client as client_mod

    fake_cfg = type("C", (), {"user_config": {}})()
    monkeypatch.setattr(client_mod.ConfigLoader, "load", lambda: fake_cfg)

    from secbaas.community.bootstrap.plugins import PluginContainer

    container = PluginContainer()
    container.config.from_dict({"plugins": {"auth": "oauth"}})
    plugin = container.auth_plugin()
    assert isinstance(plugin, OAuthPlugin)
