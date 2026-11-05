"""Mint an app principal this component presents to the BaaS gateway.

BaaS's JWT-authenticated surface (``/openapi/v1/chat/*``) verifies a token with
the shared HS256 key and then reads exactly two things out of it:

- ``principals[0].access_key.access_key`` — the resource key the request acts
  as, looked up against ``baas_resource_key``; and
- ``principals[0].tenant`` — the tenant that lookup is scoped to.

It checks neither ``aud`` nor ``iss`` nor ``kid``. Those are still set, and set
coherently, for two reasons: this token then looks like every other principal
this component mints (so an operator reading a log or a captured token is not
surprised), and a stricter verifier on the BaaS side later would not silently
reject everything this channel sends.

What this deliberately does **not** do is accept a caller-shaped identity.
There is nothing caller-supplied here but the tenant and the resource key, and
both name an operator-provisioned row — the same boundary
:mod:`.service_principal` draws for the BCS token.
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

# Same pin as the rest of this package: HS256 over the shared key is the
# contract on this wire, and signing anything else would either be rejected or,
# on a laxer verifier, accepted as an algorithm nobody agreed to.
_ALGORITHM = "HS256"

#: How long a minted BaaS token lives. The token is minted immediately before
#: the one streaming request it authorizes, so a short window matches the
#: gateway's own 60s default. BaaS checks the token when the request is
#: accepted, not per stream chunk — the long-running cases in the simulator
#: suite are what confirm that.
_BAAS_TOKEN_TTL_SECONDS = 60

#: The app identity this component states about itself in the token's ``app``
#: block. Audit-only for BaaS (it reads the resource key row, not this), so the
#: values are this component's own statement rather than a registry assignment.
_BAAS_SERVICE_APP_NAME = "agentclaw-backend"
_BAAS_SERVICE_APP_OWNERS = "agentclaw-backend"
_BAAS_SERVICE_APP_TYPE = "SERVICE"
#: Negative on purpose, matching :mod:`.service_principal`: a gateway app
#: registry only assigns positive auto-increment ids, so this can never collide
#: with, or be mistaken for, a registry row.
_BAAS_SERVICE_APP_ID = -1


def mint_baas_service_app_principal(
    signer: PrincipalSignerConfig,
    tenant: str,
    resource_key: str,
    *,
    now: float | None = None,
) -> str:
    """Return a short-lived app principal carrying ``resource_key`` for ``tenant``.

    Args:
        signer: Where the token is addressed and what signs it. ``audience`` is
            BaaS's name, ``issuer`` names this component.
        tenant: The BaaS tenant the resource key row belongs to. Must be the
            same value the row was provisioned under, or BaaS's
            ``(resource_key, tenant)`` lookup finds nothing.
        resource_key: The ``baas_resource_key.resource_key`` value this request
            acts as.
        now: Wall clock override for tests.

    Returns:
        An encoded HS256 JWT.

    Raises:
        PrincipalVerificationError: when no signing key is configured (a token
            signed with nothing is a forgeable one), or when ``tenant`` or
            ``resource_key`` is blank — BaaS would reject either, so refusing
            here keeps the failure pre-send and names the missing input.
    """
    if not signer.signing_key:
        raise PrincipalVerificationError(
            "no principal signing key is configured, so no BaaS service "
            f"principal can be minted for aud={signer.audience!r}"
        )
    tenant = (tenant or "").strip()
    if not tenant:
        raise PrincipalVerificationError(
            "refusing to mint a BaaS service principal with a blank tenant — "
            "BaaS scopes its resource-key lookup by tenant, so an unscoped "
            "token could never authorize a request and would read as a "
            "misconfiguration at the far end instead of here"
        )
    resource_key = (resource_key or "").strip()
    if not resource_key:
        raise PrincipalVerificationError(
            "refusing to mint a BaaS service principal with a blank resource "
            "key — BaaS reads principals[0].access_key.access_key and would "
            "answer 401 for a token that names no key"
        )

    # Integer seconds: every peer deserializes ``iat``/``exp`` as integers, and
    # BaaS runs the same PyJWT decode that rejects fractional values at parse
    # time.
    issued_at = int(time.time() if now is None else now)
    claims = {
        "iss": signer.issuer,
        "aud": signer.audience,
        "iat": issued_at,
        "exp": issued_at + _BAAS_TOKEN_TTL_SECONDS,
        "principals": [
            {
                "type": "app",
                "tenant": tenant,
                # The one claim BaaS actually authorizes against.
                "access_key": {"access_key": resource_key},
                "app": {
                    "app_id": _BAAS_SERVICE_APP_ID,
                    "app_name": _BAAS_SERVICE_APP_NAME,
                    "owners": _BAAS_SERVICE_APP_OWNERS,
                    "tenant": tenant,
                    "app_type": _BAAS_SERVICE_APP_TYPE,
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


__all__ = ["mint_baas_service_app_principal"]