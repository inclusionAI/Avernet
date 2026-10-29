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


@pytest.mark.parametrize("method", ["set_config", "create_record", "upsert_record", "batch_upsert_records"])
def test_generic_writers_cannot_replace_publication_summary(service, method):
    from agentclaw.community.kernel.knowledge import KNOWLEDGE_ONLINE_CONFIG_KEY

    scope = {**SCOPE, "config_key": KNOWLEDGE_ONLINE_CONFIG_KEY}
    with pytest.raises(ValueError, match="managed by publication"):
        if method == "batch_upsert_records":
            service.batch_upsert_records(env=scope["env"], records=[
                BotCommonConfigEntry(bot_id=scope["bot_id"], entity_id=scope["entity_id"],
                                     config_key=scope["config_key"], value={}),
            ])
        else:
            getattr(service, method)(**scope, value={})
    assert service.list_records()[0] == 0


@pytest.mark.parametrize("method", ["update_record", "delete_record"])
def test_generic_record_mutators_cannot_change_publication_summary(service, method):
    from agentclaw.community.kernel.knowledge import KNOWLEDGE_ONLINE_CONFIG_KEY

    # Seed via persistence, as the publication transaction does (not generic service).
    with service._repo._db.transactional_orm_session() as session:
        row = BotCommonConfig(**{**SCOPE, "config_key": KNOWLEDGE_ONLINE_CONFIG_KEY},
                              config_value='{"protected":true}')
        session.add(row)
        session.flush()
        config_id = row.id
    args = dict(config_id=config_id)
    if method == "update_record":
        args["value"] = {}
    with pytest.raises(ValueError, match="managed by publication"):
        getattr(service, method)(**args)
    row = service.get_record_by_id(config_id=config_id)
    assert row is not None and json.loads(row.config_value) == {"protected": True}


@pytest.mark.parametrize("key", ["KNOWLEDGE_ONLINE_SELECTION", "Knowledge_Online_Selection", "knowledge_online_selection ", "knówledge_online_selection", "ｋｎｏｗｌｅｄｇｅ_online_selection"])
@pytest.mark.parametrize("method", ["create_record", "upsert_record", "set_config", "batch_upsert_records"])
def test_reserved_config_key_rejects_case_and_trailing_space_aliases(service, key, method):
    scope = {**SCOPE, "config_key": key}
    with pytest.raises(ValueError, match="managed by publication"):
        if method == "batch_upsert_records":
            service.batch_upsert_records(env="pre", records=[BotCommonConfigEntry(
                bot_id="b1", entity_id="staff_10001", config_key=key, value={},
            )])
        else:
            getattr(service, method)(**scope, value={})
    assert service.list_records()[0] == 0


def test_case_insensitive_database_cannot_upsert_reserved_summary(service):
    from agentclaw.community.kernel.knowledge import KNOWLEDGE_ONLINE_CONFIG_KEY

    db = service._repo._db
    # Simulate an existing MySQL case-insensitive config_key collation with
    # a real SQLite table/index, not a mocked repository comparison.
    with db.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE ac_bot_common_config")
        connection.exec_driver_sql("""
            CREATE TABLE ac_bot_common_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id VARCHAR(128) NOT NULL, entity_id VARCHAR(128) NOT NULL,
                env VARCHAR(32) NOT NULL, config_key VARCHAR(64) COLLATE NOCASE NOT NULL,
                config_value TEXT NOT NULL, is_delete SMALLINT NOT NULL DEFAULT 0,
                gmt_create DATETIME DEFAULT CURRENT_TIMESTAMP,
                gmt_modified DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (bot_id, entity_id, env, config_key)
            )
        """)
        connection.exec_driver_sql(
            "INSERT INTO ac_bot_common_config (bot_id,entity_id,env,config_key,config_value) VALUES (?,?,?,?,?)",
            ("b1", "staff_10001", "pre", KNOWLEDGE_ONLINE_CONFIG_KEY, '{"protected":true}'),
        )
    for key in (KNOWLEDGE_ONLINE_CONFIG_KEY.upper(), KNOWLEDGE_ONLINE_CONFIG_KEY + " "):
        with pytest.raises(ValueError, match="managed by publication"):
            service.upsert_record(**{**SCOPE, "config_key": key}, value={"overwritten": True})
    with db.orm_session() as session:
        row = session.query(BotCommonConfig).one()
        assert row.config_key == KNOWLEDGE_ONLINE_CONFIG_KEY
        assert json.loads(row.config_value) == {"protected": True}
