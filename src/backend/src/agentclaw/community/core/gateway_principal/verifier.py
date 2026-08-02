"""Verify the gateway-signed principal token and project it onto our DTOs.

This is the backend half of auth design §7.1. The gateway signs the identity set
it resolved into a short-lived JWT and forwards it as ``X-Avernet-Principal``;
**a component must never trust that header unverified**, so everything here is
about earning the right to believe it:

1. the signature must check out against the shared key,
2. ``aud`` must name *this* component — a token minted for another upstream is
   not replayable here,
3. ``iss`` must be the gateway and ``exp`` must not have passed,
4. the ``principals`` payload must parse onto :mod:`.models`,
5. every principal in the set must agree on one tenant, and that tenant must not
   be the internal one,
6. the set must name an end user — see :func:`_require_user_principal`.

Any failure raises :class:`PrincipalVerificationError`. There is no partial
success and no fallback: a token we cannot fully verify yields no caller, and the
request is answered ``401``.

Transport-agnostic by design (Rule 7) — no framework, no header parsing, no
environment reads, no secret store. The adapter hands it a token string and a
config; that config is assembled in ``utils/gateway_principal_config.py``, which
resolves the signing key through ``SecretResolver``.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from pydantic import TypeAdapter, ValidationError

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.models import (
    GatewayPrincipal,
    UserPrincipal,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

# Pinned, not read from the token. Honouring the token's own ``alg`` is the
# classic JWT downgrade: a forged header saying ``none`` (or naming an
# asymmetric algorithm whose "key" is our public material) would verify.
_ALGORITHMS = ("HS256",)

# Clock skew allowance. The gateway's default TTL is 60s, so two containers a few
# seconds apart would otherwise reject live tokens. Fixed, not configurable: a
# knob here is a knob that can be set to hours.
_LEEWAY_SECONDS = 5

# Claims that must be present. ``aud``/``iss`` are also value-checked below;
# requiring them explicitly means a token that simply omits one is rejected
# rather than skipping the check.
_REQUIRED_CLAIMS = ("exp", "iat", "aud", "iss")

_PRINCIPAL_ADAPTER: TypeAdapter[GatewayPrincipal] = TypeAdapter(GatewayPrincipal)


@dataclass(frozen=True)
class PrincipalVerifierConfig:
    """Everything needed to verify a forwarded principal token.

    ``signing_key`` is the HMAC secret shared with the gateway's ``bare``
    signer. An empty key means the deployment has not been given one, or could
    not resolve it — see :func:`verify_principal_token`, which then fails every
    verification closed rather than accepting unsigned identity.

    ``audience`` must equal the gateway's upstream-server name for this
    component (``servers:`` in the gateway's ``upstreams.yaml``), and ``issuer``
    its configured ``iss``.
    """

    signing_key: str
    audience: str
    issuer: str


@dataclass(frozen=True)
class VerifiedCaller:
    """A verified caller: the full identity set the gateway resolved.

    A request can carry more than one identity (a route may require a user and
    optionally accept an app), so the caller *is* a set, not a single principal.
    ``tenant`` and ``user_id`` are the two things this surface scopes by.
    """

    principals: tuple[GatewayPrincipal, ...]

    @property
    def tenant(self) -> str:
        """The tenant every principal in the set agrees on.

        Verification rejects a set that disagrees, so reading the first is
        reading all of them.
        """
        return self.principals[0].tenant

    @property
    def user_id(self) -> str:
        """The owner id to scope data by — a ``user`` principal's subject id.

        Named ``user_id`` because that is the attribute
        ``openapi_v1/principal.py::caller_owner_id`` looks for — it was written
        against "whatever shape the auth workstream's verified principal takes",
        so satisfying it needs no change to any handler.

        A ``user`` principal is the only source. It is also the only identity
        type :func:`_require_user_principal` admits, so a caller reaching this
        property always has one and the loop always finds it. The two facts are
        the same decision seen from two sides: this surface scopes by an owner,
        so it serves only callers that name one.

        The ``""`` fallback is unreachable through
        :func:`verify_principal_token`. It stays so that a hand-constructed
        instance — a test fixture, say — degrades to "no owner", which
        ``caller_owner_id`` turns into a ``401``, rather than raising something
        no caller-facing code is written to catch.
        """
        for principal in self.principals:
            if isinstance(principal, UserPrincipal):
                return principal.subject.id
        return ""


def verify_principal_token(
    token: str, config: PrincipalVerifierConfig
) -> VerifiedCaller:
    """Verify ``token`` and return the caller it carries.

    Raises :class:`PrincipalVerificationError` on any failure — bad signature,
    wrong audience, expired, unparseable payload, contradictory tenants, an
    identity set that names no end user, or no signing key configured at all.
    """
    if not config.signing_key:
        # No key means we cannot tell a gateway token from a forged one. The
        # only safe reading of "unconfigured" is "trust nothing".
        raise PrincipalVerificationError("no principal signing key is configured")
    if not token:
        raise PrincipalVerificationError("empty principal token")

    try:
        claims = jwt.decode(
            token,
            config.signing_key,
            algorithms=list(_ALGORITHMS),
            audience=config.audience,
            issuer=config.issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except jwt.PyJWTError as exc:
        raise PrincipalVerificationError(f"principal token rejected: {exc}") from exc

    principals = _parse_principals(claims.get("principals"))
    _reject_contradictory_tenant(principals)
    _require_user_principal(principals)
    return VerifiedCaller(principals=principals)


def _parse_principals(raw: object) -> tuple[GatewayPrincipal, ...]:
    """Project the ``principals`` claim onto our DTOs, or fail closed."""
    if not isinstance(raw, list):
        raise PrincipalVerificationError(
            "principal token carries no 'principals' list"
        )
    if not raw:
        # The gateway adds no header for an empty identity set, so an empty list
        # is a malformed token rather than an anonymous caller.
        raise PrincipalVerificationError("principal token carries no identities")
    try:
        return tuple(_PRINCIPAL_ADAPTER.validate_python(item) for item in raw)
    except ValidationError as exc:
        raise PrincipalVerificationError(
            f"principal payload does not match the expected contract: {exc}"
        ) from exc


def _reject_contradictory_tenant(principals: tuple[GatewayPrincipal, ...]) -> None:
    """Require exactly one non-internal tenant across the whole identity set."""
    tenants = {principal.tenant for principal in principals}
    if len(tenants) > 1:
        # One request cannot belong to two tenants. Picking one would be
        # inventing an answer the gateway did not give us.
        raise PrincipalVerificationError(
            f"principal token mixes {len(tenants)} tenants"
        )
    tenant = next(iter(tenants))
    if not tenant:
        raise PrincipalVerificationError("principal token carries an empty tenant")
    if tenant == DEFAULT_AVERNET_TENANT:
        # ``teamclaw`` owns every pre-existing internal row. Accepting it off the
        # wire would hand an external caller the internal tenant's data — the
        # exact failure tenant isolation exists to prevent. No gateway tenant is
        # named this today; if an internal-through-gateway path is ever designed,
        # lift this guard deliberately, with that design written down.
        raise PrincipalVerificationError(
            "principal token names the internal tenant, which is not routable "
            "from the public surface"
        )


def _require_user_principal(principals: tuple[GatewayPrincipal, ...]) -> None:
    """Refuse an identity set that names no end user.

    The public surface scopes every read and write to an owner, and a ``user``
    principal is the only identity that names one. ``app.owners`` is free-text
    "developer/org" attribution (``varchar(1024)``, plural), and the gateway's
    access-key registry has no owner column at all — so neither identifies a
    person, and guessing one is a cross-account data bug. A ``bot`` principal
    does carry ``owner_id``, but a bot calling on its own behalf is not a caller
    this API is defined for; admitting it would silently let a bot act as its
    owner across the whole public contract.

    Enforced **here** rather than at each handler's owner lookup, and that is the
    point. Refusing in ``caller_owner_id`` only refuses handlers that call it —
    the four in ``openapi_v1/resources/router.py`` that scope on a caller-supplied
    ``bot_id`` never do, so an unscopeable caller reached them. A rule every
    handler has to remember is not a rule. Refusing during verification makes it
    hold for every route that exists and every route added later.

    Sets carrying *extra* identities alongside the user are accepted, not
    refused: the gateway forwards the whole set it resolved, so a route declaring
    ``user: required, app: optional`` legitimately yields two principals.
    Rejecting a set that merely *contains* a non-user identity would refuse a
    request the gateway considers valid. The user is required; the rest are
    ignored.

    This is the narrow reading of an open question, not an answer to it. What an
    ``app`` or ``access_key`` caller should own is still unsettled (auth design
    §14 Q4); delegation — a partner acting for a verified end user, which *does*
    name a person — is the designed way to widen this (§15). Lift the guard
    there, deliberately.
    """
    if any(isinstance(principal, UserPrincipal) for principal in principals):
        return
    # The types are named for the operator reading the log, never for the
    # caller: ``require_principal`` answers one fixed 401 for every verification
    # failure, so a caller cannot tell a refused identity type from a bad
    # signature.
    carried = ", ".join(sorted({principal.type.value for principal in principals}))
    raise PrincipalVerificationError(
        f"principal token names no user identity (carries: {carried}); the "
        "public surface admits only callers that name an end user"
    )
