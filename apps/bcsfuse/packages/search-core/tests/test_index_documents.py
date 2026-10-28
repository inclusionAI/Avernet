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
