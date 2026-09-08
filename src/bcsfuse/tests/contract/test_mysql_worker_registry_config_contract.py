"""MySQL worker config contract used by the fusion eligibility check."""

from unittest.mock import MagicMock, patch

import pytest
from mysql.connector import Error

from src.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore


@pytest.fixture
def mysql_store():
    pool = MagicMock()
    with patch.object(MySQLWorkerRegistryStore, "_ensure_schema"):
        store = MySQLWorkerRegistryStore(pool)
    return store, pool, pool.get_connection.return_value.cursor.return_value


def test_batch_configs_preserves_flags_and_reports_missing(mysql_store):
    store, pool, cursor = mysql_store
    cursor.fetchall.return_value = [
        {"id": "enabled:owner", "config": '{"fusion_enable": true}'},
        {"id": "disabled", "config": {"fusion_enable": False}},
        {"id": "default", "config": None},
    ]
    ids = ["enabled:owner", "missing", "disabled", "default", "enabled:owner"]

    configs, missing = store.batch_get_configs(ids)

    assert {key: value.fusion_enable for key, value in configs.items()} == {
        "enabled:owner": True, "disabled": False, "default": False,
    }
    assert missing == ["missing"]
    sql, params = cursor.execute.call_args.args
    assert "SELECT id, config FROM bcsfuse_workers" in sql
    assert sql.count("%s") == len(ids)
    assert list(params) == ids
    cursor.execute.assert_called_once()
    cursor.close.assert_called_once()
    pool.get_connection.return_value.close.assert_called_once()


def test_batch_configs_empty_skips_database(mysql_store):
    store, pool, _ = mysql_store
    assert store.batch_get_configs([]) == ({}, [])
    pool.get_connection.assert_not_called()


def test_batch_configs_all_missing(mysql_store):
    store, _, cursor = mysql_store
    cursor.fetchall.return_value = []
    assert store.batch_get_configs(["a", "b"]) == ({}, ["a", "b"])


@pytest.mark.parametrize("failure_stage", ["execute", "fetchall"])
def test_batch_configs_propagates_database_errors_and_closes(mysql_store, failure_stage):
    store, pool, cursor = mysql_store
    getattr(cursor, failure_stage).side_effect = Error("database unavailable")
    with pytest.raises(Error, match="database unavailable"):
        store.batch_get_configs(["enabled"])
    cursor.close.assert_called_once()
    pool.get_connection.return_value.close.assert_called_once()
