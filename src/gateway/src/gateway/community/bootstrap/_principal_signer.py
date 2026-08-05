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

from gateway.community.config import UserConfig
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    load_signer_config,
)
from gateway.community.spi.principal_signer import PrincipalSigner
from gateway.community.spi.secret_resolver import SecretResolver


def build_principal_signer(
    *,
    user_config: UserConfig,
    secret_resolver: SecretResolver,
) -> PrincipalSigner:
    """Build the PrincipalSigner from typed config + a resolved SecretResolver."""
    return BarePrincipalSigner(
        load_signer_config(user_config.principal_signer, secret_resolver)
    )
