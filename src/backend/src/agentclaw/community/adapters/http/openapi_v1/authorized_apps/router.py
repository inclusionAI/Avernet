"""User-granted bot authorizations — ``/openapi/v1`` HTTP adapter.

Four operations over one record, read from both ends, with a deliberate
asymmetry in **which identities each requires**:

- **Granting** needs both parties on the same request: the delegating user's
  identity and the calling application's own credential. That is what makes it a
  consent moment rather than something one side can arrange alone. The
  application is never a parameter — it is read off the verified principal — so
  a request cannot point a grant at any application but the caller.
- **Withdrawing** and **the bot's list** need only a user. A withdrawal that
  required the application's cooperation would be no withdrawal at all, which is
  precisely the situation it exists for: a credential lost, rotated, or a
  relationship ended. And answering "which apps can reach my bot?" must not
  depend on holding any one application's key.
- **The application's list** needs both again, and here the application is not
  merely required, it is what the answer is *scoped by*. The gateway's auth
  runner resolves only the identities a route declares
  (``gateway/core/authn/_runner.py``), so on a user-only rule the App would
  never reach the signed principal and the query would have nothing to filter
  on. Declaring it is what makes the scoping possible at all.

**Authority is "may this user operate this bot", and no code here decides it.**
Every bot-scoped operation goes through ``gating.resolve_delegable_bot``, which
resolves the bot and hands the question to ``core/engine_runtime/gate.py``'s
``require_bot_operator`` — the same bar that decides who may *drive* a bot: its
owner, or a collaborator at member level or above. Anyone else raises
``BotNotFoundError`` and gets the answer a caller naming a nonexistent bot gets,
byte for byte, so the surface never confirms a bot exists to someone with no
business knowing.

That is a deliberate widening. This group used to be owner-only, on the argument
that giving a machine durable, human-free access is not the same power as
driving the bot yourself. The argument loses: a delegation is bounded by the
delegator's own live access, re-adjudicated on every request the application
makes, so it confers nothing they do not already hold and cannot outlive it. See
``gating.py`` for the full reasoning, which is the load-bearing part of this
feature.

**The record therefore names two people.** ``user_id`` is the delegating user —
whose access the application borrows — and ``owner_id`` is the bot's owner, a
different person whenever the bot is shared. Everything the application may
later do is scoped by the first; the second is what lets the owner keep sight of
it.

**The owner keeps final say over their own bot.** The listing shows every grant
standing against the bot, whoever delegated it, and the owner may withdraw any
of them — including one a collaborator made. A collaborator sees and withdraws
only their own. Machine access to a bot is never invisible to the person who
owns it.

Requiring an application identity is never what authorizes a call — the
delegating user's identity is. The App is present so the record can name it, not
to grant anyone anything.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorized_apps.schemas import (
    AuthorizedApp,
    AuthorizedBot,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    Deleted,
    Envelope,
    Page,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
    require_user_and_app_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted as deleted_envelope,
    envelope_errors,
    page,
)
from agentclaw.community.adapters.http.openapi_v1.authorized_apps.gating import (
    resolve_delegable_bot,
)
from agentclaw.community.api.bot_app_grant_service import (
    BotAppGrantServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.bot_app_grant.models import BotAppGrantRecord
from agentclaw.community.core.gateway_principal import PrincipalType
from agentclaw.community.di import Injected

#: The bot-scoped group — the owner's three operations.
router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/authorized-apps", tags=["authorized-apps"]
)

#: The app-scoped read, as its own literal component under the base. A second
#: router rather than a second path on the first: it is not beneath a bot, and
#: mounting it under ``{bot_id}`` would say it was.
#:
#: ``/openapi/v1/bots/authorized`` rather than a top-level
#: ``/openapi/v1/authorized-bots``, and that is a routing constraint rather than
#: a naming preference. The gateway resolves an upstream from the segment after
#: the version base and forwards only into configured domains, so a path outside
#: ``/openapi/v1/bots`` reaches no upstream at all — it would be refused at the
#: edge rather than served. ``test_path_convention.py`` holds the rule.
app_view_router = APIRouter(
    prefix="/openapi/v1/bots/authorized", tags=["authorized-apps"]
)

#: Both parties. The APP half is checked here; the USER half comes from
#: ``require_principal``, which this depends on and which refuses a caller
#: naming no end user. Granting is a consent moment, so it is one of the
#: operations that must never admit a machine caller acting alone — the
#: dependency chain is what guarantees that rather than a check in the handler.
UserAndAppDep = Annotated[Principal, Depends(require_user_and_app_principal)]

#: The owner alone.
PrincipalDep = Annotated[Principal, Depends(require_principal)]

#: Shared by both bot-scoped path declarations so the constraint cannot drift
#: between them.
BotIdPath = Annotated[str, Path(min_length=1, max_length=256)]

#: Whose bot the request addresses, defaulting to the caller's own.
#:
#: ``bot_id`` alone does not identify a bot — ``ac_bots`` has no unique key on
#: it, and the retired ``default`` convention gave many owners one — so the pair
#: is the address. Omitting this is what these operations have always done:
#: your own bot, or nothing.
#:
#: Named and shaped to match ``owner_id`` on the engine-runtime operations,
#: which have addressed bots this way all along. Inferring it instead was this
#: group's own invention, and it could not resolve the case it existed for: a
#: caller who collaborates on two owners' same-named bots can operate both, and
#: nothing about the request says which they mean.
#:
#: ``min_length`` only, matching ``user_id``: owner ids come from the same
#: unconstrained gateway subject-id space, and a cap here would 422 a
#: collaborator naming a legitimately long owner before adjudication ran, while
#: the owner themselves — parameter omitted — sailed through.
OwnerIdQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        description=(
            "The owner of the bot this request addresses. Defaults to the "
            "caller — name it only to reach a bot shared with you. The caller "
            "must be the bot's owner or a collaborator on it; anyone else is "
            "answered exactly as if the bot did not exist (404)."
        ),
    ),
]


def _calling_app(principal: Principal) -> tuple[int, str]:
    """The calling application's id and name, off the verified principal.

    Raises :class:`MissingPrincipalError` when the set names no application.
    ``require_user_and_app_principal`` has already refused that case, so this is
    the fail-closed answer for a route that forgot the dependency rather than a
    reachable branch — it refuses instead of defaulting, because the alternative
    on the app's list view is not a weaker check but an *unscoped* one.
    """
    for entry in principal.principals:
        if entry.type == PrincipalType.APP:
            return entry.app.app_id, entry.app.app_name
    raise MissingPrincipalError("no verified application identity on this request")


def _to_authorized_app(record: BotAppGrantRecord) -> AuthorizedApp:
    return AuthorizedApp(
        app_id=record.app_id,
        user_id=record.user_id,
        app_name=record.app_name,
        bot_id=record.bot_id,
        owner_id=record.owner_id,
        granted_at=record.gmt_create,
    )


def _to_authorized_bot(record: BotAppGrantRecord) -> AuthorizedBot:
    return AuthorizedBot(
        bot_id=record.bot_id,
        owner_id=record.owner_id,
        granted_at=record.gmt_create,
    )


@router.post(
    "",
    status_code=201,
    response_model=Envelope[AuthorizedApp],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def grant_authorized_app(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    principal: UserAndAppDep,
    owner_id: OwnerIdQuery = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    grants: BotAppGrantServiceProtocol = Injected(BotAppGrantServiceProtocol),
) -> Envelope[AuthorizedApp]:
    """Lend this bot's access to the calling application.

    The caller delegates **their own** access, so they must be able to operate
    the bot — its owner, or a collaborator at member level or above. The
    resolved owner is recorded alongside them, and is someone else whenever the
    bot is shared.

    ``owner_id`` names whose bot this is and defaults to the caller's own, so
    omitting it behaves exactly as this operation always has. Name it to
    delegate on a bot shared with you: ``bot_id`` alone does not identify a bot,
    and two owners may hold the same one.

    Idempotent: granting an authorization that is already in force returns it
    unchanged rather than failing, so a partner retrying a timed-out request is
    not punished for one that actually succeeded. Two *different* users
    delegating the same application on the same bot are two separate grants, not
    a repeat — they are lending two different authorities.
    """
    # Existence check and authority in one: raises BotNotFoundError -> 404 for a
    # bot that is absent or that this caller may not operate, indistinguishably.
    resolved_owner, _ = resolve_delegable_bot(
        bot_service, collaborators, bot_id=bot_id, caller_id=user_id,
        owner_id=owner_id,
    )
    app_id, app_name = _calling_app(principal)
    record = grants.grant(
        bot_id=bot_id,
        user_id=user_id,
        owner_id=resolved_owner,
        app_id=app_id,
        app_name=app_name,
    )
    return created(_to_authorized_app(record), request)


@router.get(
    "", response_model=Envelope[Page[AuthorizedApp]], responses=USER_SCOPED_403
)
@envelope_errors
async def list_authorized_apps(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    principal: PrincipalDep,
    owner_id: OwnerIdQuery = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    grants: BotAppGrantServiceProtocol = Injected(BotAppGrantServiceProtocol),
) -> Envelope[Page[AuthorizedApp]]:
    """Which applications can reach this bot, and who let each one in.

    **The owner sees everything; a collaborator sees only their own.** An owner
    who could not see a grant a colleague made would have machine access to
    their own bot that was invisible to them, which is the failure this whole
    record exists to prevent. A collaborator has no equivalent claim on their
    colleagues' delegations, so theirs is narrowed — the same reason their
    withdrawal is.

    ``owner_id`` says whose bot to read and defaults to the caller's own. It
    addresses the bot; it does not widen the answer — a collaborator naming the
    owner still sees only what they themselves delegated.
    """
    del principal  # authority comes from the adjudicated bot resolve below
    resolved_owner, _ = resolve_delegable_bot(
        bot_service, collaborators, bot_id=bot_id, caller_id=user_id,
        owner_id=owner_id,
    )
    records = grants.list_for_bot(bot_id=bot_id, owner_id=resolved_owner)
    if user_id != resolved_owner:
        records = [record for record in records if record.user_id == user_id]
    items = [_to_authorized_app(record) for record in records]
    return page(len(items), items, request)


@router.delete(
    "/{app_id}", response_model=Envelope[Deleted], responses=USER_SCOPED_403
)
@envelope_errors
async def revoke_authorized_app(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    principal: PrincipalDep,
    owner_id: OwnerIdQuery = None,
    app_id: int = Path(ge=1),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    collaborators: CollaboratorServiceProtocol = Injected(CollaboratorServiceProtocol),
    grants: BotAppGrantServiceProtocol = Injected(BotAppGrantServiceProtocol),
) -> Envelope[Deleted]:
    """Withdraw an application's authorization for this bot.

    The application is named in the path rather than read off a principal,
    because a withdrawal must not require the application's cooperation — that
    is precisely the situation it exists for: a credential lost, rotated, or a
    relationship ended. Withdrawing an authorization that does not exist answers
    404, distinctly from a successful withdrawal (``GrantNotFoundError``).

    **How much this withdraws depends on who asks**, and it has to: since a
    delegation is keyed on the user who made it, ``{app_id}`` alone no longer
    names one row.

    - The **owner** withdraws *every* delegation of this application on their
      bot. That is what an owner means by revoking an app's access to their own
      bot — a withdrawal that left it reaching them through a colleague's grant
      would not be a withdrawal — and it is why the path needs no extra segment
      naming whose grant to remove.
    - A **collaborator** withdraws only their own. Theirs is the loan they made;
      a colleague's is not theirs to call in.

    TODO(#951 follow-up): the owner's withdrawal is all-or-nothing, and it
    should not be. An owner who wants to cut off *one* colleague's delegation of
    an application — the colleague who left the team, say — currently has to
    withdraw every delegation of that application on the bot, including their
    own and other colleagues'. The record supports the narrower operation
    already (``revoke`` is keyed on the delegating user), so what is missing is
    a way to *name* whose delegation to withdraw: another path segment, or a
    parameter. Deliberately not added here — it is a new operation on the public
    surface, not a fix.

    ``owner_id`` names whose bot is being withdrawn from and defaults to the
    caller's own. Which delegations go still follows from who is asking, not
    from this parameter.
    """
    del principal
    resolved_owner, _ = resolve_delegable_bot(
        bot_service, collaborators, bot_id=bot_id, caller_id=user_id,
        owner_id=owner_id,
    )
    if user_id == resolved_owner:
        grants.revoke_app(bot_id=bot_id, owner_id=resolved_owner, app_id=app_id)
    else:
        grants.revoke(
            bot_id=bot_id, user_id=user_id, owner_id=resolved_owner, app_id=app_id
        )
    return deleted_envelope(request)


@app_view_router.get(
    "", response_model=Envelope[Page[AuthorizedBot]], responses=USER_SCOPED_403
)
@envelope_errors
async def list_authorized_bots(
    request: Request,
    user_id: UserIdDep,
    principal: UserAndAppDep,
    grants: BotAppGrantServiceProtocol = Injected(BotAppGrantServiceProtocol),
) -> Envelope[Page[AuthorizedBot]]:
    """The application's view — which bots may it reach as this user.

    Names no bot and so performs no bot-existence check, unlike the three above:
    there is nothing to mask. The result is one user's own delegations to the
    calling application, and an empty page discloses nothing — which is why
    holding none answers ``200`` with no items rather than ``404``.

    This is also the **only** complete view of what the application may reach.
    A delegated bot the user does not own appears in no listing of that user's
    bots, so without this it would be undiscoverable.
    """
    app_id, _ = _calling_app(principal)
    records = grants.list_for_app(app_id=app_id, user_id=user_id)
    items = [_to_authorized_bot(record) for record in records]
    return page(len(items), items, request)
