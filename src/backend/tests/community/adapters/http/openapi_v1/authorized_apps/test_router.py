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
from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_app_grant.models import (
    BotAppGrantLogModel,
    BotAppGrantModel,
)
from agentclaw.community.core.bot_app_grant.services import BotAppGrantService
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
BOT = "b-1"
APP_ID = 42
APP_NAME = "partner-platform"


def _caller(app_id: int | None = APP_ID, app_name: str = APP_NAME) -> VerifiedCaller:
    """A verified caller naming the owner, and optionally an application."""
    principals: list = [UserPrincipal(subject=GatewayUser(id=OWNER, username=OWNER))]
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

    return BotAppGrantService(BotAppGrantRepository(_Db()))


@pytest.fixture
def bots():
    """A bot service that owns exactly ``BOT`` for exactly ``OWNER``.

    ``get_bot`` is the whole authority model on this surface: it raises for a
    bot that is absent *or* not the caller's, which is what makes a non-owner
    indistinguishable from a stranger.
    """

    class _Bots:
        def get_bot(self, bot_id: str, user_id: str):
            if bot_id != BOT or user_id != OWNER:
                raise BotNotFoundError(f"Bot not found: {bot_id}")
            return {"bot_id": bot_id, "owner_id": user_id}

    return _Bots()


def _build(grants, bots, caller):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bots)
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
def client(grants, bots):
    return user_scoped_client(_build(grants, bots, _caller()), OWNER)


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


def test_non_owner_answer_is_byte_identical_to_absent_bot(grants, bots):
    """A caller who may not manage the bot learns nothing — not even that it exists.

    Compares status *and* body, because "byte-identical" is the promise. The
    two requests differ only in which bot they name: one the owner does not
    own, one that does not exist at all.
    """
    client = user_scoped_client(_build(grants, bots, _caller()), OWNER)

    not_mine = client.get("/openapi/v1/bots/someone-elses-bot/authorized-apps")
    absent = client.get("/openapi/v1/bots/no-such-bot-at-all/authorized-apps")

    assert not_mine.status_code == absent.status_code == 404
    assert _without_request_id(not_mine.json()) == _without_request_id(absent.json())


def test_collaborator_may_operate_but_may_not_grant(grants, bots):
    """Narrower than the operator bar, deliberately.

    ``core/engine_runtime/gate.py`` admits a member-level collaborator to
    *operate* a bot. Managing its authorizations is a different power, and this
    surface does not grant it: the owner-scoped read refuses anyone who is not
    the owner, collaborator or not.
    """
    collaborator = "u-collab"
    caller = VerifiedCaller(
        principals=(
            UserPrincipal(subject=GatewayUser(id=collaborator, username=collaborator)),
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=APP_ID,
                    app_name=APP_NAME,
                    owners=collaborator,
                    tenant="teamclaw",
                ),
            ),
        )
    )
    client = user_scoped_client(_build(grants, bots, caller), collaborator)

    resp = client.post(f"/openapi/v1/bots/{BOT}/authorized-apps", json={})

    assert resp.status_code == 404, resp.json()


def test_application_view_is_scoped_to_the_calling_app(grants, bots):
    """Two applications, one owner: each sees only its own authorizations."""
    first = user_scoped_client(_build(grants, bots, _caller(app_id=42)), OWNER)
    second = user_scoped_client(
        _build(grants, bots, _caller(app_id=99, app_name="other")), OWNER
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
