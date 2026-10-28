"""Focused port-level tests; no full-app boot, MOSN, SSO or unmerged source."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from agentclaw.community.core.knowledge_management.repository.source import (
    KnowledgeSourceRepository,
)
from agentclaw.community.core.knowledge_management.services.service import (
    KnowledgeManagementService,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.plugin_api.models import BotModel
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def fact(kb_id="kb", **changes):
    return {
        "space_id": "1",
        "kb_id": kb_id,
        "name": "Knowledge",
        "kgbase_status": "ready",
        "can_manage": True,
        "description": "Provider description",
        "document_count": 17,
        "authorized_bot_count": 205,
        "created_at": "2026-10-24T00:00:00Z",
        "updated_at": None,
        "updated_at_basis": "unavailable",
    } | changes


def grant(bot_id="bot", owner_id="owner", **changes):
    return {
        "id": f"grant-{bot_id}-{owner_id}",
        "kb_id": "kb",
        "platform": "teamclaw",
        "platform_env": "pre",
        "bot_id": bot_id,
        "user_id": owner_id,
        "bot_owner_id": owner_id,
        "granted": True,
        "revision": 2,
        "updated_by": "manager",
    } | changes


class Catalog:
    def __init__(self):
        self.calls = []
        self.denied = False
        self.kb = fact()
        self.page = {"items": [self.kb], "next_cursor": None}
        self.grants = {"items": [grant()], "next_cursor": None}

    def list_in_space(self, **kwargs):
        self.calls.append(("list", kwargs))
        return self.page

    def detail(self, **kwargs):
        self.calls.append(("detail", kwargs))
        if self.denied:
            raise KnowledgeManagementError("KNOWLEDGE_REMOTE_ACCESS_DENIED")
        return self.kb

    def authorized_bots(self, **kwargs):
        self.calls.append(("grants", kwargs))
        return self.grants


class Enablement:
    def __init__(self):
        self.calls = []
        self.values = {}
        self.fail = False

    def read(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise KnowledgeManagementError("KNOWLEDGE_ENABLEMENT_UNAVAILABLE")
        return {
            identity: self.values.get(identity, False) for identity in kwargs["bots"]
        }


@pytest.fixture
def world():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for model in (SpaceModel, SpaceMemberModel, BotModel):
        model.__table__.create(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    class Database:
        @contextmanager
        def orm_session(self):
            with sessions.begin() as session:
                yield session

    db = Database()
    sql = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, *args: sql.append(statement),
    )
    with db.orm_session() as session:
        for ident, env, name in [
            (1, "pre", "Actual space"),
            (2, "pre", "Other space"),
            (3, "prod", "Prod"),
        ]:
            session.add(
                SpaceModel(
                    id=ident,
                    env=env,
                    name=name,
                    space_code=f"space-{ident}",
                    space_type="TEAM",
                    created_by="actor",
                    updated_by="actor",
                )
            )
        session.add_all(
            [
                SpaceMemberModel(
                    space_id=1,
                    env="pre",
                    user_id="actor",
                    role="MEMBER",
                    status="ACTIVE",
                    created_by="actor",
                ),
                SpaceMemberModel(
                    space_id=1,
                    env="pre",
                    user_id="owner",
                    user_name="Existing nickname",
                    role="MEMBER",
                    status="ACTIVE",
                    created_by="actor",
                ),
                BotModel(
                    id=1,
                    bot_id="bot",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="pre",
                    space_id=1,
                    bot_name="Real bot",
                    bot_type="personal",
                    ext='{"avatar_url":"https://img.example/avatar.png"}',
                ),
                BotModel(
                    id=2,
                    bot_id="bot",
                    owner_id="other-owner",
                    entity_id="other-owner",
                    entity_type="user",
                    creator_id="other-owner",
                    env="pre",
                    space_id=1,
                    bot_name="Other owner bot",
                    owner_name="Stored owner",
                    bot_type="service",
                ),
                BotModel(
                    id=3,
                    bot_id="outside",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="pre",
                    space_id=2,
                    bot_name="SECRET space",
                ),
                BotModel(
                    id=4,
                    bot_id="prod",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="prod",
                    space_id=1,
                    bot_name="SECRET prod",
                ),
                BotModel(
                    id=5,
                    bot_id="deleted",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="pre",
                    space_id=1,
                    is_delete=1,
                    bot_name="SECRET deleted",
                ),
            ]
        )
    source = KnowledgeSourceRepository(db, "pre", "teamclaw")
    catalog, enabled = Catalog(), Enablement()
    from agentclaw.community.plugin_api.space_knowledge import SyncedSpace

    sync = SimpleNamespace(
        get_context=lambda **kw: SyncedSpace(str(kw["space_id"]), kw["environment"], 1)
    )
    service = KnowledgeManagementService(source, catalog, enabled, "pre", sync)
    yield SimpleNamespace(
        service=service,
        source=source,
        catalog=catalog,
        enabled=enabled,
        db=db,
        sql=sql,
        engine=engine,
    )
    engine.dispose()


# Match the real app's DI-first bootstrap. Auth-first isolated imports expose a
# pre-existing public DI/auth cycle at the pinned baseline (not patched here).
import agentclaw.community.di  # noqa: E402,F401


@pytest.fixture
def client(world):
    from agentclaw.community.adapters.http.auth.dependencies import get_current_user
    from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
    from agentclaw.community.adapters.http.knowledge_management.router import router
    from agentclaw.community.api.knowledge_management_service import (
        KnowledgeManagementServiceProtocol,
    )
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi_injector import attach_injector
    from injector import Injector

    app = FastAPI()
    app.include_router(router)

    def authenticated_actor():
        return AuthenticatedUser(id="SSO", staffId="actor", operatorName="login")

    app.dependency_overrides[get_current_user] = authenticated_actor
    injector = Injector(auto_bind=False)
    injector.binder.bind(KnowledgeManagementServiceProtocol, to=world.service)
    from agentclaw.community.core.knowledge_management.config import (
        KnowledgeManagementConfig,
    )
    from agentclaw.community.core.knowledge_management.repository.source import (
        KnowledgeSourceRepository,
    )

    injector.binder.bind(
        KnowledgeManagementConfig, to=KnowledgeManagementConfig(True, "teamclaw")
    )
    injector.binder.bind(KnowledgeSourceRepository, to=world.source)
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client


def make_catalog(origin, *, enabled=False, client=None):
    from agentclaw.community.adapters.knowledge_platform.catalog import (
        EcbKnowledgeClient,
        RemoteKnowledgeCatalog,
    )
    from agentclaw.community.adapters.knowledge_platform.config import (
        KnowledgeIntegrationConfig,
    )
    from agentclaw.community.adapters.knowledge_platform.identity import (
        DirectKnowledgeIdentity,
    )

    return RemoteKnowledgeCatalog(
        EcbKnowledgeClient(
            origin, KnowledgeIntegrationConfig(enabled, origin), client=client
        ),
        DirectKnowledgeIdentity(),
    )
