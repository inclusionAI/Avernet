"""Mint this component's own service identity as a principal token for BCS.

:mod:`.signer` re-addresses a *verified* inbound token and, by that
boundary, never mints one. This module is the deliberate second half of
that line: the one place this component mints a token at all, and it
mints **only itself**. The distinction the signer docstring draws —
"minting whatever it is handed would be an oracle" — is preserved here by
there being nothing caller-shaped to hand it:

- the ``principals`` claim is fixed to *this component's* app identity
  (the constants below); no caller-supplied identity, no user claim from
  an inbound request, and no other component's assertion is ever copied
  into the token. The identity set of every token this mints is
  ``[app(backend, <tenant>)]`` and nothing else.
- the only variable input is ``tenant``, and it is not a caller's claim:
  callers pass the backend-owned tenant of the data a background job is
  about to project (the sync key). A tenant a deployment does not serve
  never reaches this function, because the repository only yields rows
  it owns — and even if one did, BCS's authority policy answers 403:
  the operator-registered ``[[bot_authority.trusted_apps]]`` rule
  (``app`` byte-exact + ``tenant`` + action) is the authorization, and
  deny-everything is its default.
- ``iss``/``aud``/``kid`` come from the same fixed-in-code envelope
  contract the re-signer uses (``utils/gateway_principal_config.py``),
  so every token names who issued it (``backend``), who it is for
  (``bcs``), and which key verifies it.

Why that is safe where "sign whatever arrived" would not: a caller can
influence *whether* a call happens, never *who the token says is
calling*. There is no oracle surface for another component's claims,
because nothing of any other component is ever accepted as input. The
credential this mints asserts "the backend service is acting for its
tenant" — a statement only this process's operator config has any
business making, checked at the far end by the shared key (every
verifier on this contract treats any key holder as able to sign, so the
key *is* the trust boundary) and by the BCS-side trusted-app rule.

The shape of the ``principals`` entry is the app-principal contract in
``src/bcs/api-contracts`` (BCN wire
``bcs-api-http/src/v1/gateway_principal/wire.rs``): one ``type=app``
entry whose outer ``tenant`` and ``app.tenant`` must agree. ``app_id``,
``app_name``, ``owners`` and ``app_type`` are freely-shaped identity
fields — BCS matches its rule on ``app_name`` alone (byte-exact) and
records the rest for audit; the values below are therefore **this
component's own statement about itself**, not anything a registry
assigned.

Transport-agnostic like the rest of this package (Rule 7): the caller
resolves the key and hands it in :class:`PrincipalSignerConfig`, so no
environment or secret store is read here. The caller is the manager-sync
composition root (``utils/gateway_principal_config.py`` bindings via
``di/modules/bot_manager_sync_module.py``).
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.signer import (
    PrincipalSignerConfig,
)

# Same pin as :mod:`.signer` — HS256 with the shared key is the contract on
# this wire; signing anything else would either be rejected or, on a laxer
# verifier, accepted as an algorithm nobody agreed to.
_ALGORITHM = "HS256"

#: How long a minted service token lives. The token is minted per send,
#: immediately before the one PUT it authorizes, so a short window matches
#: the friend-approval precedent's lifetime conventions (the gateway's own
#: 60s default) and keeps a stray token useless quickly. Short by
#: construction, not by configuration: a TTL knob is a knob that can be
#: turned up until the credential outlives its purpose.
_SERVICE_TOKEN_TTL_SECONDS = 60

#: The app identity BCS's ``[[bot_authority.trusted_apps]]`` rule names —
#: byte-exact. This is the ``app`` the operator registers on the BCS side;
#: changing it here without changing the BCS rules (fail-closed by
#: default) makes every sync answer 403.
_SERVICE_APP_NAME = "backend"

#: Negative on purpose. BCS records the calling service in its authority
#: audit as ``app_{app_id}``, and a gateway app registry can only ever
#: assign positive auto-increment ids — so this value can never collide
#: with, or be mistaken for, a third-party registry row. Audit-only: the
#: trusted-app match is on ``app_name``, never on this number.
_SERVICE_APP_ID = -1

#: Free-text developer/org attribution this component states about itself
#: (the same field a gateway registry row carries). Audit-only.
_SERVICE_APP_OWNERS = "agentclaw-backend"

#: The app's kind, in this component's own words — a first-party service,
#: not a third-party integration. Audit-only.
_SERVICE_APP_TYPE = "SERVICE"


def mint_service_app_principal(
    signer: PrincipalSignerConfig,
    tenant: str,
    *,
    now: float | None = None,
) -> str:
    """Return a short-lived ``backend``-service token for ``tenant``.

    Fails closed — :class:`PrincipalVerificationError`, the package's one
    error type — when no signing key is configured (a token signed with
    nothing is a forgeable one) or when ``tenant`` is blank (BCS rejects
    a blank tenant claim; refusing here keeps the failure pre-send
    instead of on the wire).

    ``now`` defaults to the wall clock; it exists so tests can pin the
    time window, not so callers can backdate.
    """
    if not signer.signing_key:
        raise PrincipalVerificationError(
            "no principal signing key is configured, so no service "
            f"principal can be minted for aud={signer.audience!r}"
        )
    # Integer seconds, the shape every peer deserializes: BCS parses
    # ``iat``/``exp`` as unsigned integers, and a fractional value would
    # fail claim parsing there long before any logic ran.
    issued_at = int(time.time() if now is None else now)
    if not tenant or not tenant.strip():
        raise PrincipalVerificationError(
            "refusing to mint a service principal with a blank tenant — "
            "BCS rejects it, and an unscoped identity is the one thing "
            "this component must never assert"
        )
    claims = {
        "iss": signer.issuer,
        "aud": signer.audience,
        "iat": issued_at,
        "exp": issued_at + _SERVICE_TOKEN_TTL_SECONDS,
        "principals": [
            {
                "type": "app",
                "tenant": tenant,
                "app": {
                    "app_id": _SERVICE_APP_ID,
                    "app_name": _SERVICE_APP_NAME,
                    "owners": _SERVICE_APP_OWNERS,
                    "tenant": tenant,
                    "app_type": _SERVICE_APP_TYPE,
                },
            }
        ],
    }
    return jwt.encode(
        claims,
        signer.signing_key,
        algorithm=_ALGORITHM,
        headers={"kid": signer.key_id},
    )
