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

``stage`` has since outgrown this group — the per-bot file operations (engine
config, identity) address the same three runtimes and import it from here for
that reason. It stays in this package because this is where the vocabulary and
its enum are defined, not because the group owns the parameter; ``owner_id``,
whose adjudication *is* engine-runtime's, has not moved either.

``owner_id`` is also the one parameter on this surface whose *source* depends on
the caller. For a human it is the request's, defaulting to themselves; for an
application it comes from the grant record, and a request that names a different
owner is refused. See :func:`resolve_owner_id`.

On the wire the parameter is now called ``entity_id``; ``owner_id`` is its
retiring alias, published as deprecated and read only while ``entity_id`` is
absent (``principal.addressed_owner`` holds the rule, once, for every reader).
The handler-side name stays ``owner_id`` — it is what the value *is*, and every
engine-runtime handler and core seam already spells it so — only the published
spelling changes.
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
    ENTITY_ID_QUERY,
    OWNER_ID_DESCRIPTION,
    OWNER_ID_QUERY,
    ActingCallerDep,
    AddressedBotGrantDep,
    addressed_owner,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ENTITY_ID_DESCRIPTION as _ADDRESSED_OWNER_DESCRIPTION,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: What every engine-runtime operation publishes for ``entity_id``: the text
#: shared with every other group that takes it, plus this group's adjudication.
ENTITY_ID_DESCRIPTION = (
    _ADDRESSED_OWNER_DESCRIPTION + " The caller must be the "
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

#: What an operation that **writes** to a bot's runtime publishes for ``stage``.
#:
#: A separate description, not a separate parameter: the write takes the same
#: values from the same enum, and what differs is the answer it gives for two of
#: them. Publishing the read's text on a write would advertise verify and online
#: as addressable there.
WRITE_STAGE_DESCRIPTION = (
    "Which of the bot's runtimes this request addresses. Only the draft — the "
    "bot's own workspace — accepts writes, and it is the default. A published "
    "runtime is what a release produced and is replaced by publishing again, "
    "never edited: naming verify or online is refused (409) and nothing is "
    "written anywhere."
)


async def resolve_owner_id(
    caller: ActingCallerDep,
    granted_owner_id: AddressedBotGrantDep,
    entity_id: Annotated[
        str | None,
        Query(
            alias=ENTITY_ID_QUERY,
            # ``min_length`` only, matching ``user_id``'s deliberate choice in
            # ``principal.py``: owner ids come from the same unconstrained
            # gateway subject-id space, and a cap here would 422 a collaborator
            # addressing a legitimately long owner id before adjudication ever
            # ran — while the owner themselves (parameter omitted) sailed
            # through.
            min_length=1,
            description=ENTITY_ID_DESCRIPTION,
        ),
    ] = None,
    owner_id: Annotated[
        str | None,
        Query(
            alias=OWNER_ID_QUERY,
            min_length=1,
            # Published as deprecated so generated clients and the rendered
            # document carry the migration notice without a separate channel.
            deprecated=True,
            description=OWNER_ID_DESCRIPTION,
        ),
    ] = None,
) -> str:
    """The bot owner this request addresses; the caller when unnamed.

    ``str | None`` is the external input boundary: absent is a real state
    meaning "my own bot", and it must stay distinguishable from an empty
    string (a 422). Downstream never sees the ``None`` — this dependency is
    where the default is applied, once.

    Two spellings arrive — ``entity_id`` and its retiring alias ``owner_id`` —
    and ``principal.addressed_owner`` collapses them to one value before any
    of the below runs: ``entity_id`` when present, else ``owner_id``, and a
    422 when both are present and disagree. The grant dependency this consumes
    read the wire through the same function, so what it authorized and what
    this returns cannot name different owners.

    Deliberately **no adjudication here**: whether the caller may operate the
    named owner's bot needs the resolved bot record (the collaborator table
    is keyed on its primary key), so the answer lives in
    ``core/engine_runtime``'s resolve, behind the same masked 404 as a bot
    that does not exist. This dependency only decides *which owner is being
    asked about* — exactly as ``require_user_id`` only decides which user the
    request acts for.

    **This is the one place an application caller differs from a human on these
    runtime operations.** For an application the addressed owner comes from the
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
    named = addressed_owner(entity_id, owner_id)
    if caller.is_application:
        if named is not None and named != granted_owner_id:
            # The addressed owner goes to the log bounded, and stays out of the
            # exception message: that message reaches a log line verbatim, and
            # both spellings are declared ``min_length=1`` with no upper bound,
            # so raw it would let a refused caller pad every refusal to any size.
            logger.warning(
                "[engine_runtime] app_id=%s addressed owner=%s, which its "
                "grant does not cover",
                caller.app_id,
                for_log(named),
            )
            raise GrantNotResolvableError(
                f"app {caller.app_id} addressed an owner its grant does not cover"
            )
        return granted_owner_id
    return named if named is not None else caller.user_id


#: What an engine-runtime handler declares to receive the addressed owner.
OwnerIdDep = Annotated[str, Depends(resolve_owner_id)]

#: What an engine-runtime handler declares to receive the addressed stage.
#: Declared with a default at each handler (``StageQuery = RuntimeStage.DRAFT``)
#: rather than inside ``Query(...)``, so the published schema carries the
#: default and the handler signature states it in one place.
StageQuery = Annotated[RuntimeStage, Query(description=STAGE_DESCRIPTION)]

#: The same, for a handler that writes to the addressed runtime.
WriteStageQuery = Annotated[RuntimeStage, Query(description=WRITE_STAGE_DESCRIPTION)]

__all__ = [
    "ENTITY_ID_DESCRIPTION",
    "ENTITY_ID_QUERY",
    "OWNER_ID_QUERY",
    "OwnerIdDep",
    "StageQuery",
    "WriteStageQuery",
    "resolve_owner_id",
]
