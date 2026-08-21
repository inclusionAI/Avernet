"""SQLite contract tests for OrmTtlRenewalScheduleRepository.

Proves the ORM pipeline end-to-end on a real in-memory SQLite database
without booting the container (RESEARCH study 7 / D-06'):

1. plugin ``create_all()`` builds ``baas_arca_ttl_renewal_schedule``
2. first ``register()`` inserts an ACTIVE row
3. re-registering the same uk_source performs the dialect upsert
   (sandbox_id/next_renew_at overwrite, STOPPED resurrect, fail-count
   reset, explicit gmt_modified refresh)
4. ``register_if_missing()`` is idempotent for existing rows and inserts
   missing ones

The sqlite branch of the dialect upsert is the real-execution evidence
for D-04'; MySQL semantics stay covered by the independent TEST-01
verification (D-06').
"""

from datetime import datetime

import pytest
from sqlalchemy import inspect

from secbaas.community.core.database import db_manager
from secbaas.community.core.repository.arca_ttl import (
    OrmTtlRenewalScheduleRepository,
    TtlRenewalScheduleModel,
)
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin

TABLE = "baas_arca_ttl_renewal_schedule"
ENV = "pre"
SOURCE_TABLE = "baas_device"
SOURCE_ID = 42
FIRST_RENEW = datetime(2026, 8, 20, 12, 0, 0)
NEW_RENEW = datetime(2027, 1, 15, 8, 30, 0)


@pytest.fixture
def plugin():
    """Fresh in-memory SQLite plugin registered as the global db backend."""
    plugin = SqliteOrmPlugin()
    db_manager.init_plugin(plugin)
    plugin.create_all()
    return plugin


@pytest.fixture
def repo(plugin):
    return OrmTtlRenewalScheduleRepository(database=db_manager)


def _rows() -> list[TtlRenewalScheduleModel]:
    with db_manager.orm_session() as session:
        return session.query(TtlRenewalScheduleModel).all()


def _flip_stopped_with_failures() -> None:
    """Mark the registered row STOPPED with failure history and an old mtime."""
    with db_manager.orm_session() as session:
        session.query(TtlRenewalScheduleModel).filter(
            TtlRenewalScheduleModel.env == ENV,
            TtlRenewalScheduleModel.source_table == SOURCE_TABLE,
            TtlRenewalScheduleModel.source_id == SOURCE_ID,
        ).update(
            {
                "status": "STOPPED",
                "renew_fail_count": 7,
                "gmt_modified": datetime(2020, 1, 1, 0, 0, 0),
            },
            synchronize_session=False,
        )


class TestTableCreation:
    def test_create_all_creates_schedule_table(self, plugin):
        inspector = inspect(plugin._sync_engine)
        assert inspector.has_table(TABLE)


class TestRegister:
    def test_first_register_inserts_active_row(self, repo):
        repo.register(
            ENV,
            sandbox_id="sandbox-abc@42",
            source_table=SOURCE_TABLE,
            source_id=SOURCE_ID,
            next_renew_at=FIRST_RENEW,
        )

        rows = _rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.env == ENV
        assert row.sandbox_id == "sandbox-abc@42"
        assert row.source_table == SOURCE_TABLE
        assert row.source_id == SOURCE_ID
        assert row.next_renew_at == FIRST_RENEW
        assert row.status == "ACTIVE"
        assert row.renew_fail_count == 0
        assert row.last_renewed_at is None
        assert row.gmt_create is not None

    def test_re_register_upserts_and_resurrects_stopped(self, repo):
        repo.register(
            ENV,
            sandbox_id="sandbox-old@1",
            source_table=SOURCE_TABLE,
            source_id=SOURCE_ID,
            next_renew_at=FIRST_RENEW,
        )
        _flip_stopped_with_failures()

        repo.register(
            ENV,
            sandbox_id="sandbox-new@7",
            source_table=SOURCE_TABLE,
            source_id=SOURCE_ID,
            next_renew_at=NEW_RENEW,
        )

        rows = _rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.sandbox_id == "sandbox-new@7"
        assert row.next_renew_at == NEW_RENEW
        assert row.status == "ACTIVE"
        assert row.renew_fail_count == 0
        # Pitfall 2 guard: dialect upsert does NOT apply Column.onupdate, so
        # the SET must carry gmt_modified explicitly — without it the value
        # stays at 2020-01-01 and this assertion fails.
        assert row.gmt_modified > datetime(2020, 1, 1, 0, 0, 0)


class TestRegisterIfMissing:
    def test_register_if_missing_inserts_missing_row(self, repo):
        repo.register_if_missing(
            ENV,
            sandbox_id="sandbox-abc@42",
            source_table=SOURCE_TABLE,
            source_id=SOURCE_ID,
            next_renew_at=FIRST_RENEW,
        )

        rows = _rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "ACTIVE"
        assert row.sandbox_id == "sandbox-abc@42"
        assert row.next_renew_at == FIRST_RENEW

    def test_register_if_missing_leaves_existing_row_unchanged(self, repo):
        repo.register(
            ENV,
            sandbox_id="sandbox-abc@42",
            source_table=SOURCE_TABLE,
            source_id=SOURCE_ID,
            next_renew_at=FIRST_RENEW,
        )
        first_row = _rows()[0]

        repo.register_if_missing(
            ENV,
            sandbox_id="sandbox-abc@42",
            source_table=SOURCE_TABLE,
            source_id=SOURCE_ID,
            next_renew_at=FIRST_RENEW,
        )

        rows = _rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.id == first_row.id
        assert row.sandbox_id == first_row.sandbox_id
        assert row.source_table == first_row.source_table
        assert row.source_id == first_row.source_id
        assert row.next_renew_at == first_row.next_renew_at
        assert row.status == "ACTIVE"
        assert row.renew_fail_count == 0
