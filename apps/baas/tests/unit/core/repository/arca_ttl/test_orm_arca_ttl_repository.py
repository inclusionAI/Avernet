"""Unit tests for OrmTtlRenewalScheduleRepository — all 11 methods.

Uses the community MagicMock-session pattern (see
tests/unit/core/repository/bot/test_orm_bot_repository.py): the
database mock yields a MagicMock session via ``@with_orm_session``, and
each test asserts on the captured SQLAlchemy statement — compiled with
the mysql (default mock) or sqlite dialect — plus the bound params.

Semantic coverage mirrors the enterprise test_repository.py cases
(upsert / resurrection, LEFT JOIN orphans, env-guarded joins, failure
increment, postpone-without-last_renewed_at, anti-join strictness,
D-16' no binding-side is_deleted).

Note: no ``_patch_model`` autouse fixture — this repository never
instantiates ORM model objects; ``insert()`` / ``select()`` statement
construction requires the real Table metadata, so the real model class
is used throughout (a MagicMock patch would break stmt construction).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql, sqlite

from secbaas.community.core.repository.arca_ttl import OrmTtlRenewalScheduleRepository


def _make_repo():
    """Create a repository with a mocked database session."""
    mock_session = MagicMock()
    mock_db = MagicMock()
    mock_db.orm_session.return_value.__enter__.return_value = mock_session
    mock_db.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    repo = OrmTtlRenewalScheduleRepository(database=mock_db)
    return repo, mock_session


def _make_row(mapping: dict):
    """Create a mock Row with a _mapping dict (sqlalchemy Row shape)."""
    row = MagicMock()
    row._mapping = mapping
    return row


_SQLITE = "sqlite"

# Fixed naive-UTC gate time passed to list_due_for_renewal (CR-01).
_NOW = datetime(2026, 8, 21, 0, 0, 0)


class TestRegister:
    def test_register_upserts_with_bindparams(self):
        """Test 1: register() builds the atomic upsert with correct params."""
        repo, mock_session = _make_repo()
        next_renew = datetime(2026, 8, 20, 12, 0, 0)

        repo.register(
            "test",
            sandbox_id="sb-001",
            source_table="baas_device",
            source_id=1,
            next_renew_at=next_renew,
        )

        mock_session.execute.assert_called_once()
        stmt = mock_session.execute.call_args[0][0]
        compiled = stmt.compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "INSERT INTO baas_bot_ttl_renewal_schedule" in sql_text
        assert "ON DUPLICATE KEY UPDATE" in sql_text
        assert "gmt_modified" in sql_text

        params = compiled.params
        assert params["env"] == "test"
        assert params["sandbox_id"] == "sb-001"
        assert params["source_table"] == "baas_device"
        assert params["source_id"] == 1
        assert params["next_renew_at"] == next_renew
        assert params["status"] == "ACTIVE"
        assert params["renew_fail_count"] == 0

    def test_register_resurrect_stopped(self):
        """Test 2: upsert SET resets status to ACTIVE and zeroes fail count."""
        repo, mock_session = _make_repo()

        repo.register(
            "test",
            sandbox_id="sb-002",
            source_table="baas_device",
            source_id=2,
            next_renew_at=datetime(2026, 8, 20, 12, 0, 0),
        )

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "ON DUPLICATE KEY UPDATE" in sql_text
        assert "gmt_modified = now()" in sql_text
        assert compiled.params["status"] == "ACTIVE"
        assert compiled.params["renew_fail_count"] == 0

    def test_register_sqlite_dialect_uses_on_conflict(self):
        """D-04'/D-06': the sqlite dialect branch renders ON CONFLICT."""
        repo, mock_session = _make_repo()
        mock_session.bind.dialect.name = _SQLITE

        repo.register(
            "test",
            sandbox_id="sb-003",
            source_table="baas_device",
            source_id=3,
            next_renew_at=datetime(2026, 8, 20, 12, 0, 0),
        )

        compiled = mock_session.execute.call_args[0][0].compile(
            dialect=sqlite.dialect()
        )
        sql_text = str(compiled)

        assert "INSERT INTO baas_bot_ttl_renewal_schedule" in sql_text
        assert "ON CONFLICT (env, source_table, source_id)" in sql_text
        assert "DO UPDATE SET" in sql_text
        assert "gmt_modified" in sql_text
        assert "ON DUPLICATE KEY UPDATE" not in sql_text

    def test_register_set_clears_stop_reason_mysql(self):
        """Re-register clears stop_reason to NULL in the mysql SET region."""
        repo, mock_session = _make_repo()

        repo.register(
            "test",
            sandbox_id="sb-004",
            source_table="baas_device",
            source_id=4,
            next_renew_at=datetime(2026, 8, 20, 12, 0, 0),
        )

        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        set_region = sql_text.split("ON DUPLICATE KEY UPDATE", 1)[1]
        assert "stop_reason" in set_region
        assert compiled.params["status"] == "ACTIVE"
        assert compiled.params["renew_fail_count"] == 0

    def test_register_set_clears_stop_reason_sqlite(self):
        """Re-register clears stop_reason to NULL in the sqlite SET region."""
        repo, mock_session = _make_repo()
        mock_session.bind.dialect.name = _SQLITE

        repo.register(
            "test",
            sandbox_id="sb-004",
            source_table="baas_device",
            source_id=4,
            next_renew_at=datetime(2026, 8, 20, 12, 0, 0),
        )

        compiled = mock_session.execute.call_args[0][0].compile(
            dialect=sqlite.dialect()
        )
        sql_text = str(compiled)
        set_region = sql_text.split("DO UPDATE SET", 1)[1]
        assert "stop_reason" in set_region


class TestRegisterIfMissing:
    def test_register_if_missing_inserts_via_upsert(self):
        """Test 3: register_if_missing() inserts a missing row via upsert."""
        repo, mock_session = _make_repo()
        next_renew = datetime(2026, 8, 20, 12, 0, 0)

        repo.register_if_missing(
            "test",
            sandbox_id="sb-001",
            source_table="baas_device",
            source_id=1,
            next_renew_at=next_renew,
        )

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "INSERT INTO baas_bot_ttl_renewal_schedule" in sql_text
        params = compiled.params
        assert params["env"] == "test"
        assert params["sandbox_id"] == "sb-001"
        assert params["source_table"] == "baas_device"
        assert params["source_id"] == 1

    def test_register_if_missing_existing_row_handled_by_index(self):
        """Test 4: dedup for an existing row is the uk_source index's job —
        the repo still executes the single atomic upsert."""
        repo, mock_session = _make_repo()

        repo.register_if_missing(
            "test",
            sandbox_id="sb-001",
            source_table="baas_device",
            source_id=1,
            next_renew_at=datetime(2026, 8, 20, 12, 0, 0),
        )

        mock_session.execute.assert_called_once()

    def test_register_if_missing_set_clears_stop_reason_mysql(self):
        """Register_if_missing clears stop_reason to NULL on conflict."""
        repo, mock_session = _make_repo()

        repo.register_if_missing(
            "test",
            sandbox_id="sb-001",
            source_table="baas_device",
            source_id=1,
            next_renew_at=datetime(2026, 8, 20, 12, 0, 0),
        )

        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        set_region = sql_text.split("ON DUPLICATE KEY UPDATE", 1)[1]
        assert "stop_reason" in set_region


class TestListDueForRenewal:
    def test_list_due_for_renewal_normal(self):
        """Test 5: LEFT JOIN with hot_id label and dict rows."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 1,
                    "sandbox_id": "sb-001",
                    "source_table": "baas_device",
                    "source_id": 10,
                    "next_renew_at": datetime(2026, 8, 18, 0, 0, 0),
                    "renew_fail_count": 0,
                    "device_props": '{"ttl_expiration_time":"2026-08-19T12:00:00"}',
                    "hot_id": 5,
                }
            )
        ]

        results = repo.list_due_for_renewal("test", "baas_device", 500, now=_NOW)

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "LEFT OUTER JOIN baas_device" in sql_text
        assert "provider_device_props AS device_props" in sql_text
        assert "baas_device.id AS hot_id" in sql_text
        # CR-01: the gate compares against a caller-supplied bound datetime,
        # never the DB clock function.
        assert "next_renew_at < %s" in sql_text
        assert "now()" not in sql_text
        assert "ORDER BY baas_bot_ttl_renewal_schedule.next_renew_at ASC" in sql_text
        assert "LIMIT %s" in sql_text
        # Rule 1 deviation: cold side restricted to the requested source_table
        assert "baas_bot_ttl_renewal_schedule.source_table = %s" in sql_text

        params = compiled.params
        assert params["env_1"] == "test" or params["env_2"] == "test"
        assert 500 in params.values()
        assert params["next_renew_at_1"] == _NOW

        assert len(results) == 1
        assert results[0]["hot_id"] == 5
        assert results[0]["sandbox_id"] == "sb-001"

    def test_list_due_for_renewal_orphan(self):
        """Test 6: orphaned container returns hot_id=None."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 2,
                    "sandbox_id": "sb-orphan",
                    "source_table": "baas_device",
                    "source_id": 99,
                    "next_renew_at": datetime(2026, 8, 18, 0, 0, 0),
                    "renew_fail_count": 0,
                    "device_props": None,
                    "hot_id": None,
                }
            )
        ]

        results = repo.list_due_for_renewal("test", "baas_device", 500, now=_NOW)

        sql_text = str(
            mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        )
        assert "LEFT OUTER JOIN" in sql_text
        assert "INNER JOIN" not in sql_text

        assert len(results) == 1
        assert results[0]["hot_id"] is None

    def test_list_due_for_renewal_empty(self):
        """Test 7: empty result set returns []."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = []

        results = repo.list_due_for_renewal("test", "baas_device", 500, now=_NOW)

        assert results == []

    def test_list_due_device_join_is_env_guarded(self):
        """CR-01: LEFT JOIN ON and WHERE are env-guarded."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = []

        repo.list_due_for_renewal("test", "baas_device", 500, now=_NOW)

        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        assert "baas_device.env = %s" in sql_text
        assert "baas_bot_ttl_renewal_schedule.env = %s" in sql_text
        assert "test" in compiled.params.values()
        # WR-03: the device-side JOIN also requires is_deleted = 0 (ON-side,
        # so a soft-deleted device reads as an orphan instead of renewing).
        assert "baas_device.is_deleted = %s" in sql_text
        assert 0 in compiled.params.values()

    def test_list_due_binding_join_is_env_guarded(self):
        """CR-01: the binding hot-table JOIN is env-guarded."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = []

        repo.list_due_for_renewal("test", "ac_entity_device_binding", 500, now=_NOW)

        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        assert "LEFT OUTER JOIN ac_entity_device_binding" in sql_text
        assert "ac_entity_device_binding.env = %s" in sql_text
        assert "test" in compiled.params.values()
        # D-16': the binding side stays unfiltered — production
        # ac_entity_device_binding has no is_deleted column.
        assert "is_deleted" not in sql_text

    def test_list_due_unsupported_source_table_raises(self):
        """Whitelist guard: unsupported source_table raises ValueError."""
        repo, mock_session = _make_repo()

        with pytest.raises(ValueError, match="Unsupported source_table"):
            repo.list_due_for_renewal("test", "bogus", 500, now=_NOW)


class TestUpdateAfterSuccess:
    def test_update_after_success(self):
        """Test 8: resets fail count, sets next_renew_at and last_renewed_at."""
        repo, mock_session = _make_repo()
        next_renew = datetime(2026, 8, 21, 0, 0, 0)

        repo.update_after_success("test", "baas_device", 1, next_renew)

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "UPDATE baas_bot_ttl_renewal_schedule SET" in sql_text
        assert "next_renew_at=%s" in sql_text
        assert "renew_fail_count=%s" in sql_text
        assert "last_renewed_at=now()" in sql_text
        assert "gmt_modified=now()" in sql_text

        params = compiled.params
        assert params["env_1"] == "test"
        assert params["source_table_1"] == "baas_device"
        assert params["source_id_1"] == 1
        assert params["next_renew_at"] == next_renew
        assert 0 in params.values()


class TestUpdateAfterFailure:
    def test_update_after_failure(self):
        """Test 9: sets next_renew_at and new fail count, no last_renewed_at."""
        repo, mock_session = _make_repo()
        next_renew = datetime(2026, 8, 18, 12, 30, 0)

        repo.update_after_failure("test", "baas_device", 1, next_renew, 3)

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "UPDATE baas_bot_ttl_renewal_schedule SET" in sql_text
        assert "next_renew_at=%s" in sql_text
        assert "renew_fail_count=%s" in sql_text
        assert "gmt_modified=now()" in sql_text
        assert "last_renewed_at" not in sql_text

        params = compiled.params
        assert params["env_1"] == "test"
        assert params["source_table_1"] == "baas_device"
        assert params["source_id_1"] == 1
        assert params["next_renew_at"] == next_renew
        assert params["renew_fail_count"] == 3


class TestPostponeRenewal:
    def test_postpone_renewal_does_not_set_last_renewed_at(self):
        """Test 10: postpones and clears fail count without a renewal event."""
        repo, mock_session = _make_repo()
        next_renew = datetime(2026, 8, 21, 0, 0, 0)

        repo.postpone_renewal("test", "baas_device", 1, next_renew)

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "UPDATE baas_bot_ttl_renewal_schedule SET" in sql_text
        assert "next_renew_at=%s" in sql_text
        assert "renew_fail_count=%s" in sql_text
        assert "gmt_modified=now()" in sql_text
        # CRITICAL: last_renewed_at must NOT be touched
        assert "last_renewed_at" not in sql_text

        params = compiled.params
        assert params["env_1"] == "test"
        assert params["source_table_1"] == "baas_device"
        assert params["source_id_1"] == 1
        assert params["next_renew_at"] == next_renew
        assert 0 in params.values()


class TestSetStatus:
    def test_set_status(self):
        """Test 10b: updates status via UPDATE ... WHERE."""
        repo, mock_session = _make_repo()

        repo.set_status("test", "baas_device", 1, "STOPPED")

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "UPDATE baas_bot_ttl_renewal_schedule SET" in sql_text
        assert "gmt_modified=now()" in sql_text

        params = compiled.params
        assert params["status"] == "STOPPED"
        assert params["env_1"] == "test"
        assert params["source_table_1"] == "baas_device"
        assert params["source_id_1"] == 1

    def test_set_status_with_stop_reason(self):
        """stop_reason is threaded as a bound param on the UPDATE."""
        repo, mock_session = _make_repo()

        repo.set_status(
            "test", "baas_device", 1, "STOPPED", stop_reason="threshold_gone"
        )

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())

        params = compiled.params
        assert params["status"] == "STOPPED"
        assert params["stop_reason"] == "threshold_gone"
        assert params["env_1"] == "test"
        assert params["source_table_1"] == "baas_device"
        assert params["source_id_1"] == 1

    def test_set_status_without_stop_reason_omits_bind(self):
        """GUARD: the legacy no-reason call shape renders NO stop_reason bind.

        Pins that the new column never leaks into the no-reason UPDATE
        params (a bare stop_reason=None values() entry would render a
        stop_reason = NULL bind on both dialects).
        """
        repo, mock_session = _make_repo()

        repo.set_status("test", "baas_device", 1, "STOPPED")

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        params = compiled.params
        assert "stop_reason" not in params


class TestCountActive:
    def test_count_active(self):
        """Test 11: scalar COUNT(*) over ACTIVE env rows."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 42

        result = repo.count_active("test")

        assert result == 42
        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "count(*)" in sql_text
        assert "baas_bot_ttl_renewal_schedule" in sql_text
        assert "baas_bot_ttl_renewal_schedule.env = %s" in sql_text
        assert compiled.params["status_1"] == "ACTIVE"
        assert compiled.params["env_1"] == "test"


class TestFindUnregistered:
    def test_find_unregistered_baas_device(self):
        """Test 12: device-side anti-join returns the four-key dict rows."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 1,
                    "sandbox_id": "sb-new-001",
                    "source_table": "baas_device",
                    "ttl": "2026-08-25T12:00:00",
                }
            )
        ]

        results = repo.find_unregistered("test", "baas_device", 500)

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "LEFT OUTER JOIN baas_bot_ttl_renewal_schedule" in sql_text
        assert "provider_device_id AS sandbox_id" in sql_text
        assert (
            "json_unquote(json_extract(baas_device.provider_device_props, "
            "'$.ttl_expiration_timestamp'))" in sql_text
        )
        # WR-02: dual-key projection — COALESCE prefers the new
        # ttl_expiration_timestamp key and falls back to the legacy
        # integer-ms ttl_expiration_time key for pre-release rows.
        assert "coalesce(" in sql_text
        assert (
            "json_unquote(json_extract(baas_device.provider_device_props, "
            "'$.ttl_expiration_time'))" in sql_text
        )
        assert "baas_device.provider_type = %s" in sql_text
        assert "baas_device.is_deleted = %s" in sql_text
        assert "baas_device.env = %s" in sql_text
        assert "baas_bot_ttl_renewal_schedule.id IS NULL" in sql_text

        params = compiled.params
        assert params["env_1"] == "test" or params["env_2"] == "test"
        assert 500 in params.values()
        assert 0 in params.values()  # is_deleted bound value

        assert len(results) == 1
        assert results[0]["sandbox_id"] == "sb-new-001"
        assert results[0]["source_table"] == "baas_device"
        assert sorted(results[0].keys()) == ["id", "sandbox_id", "source_table", "ttl"]

    def test_find_unregistered_ac_binding(self):
        """Test 13: binding-side anti-join with JSON unquote comparison."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 2,
                    "sandbox_id": "sb-new-002",
                    "source_table": "ac_entity_device_binding",
                    "ttl": "2026-08-25T18:00:00",
                }
            )
        ]

        results = repo.find_unregistered("test", "ac_entity_device_binding", 500)

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "LEFT OUTER JOIN baas_bot_ttl_renewal_schedule" in sql_text
        assert "ac_entity_device_binding" in sql_text
        assert (
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.sandbox_id'))" in sql_text
        )
        assert (
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.ttl_expiration_timestamp'))" in sql_text
        )
        # WR-02: dual-key projection — COALESCE falls back to the legacy
        # integer-ms ttl_expiration_time key for pre-release rows.
        assert "coalesce(" in sql_text
        assert (
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.ttl_expiration_time'))" in sql_text
        )
        assert "ac_entity_device_binding.env = %s" in sql_text
        assert "baas_bot_ttl_renewal_schedule.id IS NULL" in sql_text
        # D-16': production binding table has no is_deleted column
        assert "is_deleted" not in sql_text

        params = compiled.params
        assert params["device_provider_1"] == ["arca", "ARCA"]
        assert "test" in params.values()

        assert len(results) == 1
        assert results[0]["sandbox_id"] == "sb-new-002"
        assert results[0]["source_table"] == "ac_entity_device_binding"

    def test_find_unregistered_device_stale_sandbox_not_suppressed(self):
        """Test 12a: ON equates s.sandbox_id with provider_device_id, so a
        stale ACTIVE row for an OLD sandbox no longer suppresses the hot row."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 1,
                    "sandbox_id": "sb-new-456",
                    "source_table": "baas_device",
                    "ttl": "2026-08-25T12:00:00",
                }
            )
        ]

        results = repo.find_unregistered("test", "baas_device", 500)

        sql_text = str(
            mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        )
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "baas_device.provider_device_id" in sql_text
        )
        assert "baas_bot_ttl_renewal_schedule.id IS NULL" in sql_text

        assert len(results) == 1
        assert results[0]["sandbox_id"] == "sb-new-456"

    def test_find_unregistered_device_matching_sandbox_still_suppressed(self):
        """Test 12b: anti-join stays strict — matching source_id + same
        sandbox suppresses the hot row regardless of cold-row status."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 1,
                    "sandbox_id": "sb-new-456",
                    "source_table": "baas_device",
                    "ttl": "2026-08-25T12:00:00",
                }
            )
        ]

        repo.find_unregistered("test", "baas_device", 500)

        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        assert "baas_bot_ttl_renewal_schedule.source_id = baas_device.id" in sql_text
        # D-85-AJ1: the ON clause renders no cold-table status predicate —
        # any cold row on the same sandbox suppresses the hot row. The one
        # surviving status bind is the hot-table WHERE filter.
        assert "baas_bot_ttl_renewal_schedule.status" not in sql_text
        assert len([k for k in compiled.params if k.startswith("status")]) == 1
        assert "baas_bot_ttl_renewal_schedule.id IS NULL" in sql_text

    def test_find_unregistered_device_stopped_same_sandbox_still_suppressed(self):
        """Test 12c: a STOPPED cold row on the same sandbox still suppresses
        the hot row — no status predicate in either dialect compile."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 1,
                    "sandbox_id": "sb-new-456",
                    "source_table": "baas_device",
                    "ttl": "2026-08-25T12:00:00",
                }
            )
        ]

        results = repo.find_unregistered("test", "baas_device", 500)

        for dialect in (mysql.dialect(), sqlite.dialect()):
            compiled = mock_session.execute.call_args[0][0].compile(dialect=dialect)
            sql_text = str(compiled)
            assert "baas_bot_ttl_renewal_schedule.status" not in sql_text
            assert (
                "baas_bot_ttl_renewal_schedule.sandbox_id = "
                "baas_device.provider_device_id" in sql_text
            )
            assert (
                "baas_bot_ttl_renewal_schedule.source_id = baas_device.id" in sql_text
            )
            assert "baas_bot_ttl_renewal_schedule.id IS NULL" in sql_text
            assert len([k for k in compiled.params if k.startswith("status")]) == 1

        assert len(results) == 1
        assert results[0]["sandbox_id"] == "sb-new-456"

    def test_find_unregistered_binding_stale_sandbox_not_suppressed(self):
        """Test 13a: binding ON equates s.sandbox_id with the device_props
        sandbox JSON, so a stale row for an OLD sandbox does not suppress."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 2,
                    "sandbox_id": "sb-new-789",
                    "source_table": "ac_entity_device_binding",
                    "ttl": "2026-08-25T18:00:00",
                }
            )
        ]

        results = repo.find_unregistered("test", "ac_entity_device_binding", 500)

        sql_text = str(
            mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        )
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.sandbox_id'))" in sql_text
        )

        assert len(results) == 1
        assert results[0]["sandbox_id"] == "sb-new-789"

    def test_find_unregistered_binding_matching_sandbox_still_suppressed(self):
        """Test 13b: binding anti-join stays strict — matching source_id +
        same sandbox suppresses regardless of cold-row status."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 2,
                    "sandbox_id": "sb-new-789",
                    "source_table": "ac_entity_device_binding",
                    "ttl": "2026-08-25T18:00:00",
                }
            )
        ]

        repo.find_unregistered("test", "ac_entity_device_binding", 500)

        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        assert (
            "baas_bot_ttl_renewal_schedule.source_id = "
            "ac_entity_device_binding.id" in sql_text
        )
        # D-85-AJ1: no cold-table status predicate in the ON clause — any
        # cold row on the same sandbox suppresses. The one surviving status
        # bind is the hot-table WHERE filter.
        assert "baas_bot_ttl_renewal_schedule.status" not in sql_text
        assert len([k for k in compiled.params if k.startswith("status")]) == 1
        assert "baas_bot_ttl_renewal_schedule.id IS NULL" in sql_text

    def test_find_unregistered_binding_stopped_same_sandbox_still_suppressed(self):
        """Test 13c: a STOPPED cold row on the same binding sandbox still
        suppresses the hot row — no status predicate in either dialect."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value = [
            _make_row(
                {
                    "id": 2,
                    "sandbox_id": "sb-new-789",
                    "source_table": "ac_entity_device_binding",
                    "ttl": "2026-08-25T18:00:00",
                }
            )
        ]

        results = repo.find_unregistered("test", "ac_entity_device_binding", 500)

        mysql_compiled = mock_session.execute.call_args[0][0].compile(
            dialect=mysql.dialect()
        )
        mysql_sql = str(mysql_compiled)
        assert "baas_bot_ttl_renewal_schedule.status" not in mysql_sql
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.sandbox_id'))" in mysql_sql
        )
        assert (
            "baas_bot_ttl_renewal_schedule.source_id = "
            "ac_entity_device_binding.id" in mysql_sql
        )
        assert "baas_bot_ttl_renewal_schedule.id IS NULL" in mysql_sql
        assert len([k for k in mysql_compiled.params if k.startswith("status")]) == 1

        # D-05' idiom: flip the session dialect to sqlite before REBUILDING
        # the statement — the sqlite branch skips the json_unquote wrapper.
        mock_session.bind.dialect.name = _SQLITE
        repo.find_unregistered("test", "ac_entity_device_binding", 500)

        sqlite_compiled = mock_session.execute.call_args[0][0].compile(
            dialect=sqlite.dialect()
        )
        sqlite_sql = str(sqlite_compiled)
        assert "json_unquote" not in sqlite_sql
        assert "baas_bot_ttl_renewal_schedule.status" not in sqlite_sql
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "json_extract(ac_entity_device_binding.device_props, '$.sandbox_id')"
            in sqlite_sql
        )
        assert (
            "baas_bot_ttl_renewal_schedule.source_id = "
            "ac_entity_device_binding.id" in sqlite_sql
        )
        assert "baas_bot_ttl_renewal_schedule.id IS NULL" in sqlite_sql
        assert len([k for k in sqlite_compiled.params if k.startswith("status")]) == 1

        assert len(results) == 1
        assert results[0]["sandbox_id"] == "sb-new-789"

    def test_find_unregistered_sqlite_dialect_skips_unquote(self):
        """D-05': sqlite returns bare json_extract text — no unquote wrapper."""
        repo, mock_session = _make_repo()
        mock_session.bind.dialect.name = _SQLITE
        mock_session.execute.return_value = []

        repo.find_unregistered("test", "ac_entity_device_binding", 500)

        sql_text = str(
            mock_session.execute.call_args[0][0].compile(dialect=sqlite.dialect())
        )
        assert (
            "json_extract(ac_entity_device_binding.device_props, '$.sandbox_id')"
            in sql_text
        )
        assert (
            "json_extract(ac_entity_device_binding.device_props, "
            "'$.ttl_expiration_timestamp')" in sql_text
        )
        assert "json_unquote" not in sql_text
        assert "is_deleted" not in sql_text

    def test_find_unregistered_unsupported_side_raises(self):
        """Whitelist guard: unsupported side raises ValueError."""
        repo, mock_session = _make_repo()

        with pytest.raises(ValueError, match="Unsupported side"):
            repo.find_unregistered("test", "bogus", 500)


class TestCountHotArcaDevices:
    def test_count_hot_arca_devices_filters_by_env(self):
        """CR-01: env-scoped count on baas_device with is_deleted guard."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 42

        result = repo.count_hot_arca_devices("test")

        assert result == 42
        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "count(*)" in sql_text
        assert "baas_device" in sql_text
        assert "baas_device.provider_type = %s" in sql_text
        assert "baas_device.is_deleted = %s" in sql_text
        assert "baas_device.env = %s" in sql_text
        assert "baas_device.provider_device_id IS NOT NULL" in sql_text
        assert compiled.params["env_1"] == "test"


class TestCountHotArcaBindings:
    def test_count_hot_arca_bindings_filters_by_env(self):
        """CR-01 + D-16': env-scoped count; no is_deleted on the binding side."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 7

        result = repo.count_hot_arca_bindings("test")

        assert result == 7
        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)

        assert "count(*)" in sql_text
        assert "ac_entity_device_binding" in sql_text
        assert (
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.sandbox_id')) IS NOT NULL" in sql_text
        )
        assert "is_deleted" not in sql_text

        params = compiled.params
        assert params["env_1"] == "test"
        assert params["device_provider_1"] == ["arca", "ARCA"]


class TestCountHotCovered:
    """86-02 (WR-01): the covered count INNER JOINs the cold table — ANY
    cold status — over the established hot-side ACTIVE ARCA filters."""

    def test_count_hot_covered_device_side_inner_join(self):
        """Device side: cold ON (source_table, source_id, env, sandbox_id),
        no cold-status predicate (any status covers), hot filters replicated."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 42

        result = repo.count_hot_covered("test")

        assert result == 84  # device + binding side, 42 each
        assert mock_session.execute.call_count == 2
        compiled = mock_session.execute.call_args_list[0][0][0].compile(
            dialect=mysql.dialect()
        )
        sql_text = str(compiled)

        assert "count(*)" in sql_text
        assert "INNER JOIN baas_bot_ttl_renewal_schedule ON" in sql_text
        assert "baas_bot_ttl_renewal_schedule.source_table = %s" in sql_text
        assert "baas_bot_ttl_renewal_schedule.source_id = baas_device.id" in sql_text
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "baas_device.provider_device_id" in sql_text
        )
        assert "baas_device.provider_type = %s" in sql_text
        assert "baas_device.status = %s" in sql_text
        assert "baas_device.is_deleted = %s" in sql_text
        assert "baas_device.provider_device_id IS NOT NULL" in sql_text
        # Any-status coverage: NO cold-table status predicate in the join.
        assert "baas_bot_ttl_renewal_schedule.status" not in sql_text
        assert "test" in compiled.params.values()

    def test_count_hot_covered_binding_side_json_equality(self):
        """Binding side: JSON sandbox equality in the ON, no is_deleted
        anywhere (D-16'); the sqlite dialect skips the unquote wrapper."""
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 7

        repo.count_hot_covered("test")

        mysql_compiled = mock_session.execute.call_args_list[1][0][0].compile(
            dialect=mysql.dialect()
        )
        mysql_sql = str(mysql_compiled)
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.sandbox_id'))" in mysql_sql
        )
        assert "ac_entity_device_binding.status = %s" in mysql_sql
        assert "is_deleted" not in mysql_sql

        # D-05' flip: rebuild under the sqlite dialect before compiling.
        mock_session.bind.dialect.name = _SQLITE
        repo.count_hot_covered("test")

        sqlite_compiled = mock_session.execute.call_args_list[3][0][0].compile(
            dialect=sqlite.dialect()
        )
        sqlite_sql = str(sqlite_compiled)
        assert "json_unquote" not in sqlite_sql
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "json_extract(ac_entity_device_binding.device_props, '$.sandbox_id')"
            in sqlite_sql
        )


class TestCountSuppressedTerminal:
    """86-02 (R3): the suppressed variant adds a cold-table status bind
    ("STOPPED") to the covered count — hot-ACTIVE x cold-STOPPED only."""

    def test_count_suppressed_terminal_device_side_stopped_status_bind(self):
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 5

        result = repo.count_suppressed_terminal("test")

        assert result == 10  # device + binding side, 5 each
        assert mock_session.execute.call_count == 2
        compiled = mock_session.execute.call_args_list[0][0][0].compile(
            dialect=mysql.dialect()
        )
        sql_text = str(compiled)
        assert "baas_bot_ttl_renewal_schedule.status = %s" in sql_text
        status_binds = [v for k, v in compiled.params.items() if k.startswith("status")]
        assert sorted(status_binds) == ["ACTIVE", "STOPPED"]

    def test_count_suppressed_terminal_binding_side_both_dialects(self):
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 0

        repo.count_suppressed_terminal("test")

        mysql_compiled = mock_session.execute.call_args_list[1][0][0].compile(
            dialect=mysql.dialect()
        )
        mysql_sql = str(mysql_compiled)
        assert (
            "baas_bot_ttl_renewal_schedule.sandbox_id = "
            "json_unquote(json_extract(ac_entity_device_binding.device_props, "
            "'$.sandbox_id'))" in mysql_sql
        )
        assert "baas_bot_ttl_renewal_schedule.status = %s" in mysql_sql
        status_binds = [
            v for k, v in mysql_compiled.params.items() if k.startswith("status")
        ]
        assert sorted(status_binds) == ["ACTIVE", "STOPPED"]

        mock_session.bind.dialect.name = _SQLITE
        repo.count_suppressed_terminal("test")
        sqlite_compiled = mock_session.execute.call_args_list[3][0][0].compile(
            dialect=sqlite.dialect()
        )
        sqlite_sql = str(sqlite_compiled)
        assert "json_unquote" not in sqlite_sql
        status_binds_sqlite = [
            v for k, v in sqlite_compiled.params.items() if k.startswith("status")
        ]
        assert sorted(status_binds_sqlite) == ["ACTIVE", "STOPPED"]


class TestHotRowExists:
    """86-02 (WR-02): the orphan-recheck existence probe mirrors
    list_due_for_renewal's JOIN conditions per side (D-16' decree)."""

    def test_hot_row_exists_device_side_compiles_id_env_is_deleted(self):
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = 42

        assert repo.hot_row_exists("test", "baas_device", 42) is True

        mock_session.execute.assert_called_once()
        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        assert "baas_device.id = %s" in sql_text
        assert "baas_device.env = %s" in sql_text
        assert "baas_device.is_deleted = %s" in sql_text
        assert "test" in compiled.params.values()
        assert 42 in compiled.params.values()
        assert 0 in compiled.params.values()  # is_deleted bound value

    def test_hot_row_exists_binding_side_without_is_deleted(self):
        repo, mock_session = _make_repo()
        mock_session.execute.return_value.scalar.return_value = None

        assert repo.hot_row_exists("test", "ac_entity_device_binding", 7) is False

        compiled = mock_session.execute.call_args[0][0].compile(dialect=mysql.dialect())
        sql_text = str(compiled)
        assert "ac_entity_device_binding.id = %s" in sql_text
        assert "ac_entity_device_binding.env = %s" in sql_text
        assert "is_deleted" not in sql_text

    def test_hot_row_exists_unsupported_source_table_raises(self):
        repo, mock_session = _make_repo()

        with pytest.raises(ValueError, match="Unsupported source_table"):
            repo.hot_row_exists("test", "bogus", 1)
