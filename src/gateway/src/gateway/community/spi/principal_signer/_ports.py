"""Principal-signer SPI — sign the resolved identity set for a downstream audience.

The authn pipeline resolves a ``dict[PrincipalType, Principal]``; the gateway
signs that set into a short-lived token (auth design §7.1) so downstream
components can run ``auth.mode=none`` and merely verify the signature. Concrete
flavors (``bare`` HMAC, ``sofa`` asymmetric + KMS) implement this interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from gateway.community.spi.authn import Principal, PrincipalType


class PrincipalSigner(Protocol):
    """Sign the resolved identity set into a short-lived token for ``audience``.

    Returns a compact JWS (JWT) string. Implementations SHOULD NOT raise on
    normal input; a signing failure is a gateway-internal error the caller maps
    to a fail-closed response (do not forward). The returned token is the sole
    trust-bearer — components must never trust a bare ``X-Avernet-Principal``.
    """

    async def sign(
        self, principals: Mapping[PrincipalType, Principal], *, audience: str
    ) -> str: ...
