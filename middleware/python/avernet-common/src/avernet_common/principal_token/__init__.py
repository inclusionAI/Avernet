"""The HS256 envelope of the ``X-Avernet-Principal`` token (auth design §7.1).

One implementation of signing and verifying, shared by every Python component
on the contract, so that the pinned algorithm, the clock-skew leeway, the
required claims and the key fingerprint cannot drift between them. Issuer,
audience and key id are parameters: each hop names its own.

This package owns the envelope only. The ``principals`` claim is passed through
untouched in both directions; parsing it and deciding which callers to admit
belongs to the receiving service.
"""

from __future__ import annotations

from avernet_common.principal_token._errors import PrincipalTokenError
from avernet_common.principal_token._keys import (
    MIN_SIGNING_KEY_BYTES,
    is_weak_signing_key,
    key_fingerprint,
)
from avernet_common.principal_token._signing import (
    PrincipalSignerConfig,
    issue_principal_token,
    sign_principal_claims,
)
from avernet_common.principal_token._verification import (
    PrincipalVerifierConfig,
    decode_principal_token,
)

__all__ = [
    "MIN_SIGNING_KEY_BYTES",
    "PrincipalSignerConfig",
    "PrincipalTokenError",
    "PrincipalVerifierConfig",
    "decode_principal_token",
    "is_weak_signing_key",
    "issue_principal_token",
    "key_fingerprint",
    "sign_principal_claims",
]
