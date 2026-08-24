"""SQLite contract tests for OrmTtlRenewalScheduleRepository.

Proves the ORM pipeline end-to-end on a real in-memory SQLite database
without booting the container (RESEARCH study 7 / D-06'):

1. plugin ``create_all()`` builds ``baas_bot_ttl_renewal_schedule``
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
from secbaas.community.core.repository.device._orm_model import DeviceModel
from secbaas.community.core.repository.device_binding._orm_model import (
    DeviceBindingModel,
)
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin

TABLE = "baas_bot_ttl_renewal_schedule"
HOT_DEVICE_TABLE = "baas_device"
HOT_BINDING_TABLE = "ac_entity_device_binding"
ENV = "pre"
SOURCE_TABLE = "baas_device"
SOURCE_ID = 42
FIRST_RENEW = datetime(2026, 8, 20, 12, 0, 0)
NEW_RENEW = datetime(2027, 1, 15, 8, 30, 0)
# Fixed gate time for list_due_for_renewal: the repository was moved from
# DB-side `now()` to a caller-supplied naive-UTC bound parameter (CR-01),
# so every due-contract assertion passes the same explicit naive datetime.
NOW = datetime(2026, 8, 21, 0, 0, 0)


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

    def test_create_all_creates_both_hot_tables(self, plugin):
        inspector = inspect(plugin._sync_engine)
        assert inspector.has_table(HOT_DEVICE_TABLE)
        assert inspector.has_table(HOT_BINDING_TABLE)


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


# ==================== Hot-table seeding helpers ====================


def _seed_cold(
    *,
    env: str,
    source_table: str,
    source_id: int,
    sandbox_id: str,
    next_renew_at: datetime,
    status: str = "ACTIVE",
    renew_fail_count: int = 0,
) -> None:
    with db_manager.orm_session() as session:
        session.add(
            TtlRenewalScheduleModel(
                env=env,
                sandbox_id=sandbox_id,
                source_table=source_table,
                source_id=source_id,
                next_renew_at=next_renew_at,
                status=status,
                renew_fail_count=renew_fail_count,
            )
        )


def _seed_hot_device(
    *,
    id_val: int,
    env: str,
    provider_device_id: str | None,
    provider_type: str = "ARCA",
    status: str = "ACTIVE",
    is_deleted: int = 0,
) -> None:
    with db_manager.orm_session() as session:
        session.add(
            DeviceModel(
                id=id_val,
                device_uuid=f"uuid-{id_val}",
                tenant="tenant-t",
                env=env,
                creator="creator-c",
                modifier="modifier-m",
                status=status,
                provider_type=provider_type,
                provider_device_id=provider_device_id,
                provider_device_props=(
                    '{"ttl_expiration_time":"2026-09-01T00:00:00"}'
                    if provider_device_id
                    else None
                ),
                is_deleted=is_deleted,
            )
        )


def _seed_hot_binding(
    *,
    id_val: int,
    env: str,
    device_provider: str = "arca",
    status: str = "ACTIVE",
    device_props: str | None = None,
) -> None:
    with db_manager.orm_session() as session:
        session.add(
            DeviceBindingModel(
                id=id_val,
                entity_id=f"entity-{id_val}",
                entity_type="user",
                device_id=f"device-{id_val}",
                device_provider=device_provider,
                env=env,
                status=status,
                applied_by="applier",
                device_props=device_props,
            )
        )


def _cold_row(source_id: int, *, env: str = ENV):
    with db_manager.orm_session() as session:
        return (
            session.query(TtlRenewalScheduleModel)
            .filter(
                TtlRenewalScheduleModel.source_id == source_id,
                TtlRenewalScheduleModel.env == env,
            )
            .one()
        )


# ==================== list_due_for_renewal (JOIN / orphan / order / limit) ====


class TestListDueForRenewal:
    def _seed(self):
        # Hot device rows: matching pre row, foreign-env row, (no hot for orphan).
        _seed_hot_device(id_val=1, env=ENV, provider_device_id="sb-dev-1")
        _seed_hot_device(id_val=3, env="prod", provider_device_id="sb-dev-3")
        # Cold device rows (env=pre unless noted).
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=1,
            sandbox_id="sb-dev-1",
            next_renew_at=datetime(2020, 3, 1),
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=2,
            sandbox_id="sb-dev-2",
            next_renew_at=datetime(2999, 1, 1),
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=3,
            sandbox_id="sb-dev-3",
            next_renew_at=datetime(2020, 2, 1),
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=999,
            sandbox_id="sb-orphan",
            next_renew_at=datetime(2020, 1, 2),
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=5,
            sandbox_id="sb-stop",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )
        _seed_cold(
            env="prod",
            source_table="baas_device",
            source_id=6,
            sandbox_id="sb-prod",
            next_renew_at=datetime(2020, 1, 1),
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=7,
            sandbox_id="sb-bind-7",
            next_renew_at=datetime(2020, 1, 1),
        )
        # Binding hot row + cold binding row.
        _seed_hot_binding(
            id_val=20,
            env=ENV,
            device_props=(
                '{"sandbox_id": "sb-bind-20", '
                '"ttl_expiration_time": "2026-09-20T00:00:00"}'
            ),
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=20,
            sandbox_id="sb-bind-20",
            next_renew_at=datetime(2020, 1, 3),
        )

    def test_list_due_device_join_order_orphan_env(self, repo):
        self._seed()

        rows = repo.list_due_for_renewal(ENV, "baas_device", 500, now=NOW)

        # Due device rows only: orphan (oldest), cross-env orphan, hot match.
        assert [r["source_id"] for r in rows] == [999, 3, 1]
        assert [r["hot_id"] for r in rows] == [None, None, 1]
        first = rows[0]
        # Pitfall 4 contract keys.
        assert set(first.keys()) >= {
            "id",
            "sandbox_id",
            "source_table",
            "source_id",
            "next_renew_at",
            "renew_fail_count",
            "device_props",
            "hot_id",
        }
        matched = rows[2]
        assert (
            matched["device_props"] == '{"ttl_expiration_time":"2026-09-01T00:00:00"}'
        )
        # '他 env 行不漏出': the prod cold row is absent (WHERE env) and the
        # prod hot row cannot satisfy the env-guarded join (hot_id IS NULL).
        assert all(r["sandbox_id"] != "sb-prod" for r in rows)
        assert all(r["sandbox_id"] != "sb-stop" for r in rows)
        # Binding cold row never leaks into the device query (source_table filter).
        assert all(r["source_table"] == "baas_device" for r in rows)

    def test_list_due_binding_join(self, repo):
        self._seed()

        rows = repo.list_due_for_renewal(ENV, "ac_entity_device_binding", 500, now=NOW)

        # source_id=7 is an ACTIVE due binding cold row without a hot row
        # (orphan); source_id=20 has its hot binding row. Ordered by
        # next_renew_at ASC (7: 2020-01-01, 20: 2020-01-03).
        assert [(r["source_id"], r["hot_id"]) for r in rows] == [(7, None), (20, 20)]
        assert rows[1]["device_props"] is not None
        # Device-side cold rows never leak into the binding query
        # (source_table filter).
        assert all(r["source_table"] == "ac_entity_device_binding" for r in rows)

    def test_list_due_limit_is_applied(self, repo):
        self._seed()

        rows = repo.list_due_for_renewal(ENV, "baas_device", 2, now=NOW)

        assert [r["source_id"] for r in rows] == [999, 3]

    def test_list_due_soft_deleted_device_reads_as_orphan(self, repo):
        """WR-03: is_deleted=1 on the hot device row fails the ON-side
        JOIN condition, so the due cold row comes back with hot_id IS NULL
        and flows to orphan handling — never renewed for a soft-deleted
        device."""
        self._seed()
        _seed_hot_device(
            id_val=8, env=ENV, provider_device_id="sb-softdel", is_deleted=1
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=8,
            sandbox_id="sb-softdel",
            next_renew_at=datetime(2020, 1, 10),
        )

        rows = repo.list_due_for_renewal(ENV, "baas_device", 500, now=NOW)

        by_source = {r["source_id"]: r for r in rows}
        # The row is present (ON-side semantics, not WHERE-side exclusion)
        # but reads as an orphan: the scheduler will mark it STOPPED.
        assert 8 in by_source
        assert by_source[8]["hot_id"] is None

    def test_list_due_unsupported_source_table_raises(self, repo):
        with pytest.raises(ValueError, match="Unsupported source_table"):
            repo.list_due_for_renewal(ENV, "bogus", 500, now=NOW)


# ==================== Row-level updates ====================


class TestRowUpdates:
    SOURCE_ID = 41

    def _seed(self, *, fail_count: int = 5):
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=self.SOURCE_ID,
            sandbox_id="sb-upd",
            next_renew_at=datetime(2020, 1, 1),
            renew_fail_count=fail_count,
        )
        with db_manager.orm_session() as session:
            session.query(TtlRenewalScheduleModel).filter(
                TtlRenewalScheduleModel.source_id == self.SOURCE_ID
            ).update({"gmt_modified": datetime(2020, 1, 1)}, synchronize_session=False)
        # Foreign-env twin that must never be touched.
        _seed_cold(
            env="prod",
            source_table="baas_device",
            source_id=self.SOURCE_ID,
            sandbox_id="sb-upd",
            next_renew_at=datetime(2020, 1, 1),
            renew_fail_count=fail_count,
        )

    def _foreign_row(self):
        with db_manager.orm_session() as session:
            return (
                session.query(TtlRenewalScheduleModel)
                .filter(
                    TtlRenewalScheduleModel.source_id == self.SOURCE_ID,
                    TtlRenewalScheduleModel.env == "prod",
                )
                .one()
            )

    def test_update_after_success_resets_and_records(self, repo):
        self._seed()
        next_renew = datetime(2030, 1, 1, 0, 0, 0)

        repo.update_after_success(ENV, "baas_device", self.SOURCE_ID, next_renew)

        row = _cold_row(self.SOURCE_ID)
        assert row.next_renew_at == next_renew
        assert row.renew_fail_count == 0
        assert row.last_renewed_at is not None
        assert row.gmt_modified > datetime(2020, 1, 1)
        # env scoping: foreign-env twin untouched
        foreign = self._foreign_row()
        assert foreign.next_renew_at == datetime(2020, 1, 1)
        assert foreign.renew_fail_count == 5

    def test_update_after_failure_sets_retry_and_count(self, repo):
        self._seed(fail_count=2)
        next_renew = datetime(2031, 1, 1, 0, 0, 0)

        repo.update_after_failure(ENV, "baas_device", self.SOURCE_ID, next_renew, 3)

        row = _cold_row(self.SOURCE_ID)
        assert row.renew_fail_count == 3
        assert row.next_renew_at == next_renew
        assert row.last_renewed_at is None
        assert row.gmt_modified > datetime(2020, 1, 1)

    def test_postpone_renews_without_renewal_event(self, repo):
        self._seed(fail_count=4)
        next_renew = datetime(2032, 1, 1, 0, 0, 0)

        repo.postpone_renewal(ENV, "baas_device", self.SOURCE_ID, next_renew)

        row = _cold_row(self.SOURCE_ID)
        assert row.next_renew_at == next_renew
        assert row.renew_fail_count == 0
        assert row.last_renewed_at is None
        assert row.gmt_modified > datetime(2020, 1, 1)

    def test_set_status_stops_row(self, repo):
        self._seed()

        repo.set_status(ENV, "baas_device", self.SOURCE_ID, "STOPPED")

        row = _cold_row(self.SOURCE_ID)
        assert row.status == "STOPPED"
        assert row.gmt_modified > datetime(2020, 1, 1)
        assert self._foreign_row().status == "ACTIVE"


# ==================== find_unregistered anti-join ====================


PROPS_TTL = '"ttl_expiration_time": "2026-09-10T00:00:00"'


class TestFindUnregistered:
    def test_device_side_anti_join_and_filters(self, repo):
        # Found: 10 (unregistered), 12 (stale cold sandbox).
        _seed_hot_device(id_val=10, env=ENV, provider_device_id="sb-10")
        _seed_hot_device(id_val=12, env=ENV, provider_device_id="sb-12")
        # Suppressed: 11 (matching ACTIVE cold row).
        _seed_hot_device(id_val=11, env=ENV, provider_device_id="sb-11")
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=11,
            sandbox_id="sb-11",
            next_renew_at=datetime(2020, 1, 1),
        )
        # Stale cold row: same source, OLD sandbox — must NOT suppress 12.
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=12,
            sandbox_id="sb-old",
            next_renew_at=datetime(2020, 1, 1),
        )
        # Excluded: soft-deleted / foreign env / non-ARCA / STOPPED / null pid.
        _seed_hot_device(id_val=13, env=ENV, provider_device_id="sb-13", is_deleted=1)
        _seed_hot_device(id_val=14, env="prod", provider_device_id="sb-14")
        _seed_hot_device(
            id_val=15, env=ENV, provider_device_id="sb-15", provider_type="LOCAL"
        )
        _seed_hot_device(
            id_val=16, env=ENV, provider_device_id="sb-16", status="STOPPED"
        )
        _seed_hot_device(id_val=17, env=ENV, provider_device_id=None)

        rows = repo.find_unregistered(ENV, "baas_device", 500)

        assert [
            (r["id"], r["sandbox_id"], r["source_table"], r["ttl"]) for r in rows
        ] == [
            (10, "sb-10", "baas_device", "2026-09-01T00:00:00"),
            (12, "sb-12", "baas_device", "2026-09-01T00:00:00"),
        ]

    def test_binding_side_anti_join_json_equality(self, repo):
        # Found: 20 (unregistered), 22 (stale cold sandbox).
        _seed_hot_binding(
            id_val=20,
            env=ENV,
            device_props=('{"sandbox_id": "sb-b-20", ' + PROPS_TTL + "}"),
        )
        _seed_hot_binding(
            id_val=22,
            env=ENV,
            device_props=('{"sandbox_id": "sb-b-22", ' + PROPS_TTL + "}"),
        )
        # Suppressed: 21 (matching ACTIVE cold row via JSON sandbox equality).
        _seed_hot_binding(
            id_val=21,
            env=ENV,
            device_props=('{"sandbox_id": "sb-b-21", ' + PROPS_TTL + "}"),
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=21,
            sandbox_id="sb-b-21",
            next_renew_at=datetime(2020, 1, 1),
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=22,
            sandbox_id="sb-old",
            next_renew_at=datetime(2020, 1, 1),
        )
        # Excluded: non-arca provider / foreign env / null props / no sandbox key.
        _seed_hot_binding(
            id_val=23,
            env=ENV,
            device_provider="teclaw",
            device_props='{"sandbox_id": "sb-b-23"}',
        )
        _seed_hot_binding(
            id_val=24, env="prod", device_props='{"sandbox_id": "sb-b-24"}'
        )
        _seed_hot_binding(id_val=25, env=ENV, device_props=None)
        _seed_hot_binding(id_val=26, env=ENV, device_props='{"other": 1}')

        rows = repo.find_unregistered(ENV, "ac_entity_device_binding", 500)

        assert [
            (r["id"], r["sandbox_id"], r["source_table"], r["ttl"]) for r in rows
        ] == [
            (20, "sb-b-20", "ac_entity_device_binding", "2026-09-10T00:00:00"),
            (22, "sb-b-22", "ac_entity_device_binding", "2026-09-10T00:00:00"),
        ]
        # Four-key contract (Pitfall 4).
        assert sorted(rows[0].keys()) == ["id", "sandbox_id", "source_table", "ttl"]

    def test_unsupported_side_raises(self, repo):
        with pytest.raises(ValueError, match="Unsupported side"):
            repo.find_unregistered(ENV, "bogus", 500)


# ==================== Counts ====================


class TestCounts:
    def test_count_active_env_scoped(self, repo):
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=1,
            sandbox_id="sb-c1",
            next_renew_at=datetime(2020, 1, 1),
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=2,
            sandbox_id="sb-c2",
            next_renew_at=datetime(2020, 1, 1),
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=3,
            sandbox_id="sb-c3",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )
        _seed_cold(
            env="prod",
            source_table="baas_device",
            source_id=4,
            sandbox_id="sb-c4",
            next_renew_at=datetime(2020, 1, 1),
        )

        assert repo.count_active(ENV) == 2

    def test_count_hot_arca_devices(self, repo):
        _seed_hot_device(id_val=1, env=ENV, provider_device_id="sb-h1")
        _seed_hot_device(id_val=2, env=ENV, provider_device_id="sb-h2")
        _seed_hot_device(id_val=3, env=ENV, provider_device_id="sb-h3", is_deleted=1)
        _seed_hot_device(id_val=4, env="prod", provider_device_id="sb-h4")
        _seed_hot_device(
            id_val=5, env=ENV, provider_device_id="sb-h5", provider_type="LOCAL"
        )
        _seed_hot_device(
            id_val=6, env=ENV, provider_device_id="sb-h6", status="STOPPED"
        )
        _seed_hot_device(id_val=7, env=ENV, provider_device_id=None)

        assert repo.count_hot_arca_devices(ENV) == 2

    def test_count_hot_arca_bindings(self, repo):
        _seed_hot_binding(id_val=1, env=ENV, device_props='{"sandbox_id": "sb-b1"}')
        _seed_hot_binding(id_val=2, env=ENV, device_props='{"sandbox_id": "sb-b2"}')
        _seed_hot_binding(
            id_val=3,
            env=ENV,
            device_provider="teclaw",
            device_props='{"sandbox_id": "sb-b3"}',
        )
        _seed_hot_binding(id_val=4, env="prod", device_props='{"sandbox_id": "sb-b4"}')
        _seed_hot_binding(id_val=5, env=ENV, device_props=None)
        _seed_hot_binding(id_val=6, env=ENV, device_props='{"other": 1}')

        assert repo.count_hot_arca_bindings(ENV) == 2
