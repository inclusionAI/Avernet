"""Tenant guard behavior beyond the read path: writes, inserts, non-repository
queries, and the escape hatch.

Complements ``test_bot_tenant_isolation.py`` (the spec's read red→green test).
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.models import BotModel, CrossTenantInsertError
from agentclaw.community.plugins.bot_repository import BotRepository
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
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bots.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    BotPublishModel.__table__.create(engine)
    EntityDeviceBinding.__table__.create(engine)
    BotCollaboratorModel.__table__.create(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(db):
    return BotRepository(db)


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


# ── insert guard: stamping ──────────────────────────────────────────

def test_insert_stamps_current_tenant(repo, db):
    with avernet_tenant_scope("tenant-b"):
        repo.insert(_data(bot_id="bot-b", owner_id="own-b"))
    # Read the raw row across tenants via the escape hatch to assert the value.
    with db.orm_session() as s:
        row = (
            s.query(BotModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .filter(BotModel.bot_id == "bot-b")
            .one()
        )
        assert row.avernet_tenant == "tenant-b"


def test_insert_outside_any_request_stamps_default_tenant(repo, db):
    # No scope → the default tenant, keeping current internal behavior.
    repo.insert(_data(bot_id="bot-d", owner_id="own-d"))
    with db.orm_session() as s:
        row = (
            s.query(BotModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .filter(BotModel.bot_id == "bot-d")
            .one()
        )
        assert row.avernet_tenant == "teamclaw"


# ── insert guard: rejection ─────────────────────────────────────────

def test_explicit_conflicting_tenant_insert_raises(db):
    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(CrossTenantInsertError):
            with db.orm_session() as s:
                s.add(
                    BotModel(
                        bot_id="bot-evil",
                        entity_id="e",
                        entity_type="staff",
                        creator_id="c",
                        owner_id="o",
                        avernet_tenant="tenant-a",  # != current context
                    )
                )
                s.flush()


# ── write guards: cross-tenant update / delete are no-ops ────────────

def test_update_by_owner_cross_tenant_is_noop(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a", bot_name="Alpha"))
    with avernet_tenant_scope("tenant-b"):
        result = repo.update_by_owner("bot-a", "own-a", {"bot_name": "HACKED"})
        assert result is None  # indistinguishable from a missing row
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id("bot-a")["bot_name"] == "Alpha"  # untouched


def test_soft_delete_by_owner_cross_tenant_is_noop(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a"))
    with avernet_tenant_scope("tenant-b"):
        assert repo.soft_delete_by_owner("bot-a", "own-a") is False
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id("bot-a") is not None  # still there


# ── non-repository access is filtered too ───────────────────────────

def test_bare_session_query_is_filtered(repo, db):
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a"))
    with avernet_tenant_scope("tenant-b"):
        repo.insert(_data(bot_id="bot-b", owner_id="own-b"))
    # A direct query — the shape the nine non-repository modules use — is
    # filtered without them adding any tenant clause.
    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            rows = s.query(BotModel).all()
            assert [r.bot_id for r in rows] == ["bot-b"]


def test_skip_option_sees_all_tenants(repo, db):
    with avernet_tenant_scope("tenant-a"):
        repo.insert(_data(bot_id="bot-a", owner_id="own-a"))
    with avernet_tenant_scope("tenant-b"):
        repo.insert(_data(bot_id="bot-b", owner_id="own-b"))
    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            rows = (
                s.query(BotModel)
                .execution_options(skip_avernet_tenant_guard=True)
                .all()
            )
            assert {r.bot_id for r in rows} == {"bot-a", "bot-b"}
