"""EnvSecretResolver — environment-variable-backed SecretResolver.

Mirrors the BaaS ``secbaas.community.plugins.secret.env.EnvSecretStorePlugin``
``get from env`` behaviour: a secret resolves from its prefixed environment
variable. Unlike BaaS's un-prefixed lookup, the gateway resolver reads
``{env_prefix}{NAME}_VALUE`` so deployments can namespace gateway secrets
without changing the whole process environment.

Only the ``get_secret`` surface is provided — the wider BaaS
``SecretStorePlugin`` methods (``resolve_secret``, ``get_kv_secret``,
``generate_proxy_token``, ``resolve_common_sm4_key``, ``close``) are not needed
by the gateway and are not part of its SPI.
"""

from __future__ import annotations

import os

from gateway.community.logger import get_logger
from gateway.community.spi.secret_resolver import SecretResolver

logger = get_logger("secret_resolver")


class EnvSecretResolver(SecretResolver):
    """Resolve a named secret from a prefixed environment variable.

    Args:
        env_prefix: Prefix prepended to the ``{NAME}_VALUE`` lookup, e.g.
            ``AVERNET_SECRET_``.
    """

    def __init__(self, *, env_prefix: str = "AVERNET_SECRET_") -> None:
        self._prefix = env_prefix
        logger.info("EnvSecretResolver initialized")

    def get_secret(self, secret_name: str) -> str:
        norm = secret_name.upper().replace("-", "_")
        value = os.environ.get(f"{self._prefix}{norm}_VALUE")
        if value is None:
            raise RuntimeError(f"Secret not found in env: {secret_name}")
        return value
