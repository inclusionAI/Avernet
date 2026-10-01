"""Unit tests for EnvSecretStorePlugin."""

from __future__ import annotations

import pytest

from secbaas.community.plugins.secret.env import EnvSecretStorePlugin


@pytest.fixture
def plugin(monkeypatch) -> EnvSecretStorePlugin:
    monkeypatch.setenv("MY_SECRET", "secret-value")
    monkeypatch.setenv("MY_KV_SECRET", '{"key": "user", "value": "pass"}')
    return EnvSecretStorePlugin()


class TestEnvSecretStorePlugin:
    def test_get_secret(self, plugin: EnvSecretStorePlugin) -> None:
        assert plugin.get_secret("MY_SECRET") == "secret-value"

    def test_get_secret_missing_raises(self, plugin: EnvSecretStorePlugin) -> None:
        with pytest.raises(RuntimeError, match="not found in env"):
            plugin.get_secret("NONEXISTENT")

    def test_get_kv_secret(self, plugin: EnvSecretStorePlugin) -> None:
        key, value = plugin.get_kv_secret("MY_KV_SECRET")
        assert key == "user"
        assert value == "pass"

    def test_get_kv_secret_missing_raises(self, plugin: EnvSecretStorePlugin) -> None:
        with pytest.raises(RuntimeError, match="not found in env"):
            plugin.get_kv_secret("NONEXISTENT")

    def test_get_kv_secret_malformed_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("BAD_KV", "not-json")
        plugin = EnvSecretStorePlugin()
        with pytest.raises(RuntimeError, match="Malformed KV secret"):
            plugin.get_kv_secret("BAD_KV")

    def test_resolve_secret_with_at_prefix(self, plugin: EnvSecretStorePlugin) -> None:
        assert plugin.resolve_secret("@MY_SECRET") == "secret-value"

    def test_resolve_secret_plain_value(self, plugin: EnvSecretStorePlugin) -> None:
        assert plugin.resolve_secret("plain-value") == "plain-value"

    def test_resolve_secret_empty(self, plugin: EnvSecretStorePlugin) -> None:
        assert plugin.resolve_secret("") == ""

    def test_resolve_common_sm4_key_fallback(self, monkeypatch) -> None:
        monkeypatch.delenv("SECBAAS_SM4_KEY", raising=False)
        plugin = EnvSecretStorePlugin()
        key = plugin.resolve_common_sm4_key()
        assert len(key) > 0  # DEV_SM4_KEY fallback

    def test_resolve_common_sm4_key_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SECBAAS_SM4_KEY", "base64key==")
        plugin = EnvSecretStorePlugin()
        assert plugin.resolve_common_sm4_key() == "base64key=="

    def test_generate_proxy_token(self, plugin: EnvSecretStorePlugin) -> None:
        token = plugin.generate_proxy_token("test-target", ttl_seconds=60)
        parts = token.split(".")
        assert len(parts) == 3  # header.payload.signature
