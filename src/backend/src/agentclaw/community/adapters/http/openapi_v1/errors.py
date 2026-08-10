"""Public-API error types with no heavy imports.

Kept dependency-free so the lightweight schema / cluster modules can raise these
without pulling in the service layer (which would create an import cycle). The
error → HTTP/envelope mapping lives in ``responses.py``.
"""

from __future__ import annotations


class ClusterMismatchError(Exception):
    """Raised when a request's ``engine`` and ``cluster_name`` violate the rule.

    The public cluster enum is in strict bijection with the engine (``ANDC`` for
    ``teclaw``, ``ACRA`` for everything else); a pair that breaks it is a client
    error mapped to 400.
    """


class MissingPrincipalError(Exception):
    """Raised when a public request has no authenticated caller (→ 401).

    One error for two causes, deliberately: no ``X-Avernet-Principal`` header at
    all, and a header whose token failed verification. ``require_principal``
    funnels both here so the caller cannot tell them apart — see
    ``openapi_v1/dependencies.py``. Which one it was is logged, with the reason,
    at the point of failure.

    ``app.py`` registers a handler for this type directly rather than letting
    the catch-all answer it; the docstring there explains why that placement
    matters.
    """


class GrantNotResolvableError(Exception):
    """Raised when an application caller holds no grant for what it addressed (→ 404).

    **The response is byte-identical to a nonexistent bot**, and that is the
    whole reason this is a distinct type rather than a reused one: it needs its
    own handler in ``app.py`` — a dependency-raised error never reaches
    ``@envelope_errors`` — while producing an answer indistinguishable from
    ``BotNotFoundError``'s. An application must not be able to tell a bot it was
    not granted from one that does not exist, or the surface becomes an
    enumeration oracle for every bot id in the tenant.

    ``403`` would be exactly wrong here. On this surface it means "you are
    authenticated and this is not yours", which *confirms the bot exists* — the
    one fact the refusal is protecting.
    """


class UserIdMismatchError(Exception):
    """Raised when a request's ``user_id`` is not the verified caller's (→ 403).

    Every user-scoped public operation names the end user it acts for in a
    required ``user_id`` query parameter rather than inferring it from the
    principal. For a caller that names an end user, the only user it may name is
    itself, so the parameter must repeat the ``user`` principal's subject id and
    a disagreement is refused here.

    An **application** caller reaches none of this: it names no end user to
    compare against, so its ``user_id`` is authorized against the grant instead
    (:class:`GrantNotResolvableError`) rather than compared. The two paths are
    mutually exclusive by construction — a caller either names a user or does
    not.

    401 would be wrong — the caller *is* authenticated — and so would silently
    preferring one of the two values: trusting the parameter would let any
    verified user read another's data, and trusting the principal would answer a
    request the caller did not make. The refusal is the only answer that keeps
    the parameter honest while it is still redundant.

    Raised in a dependency, so ``@envelope_errors`` never sees it; ``app.py``
    registers a handler for this type for the reason documented there.
    """


class UnsupportedEngineError(Exception):
    """Raised when a request names an engine the platform does not support (→ 400).

    Checked up front so an unknown engine is rejected before a bot id is
    allocated or a Passport is applied for — otherwise the request would create
    side effects and only fail later at device provisioning.
    """
