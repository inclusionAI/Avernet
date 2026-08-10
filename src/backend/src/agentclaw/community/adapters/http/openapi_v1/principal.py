"""Caller-identity extraction for the public API.

The single place that resolves the **end user a public request acts for** — the
id every service call is scoped by. It's isolated here so that the shape of a
verified principal is one module's business, not every handler's.

Two things live here, and the split is the point:

- :func:`caller_owner_id` — who the *credential* says is calling. Read off the
  verified principal.
- :func:`require_user_id` — who the *request* says it acts for. Read off the
  ``user_id`` query parameter, which every user-scoped route now requires.

Handlers take the second one. They used to derive the owner from the principal
directly, which worked only because the two can't currently differ: the gateway
resolves an end user from ``x-one-id`` and signs it into the principal, so a
public request always named a person implicitly. That stops being true once an
**App calls on behalf of a user** — the app presents its own credential and the
end user is not in it — and an operation whose contract never mentioned a user
has nowhere to put one. Naming the user in the request makes the contract say
out loud what it always meant, before the identity set stops saying it.

Why the query string, and only the query string
-----------------------------------------------

``user_id`` is not an attribute of any resource on this surface. It is who the
call is for: the same value on every operation, and the same meaning on a read
as on a write. A request body describes the resource being acted on — put the
id there and, beside ``bot_name`` in a ``PUT /openapi/v1/bots/{bot_id}``
payload, it reads as a property being set on the bot. A path segment *names* the
resource — ``/bots/{bot_id}/users/{user_id}`` would claim to address a user
beneath a bot, inverting the ownership and describing something the operation
does not return.

So: always the query string, whatever the method, whatever the body. There is no
second placement and no exception table. ``bot_id`` is untouched by this rule —
it stays in the path where it addresses a bot, and in the query string where it
is a parameter.

Four operations take no ``user_id`` at all, because they have no user dimension
to scope by: ``check_bot_name`` answers a tenant-wide uniqueness question, and
``list_mcp_servers`` / ``list_mcp_tenants`` / ``get_mcp_server`` read a
marketplace catalogue identical for every caller in the tenant. They still
require an authenticated caller — that is ``require_principal``'s job, not this
parameter's.

How the parameter is *authorized*, and why it splits
----------------------------------------------------

The parameter is acquired the same way for everyone; what differs is what it is
checked against, and that follows from whether the credential names a person:

- **A caller naming an end user** must name *itself*. :func:`require_user_id`
  refuses anything else with a ``403``. Unchanged, and it is the whole of the
  "nothing else changes" promise for human callers.
- **An application acting alone** names no end user, so there is nothing to
  compare with. Its ``user_id`` is authorized against the **grant** instead —
  "has this person delegated to this application, for this bot?" — which happens
  in :data:`GrantCheckedDep` below, because only there is the bot known.

This is the split :func:`require_user_id`'s own docstring predicted: acquisition
here, adjudication one step later. It is why the parameter had to exist before
the caller did — an operation whose contract never mentioned a user has nowhere
to put one when the identity set stops carrying it.

The parameter is **never trusted on its own**. An application naming a user who
granted it nothing is refused exactly as one naming a bot that does not exist,
so guessing a ``user_id`` buys nothing.

The owner id scopes reads/writes to the caller's own bots *within* the tenant
(the tenant guard confines data to the tenant; this confines it to the caller).

The gateway verifier now supplies a
:class:`~agentclaw.community.core.gateway_principal.VerifiedCaller`, whose
``user_id`` is the ``user`` principal's subject id. A caller that names no end
user — ``app``, ``access_key``, ``bot`` — never reaches here at all: verification
refuses the identity set outright, so the ``401`` is answered before any route
runs rather than at this lookup. That is deliberate, and it is what makes the
rule independent of whether a given handler remembers to call this function.

The "carries no user_id" branch below is therefore unreachable for a verified
caller, and stays only as the fail-closed answer for a hand-constructed
principal. The tolerance of a bare string or a mapping is what let this helper
survive the stub-to-real swap unchanged.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query, Request

from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    BODY_BOT_ID_OPERATIONS,
    SKILL_SCOPED_OPERATIONS,
    ActingCaller,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    GrantNotResolvableError,
    MissingPrincipalError,
    UserIdMismatchError,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.log import get_logger

logger = get_logger()

#: The query parameter naming the end user a request acts for.
USER_ID_QUERY = "user_id"

#: What every user-scoped operation publishes for it. Defined once so the
#: parameter reads identically across all 56 of them.
USER_ID_DESCRIPTION = (
    "The end user this request acts for. Every read and write is scoped to it. "
    "A caller authenticated as a person must name themselves; naming another "
    "user is refused (403). An application calling with its own credential and "
    "no end user names the user who authorized it, and reaches only what that "
    "user has authorized it for — anything else is answered exactly as if it "
    "did not exist (404)."
)


def caller_owner_id(principal: Principal) -> str:
    """Return the caller's owner id, or raise :class:`MissingPrincipalError`.

    Accepts either a bare id string or an object/mapping exposing ``user_id``,
    so it fits whatever shape the auth workstream's verified principal takes.
    """
    if principal is None:
        raise MissingPrincipalError("no authenticated caller")
    if isinstance(principal, str):
        if not principal:
            raise MissingPrincipalError("empty caller id")
        return principal
    user_id = (
        principal.get("user_id")
        if isinstance(principal, dict)
        else getattr(principal, "user_id", None)
    )
    if not user_id:
        raise MissingPrincipalError("principal carries no user_id")
    return str(user_id)


def caller_names_a_user(principal: Principal) -> bool:
    """Whether the credential names an end user, tolerantly.

    Read together with :func:`caller_app_id`: "names no user" is not on its own
    a reason to relax anything, because every *unusable* credential also names
    no user. Only "names no user **and** names an application" is the app-only
    caller. See the branch in :func:`require_user_id`.

    Mirrors :func:`caller_owner_id`'s tolerance rather than reading
    ``VerifiedCaller.has_user`` directly, and for the same reason that helper
    has it: this module accepts a bare id string or a mapping as well as the
    real verified caller, so a stand-in keeps working. Reading the attribute
    alone would make every such stand-in look like an application acting alone —
    which is the one shape that skips the id comparison, so the failure would be
    a silently *weaker* check rather than an error.

    A real :class:`VerifiedCaller` answers from ``has_user``. Anything else is
    asked the question that actually matters: does it yield an owner id?
    """
    if principal is None:
        return False
    if isinstance(principal, str):
        return bool(principal)
    has_user = getattr(principal, "has_user", None)
    if isinstance(has_user, bool):
        return has_user
    try:
        return bool(caller_owner_id(principal))
    except MissingPrincipalError:
        return False


def caller_app_id(principal: Principal) -> int | None:
    """The calling application's id, or ``None``, tolerantly.

    ``None`` means "no application on this credential" — a human caller, or a
    stand-in that does not model one. Both are the same answer here: no grant
    governs the request.
    """
    app_id = getattr(principal, "app_id", None)
    return app_id if isinstance(app_id, int) else None


async def require_user_id(
    principal: Annotated[Principal, Depends(require_principal)],
    user_id: Annotated[
        str,
        Query(
            # ``min_length`` only, deliberately. It has a counterpart at the
            # identity boundary — ``verify_principal_token`` refuses a blank
            # subject id — so it can never reject a caller the credential
            # accepts. An upper bound has no such counterpart: ``GatewayUser.id``
            # is an unconstrained ``str``, so a cap here would 422 a caller whose
            # id is longer than it *even when the value matches the signed
            # principal*, locking them out of all 56 operations. That is a change
            # to who may call, which this change promises not to make. The log
            # line below is bounded instead — see ``log_safe.for_log``.
            alias=USER_ID_QUERY,
            min_length=1,
            description=USER_ID_DESCRIPTION,
        ),
    ],
) -> str:
    """Return the end user this request acts for, or raise.

    The one seam every user-scoped handler takes its id from. Three failures are
    answered here, and they are deliberately different answers:

    - no verified caller → ``401``, from :func:`require_principal` as before;
    - no ``user_id`` parameter (or an empty one) → ``422``, FastAPI's own
      validation failure, enveloped by the app-level handler;
    - a parameter naming someone other than the caller → ``403``.

    The third is the whole of the "nothing else changes" promise for a caller
    that names a person: such a request can only ever be scoped to itself,
    exactly as when the id was read off the principal.

    **The branch this docstring used to promise is now here.** For a caller
    naming no end user — an application acting alone — there is no second id to
    compare with, so the comparison is skipped and the parameter is authorized
    against the delegation instead. That check needs the bot, which this
    dependency does not have, so it happens one step later in
    :data:`GrantCheckedDep`. Nothing else moved: no handler, schema or path
    changed, because none of them ever named the user.

    Skipping the comparison is not skipping a check. An application that reaches
    an operation without also taking the grant-checked dependency would be
    unauthorized, which is exactly what the route inventory test refuses to
    allow — see ``admission.py``.
    """
    if caller_app_id(principal) is not None and not caller_names_a_user(principal):
        # An application acting alone. It names the user it acts for; whether it
        # may is the grant's answer, not this function's.
        #
        # The comparison is skipped only when an application is **positively
        # identified**. Testing "names no user" alone would be fail-open: a
        # ``None`` principal, an empty mapping, a blank id — every unusable
        # credential also names no user, and each would have been waved through
        # here instead of refused below. Requiring a visible ``app_id`` means an
        # unrecognised principal falls to ``caller_owner_id`` and its 401.
        return user_id
    caller = caller_owner_id(principal)
    if user_id != caller:
        # Both ids are logged because the response cannot carry them: it is a
        # fixed "Forbidden", so this line is an operator's only record of which
        # user a partner integration asked for.
        #
        # The rejected value goes through ``log_safe.for_log``, and that is not
        # decoration. This branch runs *only* when the parameter is not the
        # caller's, so by construction the value is one the caller chose and the
        # server refused — arbitrary text, unbounded now that the request-level
        # cap is gone, newlines included. Formatted raw it would let the party
        # being refused append convincing extra lines to the log and poison the
        # audit trail of refusals, and pad each one to any length they like.
        # ``app.py``'s 422 handler drops the caller's raw input entirely for the
        # same reason; this keeps a bounded, escaped form because *which* user
        # was named is the whole diagnostic value here.
        logger.warning(
            "%s=%s does not match the verified caller %s",
            USER_ID_QUERY,
            for_log(user_id),
            caller,
        )
        raise UserIdMismatchError("request user id is not the verified caller")
    return user_id


#: What a user-scoped handler declares to receive the request's user id.
#:
#: Defined once and imported by every router, rather than re-declared per module
#: like ``PrincipalDep``: it is the same dependency everywhere, and a second
#: spelling of it is a second thing to keep in step with delegation.
UserIdDep = Annotated[str, Depends(require_user_id)]


async def require_acting_caller(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
    user_id: UserIdDep,
) -> ActingCaller:
    """Who this request acts for, and what governs it.

    Built once per request and shared by everything downstream, so the two
    questions — "as whom?" and "under what authority?" — are answered together
    rather than rediscovered at each use.

    **The grant reader is resolved only for an application, and only then.** It
    is pulled from the app's injector here rather than declared as an injected
    parameter, because a declared one would be resolved on *every* request to
    every operation that takes this dependency — making the grant service a hard
    requirement of routes that will never consult it, and of every test app that
    mounts one. A human caller carries ``app_id=None`` and no reader at all,
    which is what makes it structurally impossible for one to be resolved
    against a grant.
    """
    # **Only a caller with no human on the wire is governed by a grant.** A
    # request carrying a user *and* an app is a human request — the gateway
    # forwards the whole identity set it resolved, so an App rides along on
    # every route that declares it (bot logs, the authorization group, anything
    # under a ``user: required, app: optional`` rule). Keying off the presence
    # of an app alone would put all of those through a grant lookup and refuse
    # the ones with no grant: a large, silent regression for callers who did
    # nothing but present the credential the gateway asked them for.
    app_id = None if caller_names_a_user(principal) else caller_app_id(principal)
    return ActingCaller(
        user_id=user_id,
        app_id=app_id,
        grants=_grant_reader(request) if app_id is not None else None,
    )


def _grant_reader(request: Request) -> BotAppGrantServiceProtocol | None:
    """The grant service for this app, or ``None`` when it is not wired.

    ``None`` is not a shrug: :meth:`ActingCaller.require_bot` refuses outright
    when it has no reader, so an application caller on an app without the grant
    service is denied rather than admitted unchecked. Returning ``None`` here
    and failing closed there keeps the two concerns apart — this one knows how
    to find the service, that one knows what its absence means.
    """
    injector = getattr(request.app.state, "injector", None)
    if injector is None:
        return None
    try:
        return injector.get(BotAppGrantServiceProtocol)
    except Exception:  # noqa: BLE001 — any resolution failure is "not wired"
        logger.warning(
            "no %s bound; an application caller cannot be authorized on this app",
            BotAppGrantServiceProtocol.__name__,
        )
        return None


#: The acting caller, for handlers that need to know what governs the request.
ActingCallerDep = Annotated[ActingCaller, Depends(require_acting_caller)]

#: Where a bot id may be found on the wire, in the order it is looked for.
#:
#: Path before query, deliberately: a path segment *names* the addressed
#: resource, while a query parameter is scope alongside it. On the operations
#: that carry both spellings the path is the one that identifies the bot, and
#: reading the query first would let a caller aim the grant check at one bot
#: while the handler acted on another.
_BOT_ID_KEY = "bot_id"


async def require_granted_bot(
    request: Request,
    caller: ActingCallerDep,
) -> str:
    """Authorize the addressed bot for an application caller; a no-op for a human.

    Returns the resolved **owner** of the addressed bot — which the
    engine-runtime groups need and the user-scoped groups discard. One lookup,
    one place, whichever group is asking.

    For a human caller this asks nothing at all: the operation's own owner-scoped
    resolve already refuses a bot that is not theirs, and re-deciding it here
    would risk a second, different answer.

    For an application it is the authorization: no live grant for
    ``(app, bot, delegating user)`` raises :class:`GrantNotResolvableError`,
    which the app maps to a ``404`` byte-identical to a nonexistent bot.

    Five operations have no bot on the wire — one carries it in the body, four
    name a skill — and they are **named in the table**, not detected by their
    emptiness. This dependency defers for exactly those, and their handlers
    resolve the bot and call ``require_bot`` themselves before acting. Naming
    them is what keeps "no bot id" from becoming a way through: any *other*
    operation arriving here without one is refused.
    """
    if _defers_to_its_handler(request):
        return caller.user_id
    own_bot = _resolves_owner_scoped(request)
    bot_id = request.path_params.get(_BOT_ID_KEY) or request.query_params.get(
        _BOT_ID_KEY
    )
    if not bot_id:
        if not caller.is_application:
            # A human caller on an operation that scopes some other way. Nothing
            # to check, and nothing this dependency can usefully return.
            return caller.user_id
        raise GrantNotResolvableError(
            "no bot id on a grant-checked request; the operation must resolve "
            "its own bot before acting"
        )
    return caller.require_bot(str(bot_id), must_be_own_bot=own_bot)


def _resolves_owner_scoped(request: Request) -> bool:
    """Whether this operation resolves its bot as ``(bot_id, delegating user)``.

    True for Mode A1, whose groups read through ``get_by_id_and_owner``; false
    for A2, which takes the addressed owner from the grant instead. The
    difference decides whether a grant naming *another* owner can legitimately
    authorize the request — on A1 it cannot, because ``bot_id`` is not unique
    across owners and the operation would act on the delegating user's own
    same-named bot.

    Unknown routes answer ``True``, the stricter side: an operation this cannot
    classify has not been placed in a mode, and it is refused a moment later
    anyway.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return True
    return ADMISSION.get((request.method, path)) is not AdmissionMode.A2


def _defers_to_its_handler(request: Request) -> bool:
    """Whether this operation resolves its own bot, per ``admission.py``.

    An allow-list read from the table rather than a shape test. "The request
    carries no ``bot_id``" describes the five deferring operations *and* every
    mistake that would look like them — a renamed parameter, a route placed in a
    grant-checked mode by accident. Naming them means a mistake is refused while
    the five are served, and that the exception list cannot grow by accident:
    adding to it is an edit to a table the inventory test reads.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return False
    key = (request.method, path)
    return key in BODY_BOT_ID_OPERATIONS or key in SKILL_SCOPED_OPERATIONS


#: What a grant-checked handler declares to have its bot authorized.
GrantCheckedDep = Annotated[str, Depends(require_granted_bot)]
