"""Environment variable secret plugin — reads secrets from os.environ.

A lightweight SecretStorePlugin that resolves secrets from environment
variables. Suitable for local development, singlebox deployments, and
container environments where secrets are injected via env vars.

The ``@secret_name`` convention maps directly to ``os.environ["secret_name"]``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

from secbaas.community.logger import get_logger
from secbaas.community.spi.secret import DEV_SM4_KEY, SecretStorePlugin

logger = get_logger("secret")


class EnvSecretStorePlugin(SecretStorePlugin):
    """Secret store backed by environment variables.

    Args:
        sm4_key_env: Env var name for the common SM4 key. If unset,
            falls back to ``DEV_SM4_KEY``.
        proxypass_secret_env: Env var name for the proxy signing secret.
    """

    def __init__(
        self,
        *,
        sm4_key_env: str = "SECBAAS_SM4_KEY",
        proxypass_secret_env: str = "SECBAAS_PROXYPASS_SECRET",
    ) -> None:
        self._sm4_key_env = sm4_key_env
        self._proxypass_secret_env = proxypass_secret_env
        logger.info("EnvSecretStorePlugin initialized")

    def get_secret(self, secret_name: str) -> str:
        value = os.environ.get(secret_name)
        if value is None:
            raise RuntimeError(f"Secret not found in env: {secret_name}")
        return value

    def get_kv_secret(self, secret_name: str) -> tuple[str, str]:
        raw = os.environ.get(secret_name)
        if raw is None:
            raise RuntimeError(f"KV secret not found in env: {secret_name}")
        try:
            data = json.loads(raw)
            return data["key"], data["value"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise RuntimeError(f"Malformed KV secret '{secret_name}': {e}") from e

    def resolve_secret(self, raw_value: str) -> str:
        if not raw_value:
            return raw_value
        if raw_value.startswith("@"):
            return self.get_secret(raw_value[1:])
        return raw_value

    def resolve_common_sm4_key(self) -> str:
        return os.environ.get(self._sm4_key_env, DEV_SM4_KEY)

    def generate_proxy_token(self, target: str, ttl_seconds: int | None = None) -> str:
        ttl = ttl_seconds if ttl_seconds is not None else 300
        secret_key = os.environ.get(self._proxypass_secret_env, "dev-proxypass-secret")

        header_b64 = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload_b64 = _b64url(
            json.dumps({"target": target, "exp": int(time.time()) + ttl}).encode()
        )
        signing_input = f"{header_b64}.{payload_b64}"
        sig = _b64url(
            hmac.new(
                secret_key.encode(), signing_input.encode(), hashlib.sha256
            ).digest()
        )
        return f"{header_b64}.{payload_b64}.{sig}"

    def close(self) -> None:
        pass


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
