"""Endpoint tests for ``/openapi/v1/org/user/authorized-apps``.

The account-level grant group, hosted on a minimal app with the caller
principal overridden and the service bound through the injector, mirroring
``test_router.py``. The grant service is the real one over an in-memory
database: what these tests are about — that the application is never nameable
by the request, that a withdrawal reads differently from nothing to withdraw,
that a machine caller is refused — are outcomes, not call shapes.
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

from agentclaw.community.adapters.http.openapi_v1.authorized_apps.user_router import (
    router,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
    require_user_and_app_principal,
)
from agentclaw.community.api.user_app_grant_service import (
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.base import Base
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
from agentclaw.community.core.user_app_grant.models import (
    UserAppGrantLogModel,
    UserAppGrantModel,
)
from agentclaw.community.core.user_app_grant.services import UserAppGrantService
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

USER = "u-1"
OTHER = "u-2"
APP_ID = 42
APP_NAME = "partner-platform"
BASE = "/openapi/v1/org/user/authorized-apps"


def _caller(
    *, app_id: int | None = APP_ID, user_id: str | None = USER
) -> VerifiedCaller:
    principals: list = []
    if user_id is not None:
        principals.append(UserPrincipal(subject=GatewayUser(id=user_id, username=user_id)))
    if app_id is not None:
        principals.append(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=app_id, app_name=APP_NAME, owners=USER, tenant="teamclaw"
                ),
            )
        )
    return VerifiedCaller(principals=tuple(principals))


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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

    return UserAppGrantService(UserAppGrantRepository(_Db()))


def _build(grants, caller):
    class _M(Module):
        def configure(self, binder):
            binder.bind(UserAppGrantServiceProtocol, to=grants)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: caller
    app.dependency_overrides[require_user_and_app_principal] = lambda: caller
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return app


@pytest.fixture
def client(grants):
    """The user, with the application alongside."""
    return user_scoped_client(_build(grants, _caller()), USER)


@pytest.fixture
def user_only_client(grants):
    """The user alone — enough to list and withdraw, not to grant."""
    return user_scoped_client(_build(grants, _caller(app_id=None)), USER)


def _ok(resp, code=200000, status=200):
    body = resp.json()
    assert resp.status_code == status, body
    assert body["code"] == code, body
    return body["data"]


def test_grant_reads_the_app_from_the_principal(client, grants):
    data = _ok(client.post(BASE, params={"app_id": 99}), code=201000, status=201)

    assert data["app_id"] == APP_ID
    assert data["app_name"] == APP_NAME
    assert data["user_id"] == USER
    assert grants.find(user_id=USER, app_id=APP_ID) is not None
    assert grants.find(user_id=USER, app_id=99) is None


def test_granting_twice_leaves_one_authorization(client):
    first = _ok(client.post(BASE), code=201000, status=201)
    second = _ok(client.post(BASE), code=201000, status=201)

    assert first["granted_at"] == second["granted_at"]
    listed = _ok(client.get(BASE))
    assert listed["total"] == 1


def test_listing_is_the_users_own_view(client, grants):
    grants.grant(user_id=USER, app_id=APP_ID, app_name=APP_NAME)
    grants.grant(user_id=OTHER, app_id=7, app_name="theirs")

    listed = _ok(client.get(BASE))

    assert [item["app_id"] for item in listed["items"]] == [APP_ID]


def test_listing_and_withdrawing_need_only_the_user(user_only_client, grants):
    grants.grant(user_id=USER, app_id=APP_ID, app_name=APP_NAME)

    assert _ok(user_only_client.get(BASE))["total"] == 1
    _ok(user_only_client.delete(f"{BASE}/{APP_ID}"))
    assert grants.find(user_id=USER, app_id=APP_ID) is None


def test_withdrawing_nothing_is_a_404(client):
    resp = client.delete(f"{BASE}/{APP_ID}")

    assert resp.status_code == 404, resp.json()


def test_withdrawing_is_scoped_to_the_caller(client, grants):
    grants.grant(user_id=OTHER, app_id=APP_ID, app_name=APP_NAME)

    assert client.delete(f"{BASE}/{APP_ID}").status_code == 404
    assert grants.find(user_id=OTHER, app_id=APP_ID) is not None


def test_naming_another_user_is_refused(client, grants):
    resp = client.post(BASE, params={"user_id": OTHER})

    assert resp.status_code == 403, resp.json()
    assert grants.list_for_user(user_id=OTHER) == []


def test_an_application_acting_alone_is_refused_everywhere(grants):
    app = _build(grants, _caller(user_id=None))
    client = user_scoped_client(app, USER)

    for method, path in (("post", BASE), ("get", BASE), ("delete", f"{BASE}/{APP_ID}")):
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, (method, path, resp.json())
    assert grants.list_for_user(user_id=USER) == []
