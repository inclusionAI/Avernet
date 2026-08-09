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
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep

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
    user_id: UserIdDep,
    owner_id: Annotated[
        str | None,
        Query(
            alias=OWNER_ID_QUERY,
            min_length=1,
            max_length=256,
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
    """
    return owner_id if owner_id is not None else user_id


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
