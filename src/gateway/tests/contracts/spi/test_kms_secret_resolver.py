"""SPI conformance contract tests for the Aliyun KMS secret resolver.

Matches the gateway ``SecretResolver`` SPI contract: ``get_secret(name)``
returns an object exposing ``.secret_user`` / ``.secret_value`` when the secret
exists, ``None`` when absent, and propagates (never swallows) transport errors.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.community.plugins.secret_resolver.kms import (
    AliyunKmsSecretResolver,
    KmsError,
    KmsSecretNotFoundError,
    KmsSecretResolverConfig,
)


class _FakeResponseBody:
    def __init__(self, secret_data: str | None) -> None:
        self.secret_data = secret_data


class _FakeResponse:
    def __init__(self, secret_data: str | None, use_none_body: bool = False) -> None:
        self.body = None if use_none_body else _FakeResponseBody(secret_data)


class _FakeClient:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get_secret_value(self, request: Any) -> _FakeResponse:
        name = request.secret_name
        if name not in self._values:
            raise KmsSecretNotFoundError(f"secret {name} not found")
        return _FakeResponse(self._values[name])


class _FakeFactory:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_client(self) -> Any:
        return self._client


class AliyunKmsSecretResolverContract:
    """Conformance contract every SecretResolver implementation satisfies."""

    def setup_method(self) -> None:
        self.resolver = AliyunKmsSecretResolver(
            KmsSecretResolverConfig(
                endpoint="kms.cn-hangzhou.aliyuncs.com",
                region_id="cn-hangzhou",
                access_key_id="LTAI-test",
                access_key_secret="secret-test",
            ),
            client_factory=_FakeFactory(_FakeClient({"gateway/key": "signing-key"})),
        )

    def test_get_existing_secret_exposes_secret_value(self) -> None:
        material = self.resolver.get_secret("gateway/key")
        assert material is not None
        assert material.secret_value == "signing-key"

    def test_get_existing_secret_exposes_duck_typed_shape(self) -> None:
        material = self.resolver.get_secret("gateway/key")
        assert hasattr(material, "secret_user")
        assert hasattr(material, "secret_value")

    def test_get_missing_secret_returns_none(self) -> None:
        assert self.resolver.get_secret("gateway/missing") is None


class TestAliyunKmsSecretResolverContract(AliyunKmsSecretResolverContract):
    """Concrete run of the contract against the Aliyun KMS resolver."""
