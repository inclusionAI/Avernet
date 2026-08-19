"""AliyunKmsSecretResolver — Aliyun KMS-backed gateway SecretResolver plugin.

Implements :class:`~gateway.community.spi.secret_resolver.SecretResolver` using
Aliyun KMS managed secrets as the credential source. Selected via
``plugins.secret = kms``.

Preserves the SPI's duck-typed shape: :meth:`get_secret` returns an object
exposing ``.secret_user`` / ``.secret_value`` when the secret exists, and
``None`` when Aliyun KMS reports the secret does not exist. Genuine transport,
authorization and parsing errors are **not** swallowed — they propagate so
callers that need a fallback wrap the call in ``try/except``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from gateway.community.spi.secret_resolver import SecretResolver

from ._client import KmsGetSecretValueRequest, KmsSecretNotFoundError
from ._client_factory import KmsClientProvider

if TYPE_CHECKING:
    from ._config import KmsSecretResolverConfig


@dataclass(frozen=True)
class _KmsSecret:
    """Resolved secret — the ``.secret_user`` / ``.secret_value`` surface
    consumers read off the credential object."""

    secret_user: str
    secret_value: str


class AliyunKmsSecretResolver(SecretResolver):
    """Resolve a named secret from Aliyun KMS.

    Args:
        config: KMS connection configuration (endpoint/region, access
            credentials) with already-resolved values. Accepts a
            :class:`KmsSecretResolverConfig` or a plain mapping of its fields
            (as provided by the DI container).
        client_factory: Lazy provider of the Aliyun KMS client; defaults to a
            factory built from ``config``. Injectable for tests.
    """

    def __init__(
        self,
        config: KmsSecretResolverConfig | dict[str, Any],
        client_factory: KmsClientProvider | None = None,
    ) -> None:
        if isinstance(config, dict):
            from ._config import KmsSecretResolverConfig

            config = KmsSecretResolverConfig(**config)
        self._config = config
        self._validate_config()
        if client_factory is None:
            from ._client_factory import AliyunKmsClientFactory

            client_factory = AliyunKmsClientFactory(config)
        self._client_factory = client_factory

    def _validate_config(self) -> None:
        if not self._config.endpoint or not self._config.region_id:
            raise ValueError(
                "Aliyun KMS secret resolver requires endpoint and region_id"
            )
        if not self._config.access_key_id or not self._config.access_key_secret:
            raise ValueError(
                "Aliyun KMS secret resolver requires access_key_id and "
                "access_key_secret"
            )

    def _kms_secret_name(self, secret_name: str) -> str:
        prefix = self._config.secret_name_prefix
        if prefix:
            return f"{prefix}{secret_name}"
        return secret_name

    def get_secret(self, secret_name: str) -> _KmsSecret | None:
        kms_name = self._kms_secret_name(secret_name)
        request = KmsGetSecretValueRequest(secret_name=kms_name)
        client = self._client_factory.get_client()
        try:
            response = client.get_secret_value(request)
        except KmsSecretNotFoundError:
            # Absent — KMS says the secret does not exist ⇒ None, preserving the
            # SPI's "absent ⇒ None" contract.
            return None
        body = getattr(response, "body", response)
        secret_data = getattr(body, "secret_data", "")
        if secret_data is None:
            return None
        # KMS stores a plain secret value; the ``_USER`` half is unspecified and
        # defaults to ``""`` (token-only secrets resolve with user empty).
        return _KmsSecret(secret_user="", secret_value=secret_data)
