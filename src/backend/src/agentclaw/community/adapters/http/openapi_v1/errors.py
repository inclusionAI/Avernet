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

    Every delegable user-scoped public operation names the end user it acts for
    in a required ``user_id`` query parameter. For a caller that names an end
    user, the only user it may name is itself, so the parameter must repeat the
    ``user`` principal's subject id and a disagreement is refused here.

    Non-delegable self-service operations do not expose the parameter: they
    derive the actor from the verified principal and refuse App-only callers.

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


class IamTokenUnavailableError(Exception):
    """The authenticated browser request carried no usable IAM token."""


class CallerIdentityOpenApiError(Exception):
    """A stable Caller-identity failure safe to map at the HTTP boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CallerIdentityInvalidError(CallerIdentityOpenApiError):
    """Caller identity preparation was requested with an invalid target."""


class CallerIdentityForbiddenError(CallerIdentityOpenApiError):
    """The authenticated user may not perform this Caller operation."""


class CallerIdentityConflictError(CallerIdentityOpenApiError):
    """The Caller target cannot be resolved unambiguously."""


class UnsupportedEngineError(Exception):
    """Raised when a request names an engine the platform does not support (→ 400).

    Checked up front so an unknown engine is rejected before a bot id is
    allocated or a Passport is applied for — otherwise the request would create
    side effects and only fail later at device provisioning.
    """


class BotTemplateInvalidError(Exception):
    """A ``template_type``/``template_config`` pairing is invalid (→ 422).

    Covers a ``template_config`` without a ``template_type``, an unsupported
    template type, and a template payload that fails the application-coding
    validation. Deliberately a 422, not 400: the request is well-formed but the
    template contract is wrong.
    """


class BotCombinationUnsupportedError(Exception):
    """An application-coding combination is recognized but not creatable (→ 409).

    The engine / deployment / space / service combination is valid in the
    abstract but not supported in this deployment — distinct from a malformed
    template (422) and from a deployment that cannot host workspaces at all
    (503).
    """


class ApplicationCodingUnavailableError(Exception):
    """The deployment has no Workspace Hosting bound (→ 503).

    Checked before any side effect. A 503 rather than 409: the caller's
    combination may be valid, but this deployment cannot honor it right now.
    """


class StartupScriptUnsupportedError(Exception):
    """A bot whose container cannot run a startup script was sent one.

    Refusing the write is deliberate: storing it would be the silent no-op this
    design exists to prevent, and the caller would reasonably read a 200 as
    "my provisioning is in place". The *reason* is served by GET rather than in
    this refusal, because the surface's messages are fixed by contract.
    """


class DeptLookupError(Exception):
    """The staff directory could not answer a department lookup (→ 502).

    Distinct from "no dept" (a 200 with null ``dept_*``): this is infrastructure
    failure — the backing master-data service was unreachable, errored, or a
    needed secret could not be resolved. ``StaffDeptPlugin.get_dept_by_work_no``
    raises it on those, and the ``org/user`` whoami surfaces it as a 5xx so an
    operator can tell "directory down" from "person has no dept" from "not
    authenticated" (401). Fixed message: the specific cause is logged, never
    returned, mirroring how ``MissingPrincipalError`` keeps its reason off the wire.
    """


class BotAccessRefusedError(Exception):
    """Raised when a caller is below an operation's collaborator level (→ 404).

    **The response is byte-identical to a bot that does not exist**, and that
    is the reason this is a distinct type rather than a reused one: like
    :class:`GrantNotResolvableError` it is raised in a *dependency*, so
    ``@envelope_errors`` never sees it and ``app.py`` must handle it directly.

    ``403`` would be exactly wrong, for the reason it is wrong there: on this
    surface it means "you are authenticated and this is not yours", which
    confirms the bot exists — the one fact the refusal is protecting. A caller
    who may not reach a bot must not be able to tell it from one that is not
    there, or the surface becomes an enumeration oracle over other people's
    bots.
    """
