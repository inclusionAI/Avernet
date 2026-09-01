"""Re-address a verified principal token to a second upstream.

The gateway mints one ``X-Avernet-Principal`` per hop: it signs the identity set
it resolved into a JWT whose ``aud`` names the upstream it is forwarding to, so
a token minted for ``backend`` verifies at the backend and **nowhere else** —
that is the property :mod:`.verifier` leans on when it refuses a token minted
for another component.

Which leaves a gap the moment a component has to call a second one *on the
caller's behalf*. The friend-approval flow is exactly that: the decision arrives
at ``/openapi/v1`` with a token addressed to ``backend``, and applying it means
calling BCN, which verifies the same shared key but requires ``aud=bcs`` (and
its own ``iss``/``kid`` — see ``src/bcs/api-contracts/v1/gateway-principal/
contract.md``). Forwarding the inbound header verbatim therefore fails at BCN's
audience check, every time, and no amount of retrying changes that.

This module re-addresses the token: same identities, same lifetime, new
envelope. What it deliberately does not do is *mint* one. The original must
verify first — signature, issuer, audience, expiry, and the full identity-set
admission — so the only thing this can produce is a re-addressed copy of a
credential the gateway already vouched for. A function that signed whatever
string it was handed would be a token-minting oracle for every upstream that
trusts the shared key, reachable by anyone who can reach the caller side of it.

What survives the re-addressing, and why:

- ``principals`` — untouched. It is the identity BCN authorizes against; the
  point of the exercise is that BCN sees the same caller we did.
- ``exp``/``iat`` — untouched, so the copy dies exactly when the original does.
  Re-stamping them would quietly turn a 60-second gateway credential into a
  fresh one on every hop, which is a lifetime extension nobody asked for.
- every other claim — untouched, because a claim we do not understand is one
  BCN might, and dropping it is a decision this module has no basis to make.
- ``iss``/``aud`` — replaced with what the target requires. These two *are* the
  re-addressing.
- ``kid`` — set from the target's config rather than copied, because it names
  the key the target should verify with, and the key is ours, not the inbound
  token's to assert.

Transport-agnostic like the rest of this package (Rule 7): no header parsing, no
environment reads, no secret store. ``utils/gateway_principal_config.py`` binds
the configs; ``core/work_orders/callbacks.py`` is the caller.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.verifier import (
    PrincipalVerifierConfig,
    caller_from_claims,
    decode_principal_token,
    key_fingerprint,
)

# Pinned for the same reason :mod:`.verifier` pins its accept list: every peer on
# this contract verifies HS256 with the shared key, and signing anything else
# would either be rejected or — worse, on a laxer verifier — accepted as an
# algorithm nobody agreed to.
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class PrincipalSignerConfig:
    """Where a re-addressed principal token is going, and what signs it.

    ``signing_key`` is the same HMAC secret :class:`PrincipalVerifierConfig`
    holds — one shared key across the gateway and every component on this
    contract, so re-signing needs no second credential. An empty key means the
    deployment resolved none, and :func:`resign_principal_token` then refuses to
    sign rather than emitting a token signed with nothing.

    ``audience``, ``issuer`` and ``key_id`` are the target's half of the
    contract: what *it* requires to accept a token, not what we require to
    accept one. They are fixed in code beside the verifier's own constants —
    see ``utils/gateway_principal_config.py`` for why that is a deliberate call
    rather than a missing knob.
    """

    signing_key: str
    audience: str
    issuer: str
    key_id: str

    @property
    def key_fingerprint(self) -> str:
        """This config's key as :func:`key_fingerprint` renders it for a log."""
        return key_fingerprint(self.signing_key)


def resign_principal_token(
    token: str,
    *,
    verifier: PrincipalVerifierConfig,
    signer: PrincipalSignerConfig,
) -> str:
    """Return ``token``'s claims re-addressed to ``signer``'s upstream.

    ``verifier`` is *this* component's config — the contract the inbound token
    must satisfy — and ``signer`` is the target's. Both are needed: the first
    decides whether there is anything worth re-addressing, the second decides
    what the re-addressed copy says.

    Raises:
        PrincipalVerificationError: when the inbound token does not verify (bad
            signature, expired, wrong audience, an identity set this surface
            would refuse) or when no signing key is configured on either side.
            One error type, as in :mod:`.errors`: a caller cannot act on the
            distinction, and the message carries it for the log.
    """
    if not signer.signing_key:
        # Same reading as an unconfigured verifier key, from the other side: we
        # cannot produce a credential the target will believe, and a token
        # signed with an empty key is not a lesser credential but a forgeable
        # one.
        raise PrincipalVerificationError(
            "no principal signing key is configured, so no token can be "
            f"re-addressed to aud={signer.audience!r}"
        )

    claims = decode_principal_token(token, verifier)
    # The result is deliberately dropped: this is the admission gate, not a
    # projection. Re-addressing an identity set this surface would answer 401
    # to would hand the target a caller we ourselves refuse — so the same check
    # every public route makes runs before anything is signed.
    caller_from_claims(claims)

    payload = dict(claims)
    payload["iss"] = signer.issuer
    payload["aud"] = signer.audience
    return jwt.encode(
        payload,
        signer.signing_key,
        algorithm=_ALGORITHM,
        headers={"kid": signer.key_id},
    )
