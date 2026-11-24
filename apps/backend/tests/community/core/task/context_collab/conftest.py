"""Shared SQLite harness for context collaboration service integration tests.

Mirrors ``tests/community/repository/task/context_collab/conftest.py``: a
real in-memory SQLite engine, ``Base.metadata.create_all``, and a minimal
``orm_session`` stub that commits on clean exit / rolls back on error — the
``db``/``engine`` fixtures that conftest serves to the repository subtree only,
so the core-side integration tests carry their own copy here.
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base

import agentclaw.community.core.task.context_collab.l6_products.persistence.profile_models  # noqa: F401

# Side-effect imports: register L4 and independent G3 models on Base.metadata.
import agentclaw.community.core.task.context_collab.persistence.models  # noqa: F401
import agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.persistence  # noqa: F401


class InMemorySqliteDB:
    """Minimal DatabasePlugin stand-in offering orm_session()."""

    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    def transactional_orm_session(self):
        return self.orm_session()

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    database = InMemorySqliteDB(engine)
    from agentclaw.community.di.modules.context_collab_products import (
        INSTALLED_PRODUCT_PROFILES,
    )
    from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
        PrincipalRef,
    )
    from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
        ActorContext,
    )
    from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
        ProfilePublisherAllowlist,
    )
    from agentclaw.community.core.task.context_collab.l6_products.operations.product_profile_publication import (
        ProductProfilePublicationService,
    )
    from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_repository import (
        SqlProductProfileRepository,
    )

    publisher = PrincipalRef("system", "test_profile_import")
    ProductProfilePublicationService(
        SqlProductProfileRepository(database), ProfilePublisherAllowlist((publisher,))
    ).import_profiles(INSTALLED_PRODUCT_PROFILES, ActorContext(publisher, "test_seed"))
    return database
