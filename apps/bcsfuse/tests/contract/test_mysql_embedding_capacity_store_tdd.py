"""TDD for the shared MySQL embedding-capacity window."""

from __future__ import annotations

from unittest.mock import MagicMock

from mysql.connector import Error
import pytest

from app.infra.public.stores.mysql_embedding_capacity_store import (
    MySQLEmbeddingCapacityStore,
)


def make_store() -> tuple[
    MySQLEmbeddingCapacityStore, MagicMock, MagicMock
]:
    pool = MagicMock()
    connection = pool.get_connection.return_value
    store = MySQLEmbeddingCapacityStore(pool)
    store._schema_ready = True
    return store, connection, connection.cursor.return_value


def test_mysql_schema_is_created_before_shared_capacity_actions():
    store, connection, cursor = make_store()
    store._schema_ready = False

    store.observe("shared-model")

    assert "bcsfuse_embedding_capacity_states" in cursor.execute.call_args_list[0].args[0]
    assert "bcsfuse_embedding_call_windows" in cursor.execute.call_args_list[1].args[0]
    connection.commit.assert_called_once()


def test_mysql_capacity_reserve_uses_for_update_and_commit():
    store, _, cursor = make_store()
    cursor.fetchone.return_value = None

    allowed, retry_after, reason = store.reserve(
        "shared-model",
        now_epoch_seconds=100.0,
        max_calls=1,
        period_seconds=60.0,
    )

    assert allowed is True
    assert retry_after == 0.0
    assert reason == "available"
    select_sql = cursor.execute.call_args_list[0].args[0]
    assert "FOR UPDATE" in select_sql
    assert "INSERT INTO bcsfuse_embedding_call_windows" in cursor.execute.call_args_list[-1].args[0]


def test_mysql_capacity_reserve_rejects_after_call_limit_without_update():
    store, _, cursor = make_store()
    cursor.fetchone.return_value = (100.0, 1)

    allowed, retry_after, reason = store.reserve(
        "shared-model",
        now_epoch_seconds=110.0,
        max_calls=1,
        period_seconds=60.0,
    )

    assert allowed is False
    assert retry_after == pytest.approx(50.0)
    assert reason == "embedding_rate_limit_admission_rejected"
    sql = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
    assert "UPDATE bcsfuse_embedding_call_windows" not in sql


def test_mysql_capacity_reserve_resets_an_expired_window():
    store, _, cursor = make_store()
    cursor.fetchone.return_value = (100.0, 1)

    allowed, _, _ = store.reserve(
        "shared-model",
        now_epoch_seconds=160.0,
        max_calls=1,
        period_seconds=60.0,
    )

    assert allowed is True
    assert "UPDATE bcsfuse_embedding_call_windows" in cursor.execute.call_args_list[-1].args[0]


def test_mysql_capacity_reserve_rolls_back_on_database_error():
    store, connection, cursor = make_store()
    cursor.execute.side_effect = Error("connection lost")

    with pytest.raises(RuntimeError, match="reserve failed"):
        store.reserve(
            "shared-model",
            now_epoch_seconds=100.0,
            max_calls=1,
            period_seconds=60.0,
        )

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


def test_mysql_exhaustion_uses_upsert_and_greatest_expiry():
    store, _, cursor = make_store()
    cursor.fetchone.return_value = ("embedding_quota_exhausted", 130.0)

    reason, expires_at = store.mark_exhausted(
        "shared-model",
        blocked_until_epoch_seconds=130.0,
        reason_code="embedding_quota_exhausted",
    )

    assert reason == "embedding_quota_exhausted"
    assert expires_at == pytest.approx(130.0)
    assert "ON DUPLICATE KEY UPDATE" in cursor.execute.call_args_list[0].args[0]
    assert "GREATEST(expires_at, VALUES(expires_at))" in cursor.execute.call_args_list[0].args[0]
