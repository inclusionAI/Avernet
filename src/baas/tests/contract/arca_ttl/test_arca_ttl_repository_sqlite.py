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


def _flip_stopped_with_failures(*, stop_reason: str | None = None) -> None:
    """Mark the registered row STOPPED with failure history, an optional
    stop_reason stamp, and an old mtime."""
    with db_manager.orm_session() as session:
        session.query(TtlRenewalScheduleModel).filter(
            TtlRenewalScheduleModel.env == ENV,
            TtlRenewalScheduleModel.source_table == SOURCE_TABLE,
            TtlRenewalScheduleModel.source_id == SOURCE_ID,
        ).update(
            {
                "status": "STOPPED",
                "renew_fail_count": 7,
                "stop_reason": stop_reason,
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
        _flip_stopped_with_failures(stop_reason="threshold_gone")

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
        # Resurrection clears the stale STOPPED origin along with the status.
        assert row.stop_reason is None
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
    stop_reason: str | None = None,
) -> None:
    record = TtlRenewalScheduleModel(
        env=env,
        sandbox_id=sandbox_id,
        source_table=source_table,
        source_id=source_id,
        next_renew_at=next_renew_at,
        status=status,
        renew_fail_count=renew_fail_count,
    )
    if stop_reason is not None:
        record.stop_reason = stop_reason
    with db_manager.orm_session() as session:
        session.add(record)


def _seed_hot_device(
    *,
    id_val: int,
    env: str,
    provider_device_id: str | None,
    provider_type: str = "ARCA",
    status: str = "ACTIVE",
    is_deleted: int = 0,
    provider_device_props: str | None = None,
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
                    provider_device_props
                    if provider_device_props is not None
                    else (
                        '{"ttl_expiration_time":"2026-09-01T00:00:00"}'
                        if provider_device_id
                        else None
                    )
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

    def test_set_status_with_stop_reason_persists_and_env_scopes(self, repo):
        self._seed()

        repo.set_status(
            ENV, "baas_device", self.SOURCE_ID, "STOPPED", stop_reason="threshold_gone"
        )

        row = _cold_row(self.SOURCE_ID)
        assert row.status == "STOPPED"
        assert row.stop_reason == "threshold_gone"
        assert row.gmt_modified > datetime(2020, 1, 1)
        # env scoping: the foreign-env twin stays ACTIVE with no reason.
        foreign = self._foreign_row()
        assert foreign.status == "ACTIVE"
        assert foreign.stop_reason is None


# ==================== find_unregistered anti-join ====================


PROPS_TTL = (
    '"ttl_expiration_time": "2026-09-10T00:00:00", '
    '"ttl_expiration_timestamp": 1788969600000'
)


class TestFindUnregistered:
    def test_device_side_anti_join_and_filters(self, repo):
        # Found: 10 (unregistered), 12 (stale cold sandbox).
        # Dual-key pair-write props mirror the health_check scanner idiom (CR-GAP-01):
        # the retargeted reader labels ttl from $.ttl_expiration_timestamp.
        _seed_hot_device(
            id_val=10,
            env=ENV,
            provider_device_id="sb-10",
            provider_device_props='{"ttl_expiration_time":"2026-09-01T00:00:00","ttl_expiration_timestamp":1788192000000}',
        )
        _seed_hot_device(
            id_val=12,
            env=ENV,
            provider_device_id="sb-12",
            provider_device_props='{"ttl_expiration_time":"2026-09-01T00:00:00","ttl_expiration_timestamp":1788192000000}',
        )
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
            (10, "sb-10", "baas_device", 1788192000000),
            (12, "sb-12", "baas_device", 1788192000000),
        ]

    def test_stopped_cold_row_same_sandbox_suppresses_hot(self, repo):
        """D-85-AJ1 row-level pin (device side): a STOPPED cold row matching
        (env, source_table, source_id, sandbox_id) suppresses the hot row —
        threshold-STOPPED is terminal, no resurrection loop from discovery."""
        _seed_hot_device(
            id_val=40,
            env=ENV,
            provider_device_id="sb-40",
            provider_device_props=(
                '{"ttl_expiration_time":"2026-09-01T00:00:00",'
                '"ttl_expiration_timestamp":1788192000000}'
            ),
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=40,
            sandbox_id="sb-40",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )

        rows = repo.find_unregistered(ENV, "baas_device", 500)

        assert rows == []

    def test_stopped_cold_row_stale_sandbox_does_not_suppress_hot(self, repo):
        """D-85-AJ1 row-level pin (device side): a STOPPED cold row for an
        OLD sandbox (destroy+create swap) does not match the anti-join —
        the swapped-in hot row stays discoverable as the safety net."""
        _seed_hot_device(
            id_val=41,
            env=ENV,
            provider_device_id="sb-41",
            provider_device_props=(
                '{"ttl_expiration_time":"2026-09-01T00:00:00",'
                '"ttl_expiration_timestamp":1788192000000}'
            ),
        )
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=41,
            sandbox_id="sb-old",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )

        rows = repo.find_unregistered(ENV, "baas_device", 500)

        assert [
            (r["id"], r["sandbox_id"], r["source_table"], r["ttl"]) for r in rows
        ] == [
            (41, "sb-41", "baas_device", 1788192000000),
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
            (20, "sb-b-20", "ac_entity_device_binding", 1788969600000),
            (22, "sb-b-22", "ac_entity_device_binding", 1788969600000),
        ]
        # Four-key contract (Pitfall 4).
        assert sorted(rows[0].keys()) == ["id", "sandbox_id", "source_table", "ttl"]

    def test_stopped_cold_row_same_sandbox_suppresses_hot_binding(self, repo):
        """IN-01 row-level pin (binding side): a STOPPED binding cold row
        matching (env, source_table, source_id, sandbox_id) suppresses the
        hot binding row — threshold-STOPPED is terminal, no resurrection
        loop from discovery. Closes the R5 gap: binding suppression was
        provable only via mocked compiled-SQL asserts (13b/13c); this pins
        the real-SQLite row-level truth."""
        _seed_hot_binding(
            id_val=50,
            env=ENV,
            device_props=('{"sandbox_id": "sb-b-50", ' + PROPS_TTL + "}"),
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=50,
            sandbox_id="sb-b-50",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )

        rows = repo.find_unregistered(ENV, "ac_entity_device_binding", 500)

        assert rows == []

    def test_stopped_cold_row_stale_sandbox_does_not_suppress_hot_binding(self, repo):
        """IN-01 row-level pin (binding side): a STOPPED cold row for an
        OLD sandbox (destroy+create swap) does not match the anti-join —
        the swapped-in hot binding row stays discoverable as the safety net."""
        _seed_hot_binding(
            id_val=51,
            env=ENV,
            device_props=('{"sandbox_id": "sb-b-51", ' + PROPS_TTL + "}"),
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=51,
            sandbox_id="sb-b-old",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )

        rows = repo.find_unregistered(ENV, "ac_entity_device_binding", 500)

        assert [
            (r["id"], r["sandbox_id"], r["source_table"], r["ttl"]) for r in rows
        ] == [
            (51, "sb-b-51", "ac_entity_device_binding", 1788969600000),
        ]
        # Four-key contract (Pitfall 4).
        assert sorted(rows[0].keys()) == ["id", "sandbox_id", "source_table", "ttl"]

    def test_legacy_ttl_expiration_time_key_falls_back_in_discovery(self, repo):
        """WR-02: pre-release rows persisted only the legacy integer-ms
        ttl_expiration_time key (no ttl_expiration_timestamp) — the
        dual-key COALESCE must project that legacy value instead of NULL,
        so pre-existing ACTIVE containers keep their real expiry instead
        of degrading to the discovery now+window fallback."""
        _seed_hot_device(
            id_val=30,
            env=ENV,
            provider_device_id="sb-30",
            provider_device_props='{"ttl_expiration_time": 1788192000000}',
        )
        _seed_hot_binding(
            id_val=31,
            env=ENV,
            device_props='{"sandbox_id": "sb-b-31", "ttl_expiration_time": 1788969600000}',
        )

        device_rows = repo.find_unregistered(ENV, "baas_device", 500)
        binding_rows = repo.find_unregistered(ENV, "ac_entity_device_binding", 500)

        assert [(r["id"], r["ttl"]) for r in device_rows] == [(30, 1788192000000)]
        assert [(r["id"], r["ttl"]) for r in binding_rows] == [(31, 1788969600000)]

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

    def test_count_hot_covered_any_status_coverage(self, repo):
        """86-02 (WR-01): a hot device covered by a STOPPED cold row counts
        as covered; an uncovered hot device does not; an ACTIVE-covered hot
        device also counts — coverage is any-status, the STOPPED-only
        variant is the suppressed-terminal population."""
        # Covered by a STOPPED cold row (terminal suppression).
        _seed_hot_device(id_val=101, env=ENV, provider_device_id="sb-101")
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=101,
            sandbox_id="sb-101",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )
        # Uncovered hot device (no cold row at all).
        _seed_hot_device(id_val=102, env=ENV, provider_device_id="sb-102")

        assert repo.count_hot_covered(ENV) == 1
        assert repo.count_suppressed_terminal(ENV) == 1

        # Extra hot device covered by an ACTIVE cold row: covered rises to
        # 2 while the STOPPED-only count stays 1 (any-status vs stopped-only).
        _seed_hot_device(id_val=103, env=ENV, provider_device_id="sb-103")
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=103,
            sandbox_id="sb-103",
            next_renew_at=datetime(2020, 1, 1),
            status="ACTIVE",
        )

        assert repo.count_hot_covered(ENV) == 2
        assert repo.count_suppressed_terminal(ENV) == 1

    def test_count_covered_binding_side_through_json_sandbox(self, repo):
        """86-02: the binding-side covered counts INNER JOIN on the JSON
        sandbox equality — a STOPPED binding cold row covers its hot row,
        a stale cold sandbox does not."""
        _seed_hot_binding(
            id_val=201, env=ENV, device_props='{"sandbox_id": "sb-b-201"}'
        )
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=201,
            sandbox_id="sb-b-201",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )
        _seed_hot_binding(
            id_val=202, env=ENV, device_props='{"sandbox_id": "sb-b-202"}'
        )
        # Stale cold row for an OLD sandbox must NOT cover 202.
        _seed_cold(
            env=ENV,
            source_table="ac_entity_device_binding",
            source_id=202,
            sandbox_id="sb-old",
            next_renew_at=datetime(2020, 1, 1),
            status="STOPPED",
        )

        assert repo.count_hot_covered(ENV) == 1
        assert repo.count_suppressed_terminal(ENV) == 1

    def test_count_hot_covered_env_scoped_on_both_join_sides(self, repo):
        """86-02: the coverage join is env-guarded — a prod-env hot row
        matching a pre-env cold row is NOT counted as covered."""
        _seed_hot_device(id_val=301, env="prod", provider_device_id="sb-301")
        _seed_cold(
            env=ENV,
            source_table="baas_device",
            source_id=301,
            sandbox_id="sb-301",
            next_renew_at=datetime(2020, 1, 1),
        )

        # pre-env count: join env misses the prod hot row.
        assert repo.count_hot_covered(ENV) == 0
        # prod-env count: the cold row is pre-env, so the join still misses.
        assert repo.count_hot_covered("prod") == 0


class TestHotRowExists:
    """86-02 (WR-02): the orphan-recheck existence probe — mirrors the due
    JOIN conditions (soft-deleted device reads absent; binding side carries
    no is_deleted, D-16')."""

    def test_soft_deleted_device_reads_absent(self, repo):
        _seed_hot_device(id_val=401, env=ENV, provider_device_id="sb-401", is_deleted=1)

        assert repo.hot_row_exists(ENV, "baas_device", 401) is False

    def test_live_device_and_binding_rows_exist(self, repo):
        _seed_hot_device(id_val=402, env=ENV, provider_device_id="sb-402")
        _seed_hot_binding(
            id_val=403, env=ENV, device_props='{"sandbox_id": "sb-b-403"}'
        )

        assert repo.hot_row_exists(ENV, "baas_device", 402) is True
        assert repo.hot_row_exists(ENV, "ac_entity_device_binding", 403) is True

    def test_absent_id_and_foreign_env_read_absent(self, repo):
        _seed_hot_device(id_val=404, env="prod", provider_device_id="sb-404")

        assert repo.hot_row_exists(ENV, "baas_device", 999) is False
        # Env-scoped: the prod row is invisible to a pre-env recheck.
        assert repo.hot_row_exists(ENV, "baas_device", 404) is False
        assert repo.hot_row_exists("prod", "baas_device", 404) is True

    def test_unsupported_source_table_raises(self, repo):
        with pytest.raises(ValueError, match="Unsupported source_table"):
            repo.hot_row_exists(ENV, "bogus", 1)
