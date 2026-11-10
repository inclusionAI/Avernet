"""Operator status exposes durable Docs and actual index points separately."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from search_core.adapters.http.catalog_cards import create_card_router
from search_core.api.models import VerifiedIdentity
from search_core.catalog.updates import CardUpdates
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import DocumentText
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.qdrant_index import QdrantRetrievalIndex
from search_core.providers.sql_catalog import sqlite_catalog_repository

PATH = "/api/v2/agent-cards/demo/index-status"


@pytest.fixture
def world(tmp_path):
    class Model:
        dimension = 16
        calls = 0

        def embed(self, text):
            self.calls += 1
            return [0.25] * 16

    class Converter:
        version = "test"

        def convert(self, card):
            return CardProjection(
                [
                    DocumentText("main", "invoice search"),
                    DocumentText("question", "find bills"),
                ],
                {"region": ("cn",)},
            )

    async def auth():
        return VerifiedIdentity("operator")

    repo = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    index = QdrantRetrievalIndex(path=str(tmp_path / "qdrant"), dimension=16)
    model = Model()
    updates = CardUpdates(
        repo,
        model,
        index,
        embedding_key="model/16",
        instance_id="node",
        index_key="current",
        filter_fields=frozenset({"region"}),
        document_converter=Converter(),
    )
    repo.upsert("demo", {"name": "billing"}, None, {})
    app = FastAPI()
    app.include_router(create_card_router(updates, auth))
    with TestClient(app) as client:
        yield repo, index, model, updates, client
    index.close()


def test_status_shows_all_documents_and_real_index_content_without_embedding(world):
    _, _, model, updates, client = world
    empty = client.get(PATH).json()
    assert empty["documents"] == []
    assert empty["qdrant"]["status"] == "ok"
    assert empty["qdrant"]["vector_documents"] == []
    updates.run_once()
    before = model.calls
    response = client.get(PATH)
    assert response.status_code == 200
    body = response.json()
    assert len(body["documents"]) == 2
    assert {d["search_text"] for d in body["documents"]} == {
        "invoice search",
        "find bills",
    }
    for doc in body["documents"]:
        assert doc["filters"] == {"region": ["cn"]}
        assert doc["vector"] == {"dimension": 16, "preview": [0.25] * 8}
    assert body["qdrant"]["status"] == "ok"
    for collection in ("vector_documents", "keyword_documents"):
        assert len(body["qdrant"][collection]) == 2
        assert all(d["index_key"] == "current" for d in body["qdrant"][collection])
    assert model.calls == before
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert "documents" in schema["CardStatus"]["properties"]


def test_status_exposes_drift_even_if_last_applied_versions_match(world):
    _, index, _, updates, client = world
    updates.run_once()
    index.upsert(
        [
            VectorPoint(
                id="demo",
                vector=[1.0] + [0.0] * 15,
                payload={
                    "source_id": "demo",
                    "search_text": "wrong text",
                    "region": ["us"],
                    "index_key": "old",
                },
            )
        ]
    )
    body = client.get(PATH).json()
    assert body["card_version"] == body["indexed_card_version"] == 1
    assert body["doc_version"] == body["indexed_doc_version"] == 1
    actual = next(d for d in body["qdrant"]["vector_documents"] if d["id"] == "demo")
    assert actual["search_text"] == "wrong text"
    assert actual["index_key"] == "old"
    assert actual["filters"] == {"region": ["us"]}
    assert actual["vector"]["preview"] == [1.0] + [0.0] * 7
    assert body["documents"][0]["search_text"] == "invoice search"
    index.delete(["demo"])
    body = client.get(PATH).json()
    assert not any(d["id"] == "demo" for d in body["qdrant"]["vector_documents"])
    assert any(d["id"] == "demo" for d in body["documents"])


def test_index_read_failure_does_not_claim_empty_or_hide_ob_state(world, monkeypatch):
    _, index, _, updates, client = world
    updates.run_once()

    def fail(source_id):
        raise OSError("private connection detail")

    monkeypatch.setattr(index, "read_source_points", fail)
    response = client.get(PATH)
    assert response.status_code == 200
    body = response.json()
    assert len(body["documents"]) == 2
    assert body["qdrant"]["status"] == "unavailable"
    assert body["qdrant"]["error"] == "QDRANT_READ_FAILED"
    assert "private connection detail" not in response.text


def test_deleted_card_keeps_doc_tombstones_but_has_no_qdrant_points(world):
    repo, _, _, updates, client = world
    updates.run_once()
    repo.remove("demo")
    updates.run_once()
    body = client.get(PATH).json()
    assert body["deleted"]
    assert all(d["deleted"] for d in body["documents"])
    assert body["qdrant"]["status"] == "ok"
    assert body["qdrant"]["vector_documents"] == []
    assert body["qdrant"]["keyword_documents"] == []
