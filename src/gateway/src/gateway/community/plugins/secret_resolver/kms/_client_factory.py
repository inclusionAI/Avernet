"""Lazy Aliyun KMS client factory.

Builds a lightweight HTTP-based Aliyun KMS client (see :mod:`._client`) that
talks to the KMS RPC endpoint directly. Unlike the official
``alibabacloud-kms20160120`` SDK, it does not depend on the
``alibabacloud-tea-openapi`` / ``alibabacloud-credentials`` stack, so the
gateway does not inherit an ``aiofiles<25`` pin that conflicts with other
dependencies. Mirrors ``secbaas.community.plugins.secret.kms._client_factory``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ._client import KmsClient

if TYPE_CHECKING:
    from ._config import KmsSecretResolverConfig


class KmsClientProvider(Protocol):
    """Protocol for objects able to build an Aliyun KMS client.

    Satisfying this lazily defers client construction until first use, which
    keeps the resolver mockable in tests.
    """

    def get_client(self) -> Any:
        """Return a lazily-constructed Aliyun KMS client."""
        ...


class AliyunKmsClientFactory:
    """Builds a lightweight HTTP KMS client from a KMS config object.

    Construction is deferred until ``get_client`` is first called so unit tests
    never perform network or client setup. The config object exposes
    ``endpoint``, ``region_id``, ``access_key_id`` and ``access_key_secret``
    attributes (a :class:`KmsSecretResolverConfig`).
    """

    def __init__(self, config: KmsSecretResolverConfig) -> None:
        self._config = config
        self._client: Any | None = None

    def get_client(self) -> Any:
        """Return (and cache) the Aliyun KMS client."""
        if self._client is not None:
            return self._client
        self._client = self._build()
        return self._client

    def _build(self) -> KmsClient:
        return KmsClient(
            access_key_id=self._config.access_key_id,
            access_key_secret=self._config.access_key_secret,
            endpoint=self._config.endpoint,
            region_id=self._config.region_id,
        )
