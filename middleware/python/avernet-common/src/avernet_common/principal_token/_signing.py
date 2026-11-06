"""Sign a principal token for the component it is addressed to.

Two ways to produce a token, and the difference is the lifetime:

- :func:`issue_principal_token` mints a **fresh** credential: it stamps
  ``iat``/``exp`` from the clock. The gateway does this for every forwarded
  request, and a service does it when it calls another one as itself.
- :func:`sign_principal_claims` **re-signs** claims that already carry a
  lifetime, replacing only the envelope (``iss``/``aud``/``kid``). A service
  does this to call a second one on the original caller's behalf, and keeping
  ``exp`` is what stops each hop from extending a short-lived credential.

Neither decides *what* may be signed. Re-signing unverified input, or minting
claims a caller supplied, turns the signer into a token-minting oracle for
every component that trusts the shared key — that judgement belongs to the
calling service, which knows where its claims came from.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jwt

from avernet_common.principal_token._errors import PrincipalTokenError
from avernet_common.principal_token._keys import key_fingerprint

# Pinned: every peer on this contract verifies HS256 with the shared key, and
# signing anything else would either be rejected or — worse, on a laxer
# verifier — accepted as an algorithm nobody agreed to.
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class PrincipalSignerConfig:
    """Who signs a principal token, and who it is addressed to.

    ``issuer`` names **the signing component** — a token is issued by whoever
    signs it, whatever the claims inside assert — and the receiver has to be
    configured to trust it. ``audience`` and ``key_id`` are the receiver's half
    of the contract: what *it* requires in ``aud`` and the JOSE ``kid`` header.

    ``signing_key`` is the shared HMAC secret. An empty key means the deployment
    resolved none, and signing then fails rather than emitting a token signed
    with nothing, which would be forgeable rather than merely weak.
    """

    signing_key: str
    audience: str
    issuer: str
    key_id: str

    @property
    def key_fingerprint(self) -> str:
        """This config's key as :func:`key_fingerprint` renders it for a log."""
        return key_fingerprint(self.signing_key)


def sign_principal_claims(
    claims: Mapping[str, Any], signer: PrincipalSignerConfig
) -> str:
    """Sign ``claims`` under ``signer``'s envelope.

    ``iss`` and ``aud`` are replaced with ``signer``'s, and ``kid`` is set in
    the JOSE header. Every other claim — ``iat``, ``exp`` and ``principals``
    included — is signed exactly as given.

    Raises:
        PrincipalTokenError: when no signing key is configured.
    """
    if not signer.signing_key:
        raise PrincipalTokenError(
            "no principal signing key is configured, so no token can be "
            f"signed for aud={signer.audience!r}"
        )
    payload = dict(claims)
    payload["iss"] = signer.issuer
    payload["aud"] = signer.audience
    return jwt.encode(
        payload,
        signer.signing_key,
        algorithm=_ALGORITHM,
        headers={"kid": signer.key_id},
    )


def issue_principal_token(
    claims: Mapping[str, Any],
    signer: PrincipalSignerConfig,
    *,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    """Mint a fresh token carrying ``claims``, valid for ``ttl_seconds``.

    Stamps ``iat`` and ``exp`` (overriding any in ``claims``), then signs
    through :func:`sign_principal_claims`, which sets the envelope.

    ``now`` defaults to the wall clock; it exists so tests can pin the time
    window, not so callers can backdate.

    Raises:
        PrincipalTokenError: when no signing key is configured, or
            ``ttl_seconds`` is not positive.
    """
    if ttl_seconds <= 0:
        raise PrincipalTokenError(
            f"principal token ttl must be positive, got {ttl_seconds}"
        )
    # Integer seconds, the shape every peer deserializes: BCS parses
    # ``iat``/``exp`` as unsigned integers, and a fractional value would fail
    # claim parsing there long before any logic ran.
    issued_at = int(time.time() if now is None else now)
    payload = dict(claims)
    payload["iat"] = issued_at
    payload["exp"] = issued_at + ttl_seconds
    return sign_principal_claims(payload, signer)
