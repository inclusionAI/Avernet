"""Resource IDs respect the actual SQLite integer range."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from agentclaw.community.core.repository.implementations.platform.resource import (
    ResourceRepository,
)
from agentclaw.community.plugin_api.models import ResourceModel


@pytest.fixture
def repository():
    engine = create_engine("sqlite:///:memory:")
    ResourceModel.__table__.create(engine)

    class DB:
        @contextmanager
        def orm_session(self):
            with Session(engine) as session:
                yield session

    with Session(engine) as session:
        session.add_all(
            [
                ResourceModel(id=-(1 << 63), name="low", resource_type="link"),
                ResourceModel(id=-1, name="negative", resource_type="link"),
                ResourceModel(id=(1 << 63) - 1, name="high", resource_type="link"),
            ]
        )
        session.commit()
    yield ResourceRepository(DB())
    engine.dispose()


@pytest.mark.parametrize("value", [-(1 << 63), -1, (1 << 63) - 1])
def test_sqlite_existing_ids_are_preserved(repository, value):
    assert repository.get_by_id(str(value))["id"] == str(value)


@pytest.mark.parametrize("value", [-(1 << 63) - 1, 1 << 63, (1 << 64) - 1])
def test_sqlite_out_of_range_is_not_found(repository, value):
    assert repository.get_by_id(str(value)) is None


def test_unrelated_database_error_is_not_hidden(repository):
    with repository._db.orm_session() as session:
        session.get_bind().dispose()
    with pytest.raises(OperationalError):
        repository.get_by_id("1")
