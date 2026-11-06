"""Re-address a verified principal token to a second upstream.

The gateway mints one ``X-Avernet-Principal`` per hop: it signs the identity set
it resolved into a JWT whose ``aud`` names the upstream it is forwarding to, so
a token minted for ``backend`` verifies at the backend and **nowhere else** —
that is the property :mod:`.verifier` leans on when it refuses a token minted
for another component.

Which leaves a gap the moment a component has to call a second one *on the
caller's behalf*. The friend-approval flow is exactly that: the decision arrives
at ``/openapi/v1`` with a token addressed to ``backend``, and applying it means
calling BCN, which verifies the same shared key but requires ``aud=bcs`` (see
``src/bcs/api-contracts/v1/gateway-principal/contract.md``). Forwarding the
inbound header verbatim therefore fails at BCN's audience check, every time, and
no amount of retrying changes that.

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
- ``aud`` — replaced with the target's name. Together with ``iss`` below, this
  *is* the re-addressing.
- ``iss`` — replaced with **the signing component's own name**, because that is
  what an issuer claim means: the claims inside are still the gateway's
  assertions, but the signature over this copy is ours, and a token that named
  the gateway as its issuer would be claiming a provenance it does not have.
  The target has to trust that issuer for the call to be accepted.
- ``kid`` — set from the target's config rather than copied, because it names
  the key the target should verify with, and the key is ours, not the inbound
  token's to assert.

Transport-agnostic like the rest of this package (Rule 7): no header parsing, no
environment reads, no secret store. ``utils/gateway_principal_config.py`` binds
the configs; ``core/work_orders/callbacks.py`` is the caller.
"""

from __future__ import annotations

from avernet_common.principal_token import (
    PrincipalSignerConfig,
    PrincipalTokenError,
    sign_principal_claims,
)

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.verifier import (
    PrincipalVerifierConfig,
    caller_from_claims,
    decode_principal_token,
)

# ``PrincipalSignerConfig`` is the shared envelope config, re-exported so
# existing ``gateway_principal.signer`` imports keep working. Its ``issuer`` is
# this component's own name; ``audience`` and ``key_id`` are the target's
# requirements. All three are fixed in code beside the verifier's own constants —
# see ``utils/gateway_principal_config.py`` for why that is a deliberate call
# rather than a missing knob.
__all__ = ["PrincipalSignerConfig", "resign_principal_token"]


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
    claims = decode_principal_token(token, verifier)
    # The result is deliberately dropped: this is the admission gate, not a
    # projection. Re-addressing an identity set this surface would answer 401
    # to would hand the target a caller we ourselves refuse — so the same check
    # every public route makes runs before anything is signed.
    caller_from_claims(claims)

    # Replaces ``iss``/``aud`` and sets ``kid``; ``principals``, ``iat`` and
    # ``exp`` are signed unchanged. An empty signing key fails closed there:
    # a token signed with ``""`` is not a lesser credential but a forgeable one.
    try:
        return sign_principal_claims(claims, signer)
    except PrincipalTokenError as exc:
        raise PrincipalVerificationError(str(exc)) from exc
