"""ChannelConfig tenant isolation and unified repository coverage."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.chat.channel import (
    ChannelRepository,
)
from agentclaw.community.plugin_api.models import (
    ChannelConfig,
    CrossTenantInsertError,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

pytestmark = pytest.mark.integration


class _DB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def db_and_repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'channels.db'}",
        connect_args={"check_same_thread": False},
    )
    ChannelConfig.__table__.create(engine)
    db = _DB(engine)
    return db, ChannelRepository(db)


def _create(repo: ChannelRepository, *, description: str) -> int:
    return repo.insert_channel(
        type="dingding",
        description=description,
        identity_id="same-owner",
        bind_bot_id="default",
        config={"client_id": "id", "client_secret": "secret"},
        status="0",
        stage="draft",
    )


def test_insert_stamps_current_tenant(db_and_repo):
    db, repo = db_and_repo
    with avernet_tenant_scope("tenant-a"):
        _create(repo, description="a")

    with db.orm_session() as session:
        row = (
            session.query(ChannelConfig)
            .execution_options(skip_avernet_tenant_guard=True)
            .one()
        )
        assert row.avernet_tenant == "tenant-a"


def test_explicit_cross_tenant_insert_is_rejected(db_and_repo):
    db, _ = db_and_repo
    with avernet_tenant_scope("tenant-a"):
        with pytest.raises(CrossTenantInsertError):
            with db.orm_session() as session:
                session.add(
                    ChannelConfig(
                        type="dingding",
                        identity_id="same-owner",
                        bind_bot_id="default",
                        config="{}",
                        status="0",
                        deleted=0,
                        env="pre",
                        stage="draft",
                        avernet_tenant="tenant-b",
                    )
                )
                session.flush()


def test_same_owner_and_bot_are_isolated_between_tenants(db_and_repo):
    _, repo = db_and_repo
    with avernet_tenant_scope("tenant-a"):
        id_a = _create(repo, description="tenant-a")
    with avernet_tenant_scope("tenant-b"):
        id_b = _create(repo, description="tenant-b")

    with avernet_tenant_scope("tenant-a"):
        rows = repo.get_by_type_and_identity_ids(
            type="dingding",
            identity_ids=["same-owner"],
            bind_bot_id="default",
        )
        assert [row.id for row in rows] == [id_a]
        assert repo.get_by_id(id_b) is None

    with avernet_tenant_scope("tenant-b"):
        rows = repo.get_by_type_and_identity_ids(
            type="dingding",
            identity_ids=["same-owner"],
            bind_bot_id="default",
        )
        assert [row.id for row in rows] == [id_b]
        assert repo.get_by_id(id_a) is None


def test_cross_tenant_update_and_delete_are_noops(db_and_repo):
    _, repo = db_and_repo
    with avernet_tenant_scope("tenant-a"):
        channel_id = _create(repo, description="original")

    with avernet_tenant_scope("tenant-b"):
        repo.update_by_id(
            channel_id=channel_id,
            type="dingding",
            description="hacked",
            identity_id="same-owner",
            bind_bot_id="default",
            config={},
            status="1",
            stage="draft",
        )
        repo.delete_by_id(channel_id=channel_id)

    with avernet_tenant_scope("tenant-a"):
        row = repo.get_by_id(channel_id)
        assert row is not None
        assert row.description == "original"
        assert row.deleted == 0
