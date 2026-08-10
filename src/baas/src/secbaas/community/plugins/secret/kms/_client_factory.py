"""Lazy Aliyun KMS client factory.

Isolates all Alibaba Cloud SDK construction and imports behind a single
callable so that the SDK is only imported when the ``kms`` secret plugin is
actually selected and a client is needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ._config import KmsSecretStoreConfig


class KmsClientProvider(Protocol):
    """Protocol for objects able to build an Aliyun KMS client.

    Satisfying this lazily defers the underlying SDK import and client
    construction until first use, which keeps the plugin mockable in tests.
    """

    def get_client(self) -> Any:
        """Return a lazily-constructed Aliyun KMS client."""
        ...


class AliyunKmsClientFactory:
    """Builds the Aliyun KMS ``Client`` from :class:`KmsSecretStoreConfig`.

    The real Alibaba Cloud SDK is imported and instantiated lazily so unit
    tests never pull the network stack, and construction only happens when
    the ``kms`` secret plugin is actually used.
    """

    def __init__(self, config: KmsSecretStoreConfig) -> None:
        self._config = config
        self._client: Any | None = None

    def get_client(self) -> Any:
        """Return (and cache) the Aliyun KMS client.

        Raises:
            RuntimeError: If the Aliyun KMS SDK is unavailable.
        """
        if self._client is not None:
            return self._client
        self._client = self._build()
        return self._client

    def _build(self) -> Any:
        try:
            from alibabacloud_kms20160120.client import Client
            from alibabacloud_tea_openapi.models import Config
        except ImportError as exc:  # pragma: no cover - depends on SDK install
            raise RuntimeError(
                "Aliyun KMS SDK is not installed; add alibabacloud-kms20160120"
            ) from exc

        config = Config(
            access_key_id=self._config.access_key_id,
            access_key_secret=self._config.access_key_secret,
            endpoint=self._config.endpoint,
            region_id=self._config.region_id,
        )
        return Client(config)
