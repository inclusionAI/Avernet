"""Verify the gateway-signed principal token and project it onto our DTOs.

This is the backend half of auth design §7.1. The gateway signs the identity set
it resolved into a short-lived JWT and forwards it as ``X-Avernet-Principal``;
**a component must never trust that header unverified**, so everything here is
about earning the right to believe it:

1. the signature must check out against the shared key,
2. ``aud`` must name *this* component — a token minted for another upstream is
   not replayable here,
3. ``iss`` must name a trusted component and ``exp`` must not have passed,
4. the ``principals`` payload must parse onto :mod:`.models`,
5. every principal that *asserts* a tenant must agree on one, and that tenant
   must not be the internal one (a ``user`` principal asserts none — see
   :func:`_asserted_tenants`),
6. the set must name **an end user or an application** — see
   :func:`_require_admissible_principal`.

Steps 1–3 are the token's envelope, checked by the shared
``avernet_common.principal_token`` so every component on the contract checks it
the same way. Steps 4–6 are this backend's own reading of the ``principals``
claim and live here.

Any failure raises :class:`PrincipalVerificationError`. There is no partial
success and no fallback: a token we cannot fully verify yields no caller, and the
request is answered ``401``.

Transport-agnostic by design (Rule 7) — no framework, no header parsing, no
environment reads, no secret store. The adapter hands it a token string and a
config; that config is assembled in ``utils/gateway_principal_config.py``, which
resolves the signing key through ``SecretResolver``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from avernet_common.principal_token import (
    MIN_SIGNING_KEY_BYTES,
    PrincipalTokenError,
    PrincipalVerifierConfig,
    decode_principal_token as decode_envelope,
    is_weak_signing_key,
    key_fingerprint,
)
from pydantic import TypeAdapter, ValidationError

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.models import (
    AppPrincipal,
    GatewayPrincipal,
    GatewayUser,
    UserPrincipal,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

# The config type and key diagnostics are re-exported from the shared package so
# existing ``gateway_principal.verifier`` imports keep working.
__all__ = [
    "MIN_SIGNING_KEY_BYTES",
    "PrincipalVerifierConfig",
    "VerifiedCaller",
    "caller_from_claims",
    "decode_principal_token",
    "is_weak_signing_key",
    "key_fingerprint",
    "verify_principal_token",
]

_PRINCIPAL_ADAPTER: TypeAdapter[GatewayPrincipal] = TypeAdapter(GatewayPrincipal)


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
        """The tenant to scope this request by.

        Every principal that asserts a tenant agrees on it — verification
        rejects a set that disagrees — so reading any one of them reads all of
        them.

        A set that asserts **none** is the user-only case, and it resolves to
        :data:`DEFAULT_AVERNET_TENANT`. That is not a guess standing in for a
        missing claim: a caller who presents nothing but a first-party user
        identity *is* an internal caller, which is exactly the scope
        ``teamclaw`` names. Every other path through this component already
        resolves to it (background work, the internal ``/api`` surface), so the
        public surface agreeing is one rule rather than two.

        Reached only through :func:`verify_principal_token`, which admits no set
        naming neither a user nor an app — so "asserts no tenant" always means
        "a user and nothing else", never "an empty set". An **app-only** caller
        always lands in the other branch: an ``app`` principal is registered to
        a tenant and asserts it, which is what lets a machine caller be scoped
        at all.
        """
        return next(iter(_asserted_tenants(self.principals)), DEFAULT_AVERNET_TENANT)

    @property
    def user_id(self) -> str:
        """The owner id to scope data by — a ``user`` principal's subject id.

        Named ``user_id`` because that is the attribute
        ``openapi_v1/principal.py::caller_owner_id`` looks for — it was written
        against "whatever shape the auth workstream's verified principal takes",
        so satisfying it needs no change to any handler.

        A ``user`` principal is the only source, and both this and the
        admission check read the set through :func:`_first_user_principal`, so
        they cannot disagree about *which* principal supplies the owner.

        **The ``""`` fallback is now reachable**, and that is the whole shape of
        the app-only change. An application calling alone names no end user, so
        this answers ``""`` — and ``caller_owner_id`` turns that into a ``401``.
        For an operation that has not opted into admitting a machine caller,
        that ``401`` *is* the wanted answer: the surface refuses a caller it
        cannot scope. Operations that have opted in never reach here for their
        scoping — they read the delegating user off the request and authorize it
        against the grant.

        So this property means "the end user the credential names", not "the
        user this request acts for". They coincide for every human caller and
        differ for a machine one.
        """
        principal = _first_user_principal(self.principals)
        return principal.subject.id if principal is not None else ""

    @property
    def user(self) -> GatewayUser | None:
        """The end user's verified subject, or ``None`` for a machine-only set.

        The full profile the gateway resolved — ``username`` and the optional
        display attributes — where :attr:`user_id` is only its id. Both read the
        set through :func:`_first_user_principal`, so they cannot name different
        people. ``None`` is the same state ``has_user`` answers ``False`` for:
        an application acting alone, which has no subject to return.
        """
        principal = _first_user_principal(self.principals)
        return principal.subject if principal is not None else None

    @property
    def has_user(self) -> bool:
        """Whether this set names an end user at all.

        The question ``user_id`` cannot answer: ``""`` is both "no user" and
        "a user whose id is blank", and only the first is a caller shape.
        Verification refuses the second, so a *verified* caller with no
        ``user_id`` is always an application acting alone — but callers should
        ask this rather than compare against ``""`` and rely on that.
        """
        return _first_user_principal(self.principals) is not None

    @property
    def app_id(self) -> int | None:
        """The calling application's id, or ``None`` when the set names none.

        ``None`` is a real state of the contract — "this caller is a human, and
        no grant applies to it" — rather than a widened return type. Every
        consumer branches on it explicitly, which is what keeps a human caller
        from ever being resolved against a grant, and an application from ever
        falling back to an unscoped read.
        """
        for principal in self.principals:
            if isinstance(principal, AppPrincipal):
                return principal.app.app_id
        return None


def verify_principal_token(
    token: str, config: PrincipalVerifierConfig
) -> VerifiedCaller:
    """Verify ``token`` and return the caller it carries.

    Raises :class:`PrincipalVerificationError` on any failure — bad signature,
    expired, unparseable payload, contradictory tenants, an
    identity set naming neither an end user nor an application, or no signing
    key configured at all.

    The two halves are also callable on their own — see
    :func:`decode_principal_token` and :func:`caller_from_claims`. This is the
    composition of them, and the only form any HTTP seam should reach for: a
    caller that decodes without admitting has verified a signature and nothing
    else.
    """
    return caller_from_claims(decode_principal_token(token, config))


def decode_principal_token(
    token: str, config: PrincipalVerifierConfig
) -> dict[str, Any]:
    """Return ``token``'s claims once its signature and time window check out.

    The cryptographic half of :func:`verify_principal_token`, delegated to the
    shared ``avernet_common.principal_token.decode_principal_token``: signature
    against the shared key, ``alg`` pinned to HS256, ``iss`` and (when
    configured) ``aud`` value-checked, ``exp``/``iat``/``iss`` required to be
    present, ``exp`` judged with a few seconds of skew.

    What comes back is **authenticated but not yet admitted**: the gateway
    signed these claims, so they are not a caller's assertion — but nothing here
    has looked at *who* they name, whether the identity set parses, or whether
    the tenants agree. :func:`caller_from_claims` is that half, and any decision
    about the caller belongs to it.

    Separate from it because one consumer needs the claims themselves rather
    than the caller they project onto: :mod:`.signer` re-addresses a verified
    token to a second upstream, and re-addressing means preserving every claim
    the gateway wrote (see that module). Every other consumer wants
    :func:`verify_principal_token`.
    """
    try:
        return decode_envelope(token, config)
    except PrincipalTokenError as exc:
        # Same message, backend's type: ``ENVELOPE_ERRORS`` maps
        # :class:`PrincipalVerificationError` to the fixed public ``401``, so
        # the message never reaches the caller.
        raise PrincipalVerificationError(str(exc)) from exc


def caller_from_claims(claims: Mapping[str, Any]) -> VerifiedCaller:
    """Project already-authenticated ``claims`` onto the caller they name.

    The identity half of :func:`verify_principal_token`: the ``principals``
    payload must parse onto :mod:`.models`, every principal that asserts a
    tenant must agree on one, and the set must name an end user or an
    application.

    **Takes claims a signature has already vouched for**, which is the whole of
    its precondition — it has no way to check that itself, so its only callers
    are :func:`verify_principal_token` and :mod:`.signer`, each of which decodes
    through :func:`decode_principal_token` first.
    """
    principals = _parse_principals(claims.get("principals"))
    _reject_contradictory_tenant(principals)
    _require_admissible_principal(principals)
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


def _asserted_tenants(principals: tuple[GatewayPrincipal, ...]) -> set[str]:
    """The distinct tenants the identity set actually claims.

    A ``user`` principal carries no tenant and so contributes nothing here: the
    gateway authenticates a person, and nothing about a person proves which
    tenant they act for (see :class:`~.models.UserPrincipal`). The other three
    are each registered to a tenant, and that registration is what their
    principal asserts.

    Empty is therefore a real result, not a failure — it is the user-only
    request, and :attr:`VerifiedCaller.tenant` answers it with the internal
    default. Keeping "what was claimed" separate from "what we scope by" is the
    point of this function: the guard below judges only claims, so the fallback
    stays a decision made here rather than a value a token can supply.
    """
    return {
        principal.tenant
        for principal in principals
        if not isinstance(principal, UserPrincipal)
    }


def _reject_contradictory_tenant(principals: tuple[GatewayPrincipal, ...]) -> None:
    """Require at most one tenant, named and non-empty, across the identity set.

    "At most" because a user-only set claims no tenant at all; there is then
    nothing to contradict and nothing to vet, and
    :attr:`VerifiedCaller.tenant` supplies the internal default locally.

    Tenants are judged by count and emptiness, never by name.
    :data:`DEFAULT_AVERNET_TENANT` used to be refused here — see the module
    README's change-impact note — because no gateway tenant was registered under
    it. One is now, so it passes like any other; a set that pairs it with a
    second tenant still fails the contradiction check above.
    """
    tenants = _asserted_tenants(principals)
    if not tenants:
        return
    if len(tenants) > 1:
        # One request cannot belong to two tenants. Picking one would be
        # inventing an answer the gateway did not give us.
        raise PrincipalVerificationError(
            f"principal token mixes {len(tenants)} tenants"
        )
    tenant = next(iter(tenants))
    if not tenant:
        raise PrincipalVerificationError("principal token carries an empty tenant")


def _first_user_principal(
    principals: tuple[GatewayPrincipal, ...],
) -> UserPrincipal | None:
    """The user principal an owner is derived from, or ``None`` when there is none.

    ``None`` is a real state of the contract here — "this identity set names no
    end user" is exactly what :func:`_require_admissible_principal` weighs —
    so the optional return is the state, not a widened type.

    One function rather than two loops so the admission check and
    :attr:`VerifiedCaller.user_id` cannot disagree about *which* principal
    supplies the owner. That matters for a set carrying two user principals: the
    gateway never produces one (it resolves at most a single identity per type),
    but the ``principals`` claim is a list, so a token can present two. Checking
    "some user has a usable id" while deriving the owner from "the first user"
    would admit a set whose first user has a blank id and then scope by nothing.
    """
    for principal in principals:
        if isinstance(principal, UserPrincipal):
            return principal
    return None


def _require_admissible_principal(principals: tuple[GatewayPrincipal, ...]) -> None:
    """Refuse an identity set that names neither an end user nor an application.

    **This guard moved; it was not removed.** It used to require an *end user*,
    full stop, and it lives on in two places that between them cover strictly
    more than it did:

    - ``openapi_v1/dependencies.py::require_principal`` — the dependency every
      public route already declares — now carries the end-user requirement. A
      route that says nothing gets it, so the default is still "refuse a caller
      with no user", including for routes nobody has written yet.
    - ``openapi_v1/admission.py`` decides, per operation, whether that same
      dependency also admits an application acting alone. An operation absent
      from its table is refused, so the default survives the move.

    Why it had to move at all: this component is transport-agnostic (Rule 7) and
    route-blind, and the middleware that drives it runs before routing. Deciding
    *per operation* here would mean a second route table in the backend,
    hand-synced with the gateway's. The rule that a route inherits by saying
    nothing is the property worth keeping, and moving the guard one layer up
    keeps it — a route still cannot become admissible by accident, only by
    naming the dependency that admits it.

    What is left here is the floor: a set naming no principal this surface can
    do anything with at all. ``access_key`` and ``bot`` remain refused outright.
    ``app.owners`` is free-text "developer/org" attribution
    (``varchar(1024)``, plural) and the access-key registry has no owner column,
    so neither identifies a person; a ``bot`` principal does carry ``owner_id``,
    but a bot calling on its own behalf is not a caller this API is defined for,
    and admitting it would silently let a bot act as its owner across the whole
    public contract.

    An **application** is admissible here because it can now be authorized to
    act for a person — a grant naming the delegating user, checked per request
    (``core/bot_app_grant``). That is the delegation auth design §15 anticipated,
    arriving as a machine credential standing in for an absent human rather than
    a verified one. What it may reach is decided per operation, upstairs, and an
    app-only caller reaching an operation not placed in an admission group is
    answered ``401`` exactly as before.

    Sets carrying *extra* identities are accepted, not refused: the gateway
    forwards the whole set it resolved, so a route declaring ``user: required,
    app: optional`` legitimately yields two principals.
    """
    user = _first_user_principal(principals)
    if user is None:
        if any(isinstance(principal, AppPrincipal) for principal in principals):
            # An application alone. Admissible at this layer; whether it may
            # reach the operation it is calling is decided per route.
            return
        # The types are named for the operator reading the log, never for the
        # caller: ``require_principal`` answers one fixed 401 for every
        # verification failure, so a caller cannot tell a refused identity type
        # from a bad signature.
        carried = ", ".join(sorted({principal.type.value for principal in principals}))
        raise PrincipalVerificationError(
            f"principal token names neither a user nor an app identity "
            f"(carries: {carried}); the public surface admits only callers that "
            "name an end user, or an application acting under a grant"
        )
    # A ``user`` principal with a blank subject id names an end user no better
    # than an access key does — and the type check alone would admit it, because
    # both sides model the id as an unconstrained ``str``. It is reachable: the
    # gateway's google strategy reads ``body["sub"]``, which raises on a
    # *missing* claim but passes an empty one straight through.
    #
    # Whitespace is stripped for the test rather than merely checked falsy (the
    # empty-tenant guard above can be laxer because a tenant is compared, not
    # used as a key): an id of ``"   "`` would be carried into owner-scoped
    # queries as a real value.
    if not user.subject.id.strip():
        raise PrincipalVerificationError(
            "principal token carries a user identity with a blank subject id, "
            "which names no owner to scope by"
        )
