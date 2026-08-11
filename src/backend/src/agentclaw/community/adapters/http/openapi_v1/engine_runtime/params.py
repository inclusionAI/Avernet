"""The two parameters that name an engine-runtime operation's target.

The explicit-user-id change split "which user is this request for?" into
acquisition (the request names it) and adjudication (one seam decides). These
are the same split's second half for "whose bot, at which stage?": the request
names the owner and the stage here, and ``core/engine_runtime`` adjudicates
whether the caller may operate the named bot.

Both are **optional query parameters whose defaults preserve the old
contract** — a request that names neither behaves byte-for-byte as before
they existed. They follow the placement rule stated in
``openapi_v1/principal.py``: the query string, never a body field, never a
path segment, because — like ``user_id`` — they describe who and what the
call is *for*, not an attribute of any resource.

Defined once and imported by every engine-runtime router, like ``UserIdDep``:
a second spelling would be a second thing to keep in step.

``owner_id`` is also the one parameter on this surface whose *source* depends on
the caller. For a human it is the request's, defaulting to themselves; for an
application it comes from the grant record, and a request that names a different
owner is refused. See :func:`resolve_owner_id`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    GrantNotResolvableError,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    GrantCheckedDep,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: The query parameter naming the bot owner a request addresses.
OWNER_ID_QUERY = "owner_id"

#: What every engine-runtime operation publishes for it.
OWNER_ID_DESCRIPTION = (
    "The owner of the bot this request addresses. Defaults to the caller — "
    "name it only to operate a bot shared with you. The caller must be the "
    "bot's owner or a collaborator on it; anyone else is answered exactly as "
    "if the bot did not exist (404)."
)

#: What every engine-runtime operation publishes for ``stage``.
STAGE_DESCRIPTION = (
    "Which of the bot's runtimes this request addresses. Defaults to the "
    "draft — the bot's own workspace, the only runtime a personal bot has. "
    "A service bot's verify/online runtimes are addressable while live; a "
    "stage with no live runtime is refused (409)."
)


async def resolve_owner_id(
    caller: ActingCallerDep,
    granted_owner_id: GrantCheckedDep,
    owner_id: Annotated[
        str | None,
        Query(
            alias=OWNER_ID_QUERY,
            # ``min_length`` only, matching ``user_id``'s deliberate choice in
            # ``principal.py``: owner ids come from the same unconstrained
            # gateway subject-id space, and a cap here would 422 a collaborator
            # addressing a legitimately long owner id before adjudication ever
            # ran — while the owner themselves (parameter omitted) sailed
            # through.
            min_length=1,
            description=OWNER_ID_DESCRIPTION,
        ),
    ] = None,
) -> str:
    """The bot owner this request addresses; the caller when unnamed.

    ``str | None`` is the external input boundary: absent is a real state
    meaning "my own bot", and it must stay distinguishable from an empty
    string (a 422). Downstream never sees the ``None`` — this dependency is
    where the default is applied, once.

    Deliberately **no adjudication here**: whether the caller may operate the
    named owner's bot needs the resolved bot record (the collaborator table
    is keyed on its primary key), so the answer lives in
    ``core/engine_runtime``'s resolve, behind the same masked 404 as a bot
    that does not exist. This dependency only decides *which owner is being
    asked about* — exactly as ``require_user_id`` only decides which user the
    request acts for.

    **This is the one place an application caller differs from a human on these
    sixteen operations.** For an application the addressed owner comes from the
    **grant record** (``granted_owner_id``), never from the request, and an
    explicitly supplied value must agree with it. Two reasons it is refused here
    rather than left to fail downstream:

    - A request naming some other owner would 404 at the resolve anyway, but
      only *coincidentally* — two independent refusals happening to line up. A
      boundary that holds by coincidence is not a boundary.
    - The grant is what says which bot the delegation covers. Letting the
      request re-nominate the owner would let an application aim a grant it
      holds at a bot it does not, and rely on the next check to notice.

    A human caller's use of the parameter is untouched: naming another owner
    still works and is still adjudicated against the collaborator table.
    """
    if caller.is_application:
        if owner_id is not None and owner_id != granted_owner_id:
            # The addressed owner goes to the log bounded, and stays out of the
            # exception message: that message reaches a log line verbatim, and
            # ``owner_id`` is declared ``min_length=1`` with no upper bound, so
            # raw it would let a refused caller pad every refusal to any size.
            logger.warning(
                "[engine_runtime] app_id=%s addressed owner=%s, which its "
                "grant does not cover",
                caller.app_id,
                for_log(owner_id),
            )
            raise GrantNotResolvableError(
                f"app {caller.app_id} addressed an owner its grant does not cover"
            )
        return granted_owner_id
    return owner_id if owner_id is not None else caller.user_id


#: What an engine-runtime handler declares to receive the addressed owner.
OwnerIdDep = Annotated[str, Depends(resolve_owner_id)]

#: What an engine-runtime handler declares to receive the addressed stage.
#: Declared with a default at each handler (``StageQuery = RuntimeStage.DRAFT``)
#: rather than inside ``Query(...)``, so the published schema carries the
#: default and the handler signature states it in one place.
StageQuery = Annotated[RuntimeStage, Query(description=STAGE_DESCRIPTION)]

__all__ = [
    "OWNER_ID_QUERY",
    "OwnerIdDep",
    "StageQuery",
    "resolve_owner_id",
]
