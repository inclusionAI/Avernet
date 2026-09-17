"""The user-level delegation's three operations, over the real service.

``/openapi/v1/bots/authorized-apps`` — the same resource one level up from the
bot-scoped group, and the same asymmetry: granting needs the user and the
application on the wire, listing and withdrawing need only the user.

The service runs over a real in-memory database rather than a mock, for the
reason the bot-scoped router's tests give: what is asserted here — a listing
carrying the row a grant wrote, a withdrawal finding something live — are
outcomes.
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
    user_router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.api.user_app_grant_service import (
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_app_grant.models import (
    UserAppGrantLogModel,
    UserAppGrantModel,
)
from agentclaw.community.core.bot_app_grant.services import UserAppGrantService
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    GatewayUser,
    UserPrincipal,
    VerifiedCaller,
)
from agentclaw.community.core.repository.implementations.bot.user_app_grant import (
    UserAppGrantRepository,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

USER = "u-1"
OTHER = "u-2"
APP_ID = 42
APP_NAME = "partner-platform"
PATH = "/openapi/v1/bots/authorized-apps"


def _caller(*, with_app: bool, user_id: str = USER) -> VerifiedCaller:
    principals: list = [UserPrincipal(subject=GatewayUser(id=user_id, username=user_id))]
    if with_app:
        principals.append(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=APP_ID, app_name=APP_NAME, owners=USER, tenant="teamclaw"
                ),
            )
        )
    return VerifiedCaller(principals=tuple(principals))


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine, tables=[UserAppGrantModel.__table__, UserAppGrantLogModel.__table__]
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

    return UserAppGrantService(repository=UserAppGrantRepository(_Db()))


@pytest.fixture
def make_client(grants):
    def _build(*, with_app: bool, user_id: str = USER):
        class _M(Module):
            def configure(self, binder):
                binder.bind(UserAppGrantServiceProtocol, to=grants)

        app = FastAPI()
        app.include_router(user_router)
        app.dependency_overrides[require_principal] = lambda: _caller(
            with_app=with_app, user_id=user_id
        )
        attach_injector(app, Injector([_M()]))
        mount_public_error_handlers(app)
        return user_scoped_client(app, user_id)

    return _build


def test_granting_records_the_calling_app_for_the_user(make_client):
    client = make_client(with_app=True)

    response = client.post(PATH)

    assert response.status_code == 201, response.json()
    data = response.json()["data"]
    assert data["app_id"] == APP_ID
    assert data["app_name"] == APP_NAME
    assert data["user_id"] == USER
    assert "granted_at" in data


def test_granting_needs_the_application_on_the_wire(make_client):
    """Consent names both parties; the application is never a parameter."""
    response = make_client(with_app=False).post(PATH)
    assert response.status_code == 401


def test_the_listing_needs_only_the_user_and_shows_only_their_own(make_client, grants):
    make_client(with_app=True).post(PATH)
    grants.grant(user_id=OTHER, app_id=APP_ID, app_name=APP_NAME)

    mine = make_client(with_app=False).get(PATH)

    assert mine.status_code == 200, mine.json()
    assert [item["app_id"] for item in mine.json()["data"]["items"]] == [APP_ID]
    assert {item["user_id"] for item in mine.json()["data"]["items"]} == {USER}


def test_withdrawing_needs_only_the_user(make_client, grants):
    make_client(with_app=True).post(PATH)

    response = make_client(with_app=False).delete(f"{PATH}/{APP_ID}")

    assert response.status_code == 200, response.json()
    assert grants.find(user_id=USER, app_id=APP_ID) is None


def test_withdrawing_nothing_answers_404(make_client):
    response = make_client(with_app=False).delete(f"{PATH}/{APP_ID}")
    assert response.status_code == 404


def test_regranting_is_idempotent(make_client):
    client = make_client(with_app=True)
    first = client.post(PATH).json()["data"]
    second = client.post(PATH).json()["data"]
    assert first == second


def test_naming_another_user_is_forbidden(make_client):
    """The human rule is unchanged: a person names themselves or nobody."""
    client = make_client(with_app=True)
    response = client.post(PATH, params={"user_id": OTHER})
    assert response.status_code == 403
