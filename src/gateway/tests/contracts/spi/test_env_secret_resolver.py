"""SPI conformance contract tests for the env (env-backed) secret resolver.

Matches the gateway ``SecretResolver`` SPI contract (BaaS-aligned ``env``
flavor): ``get_secret(name)`` returns the plain-text string value, raising
``RuntimeError`` when absent; ``resolve_secret`` resolves ``@`` references;
``get_kv_secret`` parses a key/value pair. The env resolver reads from
prefixed environment variables.
"""

from __future__ import annotations

import pytest

from gateway.community.plugins.secret_resolver.env import EnvSecretResolver
from gateway.community.spi.secret_resolver import DEV_SM4_KEY, SecretResolver

_PREFIX = "AVERNET_SECRET_"


def _resolver() -> EnvSecretResolver:
    return EnvSecretResolver(env_prefix=_PREFIX)


class EnvSecretResolverContract:
    """Conformance contract every BaaS-aligned SecretResolver satisfies."""

    def setup_method(self) -> None:
        self.resolver = _resolver()

    def test_resolver_implements_secret_resolver_protocol(self) -> None:
        assert isinstance(self.resolver, SecretResolver)

    def test_get_existing_secret_returns_plain_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{_PREFIX}TEST_SECRET_VALUE", "signing-key")
        assert self.resolver.get_secret("test-secret") == "signing-key"

    def test_get_secret_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(f"{_PREFIX}TEST_MISSING_VALUE", raising=False)
        with pytest.raises(RuntimeError):
            self.resolver.get_secret("test-missing")

    def test_resolve_secret_passes_through_plain_values(self) -> None:
        assert self.resolver.resolve_secret("redis://localhost") == "redis://localhost"

    def test_resolve_secret_resolves_at_reference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{_PREFIX}REDIS_PASSWORD_VALUE", "pw")
        assert self.resolver.resolve_secret("@redis-password") == "pw"

    def test_get_kv_secret_returns_key_value_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(f"{_PREFIX}KV_PAIR_VALUE", '{"key": "k", "value": "v"}')
        assert self.resolver.get_kv_secret("kv-pair") == ("k", "v")

    def test_resolve_common_sm4_key_falls_back_to_dev_key(self) -> None:
        assert self.resolver.resolve_common_sm4_key() == DEV_SM4_KEY


class TestEnvSecretResolverContract(EnvSecretResolverContract):
    """Concrete run of the contract against the env (env-backed) resolver."""
