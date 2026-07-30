"""ResourceModel tenant guards (spec §6.4 green)."""
from contextlib import contextmanager
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import ResourceModel, CrossTenantInsertError
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _DB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db; db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'rg.db'}",
                          connect_args={"check_same_thread": False})
    ResourceModel.__table__.create(engine)
    return _DB(engine)


def test_insert_under_scope_stamps_tenant(db):
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            r = ResourceModel(name="r", resource_type="file")
            s.add(r)
    # No explicit tenant set; guard stamped tenant-a. Read the raw row across
    # tenants via the escape hatch to assert the value (matches the bot
    # guard's test_insert_stamps_current_tenant in test_bot_tenant_guard.py).
    with db.orm_session() as s:
        row = (
            s.query(ResourceModel)
            .execution_options(skip_avernet_tenant_guard=True)
            .first()
        )
        assert row.avernet_tenant == "tenant-a"


def test_insert_outside_request_gets_default(db):
    with db.orm_session() as s:
        r = ResourceModel(name="r2", resource_type="file")
        s.add(r)
    with db.orm_session() as s:
        assert s.query(ResourceModel).filter_by(name="r2").first().avernet_tenant == "teamclaw"


def test_explicit_conflicting_tenant_insert_raises(db):
    # Match the bot guard pattern (test_bot_tenant_guard.py): wrap the orm
    # session in pytest.raises and call s.flush() inside, so the before_insert
    # event (autoflush=False, so add() alone won't trigger it) fires within the
    # asserted block.
    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(CrossTenantInsertError):
            with db.orm_session() as s:
                s.add(ResourceModel(name="r3", resource_type="file",
                                   avernet_tenant="tenant-b"))
                s.flush()


def test_bare_query_filtered(db):
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            s.add(ResourceModel(name="r", resource_type="file"))
    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            assert s.query(ResourceModel).all() == []


def test_skip_option_sees_all(db):
    with avernet_tenant_scope("tenant-a"):
        with db.orm_session() as s:
            s.add(ResourceModel(name="r", resource_type="file"))
    with avernet_tenant_scope("tenant-b"):
        with db.orm_session() as s:
            rows = s.query(ResourceModel).execution_options(
                skip_avernet_tenant_guard=True).all()
            assert len(rows) == 1
