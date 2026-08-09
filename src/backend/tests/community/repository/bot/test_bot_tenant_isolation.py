"""Cross-tenant isolation for bot records (spec's red→green test).

This is the test the spec requires to FAIL before the tenant guards exist and
PASS after. Written at Task 4 (column present, guards absent) it fails: reads
are unfiltered, so a read under tenant B sees tenant A's bot. Task 5 installs
the `before_insert` stamp (giving the two seeded rows distinct tenants) and the
`do_orm_execute` read filter (confining every read to the current tenant), which
turns it green.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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

    session = orm_session


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bots.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    BotPublishModel.__table__.create(engine)
    EntityDeviceBinding.__table__.create(engine)
    BotCollaboratorModel.__table__.create(engine)
    return BotRepository(_FileSqliteDB(engine))


def _data(**ov):
    base = dict(
        bot_id="bot-1",
        bot_name="Bot One",
        bot_desc="d",
        entity_id="staff_x",
        entity_type="staff",
        creator_id="emp1",
        owner_id="emp1",
        status="ACTIVE",
        owner_name="Alice",
    )
    base.update(ov)
    return base


@pytest.fixture
def two_tenant_bots(repo):
    """Seed one bot for tenant A and one for tenant B, each under its scope."""
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a", bot_name="Alpha Bot"))
    with avernet_tenant_scope("tenant-b"):
        repo.insert(_data(bot_id="bot-b", owner_id="own-b", bot_name="Beta Bot"))
    return repo


def test_read_by_id_is_tenant_scoped(two_tenant_bots):
    repo = two_tenant_bots
    with avernet_tenant_scope("tenant-b"):
        # Tenant B can see its own bot...
        assert repo.get_by_id("bot-b") is not None
        # ...but never tenant A's.
        assert repo.get_by_id("bot-a") is None


def test_read_by_id_and_owner_is_tenant_scoped(two_tenant_bots):
    repo = two_tenant_bots
    with avernet_tenant_scope("tenant-b"):
        assert repo.get_by_id_and_owner("bot-a", "own-a") is None


def test_list_by_owner_is_tenant_scoped(two_tenant_bots):
    repo = two_tenant_bots
    with avernet_tenant_scope("tenant-b"):
        total, items = repo.list_by_owner("own-a")
        assert total == 0
        assert items == []


def test_count_by_owner_is_tenant_scoped(two_tenant_bots):
    repo = two_tenant_bots
    with avernet_tenant_scope("tenant-b"):
        assert repo.count_by_owner("own-a") == 0


def test_exists_by_bot_name_is_tenant_scoped(two_tenant_bots):
    repo = two_tenant_bots
    with avernet_tenant_scope("tenant-b"):
        assert repo.exists_by_bot_name("Alpha Bot") is False


def test_search_bots_is_tenant_scoped(two_tenant_bots):
    repo = two_tenant_bots
    with avernet_tenant_scope("tenant-b"):
        total, items = repo.search_bots(key="Alpha")
        assert total == 0
        assert items == []


def test_own_tenant_still_visible(two_tenant_bots):
    """Isolation must not hide a tenant's own data."""
    repo = two_tenant_bots
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id("bot-a") is not None
        assert repo.exists_by_bot_name("Alpha Bot") is True
        total, _ = repo.list_by_owner("own-a")
        assert total == 1
