"""EnvSecretResolver — environment-variable-backed SecretResolver.

Mirrors the BaaS ``secbaas.community.plugins.secret.env.EnvSecretStorePlugin``
contract: secrets resolve from ``os.environ``, ``@name`` references resolve
through :meth:`resolve_secret`, and the common SM4 key / proxy token come from
env with deterministic dev fallbacks. Unlike BaaS's un-prefixed lookup, the
gateway resolver reads ``{env_prefix}{NAME}_VALUE`` / ``{env_prefix}{NAME}_USER``
so deployments can namespace gateway secrets without changing the whole process
environment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

from gateway.community.logger import get_logger
from gateway.community.spi.secret_resolver import DEV_SM4_KEY, SecretResolver

logger = get_logger("secret_resolver")


class EnvSecretResolver(SecretResolver):
    """Resolve gateway secrets from prefixed environment variables.

    Args:
        env_prefix: Prefix prepended to ``{NAME}_VALUE`` / ``{NAME}_USER``
            lookups, e.g. ``AVERNET_SECRET_``.
        sm4_key_env: Env var name for the common SM4 key. If unset, falls back
            to ``DEV_SM4_KEY``.
        proxypass_secret_env: Env var name for the proxy signing secret.
    """

    def __init__(
        self,
        *,
        env_prefix: str = "AVERNET_SECRET_",
        sm4_key_env: str = "AVERNET_SECRET_SM4_KEY",
        proxypass_secret_env: str = "AVERNET_SECRET_PROXYPASS_SECRET",
    ) -> None:
        self._prefix = env_prefix
        self._sm4_key_env = sm4_key_env
        self._proxypass_secret_env = proxypass_secret_env
        logger.info("EnvSecretResolver initialized")

    def get_secret(self, secret_name: str) -> str:
        norm = secret_name.upper().replace("-", "_")
        value = os.environ.get(f"{self._prefix}{norm}_VALUE")
        if value is None:
            raise RuntimeError(f"Secret not found in env: {secret_name}")
        return value

    def get_kv_secret(self, secret_name: str) -> tuple[str, str]:
        norm = secret_name.upper().replace("-", "_")
        raw = os.environ.get(f"{self._prefix}{norm}_VALUE")
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
