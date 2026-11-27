"""Real multi-connection SQLite with FKs; only the five business tables exist."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_use import (
    DenyProductProfileUse,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.product_runtime import (
    OwnerProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
    BUSINESS_TABLES,
    ProductProfileRow,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_repository import (
    SqlProductBusinessRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    default_composition,
)

STAGES = """stages:
  - name: z_design
    candidates: [{type: bot, uuid: worker}]
    depends_on: []
    acceptance: {condition: design accepted}
  - name: a_build
    candidates: [{type: bot, uuid: worker}]
    depends_on: [z_design]
    acceptance: {condition: tests pass}
"""


class DB:
    def __init__(self, engine):
        self.factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def transactional_orm_session(self):
        with self.factory() as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    orm_session = transactional_orm_session


class UseGrant:
    def __init__(self):
        self.calls = []

    def check_use(self, profile_id, context):
        self.calls.append((profile_id, context))


@pytest.fixture
def business(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'business.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def sqlite_setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=BUSINESS_TABLES)
    db, grant = DB(engine), UseGrant()
    owner = ActorContext(PrincipalRef("human", "creator"), "test")
    publisher = ProfilePublisherAllowlist((owner.actor,))
    repo = SqlProductBusinessRepository(
        db, publisher, grant, OwnerProductRuntimeAuthorization()
    )
    result = SimpleNamespace(
        engine=engine,
        db=db,
        grant=grant,
        owner=owner,
        publisher=publisher,
        repo=repo,
        stages=STAGES,
        composition=default_composition(),
        denied=SqlProductBusinessRepository(
            db, publisher, DenyProductProfileUse(), OwnerProductRuntimeAuthorization()
        ),
    )
    yield result
    engine.dispose()


@pytest.fixture
def online(business):
    h = business
    h.profile = h.repo.create_profile(
        "coding", h.composition, h.stages, h.owner, description="配置描述"
    )
    # Explicit test administration; no public/implicit draft -> online API.
    with h.db.orm_session() as session:
        session.query(ProductProfileRow).filter_by(id=h.profile.id).update(
            {"status": "ONLINE"}
        )
    return h
