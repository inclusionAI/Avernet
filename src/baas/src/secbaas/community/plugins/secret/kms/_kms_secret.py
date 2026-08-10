"""Aliyun KMS-backed security key management plugin.

Implements :class:`~secbaas.community.spi.secret.SecretStorePlugin` using
Aliyun KMS managed secrets as the source of security keys/secrets. Selected
via ``plugins.secret = aliyun_kms``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64encode
from typing import TYPE_CHECKING, Any

from secbaas.community.spi.secret import SecretStorePlugin

from ._client_factory import KmsClientProvider

if TYPE_CHECKING:
    from ._config import KmsSecretStoreConfig


class AliyunKmsSecretStorePlugin(SecretStorePlugin):
    """Secret/key management backed by Aliyun KMS.

    Secrets are retrieved from Aliyun KMS managed secrets. Protocol secret
    names are resolved to KMS secret names via an optional
    ``secret_name_prefix`` plus the direct name. The common SM4 key, proxy
    auth token signing secret, and admin token are resolved from dedicated
    KMS secrets named in the configuration.

    Args:
        config: KMS plugin configuration (endpoint/region, credentials,
            KMS secret names). Accepts a :class:`KmsSecretStoreConfig` or a
            plain mapping of its fields (as provided by the DI container).
        client_factory: Lazy provider of the Aliyun KMS client; defaults to
            a factory built from ``config``. Injectable for tests.
    """

    def __init__(
        self,
        config: KmsSecretStoreConfig | dict[str, Any],
        client_factory: KmsClientProvider | None = None,
    ) -> None:
        if isinstance(config, dict):
            from ._config import KmsSecretStoreConfig

            config = KmsSecretStoreConfig(**config)
        self._config = config
        self._validate_config()
        if client_factory is None:
            from ._client_factory import AliyunKmsClientFactory

            client_factory = AliyunKmsClientFactory(config)
        self._client_factory = client_factory

    def _validate_config(self) -> None:
        if not self._config.endpoint or not self._config.region_id:
            raise ValueError("Aliyun KMS secret plugin requires endpoint and region_id")
        if not self._config.access_key_id or not self._config.access_key_secret:
            raise ValueError(
                "Aliyun KMS secret plugin requires access_key_id and access_key_secret"
            )

    def _kms_secret_name(self, secret_name: str) -> str:
        prefix = self._config.secret_name_prefix
        if prefix:
            return f"{prefix}{secret_name}"
        return secret_name

    def _get_via_kms(self, secret_name: str) -> str:
        """Retrieve a plain secret value from KMS, or raise RuntimeError."""
        try:
            from alibabacloud_kms20160120 import models
        except ImportError as exc:  # pragma: no cover - SDK present at runtime
            raise RuntimeError(
                "Aliyun KMS SDK is not installed; add alibabacloud-kms20160120"
            ) from exc

        kms_name = self._kms_secret_name(secret_name)
        request = models.GetSecretValueRequest(secret_name=kms_name)
        client = self._client_factory.get_client()
        try:
            response = client.get_secret_value(request)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to retrieve KMS secret {kms_name}: {exc}"
            ) from exc
        body = response.body
        if body is None or getattr(body, "secret_data", "") is None:
            raise RuntimeError(f"KMS secret {kms_name} was not found")
        return getattr(body, "secret_data", "") or ""

    def get_secret(self, secret_name: str) -> str:
        return self._get_via_kms(secret_name)

    def get_kv_secret(self, secret_name: str) -> tuple[str, str]:
        value = self._get_via_kms(secret_name)
        parts = value.split(":", 1)
        if len(parts) != 2:
            raise RuntimeError(f"KV secret {secret_name} is malformed")
        return parts[0], parts[1]

    def resolve_secret(self, raw_value: str) -> str:
        if not raw_value:
            return raw_value
        if raw_value.startswith("@"):
            return self.get_secret(raw_value[1:])
        return raw_value

    def resolve_common_sm4_key(self) -> str:
        if not self._config.sm4_key_secret_name:
            raise RuntimeError("sm4_key_secret_name is not configured for KMS plugin")
        return self._get_via_kms(self._config.sm4_key_secret_name)

    def generate_proxy_token(self, target: str, ttl_seconds: int | None = None) -> str:
        secret_key = self._resolve_proxy_secret()
        ttl = ttl_seconds if ttl_seconds is not None else 300
        header_b64 = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = json.dumps({"target": target, "exp": int(time.time()) + ttl})
        payload_b64 = _b64url(payload.encode())
        signing_input = f"{header_b64}.{payload_b64}"
        signature = _b64url(
            hmac.new(
                secret_key.encode(), signing_input.encode(), hashlib.sha256
            ).digest()
        )
        return f"{header_b64}.{payload_b64}.{signature}"

    def _resolve_proxy_secret(self) -> str:
        if not self._config.proxypass_secret_name:
            raise RuntimeError("proxypass_secret_name is not configured for KMS plugin")
        return self._get_via_kms(self._config.proxypass_secret_name)

    def close(self) -> None:
        pass


def _b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()
