"""Environment-scoped durable artifacts obey TC-style bounded replay contracts."""

import sqlite3

import pytest
from search_core.plugin_api.index_documents import IndexDocument
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.sql_index_documents import sqlite_index_documents


def write(store, key, revision=1, deleted=False):
    with sqlite3.connect(store.path) as conn:
        result = store.save_in_transaction(
            conn.cursor(),
            IndexDocument(
                VectorPoint(id=key, vector=[1.0, 0.0], payload={"content": "body"}),
                revision,
                {"questions": ["one"]},
                deleted,
            ),
        )
        conn.commit()
        return result


def test_environment_and_model_generation_isolate_every_read_and_change(tmp_path):
    path = str(tmp_path / "source.db")
    pre = sqlite_index_documents(path, environment="prepub", index_key="model-one")
    prod = sqlite_index_documents(path, environment="prod", index_key="model-one")
    next_model = sqlite_index_documents(
        path, environment="prepub", index_key="model-two"
    )
    write(pre, "same")
    assert pre.load_by_id("same").source_revision == 1
    assert prod.load_by_id("same") is None and next_model.load_by_id("same") is None
    assert list(prod.iter_batches(10)) == []
    assert prod.get_last_modified_time() == 0
    assert list(prod.iter_change_batches_since(0, 10)) == []
    write(prod, "same", 2)
    write(pre, "two")
    assert [len(p) for p in pre.iter_batches(1)] == [1, 1]
    changes = list(pre.iter_change_batches_since(0, 1))
    assert len(changes) == 2 and all(len(p.upserts) == 1 for p in changes)
    write(pre, "same", 3, deleted=True)
    assert pre.load_by_id("same").is_deleted
    assert not prod.load_by_id("same").is_deleted
    assert not write(pre, "same", 2)
    assert list(pre.iter_versions(1)) == [[("same", 2)], [("two", 1)]]
    assert list(prod.iter_versions(10)) == [[("same", 1)]]
    assert list(next_model.iter_versions(10)) == []
    with pytest.raises(ValueError):
        sqlite_index_documents(path, environment="", index_key="x")


def test_lightweight_changes_and_source_lookup_are_scoped(tmp_path):
    path = str(tmp_path / "source.db")
    store = sqlite_index_documents(path, environment="local", index_key="one")
    with sqlite3.connect(path) as conn:
        for key in ("a", "b"):
            store.save_in_transaction(
                conn.cursor(),
                IndexDocument(
                    VectorPoint(id=key, vector=[1.0], payload={"source_id": "source"}),
                    1,
                    is_deleted=key == "b",
                ),
            )
    pages = list(store.iter_change_versions_since(0, 1))
    assert len(pages) == 2
    assert {v.id for page in pages for v in page} == {"a", "b"}
    assert all(
        v.source_id == "source" and not hasattr(v, "point")
        for page in pages
        for v in page
    )
    assert {d.id for d in store.load_by_source("source")} == {"a", "b"}
    other = sqlite_index_documents(path, environment="prod", index_key="one")
    assert other.load_by_source("source") == []
    assert list(other.iter_version_batches(10)) == []


def test_local_schema_upgrade_preserves_legacy_source_ids(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE search_index_documents (
            environment TEXT,index_key TEXT,id TEXT,vector TEXT,payload TEXT,extra TEXT,
            source_revision INTEGER,version INTEGER,is_deleted INTEGER,gmt_modify REAL,
            PRIMARY KEY(environment,index_key,id))""")
        conn.execute("""INSERT INTO search_index_documents VALUES
            ('local','one','doc','[1]','{"source_id":"agent"}','{}',1,1,0,100)""")
    store = sqlite_index_documents(path, environment="local", index_key="one")
    assert [d.id for d in store.load_by_source("agent")] == ["doc"]
    assert next(iter(store.iter_version_batches(10)))[0].source_id == "agent"


def test_local_migration_failure_rolls_back_schema_for_safe_retry(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE search_index_documents (
            environment TEXT,index_key TEXT,id TEXT,vector TEXT,payload TEXT,extra TEXT,
            source_revision INTEGER,version INTEGER,is_deleted INTEGER,gmt_modify REAL,
            PRIMARY KEY(environment,index_key,id))""")
        conn.execute("""INSERT INTO search_index_documents VALUES
            ('local','one','doc','[1]','broken-json','{}',1,1,0,100)""")
    with pytest.raises(ValueError):
        sqlite_index_documents(path, environment="local", index_key="one")
    with sqlite3.connect(path) as conn:
        assert "source_id" not in {
            r[1] for r in conn.execute("PRAGMA table_info(search_index_documents)")
        }
        conn.execute("UPDATE search_index_documents SET payload='{}'")
    store = sqlite_index_documents(path, environment="local", index_key="one")
    assert store.load_by_source("doc")[0].id == "doc"
