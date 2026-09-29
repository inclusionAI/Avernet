"""MySQL worker config contract used by the fusion eligibility check."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from mysql.connector import Error

from src.application.ports.worker_registry_store import WorkerRegistryStore
from src.domain.exceptions import WorkerNotFoundException
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
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


def test_mysql_store_implements_public_worker_registry_port(mysql_store):
    store, _, _ = mysql_store

    assert isinstance(store, WorkerRegistryStore)


def test_get_by_ids_returns_domain_workers_and_reports_no_phantom_rows(mysql_store):
    store, _, cursor = mysql_store
    cursor.fetchall.return_value = [
        {
            "id": "worker-a",
            "type": "bot",
            "identity_name": "Worker A",
            "identity_handle": "@worker-a",
            "identity_description": None,
            "responsibilities": '["testing"]',
            "domains": "[]",
            "capabilities": '[{"name": "testing", "level": "expert"}]',
            "skills": "[]",
            "resources": "[]",
            "state_availability": "public",
            "state_trust_level": "trusted",
            "state_runtime_state": "offline",
            "lifecycle_state": "active",
            "source_type": "api",
            "source_ref": None,
            "external_id": None,
            "active_profile_key": None,
            "config": None,
            "version": 1,
            "gmt_create": datetime(2026, 1, 1),
            "gmt_modify": datetime(2026, 1, 1),
            "created_by": None,
            "updated_by": None,
        }
    ]

    workers = store.get_by_ids(["worker-a", "worker-missing"])

    assert list(workers) == ["worker-a"]
    assert workers["worker-a"].identity.name == "Worker A"
    sql, params = cursor.execute.call_args.args
    assert "SELECT * FROM bcsfuse_workers" in sql
    assert list(params) == ["worker-a", "worker-missing"]


def test_get_by_ids_empty_skips_database(mysql_store):
    store, pool, _ = mysql_store

    assert store.get_by_ids([]) == {}
    pool.get_connection.assert_not_called()


def test_delete_rolls_back_mysql_cascade_failure(mysql_store):
    store, pool, cursor = mysql_store
    connection = pool.get_connection.return_value
    connection.autocommit = True
    cursor.execute.side_effect = [None, Error("runtime state delete failed")]

    with patch.object(store, "exists", return_value=True):
        with pytest.raises(Error, match="runtime state delete failed"):
            store.delete("worker-a")

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    assert connection.autocommit is True


def test_delete_commits_mysql_cascade_as_one_transaction(mysql_store):
    store, pool, _ = mysql_store
    connection = pool.get_connection.return_value
    connection.autocommit = True

    with patch.object(store, "exists", return_value=True):
        assert store.delete("worker-a") is True

    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    assert connection.autocommit is True


def test_update_lifecycle_state_raises_for_missing_worker(mysql_store):
    store, _, _ = mysql_store

    with patch.object(store, "get_by_id", return_value=None):
        with pytest.raises(WorkerNotFoundException):
            store.update_lifecycle_state(
                "worker-missing",
                WorkerLifecycleState.ACTIVE,
                version=1,
            )


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
