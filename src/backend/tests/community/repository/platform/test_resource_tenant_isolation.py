"""Cross-tenant isolation for resource records (spec §6.4 red→green).

RED at this task: ac_resource has no avernet_tenant column yet (or column
present but guards absent), so a read under tenant-b sees tenant-a's resource.
Task 4 adds the column; Task 5 installs the guards; both turn this green.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import ResourceModel
from agentclaw.community.core.repository.implementations.platform.resource import ResourceRepository
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
        f"sqlite:///{tmp_path / 'resources.db'}",
        connect_args={"check_same_thread": False},
    )
    ResourceModel.__table__.create(engine)
    return ResourceRepository(_FileSqliteDB(engine))


def _data(**ov):
    base = dict(
        name="res-a",
        resource_type="file",
        status="active",
        user_id="emp1",
        created_by="emp1",
        source="upload",
        bolt_id="bot-a",
    )
    base.update(ov)
    return base


@pytest.fixture
def two_tenant_resources(repo):
    with avernet_tenant_scope("tenant-a"):
        repo.create(_data(name="res-a", bolt_id="bot-a"))
    with avernet_tenant_scope("tenant-b"):
        repo.create(_data(name="res-b", bolt_id="bot-b"))
    return repo


def test_get_by_id_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-a"):
        a = repo.list_resources(bolt_id="bot-a")
        a_id = a[0]["id"]
    with avernet_tenant_scope("tenant-b"):
        assert repo.get_by_id(a_id) is None


def test_list_resources_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-b"):
        items = repo.list_resources(bolt_id="bot-a")
        assert items == []


def test_update_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-a"):
        a_id = repo.list_resources(bolt_id="bot-a")[0]["id"]
    with avernet_tenant_scope("tenant-b"):
        assert repo.update(a_id, {"name": "hacked"}) is None


def test_delete_is_tenant_scoped(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-a"):
        a_id = repo.list_resources(bolt_id="bot-a")[0]["id"]
    with avernet_tenant_scope("tenant-b"):
        assert repo.delete(a_id) is False
    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_id(a_id) is not None


def test_own_tenant_still_visible(two_tenant_resources):
    repo = two_tenant_resources
    with avernet_tenant_scope("tenant-a"):
        assert repo.list_resources(bolt_id="bot-a") != []
