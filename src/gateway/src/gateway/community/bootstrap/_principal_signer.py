"""Composition root for the PrincipalSigner (auth design §7.1).

Builds the bare (HMAC) signer from typed config: non-secret parameters
(``kid`` / ``issuer`` / ``ttl_seconds``) come from ``user_config.principal_signer``
and the signing key is resolved via the :class:`SecretResolver` SPI. The
``SecretResolver`` implementation is selected by the ``plugins.secret`` config
selector (community = env-backed; enterprise may register a corp/KMS-backed
option via ``plugin_registry``), so the resolver is resolved from the DI
container and passed in — this module never constructs a concrete flavor. The
adapter receives the built signer via ``app.state.principal_signer`` and calls
it at the forwarder seam.
"""

from __future__ import annotations

import os

from gateway.community.config import UserConfig
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    load_signer_config,
)
from gateway.community.spi.principal_signer import PrincipalSigner
from gateway.community.spi.secret_resolver import SecretResolver

# Deployment profiles that must have a real signing key. Read from the same
# ``SERVER_ENV`` the config loader reads and gated on the same two values the
# backend gates its verifier on, so one contract has one rule rather than a
# per-side interpretation of it.
_STRICT_ENVS = ("pre", "prod")


def build_principal_signer(
    *,
    user_config: UserConfig,
    secret_resolver: SecretResolver,
) -> PrincipalSigner:
    """Build the PrincipalSigner from typed config + a resolved SecretResolver.

    Reads ``SERVER_ENV`` to decide whether a missing key is fatal. The
    environment read belongs here rather than in the plugin: this is the
    composition root, and the plugin stays a pure function of the arguments it
    is handed.

    Raises:
        PrincipalSigningKeyMissingError: in ``pre``/``prod`` with no key.
    """
    return BarePrincipalSigner(
        load_signer_config(
            user_config.principal_signer,
            secret_resolver,
            strict=os.getenv("SERVER_ENV", "").strip() in _STRICT_ENVS,
        )
    )
