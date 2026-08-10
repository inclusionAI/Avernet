"""Endpoint tests for ``/openapi/v1`` bot authorizations.

A minimal app hosts both routers with the caller principal overridden and the
services bound through the injector, mirroring ``test_bots_endpoints.py``.

The grant service is the **real** one over a real in-memory database rather than
a mock: what these tests are about — that a listing cannot widen, that a
non-owner learns nothing, that withdrawing something absent reads differently
from withdrawing something present — are outcomes, and a mock would only
confirm the handler calls itself the way it was written.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.adapters.http.openapi_v1.authorized_apps.gating import (
    CANDIDATE_CAP,
)
from agentclaw.community.adapters.http.openapi_v1.authorized_apps.router import (
    app_view_router,
    router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
    require_user_and_app_principal,
)
from agentclaw.community.api.bot_app_grant_service import (
    BotAppGrantServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_app_grant.models import (
    BotAppGrantLogModel,
    BotAppGrantModel,
)
from agentclaw.community.core.bot_app_grant.services import BotAppGrantService
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from agentclaw.community.core.repository.implementations.bot.app_grant import (
    BotAppGrantRepository,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

OWNER = "u-1"
#: A member-level collaborator on ``BOT`` — someone who may operate it without
#: owning it, which is the case this group exists to serve.
COLLAB = "u-2"
#: A user with no relationship to ``BOT`` at all.
STRANGER = "u-9"
#: Another owner entirely, who also has a bot called ``default``. Their
#: existence is what makes the id ambiguous tenant-wide.
OTHER_OWNER = "u-3"
#: Collaborates on **both** ``default`` bots at member level, so ``bot_id``
#: alone genuinely cannot say which one they mean.
DOUBLE_COLLAB = "u-4"
#: Reaches many same-named bots but may operate exactly one. Deliberately more
#: than a small bound would fetch and fewer than the cap, so the resolve has to
#: weigh all of them to find the answer.
CROWDED_COLLAB = "u-5"
#: Reaches more than the resolve is willing to weigh at all.
OVERFLOW_COLLAB = "u-6"
#: Padding for ``CROWDED_COLLAB`` — comfortably past any small fetch limit.
CROWD_SIZE = 12
BOT = "b-1"
#: A legacy ``default``-style id shared by several live bots, which the
#: owner-blind resolve must fail closed on rather than pick a row for.
AMBIGUOUS_BOT = "default"
#: ``BOT``'s primary key. The collaborator table is keyed on it rather than on
#: ``bot_id``, which is not unique across owners.
BOT_PK = 7
#: ``AMBIGUOUS_BOT``'s primary key under ``OWNER``. Another owner's row with the
#: same ``bot_id`` would have a different one — which is the whole problem.
AMBIGUOUS_PK = 8
#: ``AMBIGUOUS_BOT``'s primary key under ``OTHER_OWNER``: same ``bot_id``,
#: different bot.
OTHER_AMBIGUOUS_PK = 9
APP_ID = 42
APP_NAME = "partner-platform"


def _caller(
    app_id: int | None = APP_ID,
    app_name: str = APP_NAME,
    user_id: str = OWNER,
) -> VerifiedCaller:
    """A verified caller naming a user, and optionally an application."""
    principals: list = [
        UserPrincipal(subject=GatewayUser(id=user_id, username=user_id))
    ]
    if app_id is not None:
        principals.append(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=app_id,
                    app_name=app_name,
                    owners=OWNER,
                    tenant="teamclaw",
                ),
            )
        )
    return VerifiedCaller(principals=tuple(principals))


@pytest.fixture
def sessions():
    """One in-memory database, shared across threads.

    ``StaticPool`` plus ``check_same_thread=False`` is load-bearing, not
    boilerplate: ``TestClient`` runs the app in a worker thread, and a default
    in-memory SQLite engine hands each thread its own connection — which means
    its own empty database. Without this the tables created here are invisible
    to the request and every call fails with "no such table".
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[BotAppGrantModel.__table__, BotAppGrantLogModel.__table__]
    )
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def grants(sessions):
    class _Db:
        @contextmanager
        def _session(self):
            session = sessions()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        def orm_session(self):
            return self._session()

        def transactional_orm_session(self):
            return self._session()

    class _LiveBots:
        """Every bot is live; deletion is covered in the service tests."""

        def filter_live_bots(
            self, pairs: list[tuple[str, str]]
        ) -> set[tuple[str, str]]:
            return set(pairs)

    return BotAppGrantService(BotAppGrantRepository(_Db()), _LiveBots())


@pytest.fixture
def bots():
    """A bot service holding exactly ``BOT``, owned by ``OWNER``.

    ``get_bot_by_id`` resolves by id alone and decides **nothing** about who may
    reach it — that is the collaborator double's job below. The split mirrors
    production: resolution and adjudication are two steps, and only running both
    is a check.
    """

    class _Bots:
        def get_bot(self, bot_id: str, user_id: str):
            """Owner-scoped, as production is: only the caller's own bot.

            ``OWNER`` holds both ``BOT`` and a legacy ``AMBIGUOUS_BOT``. This
            read resolves the second correctly *because* it is owner-scoped —
            which is exactly what the owner-blind read below cannot do.
            """
            if user_id != OWNER:
                raise BotNotFoundError(f"Bot not found: {bot_id}")
            if bot_id == BOT:
                return {"id": BOT_PK, "bot_id": bot_id, "owner_id": OWNER}
            if bot_id == AMBIGUOUS_BOT:
                return {"id": AMBIGUOUS_PK, "bot_id": bot_id, "owner_id": OWNER}
            raise BotNotFoundError(f"Bot not found: {bot_id}")

        def get_bot_by_id(self, bot_id: str):
            """Owner-blind, and fails closed when the id is ambiguous.

            ``AMBIGUOUS_BOT`` stands for a legacy ``default`` bot: several live
            rows share the id, so no single owner can be resolved from it alone.
            """
            if bot_id == AMBIGUOUS_BOT:
                raise BotLookupAmbiguousError(bot_id)
            if bot_id != BOT:
                raise BotNotFoundError(f"Bot not found: {bot_id}")
            return {"id": BOT_PK, "bot_id": bot_id, "owner_id": OWNER}

        def list_bots_reachable_by_id(
            self, bot_id: str, caller_id: str, limit: int
        ):
            """The caller's own candidates — what breaks the ambiguity above.

            Reachability is ownership **or** collaboration at any level, which
            is deliberately below the operator bar: the caller filters what
            comes back. ``DOUBLE_COLLAB`` reaches both ``default`` bots, which
            is the case that stays refused.

            ``limit`` is honoured, and unordered truncation is modelled the way
            the real query behaves: the extras are prepended, so a caller that
            trimmed and then answered would lose the operable bot rather than
            one of the padding rows.
            """
            everything = [
                {"id": BOT_PK, "bot_id": BOT, "owner_id": OWNER},
                {"id": AMBIGUOUS_PK, "bot_id": AMBIGUOUS_BOT, "owner_id": OWNER},
                {
                    "id": OTHER_AMBIGUOUS_PK,
                    "bot_id": AMBIGUOUS_BOT,
                    "owner_id": OTHER_OWNER,
                },
            ]
            reachable = {
                OWNER: {BOT_PK, AMBIGUOUS_PK},
                COLLAB: {BOT_PK, OTHER_AMBIGUOUS_PK},
                DOUBLE_COLLAB: {AMBIGUOUS_PK, OTHER_AMBIGUOUS_PK},
            }.get(caller_id, set())
            matches = [
                bot
                for bot in everything
                if bot["bot_id"] == bot_id and bot["id"] in reachable
            ]
            crowd = {CROWDED_COLLAB: CROWD_SIZE, OVERFLOW_COLLAB: CANDIDATE_CAP + 4}
            if caller_id in crowd and bot_id == AMBIGUOUS_BOT:
                # Reachable-but-not-operable padding, with the one operable bot
                # **last** — so any truncation drops precisely the answer.
                matches = [
                    {"id": 1000 + n, "bot_id": AMBIGUOUS_BOT, "owner_id": f"o-{n}"}
                    for n in range(crowd[caller_id])
                ] + [
                    {
                        "id": OTHER_AMBIGUOUS_PK,
                        "bot_id": AMBIGUOUS_BOT,
                        "owner_id": OTHER_OWNER,
                    }
                ]
            return matches[:limit]

    return _Bots()


@pytest.fixture
def collaborators():
    """The role table: ``OWNER`` owns ``BOT``, ``COLLAB`` is a member on it.

    Mirrors ``CollaboratorService.get_permission_level``, including its owner
    short-circuit — which is what makes the owner reach the bot without a row.
    Anyone else is ``NONE`` and is refused by ``require_bot_operator``.
    """

    class _Collaborators:
        def get_permission_level(self, bot_pk: int, user_id: str, owner_id: str):
            if user_id == owner_id:
                return PermissionLevel.OWNER
            if bot_pk == BOT_PK and user_id == COLLAB:
                return PermissionLevel.MEMBER
            # ``COLLAB`` is a member on *another owner's* ``default`` and on
            # none of ``OWNER``'s — the shape the ambiguous id has to resolve.
            if bot_pk == OTHER_AMBIGUOUS_PK and user_id == COLLAB:
                return PermissionLevel.MEMBER
            if user_id == DOUBLE_COLLAB and bot_pk in (
                AMBIGUOUS_PK,
                OTHER_AMBIGUOUS_PK,
            ):
                return PermissionLevel.MEMBER
            # Member on exactly one of the crowd. The rest come back from the
            # reachability query but adjudicate to NONE — the gap between
            # "reachable" and "operable" the design turns on, and the reason
            # the filter cannot be replaced by the query.
            if user_id in (CROWDED_COLLAB, OVERFLOW_COLLAB):
                if bot_pk == OTHER_AMBIGUOUS_PK:
                    return PermissionLevel.MEMBER
                return PermissionLevel.NONE
            return PermissionLevel.NONE

    return _Collaborators()


def _build(grants, bots, collaborators, caller):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bots)
            binder.bind(CollaboratorServiceProtocol, to=collaborators)
            binder.bind(BotAppGrantServiceProtocol, to=grants)

    app = FastAPI()
    app.include_router(router)
    app.include_router(app_view_router)
    app.dependency_overrides[require_principal] = lambda: caller
    app.dependency_overrides[require_user_and_app_principal] = lambda: caller
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return app


@pytest.fixture
def client(grants, bots, collaborators):
    """The bot's owner."""
    return user_scoped_client(_build(grants, bots, collaborators, _caller()), OWNER)


@pytest.fixture
def collab_client(grants, bots, collaborators):
    """A member-level collaborator: may operate the bot, does not own it."""
    return user_scoped_client(
        _build(grants, bots, collaborators, _caller(user_id=COLLAB)), COLLAB
    )


@pytest.fixture
def stranger_client(grants, bots, collaborators):
    """No relationship to the bot. Must be answered as if it did not exist."""
    return user_scoped_client(
        _build(grants, bots, collaborators, _caller(user_id=STRANGER)), STRANGER
    )


def _ok(resp, code=200000, status=200):
    """Assert a success envelope and return its payload.

    Both halves are checked. The surface carries a per-operation code in the
    envelope (``201000`` for a creation) **and** sets the matching HTTP status,
    as every other creation route on this surface does — ``created()`` only
    supplies the envelope half, so the route must declare ``status_code=201``
    itself.
    """
    body = resp.json()
    assert resp.status_code == status, body
    assert body["code"] == code, body
    return body["data"]


def test_grant_reads_app_from_principal_not_from_request(client, sessions):
    """The application is never nameable by the request.

    There is no ``app_id`` parameter on this operation, so the only application
    a grant can name is the one whose credential is on the call. A body that
    tries to say otherwise is ignored, not honoured.
    """
    data = _ok(client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={"app_id": 999}), 201000, 201)

    assert data["app_id"] == APP_ID
    assert data["app_name"] == APP_NAME
    with sessions() as session:
        assert session.query(BotAppGrantModel).one().app_id == APP_ID


def test_grant_is_idempotent_over_http(client, sessions):
    first = _ok(client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={}), 201000, 201)
    second = _ok(client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={}), 201000, 201)

    assert first["granted_at"] == second["granted_at"]
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 1


def test_owner_lists_the_apps_that_can_reach_the_bot(client):
    client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})

    data = _ok(client.get(f"/openapi/v1/bots/{BOT}/authorized-apps"))

    assert [item["app_id"] for item in data["items"]] == [APP_ID]
    assert data["items"][0]["app_name"] == APP_NAME


def test_list_excludes_withdrawn_authorizations(client):
    client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})
    client.delete(f"/openapi/v1/bots/{BOT}/authorized-apps/{APP_ID}")

    data = _ok(client.get(f"/openapi/v1/bots/{BOT}/authorized-apps"))
    assert data["items"] == []


def test_revoke_absent_grant_is_404_distinct_from_successful_revoke(client):
    client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})

    first = client.delete(f"/openapi/v1/bots/{BOT}/authorized-apps/{APP_ID}")
    second = client.delete(f"/openapi/v1/bots/{BOT}/authorized-apps/{APP_ID}")

    assert first.status_code == 200, first.json()
    assert second.status_code == 404, second.json()


def test_stranger_answer_is_byte_identical_to_absent_bot(stranger_client):
    """A caller who may not operate the bot learns nothing — not even that it exists.

    Compares status *and* body, because "byte-identical" is the promise. The two
    requests differ only in which bot they name: one that exists and this caller
    has no relationship to, and one that does not exist at all.
    """
    refused = stranger_client.get(f"/openapi/v1/bots/{BOT}/authorized-apps")
    absent = stranger_client.get("/openapi/v1/bots/no-such-bot-at-all/authorized-apps")

    assert refused.status_code == absent.status_code == 404
    assert _without_request_id(refused.json()) == _without_request_id(absent.json())


def test_stranger_may_not_grant(stranger_client):
    """The widening admits collaborators, not everyone."""
    resp = stranger_client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})

    assert resp.status_code == 404, resp.json()


def test_collaborator_may_delegate_the_access_they_have(collab_client, sessions):
    """The widening, and the reason this feature exists.

    ``core/engine_runtime/gate.py`` admits a member-level collaborator to
    *operate* a bot, and delegation is now measured by the same bar: you may
    lend exactly the access you hold. An earlier revision refused this, which
    left an integration onboarded by anyone but the bot's creator able to reach
    nothing — indistinguishably from a missing grant.

    The row records both people: the collaborator as the delegator, and the
    bot's real owner as the owner.
    """
    data = _ok(
        collab_client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={}),
        201000,
        201,
    )

    assert data["user_id"] == COLLAB, "the response names who delegated"
    with sessions() as session:
        row = session.query(BotAppGrantModel).one()
        assert (row.user_id, row.owner_id) == (COLLAB, OWNER)


def test_application_view_is_scoped_to_the_calling_app(grants, bots, collaborators):
    """Two applications, one owner: each sees only its own authorizations."""
    first = user_scoped_client(_build(grants, bots, collaborators, _caller(app_id=42)), OWNER)
    second = user_scoped_client(
        _build(grants, bots, collaborators, _caller(app_id=99, app_name="other")), OWNER
    )
    first.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})

    mine = _ok(first.get("/openapi/v1/bots/authorized"))
    theirs = _ok(second.get("/openapi/v1/bots/authorized"))

    assert [item["bot_id"] for item in mine["items"]] == [BOT]
    assert theirs["items"] == [], "another application must see nothing"


def test_application_view_empty_is_200_not_404(client):
    """Holding no authorizations is a valid answer, not a missing resource."""
    resp = client.get("/openapi/v1/bots/authorized")

    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["items"] == []


def test_application_view_excludes_withdrawn(client):
    client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})
    client.delete(f"/openapi/v1/bots/{BOT}/authorized-apps/{APP_ID}")

    data = _ok(client.get("/openapi/v1/bots/authorized"))
    assert data["items"] == []


def _without_request_id(body: dict) -> dict:
    """The response minus its per-request trace id, which is never equal."""
    return {key: value for key, value in body.items() if key != "request_id"}


# ── The owner's visibility and override ────────────────────────────────────


def _grant_both(client, collab_client):
    """One application, delegated on one bot by the owner and by a collaborator.

    Two rows, not one: they are two loans of two different authorities.
    """
    client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})
    collab_client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})


def test_owner_sees_a_grant_a_collaborator_made(client, collab_client):
    """Machine access to a bot is never invisible to the person who owns it.

    The owner did not create the collaborator's delegation and cannot be
    expected to know about it, which is exactly why the listing has to show it —
    and name who made it, or the owner learns only that *someone* did.
    """
    _grant_both(client, collab_client)

    listed = _ok(client.get(f"/openapi/v1/bots/{BOT}/authorized-apps"))

    assert {item["user_id"] for item in listed["items"]} == {OWNER, COLLAB}
    assert listed["total"] == 2


def test_collaborator_sees_only_their_own_delegation(client, collab_client):
    """A collaborator has no claim on their colleagues' delegations."""
    _grant_both(client, collab_client)

    listed = _ok(collab_client.get(f"/openapi/v1/bots/{BOT}/authorized-apps"))

    assert [item["user_id"] for item in listed["items"]] == [COLLAB]
    assert listed["total"] == 1


def test_owner_withdrawal_removes_every_delegation_of_that_app(
    client, collab_client, sessions
):
    """"Revoke this app's access to my bot" means all of it.

    Leaving the application still reaching the bot through a colleague's grant
    would not be a withdrawal, and the owner has no way to name the colleague's
    delegation separately — the path names only the application.
    """
    _grant_both(client, collab_client)

    resp = client.delete(f"/openapi/v1/bots/{BOT}/authorized-apps/{APP_ID}")

    assert resp.status_code == 200, resp.json()
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 0


def test_collaborator_withdrawal_leaves_the_owners_delegation_alone(
    client, collab_client, sessions
):
    """The narrow half of the same operation."""
    _grant_both(client, collab_client)

    resp = collab_client.delete(f"/openapi/v1/bots/{BOT}/authorized-apps/{APP_ID}")

    assert resp.status_code == 200, resp.json()
    with sessions() as session:
        rows = session.query(BotAppGrantModel).all()
        assert [row.user_id for row in rows] == [OWNER]


def test_collaborator_cannot_withdraw_the_owners_delegation(client, collab_client):
    """With only the owner's grant live, the collaborator has nothing to remove.

    404 rather than a silent success: "there was nothing of mine to withdraw"
    must not read as "withdrawn", and it must not reach across to a grant that
    is not theirs.
    """
    client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})

    resp = collab_client.delete(f"/openapi/v1/bots/{BOT}/authorized-apps/{APP_ID}")

    assert resp.status_code == 404, resp.json()


def test_an_ambiguous_bot_id_resolves_to_the_one_the_caller_can_operate(
    collab_client, sessions
):
    """The case the feature exists for, and it used to be refused.

    ``COLLAB`` is a member on ``OTHER_OWNER``'s ``default`` and on none of
    ``OWNER``'s. The owner-scoped read misses (they own nothing) and the
    owner-blind one fails closed (two live bots share the id), so the bot they
    can plainly operate was unaddressable — a member-level collaborator refused
    by a name collision with a stranger's bot.

    Resolving inside the caller's own reach removes the stranger's bot from the
    question, and one candidate remains.
    """
    resp = collab_client.post(
        f"/openapi/v1/bots/{AMBIGUOUS_BOT}/authorized-apps", json={}
    )

    assert resp.status_code == 201, resp.json()
    with sessions() as session:
        row = session.query(BotAppGrantModel).one()
        # The grant records the bot COLLAB can actually operate, not OWNER's
        # same-named one.
        assert (row.user_id, row.owner_id) == (COLLAB, OTHER_OWNER)


def test_an_ambiguous_bot_id_is_still_refused_when_the_caller_reaches_none(
    stranger_client,
):
    """A duplicated ``bot_id`` must not answer 500, and must not admit.

    ``BotLookupAmbiguousError`` is a bare ``RuntimeError`` and is not in this
    surface's status map, so letting it escape would answer 500 — telling an
    unrelated caller that two live bots share an id, on a surface whose entire
    refusal story is that a stranger learns nothing.
    """
    resp = stranger_client.get(f"/openapi/v1/bots/{AMBIGUOUS_BOT}/authorized-apps")

    assert resp.status_code == 404, resp.json()


def test_a_crowded_bot_id_still_finds_the_one_operable_bot(
    grants, bots, collaborators, sessions
):
    """A fetch bound must not decide the answer.

    The candidate rows come back unordered and the operator filter runs
    *after* them, so a bound small enough to trim a realistic set picks the
    outcome by whichever rows the database happened to return — and the one
    operable bot is as likely to be trimmed as any other. Here it is
    deliberately last among thirteen, so any truncation loses it and the caller
    is refused a delegation they may plainly make, for reasons no one can see.

    That is the same silent 404 this resolve was written to remove, which is
    why the bound is generous and every candidate under it is weighed.
    """
    client = user_scoped_client(
        _build(grants, bots, collaborators, _caller(user_id=CROWDED_COLLAB)),
        CROWDED_COLLAB,
    )

    resp = client.post(f"/openapi/v1/bots/{AMBIGUOUS_BOT}/authorized-apps", json={})

    assert resp.status_code == 201, resp.json()
    with sessions() as session:
        row = session.query(BotAppGrantModel).one()
        assert (row.user_id, row.owner_id) == (CROWDED_COLLAB, OTHER_OWNER)


def test_past_the_cap_the_resolve_refuses_instead_of_guessing(
    grants, bots, collaborators, sessions
):
    """Over the cap, refuse — do not answer from an arbitrary subset.

    Refusing on the *count*, before weighing any candidate, is what keeps the
    outcome from depending on row order. A caller past this is reaching more
    same-named bots than a bare ``bot_id`` can address at all.
    """
    client = user_scoped_client(
        _build(grants, bots, collaborators, _caller(user_id=OVERFLOW_COLLAB)),
        OVERFLOW_COLLAB,
    )

    resp = client.post(f"/openapi/v1/bots/{AMBIGUOUS_BOT}/authorized-apps", json={})

    assert resp.status_code == 404, resp.json()
    with sessions() as session:
        assert session.query(BotAppGrantModel).count() == 0


def test_an_ambiguous_bot_id_is_refused_when_the_caller_can_operate_both(
    grants, bots, collaborators
):
    """The genuine ambiguity, and the one case that must stay refused.

    ``DOUBLE_COLLAB`` is a member on both ``default`` bots. Narrowing to the
    caller's reach does not help here — they really can operate both, the
    request says only ``bot_id``, and it does not say which. Picking one would
    delegate the wrong bot silently, which is worse than refusing.
    """
    client = user_scoped_client(
        _build(grants, bots, collaborators, _caller(user_id=DOUBLE_COLLAB)),
        DOUBLE_COLLAB,
    )

    resp = client.post(f"/openapi/v1/bots/{AMBIGUOUS_BOT}/authorized-apps", json={})

    assert resp.status_code == 404, resp.json()


def test_the_owner_of_an_ambiguous_bot_id_still_works(client):
    """The owner-scoped read resolves a duplicated id correctly, as it always did.

    This is why the owner path is tried first rather than second. Reaching for
    the owner-blind read would turn three operations that work today into
    refusals for every owner of a legacy ``default`` bot — a functional
    regression, delivered quietly, to callers who did nothing.
    """
    granted = client.post(
        f"/openapi/v1/bots/{AMBIGUOUS_BOT}/authorized-apps", json={}
    )
    listed = client.get(f"/openapi/v1/bots/{AMBIGUOUS_BOT}/authorized-apps")

    assert granted.status_code == 201, granted.json()
    assert listed.status_code == 200, listed.json()
    assert [item["bot_id"] for item in listed.json()["data"]["items"]] == [
        AMBIGUOUS_BOT
    ]
