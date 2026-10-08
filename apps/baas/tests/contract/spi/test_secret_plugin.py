"""Conformance contract for SecretStorePlugin implementations."""

from __future__ import annotations

import pytest

from secbaas.community.plugins.secret.stub import StubSecretStorePlugin
from secbaas.community.spi.secret import SecretStorePlugin


class SecretStorePluginContract:
    """Abstract conformance contract for SecretStorePlugin implementations.

    Every SecretStorePlugin implementation must pass these tests.
    """

    plugin: SecretStorePlugin

    def test_get_secret_returns_value(self) -> None:
        value = self.plugin.get_secret("baas/app/plain")
        assert isinstance(value, str)
        assert value == "plain-value"

    def test_get_secret_not_found_raises(self) -> None:
        with pytest.raises(RuntimeError):
            self.plugin.get_secret("nonexistent")

    def test_get_kv_secret_returns_tuple(self) -> None:
        kv = self.plugin.get_kv_secret("baas/app/kv")
        assert isinstance(kv, tuple)
        assert len(kv) == 2

    def test_resolve_secret_resolves_at_prefix(self) -> None:
        assert self.plugin.resolve_secret("@baas/app/plain") == "plain-value"

    def test_resolve_secret_pass_through_non_at(self) -> None:
        assert self.plugin.resolve_secret("plain_value") == "plain_value"

    def test_resolve_common_sm4_key_returns_str(self) -> None:
        assert isinstance(self.plugin.resolve_common_sm4_key(), str)

    def test_generate_proxy_token_jwt_shape(self) -> None:
        token = self.plugin.generate_proxy_token("target")
        assert isinstance(token, str)
        assert len(token.split(".")) == 3


class TestStubSecretStorePlugin(SecretStorePluginContract):
    def setup_method(self) -> None:
        self.plugin = StubSecretStorePlugin(
            secrets={"baas/app/plain": "plain-value"},
            kv_secrets={"baas/app/kv": ("user", "pass")},
        )
