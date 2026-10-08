"""BotCommonConfigRepository CRUD/upsert tests on real SQLite persistence."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.config.bot_common_config import (
    BotCommonConfigRepository,
)
from agentclaw.community.plugin_api.models import BotCommonConfig

SCOPE = dict(
    bot_id="b1", entity_id="staff_10001", env="pre", config_key="storage_policy"
)


class Database:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})
        BotCommonConfig.__table__.create(self.engine)
        self.factory = sessionmaker(self.engine)

    @contextmanager
    def orm_session(self):
        with self.factory.begin() as session:
            yield session

    @contextmanager
    def transactional_orm_session(self):
        with self.orm_session() as session:
            yield session


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / "bot_config.db")
    yield BotCommonConfigRepository(db)
    db.engine.dispose()


def test_create_get_list_update_delete(repo):
    config_id = repo.create_record(**SCOPE, config_value='{"storage_type":"upfs"}')

    row = repo.get_record_by_id(config_id=config_id)
    assert row is not None
    assert row.id == config_id
    assert row.config_value == '{"storage_type":"upfs"}'
    assert row.env == "pre"
    assert repo.get(**SCOPE) == '{"storage_type":"upfs"}'

    total, items = repo.list_records(bot_id="b1", env="pre")
    assert total == 1
    assert [r.id for r in items] == [config_id]

    assert repo.update_record(config_id=config_id, config_value='{"storage_type":"nas"}')
    assert repo.get(**SCOPE) == '{"storage_type":"nas"}'

    assert repo.delete_record(config_id=config_id)
    assert repo.get(**SCOPE) is None
    assert repo.get_record_by_id(config_id=config_id) is None
    assert repo.list_records(bot_id="b1")[0] == 0


def test_create_duplicate_key_raises(repo):
    repo.create_record(**SCOPE, config_value="{}")
    with pytest.raises(Exception):
        repo.create_record(**SCOPE, config_value="{}")


def test_update_and_delete_missing_returns_false(repo):
    assert repo.update_record(config_id=404, config_value="{}") is False
    assert repo.delete_record(config_id=404) is False
    assert repo.get_record_by_id(config_id=404) is None


def test_upsert_inserts_then_updates_and_revives_soft_deleted(repo):
    first = repo.upsert_record(**SCOPE, config_value='{"v":1}')
    second = repo.upsert_record(**SCOPE, config_value='{"v":2}')
    assert first == second
    assert repo.get(**SCOPE) == '{"v":2}'

    assert repo.delete_record(config_id=first)
    revived = repo.upsert_record(**SCOPE, config_value='{"v":3}')
    assert revived == first
    assert repo.get(**SCOPE) == '{"v":3}'


def test_batch_upsert_mixed_insert_update(repo):
    existing = repo.create_record(**SCOPE, config_value='{"v":0}')
    ids = repo.batch_upsert_records(
        records=[
            {**SCOPE, "config_value": '{"v":1}'},
            {**SCOPE, "bot_id": "b2", "config_value": '{"v":2}'},
        ]
    )
    assert ids[0] == existing
    assert len(ids) == 2
    assert repo.get(**SCOPE) == '{"v":1}'
    assert repo.get(**{**SCOPE, "bot_id": "b2"}) == '{"v":2}'
    assert repo.list_records(env="pre")[0] == 2


def test_list_records_filters_and_pagination(repo):
    for i in range(5):
        repo.create_record(
            **{**SCOPE, "bot_id": f"bot_{i}"}, config_value='{"v":1}'
        )
    total, page1 = repo.list_records(env="pre", page_num=1, page_size=2)
    assert total == 5
    assert len(page1) == 2
    _, page3 = repo.list_records(env="pre", page_num=3, page_size=2)
    assert len(page3) == 1

    total, items = repo.list_records(bot_id="bot_3", config_key="storage_policy")
    assert total == 1
    assert items[0].bot_id == "bot_3"

    assert repo.list_records(env="prod")[0] == 0
    with pytest.raises(ValueError):
        repo.list_records(page_num=0)
    with pytest.raises(ValueError):
        repo.list_records(page_size=1000)
