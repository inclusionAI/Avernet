"""Deletion belongs to operational columns and retires every index generation."""

import json
import sqlite3

import pytest
from search_core.catalog.updates import CardUpdates
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import DocumentText
from search_core.providers.sql_catalog import sqlite_catalog_repository
from test_catalog_startup import Index, Model


class Converter:
    version = "test"

    def convert(self, card):
        return CardProjection(
            [
                DocumentText("main", card.source_card["name"]),
                DocumentText("detail", "extra document"),
            ],
            {},
        )


def runtime(repo, key):
    return CardUpdates(
        repo,
        Model(),
        Index(),
        embedding_key="model/2",
        instance_id=key,
        index_key=key,
        document_converter=Converter(),
    )


def test_delete_preserves_card_json_and_retires_all_generations_and_documents(tmp_path):
    path = str(tmp_path / "cards.db")
    repo = sqlite_catalog_repository(path, environment="pre")
    prod = sqlite_catalog_repository(path, environment="prod")
    business = {"name": "invoice", "status": "active", "deleted": False}
    repo.upsert("a", business, None, {})
    prod.upsert("a", business, None, {})
    workers = [runtime(repo, "v1"), runtime(repo, "v2"), runtime(prod, "v1")]
    for worker in workers:
        worker.run_once()
        assert len(worker.index.points) == 2
    with repo.connect() as db:
        before = db.execute(
            "SELECT card_json FROM bcsfuse_agent_cards WHERE environment='pre'"
        ).fetchone()[0]
    calls = [w.embedding.calls for w in workers]
    repo.remove("a")
    with repo.connect() as db:
        after, deleted = db.execute(
            "SELECT card_json,is_deleted FROM bcsfuse_agent_cards WHERE environment='pre'"
        ).fetchone()
        docs = db.execute(
            "SELECT index_key,is_deleted,source_revision,version FROM search_index_documents WHERE environment='pre'"
        ).fetchall()
    assert before == after
    assert deleted == 1
    assert "deleted" not in json.loads(after)
    assert json.loads(after)["source_card"] == business
    assert len(docs) == 4
    assert {row[0] for row in docs} == {"v1", "v2"}
    assert all(row[1:] == (1, 2, 2) for row in docs)
    repo.remove("a")
    assert repo.read_card("a").revision == 2
    for worker, before_calls in zip(workers[:2], calls):
        worker.run_once()
        assert not worker.index.points
        assert worker.embedding.calls == before_calls
    # A version that was offline at deletion must also restore as empty.
    restarted = runtime(repo, "v1")
    restarted.run_once()
    assert restarted.ready and not restarted.index.points
    assert restarted.embedding.calls == 0
    workers[2].run_once()
    assert len(workers[2].index.points) == 2
    assert not prod.read_card("a").card.deleted
    # Recreation is a new revision, not resurrection of any old Doc group.
    repo.upsert("a", {"name": "replacement"}, None, {})
    assert not repo.read_card("a").card.deleted
    assert repo.read_card("a").revision == 3
    for worker in workers[:2]:
        worker.run_once()
        assert len(worker.index.points) == 2
        assert worker.status("a")["local_applied_revision"] == 3


def test_doc_deletion_failure_rolls_back_card_even_with_autocommit(tmp_path):
    path = str(tmp_path / "cards.db")
    repo = sqlite_catalog_repository(path)
    repo.upsert("a", {"name": "invoice"}, None, {})
    runtime(repo, "v1").run_once()
    repo.connect = lambda: sqlite3.connect(path, isolation_level=None)
    with repo.connect() as db:
        db.execute("""CREATE TRIGGER reject_deletion BEFORE UPDATE OF is_deleted
            ON search_index_documents BEGIN SELECT RAISE(ABORT, 'delete failed'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="delete failed"):
        repo.remove("a")
    assert not repo.read_card("a").card.deleted
    assert repo.read_card("a").revision == 1
    assert not repo.index_documents("v1").load_by_id("a").is_deleted


def test_delete_fences_inflight_publication_in_another_generation(tmp_path):
    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repo.upsert("a", {"name": "invoice"}, None, {})
    worker = runtime(repo, "v2")
    row = repo.read_card("a")
    assert repo.claim_preparation("a", row.revision, "v2", "old-owner", 60)
    documents = worker._prepare_documents(row, [])
    repo.remove("a")
    assert not repo.publish_documents(documents, "v2", "old-owner")
    assert repo.index_documents("v2").load_by_id("a") is None
