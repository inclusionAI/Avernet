"""Composition root for the PrincipalSigner (auth design §7.1).

Builds the bare (HMAC) signer from env. The adapter receives the built signer
via ``app.state.principal_signer`` and calls it at the forwarder seam.
"""

from __future__ import annotations

from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    load_signer_config,
)
from gateway.community.spi.principal_signer import PrincipalSigner


def build_principal_signer() -> PrincipalSigner:
    """Build the PrincipalSigner from env (bare HMAC flavor)."""
    return BarePrincipalSigner(load_signer_config())
