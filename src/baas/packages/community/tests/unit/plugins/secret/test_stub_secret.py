"""Unit tests for StubSecretStorePlugin."""

import pytest

from secbaas.plugins.secret.stub import StubSecretStorePlugin


@pytest.fixture
def plugin() -> StubSecretStorePlugin:
    return StubSecretStorePlugin(
        secrets={"my.secret_name": "secret-value"},
        kv_secrets={"my.kv_secret": ("kv-user", "kv-value")},
    )


def test_import(plugin: StubSecretStorePlugin) -> None:
    assert isinstance(plugin, StubSecretStorePlugin)


def test_get_secret_returns_value(plugin: StubSecretStorePlugin) -> None:
    assert plugin.get_secret("my.secret_name") == "secret-value"


def test_get_secret_not_found_raises(plugin: StubSecretStorePlugin) -> None:
    with pytest.raises(RuntimeError, match="not found"):
        plugin.get_secret("nonexistent")


def test_get_kv_secret_returns_tuple(plugin: StubSecretStorePlugin) -> None:
    assert plugin.get_kv_secret("my.kv_secret") == ("kv-user", "kv-value")


def test_get_kv_secret_not_found_raises(plugin: StubSecretStorePlugin) -> None:
    with pytest.raises(RuntimeError, match="not found"):
        plugin.get_kv_secret("nonexistent")


def test_resolve_secret_resolves_at_prefix(plugin: StubSecretStorePlugin) -> None:
    assert plugin.resolve_secret("@my.secret_name") == "secret-value"


def test_resolve_secret_pass_through_non_at(plugin: StubSecretStorePlugin) -> None:
    assert plugin.resolve_secret("plain_value") == "plain_value"


def test_resolve_secret_empty_string(plugin: StubSecretStorePlugin) -> None:
    assert plugin.resolve_secret("") == ""


def test_generate_proxy_token_non_empty(plugin: StubSecretStorePlugin) -> None:
    token = plugin.generate_proxy_token("ARCA_sandbox-123")
    assert isinstance(token, str)
    assert len(token) > 0
    parts = token.split(".")
    assert len(parts) == 3


def test_generate_proxy_token_custom_ttl(plugin: StubSecretStorePlugin) -> None:
    token = plugin.generate_proxy_token("ARCA_test", ttl_seconds=60)
    assert isinstance(token, str)
    assert len(token.split(".")) == 3


def test_set_secret_convenience() -> None:
    plugin = StubSecretStorePlugin()
    plugin.set_secret("dynamic", "val")
    assert plugin.get_secret("dynamic") == "val"


def test_set_kv_secret_convenience() -> None:
    plugin = StubSecretStorePlugin()
    plugin.set_kv_secret("dynamic_kv", "user1", "pass1")
    assert plugin.get_kv_secret("dynamic_kv") == ("user1", "pass1")
