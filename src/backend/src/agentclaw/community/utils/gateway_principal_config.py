"""Environment-driven config for gateway principal verification.

A low-level config loader, deliberately shaped like ``utils/env_utils`` rather
than a DI-provided type. The consumer is ``resolve_avernet_tenant``, which
``AvernetTenantMiddleware`` calls from the raw ASGI layer *before* any route and
therefore outside the injector — so a DI-provided config could not reach it, and
importing the composition root from here would create a reverse dependency and a
cold-start cycle (the same reasoning ``utils/env_utils`` documents).

Environment (the names are the gateway's — one shared vocabulary for one shared
secret, see its ``plugins/principal_signer/bare/_plugin.py``):

- ``AVERNET_PRINCIPAL_SIGNING_KEY`` — the HMAC secret shared with the gateway.
  **Unset means the public surface answers 401 to everything**, which is exactly
  the pre-auth state this replaces. Unlike the gateway's ``bare`` signer we ship
  **no dev fallback key**: a committed shared secret is a committed credential,
  and here the failure mode of not having one is safe (deny) rather than
  inconvenient. Single-box sets the same value on both sides. Use at least 32
  bytes — RFC 7518 §3.2 for SHA-256, and PyJWT warns below it.
- ``AVERNET_PRINCIPAL_AUDIENCE`` — the ``aud`` we accept; defaults to
  ``backend``, this component's name under ``servers:`` in the gateway's
  ``upstreams.yaml``.
- ``AVERNET_PRINCIPAL_ISSUER`` — the ``iss`` we accept; defaults to ``gateway``,
  the signer's own default.

Read once per process and cached: the values are deployment configuration, and a
per-request ``os.environ`` read on the hot path buys nothing.
"""

from __future__ import annotations

import os
from functools import lru_cache

from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig

_KEY_ENV = "AVERNET_PRINCIPAL_SIGNING_KEY"
_AUDIENCE_ENV = "AVERNET_PRINCIPAL_AUDIENCE"
_ISSUER_ENV = "AVERNET_PRINCIPAL_ISSUER"

_DEFAULT_AUDIENCE = "backend"
_DEFAULT_ISSUER = "gateway"


@lru_cache(maxsize=1)
def get_principal_verifier_config() -> PrincipalVerifierConfig:
    """Return the process-wide verifier config, read from the environment."""
    return PrincipalVerifierConfig(
        signing_key=os.getenv(_KEY_ENV, "").strip(),
        audience=os.getenv(_AUDIENCE_ENV, "").strip() or _DEFAULT_AUDIENCE,
        issuer=os.getenv(_ISSUER_ENV, "").strip() or _DEFAULT_ISSUER,
    )


def reset_principal_verifier_config_cache() -> None:
    """Drop the cached config so a test can change the environment.

    Production never calls this — the config is fixed for a process lifetime.
    """
    get_principal_verifier_config.cache_clear()
