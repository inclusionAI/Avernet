"""Admin CRUD service tests + storage-format compatibility with get_config.

The compatibility tests pin the contract from the architecture review:
rows written through the admin API must stay readable by ``get_config``
(the pre-existing consumer path used by BotStoragePolicyService).
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.common_config.bot_config_protocol import (
    BotCommonConfigEntry,
)
from agentclaw.community.core.common_config.bot_config_service import (
    BotCommonConfigService,
)
from agentclaw.community.core.repository.implementations.config.bot_common_config import (
    BotCommonConfigRepository,
)
from agentclaw.community.plugin_api.models import BotCommonConfig

SCOPE = dict(bot_id="b1", entity_id="staff_10001", env="pre", config_key="storage_policy")


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
def service(tmp_path):
    db = Database(tmp_path / "admin.db")
    yield BotCommonConfigService(BotCommonConfigRepository(db))
    db.engine.dispose()


def test_admin_crud_round_trip(service):
    config_id = service.create_record(**SCOPE, value={"storage_type": "upfs"})

    record = service.get_record_by_id(config_id=config_id)
    assert record is not None
    assert json.loads(record.config_value) == {"storage_type": "upfs"}
    assert record.env == "pre"

    total, items = service.list_records(bot_id="b1", env="pre")
    assert total == 1
    assert items[0].id == config_id

    assert service.update_record(config_id=config_id, value={"storage_type": "nas"})
    updated = service.get_record_by_id(config_id=config_id)
    assert updated is not None
    assert json.loads(updated.config_value) == {"storage_type": "nas"}

    assert service.delete_record(config_id=config_id)
    assert service.get_record_by_id(config_id=config_id) is None


def test_upsert_then_get_config_reads_same_value(service):
    service.upsert_record(**SCOPE, value={"storage_type": "upfs", "source": "manual"})
    assert service.get_config(**SCOPE) == {"storage_type": "upfs", "source": "manual"}


def test_plain_string_written_by_admin_stays_readable_by_get_config(service):
    """Regression: a plain-string admin write must not crash get_config."""
    service.upsert_record(**SCOPE, value="manual")
    assert service.get_config(**SCOPE) == "manual"


@pytest.mark.parametrize("value", [None, True, 1, 1.5, ["a"], {"k": None}])
def test_json_scalar_round_trip_via_admin_write_and_get_config(service, value):
    service.upsert_record(**SCOPE, value=value)
    assert service.get_config(**SCOPE) == value


def test_batch_upsert_then_get_config_per_key(service):
    ids = service.batch_upsert_records(
        env="pre",
        records=[
            BotCommonConfigEntry(
                bot_id="b1",
                entity_id="staff_10001",
                config_key="storage_policy",
                value={"storage_type": "upfs"},
            ),
            BotCommonConfigEntry(
                bot_id="b2",
                entity_id="staff_10001",
                config_key="storage_policy",
                value={"storage_type": "upfs"},
            ),
        ],
    )
    assert len(ids) == 2
    assert service.get_config(**SCOPE) == {"storage_type": "upfs"}
    assert service.get_config(**{**SCOPE, "bot_id": "b2"}) == {"storage_type": "upfs"}
    total, _ = service.list_records(env="pre")
    assert total == 2
