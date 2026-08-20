"""SPI conformance contract tests for the env (env-backed) secret resolver.

Matches the gateway ``SecretResolver`` SPI contract: ``get_secret(name)`` reads
a named secret from a prefixed environment variable and returns the plain-text
string value, raising ``RuntimeError`` when absent. The wider BaaS
``SecretStorePlugin`` surface (``resolve_secret``, ``get_kv_secret``,
``generate_proxy_token``, ``resolve_common_sm4_key``) is intentionally not part
of the gateway SPI and is not tested here.
"""

from __future__ import annotations

import pytest

from gateway.community.plugins.secret_resolver.env import EnvSecretResolver
from gateway.community.spi.secret_resolver import SecretResolver

_PREFIX = "AVERNET_SECRET_"


def _resolver() -> EnvSecretResolver:
    return EnvSecretResolver(env_prefix=_PREFIX)


class EnvSecretResolverContract:
    """Conformance contract every SecretResolver satisfies."""

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


class TestEnvSecretResolverContract(EnvSecretResolverContract):
    """Concrete run of the contract against the env (env-backed) resolver."""
