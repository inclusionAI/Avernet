"""Conformance contract for SecretStorePlugin implementations."""

from __future__ import annotations

from typing import Any

import pytest

from secbaas.community.plugins.secret import AliyunKmsSecretStorePlugin
from secbaas.community.plugins.secret.kms import KmsSecretStoreConfig
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


class TestAliyunKmsSecretStorePluginConformance(SecretStorePluginContract):
    def setup_method(self) -> None:
        self.plugin = AliyunKmsSecretStorePlugin(
            KmsSecretStoreConfig(
                endpoint="kms.cn-hangzhou.aliyuncs.com",
                region_id="cn-hangzhou",
                access_key_id="LTAI-test",
                access_key_secret="secret-test",
                sm4_key_secret_name="baas/chain/sm4",
                proxypass_secret_name="baas/proxypass",
            ),
            client_factory=_MockFactory(
                {
                    "baas/app/plain": "plain-value",
                    "baas/app/kv": "user:pass",
                    "baas/chain/sm4": "cmF3c2tleQ==",
                    "baas/proxypass": "proxypass-secret",
                }
            ),
        )


class _MockBody:
    def __init__(self, secret_data: str) -> None:
        self.secret_data = secret_data


class _MockResponse:
    body: Any

    def __init__(self, secret_data: str) -> None:
        self.body = _MockBody(secret_data)


class _MockClient:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get_secret_value(self, request: Any) -> _MockResponse:
        name = request.secret_name
        if name not in self._values:
            raise RuntimeError(f"secret {name} not found")
        return _MockResponse(self._values[name])


class _MockFactory:
    def __init__(self, values: dict[str, str]) -> None:
        self._client = _MockClient(values)

    def get_client(self) -> _MockClient:
        return self._client
