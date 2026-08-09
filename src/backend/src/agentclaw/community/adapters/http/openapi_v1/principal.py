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

Today the request's id and the credential's must still agree:
:func:`require_user_id` refuses a parameter that names anyone but the verified
caller, so **nothing about who may call what changes** — a request with no
verified principal is still refused, and a caller still reaches only its own
data. That equality check is the one line delegation will relax (auth design
§15); until then it is what keeps the parameter from being a way to read someone
else's bots.

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

from fastapi import Depends, Query

from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    MissingPrincipalError,
    UserIdMismatchError,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: The query parameter naming the end user a request acts for.
USER_ID_QUERY = "user_id"

#: What every user-scoped operation publishes for it. Defined once so the
#: parameter reads identically across all 56 of them.
USER_ID_DESCRIPTION = (
    "The end user this request acts for. Every read and write is scoped to it. "
    "It must currently be the authenticated caller's own id; naming another "
    "user is refused (403)."
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


async def require_user_id(
    principal: Annotated[Principal, Depends(require_principal)],
    user_id: Annotated[
        str,
        Query(
            alias=USER_ID_QUERY,
            min_length=1,
            max_length=256,
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

    The last one is the whole of the "nothing else changes" promise. A request
    can only ever be scoped to the caller, exactly as when the id was read off
    the principal; the parameter states that scope instead of leaving it implied.
    When an App may act for a user (auth design §15), this function stops
    comparing the two ids and asks whether the delegation was granted — and no
    handler, schema or path changes, because none of them ever named the user.
    """
    caller = caller_owner_id(principal)
    if user_id != caller:
        # Both ids are logged because the response cannot carry them: it is a
        # fixed "Forbidden", so this line is an operator's only record of which
        # user a partner integration asked for.
        #
        # The rejected value is quoted with ``%r``, and that is not decoration.
        # This branch runs *only* when the parameter is not the caller's, so by
        # construction the value is one the caller chose and the server refused
        # — up to 256 characters of arbitrary text, newlines included. Formatted
        # raw it would let the party being refused append convincing extra lines
        # to the log and poison the audit trail of refusals. ``repr`` escapes
        # them, so a forged line arrives as one visibly-quoted string.
        # ``app.py``'s 422 handler drops the caller's raw input for the same
        # reason; this keeps it because *which* user was named is the whole
        # diagnostic value here, and escaping is enough to make it safe.
        logger.warning(
            "%s=%r does not match the verified caller %s",
            USER_ID_QUERY,
            user_id,
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
