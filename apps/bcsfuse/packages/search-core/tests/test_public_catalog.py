"""Real SQLite/Qdrant Local acceptance, with no internal application imports."""

import importlib.util
import time

from fastapi.testclient import TestClient


def local_factory():
    assert importlib.util.find_spec("search_core.bootstrap"), (
        "public standalone startup is required"
    )
    from search_core.bootstrap.local import create_local_app

    return create_local_app


def applied(client, revision, deleted=False):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get("/api/v2/agent-cards/demo/index-status")
        if response.status_code == 200:
            status = response.json()
            if (
                status["indexed_card_version"] == revision
                and status["deleted"] == deleted
            ):
                return status
        time.sleep(0.05)
    raise AssertionError(
        f"Card revision {revision} did not become searchable: {response.text}"
    )


def search(client, query):
    result = client.post("/api/v2/search", json={"query": query, "limit": 5})
    assert result.status_code == 200, result.text
    return result.json()


def test_public_write_search_update_delete_and_restart(tmp_path):
    create = local_factory()
    with TestClient(
        create(tmp_path, poll_seconds=0.05),
        client=("127.0.0.1", 50000),
        base_url="http://127.0.0.1",
    ) as client:
        saved = client.put(
            "/api/v2/agent-cards/demo",
            json={"card": {"name": "Nutrition", "description": None}},
        )
        assert saved.status_code == 202, saved.text
        applied(client, saved.json()["card_version"])
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert "index_counts" not in ready.json()
        counted = client.get("/ready?include_counts=true")
        assert counted.status_code == 200, counted.text
        assert counted.json() == {
            **ready.json(),
            "index_counts": {
                "vector_points": 1,
                "keyword_points": 1,
                "vector_matching_points": 1,
                "keyword_matching_points": 1,
                "vector_mismatched_points": 0,
                "keyword_mismatched_points": 0,
            },
        }
        assert search(client, "nutrition")["items"][0]["id"] == "demo"
        assert search(client, "nutrition")["items"][0]["summary"] == ""
        changed = client.put(
            "/api/v2/agent-cards/demo",
            json={
                "card": {"name": "Travel", "description": "railway journey planning"}
            },
        )
        applied(client, changed.json()["card_version"])
        assert search(client, "railway")["items"][0]["name"] == "Travel"
    # Restore durable artifacts, then delete through the same public endpoint.
    with TestClient(
        create(tmp_path, poll_seconds=0.05),
        client=("127.0.0.1", 50000),
        base_url="http://127.0.0.1",
    ) as client:
        applied(client, changed.json()["card_version"])
        assert search(client, "railway")["items"][0]["id"] == "demo"
        assert client.delete("/api/v2/agent-cards/demo").status_code == 202
        state = client.get("/api/v2/agent-cards/demo/index-status").json()
        applied(client, state["card_version"], deleted=True)
        assert search(client, "railway")["items"] == []
        assert client.get("/ready?include_counts=true").json()["index_counts"] == {
            "vector_points": 0,
            "keyword_points": 0,
            "vector_matching_points": 0,
            "keyword_matching_points": 0,
            "vector_mismatched_points": 0,
            "keyword_mismatched_points": 0,
        }


def test_business_converter_multi_doc_update_and_restore_without_models(tmp_path):
    from search_core.catalog.cards import CardSnapshot
    from search_core.catalog.updates import CardUpdates, configuration_index_key
    from search_core.plugin_api.card_documents import CardProjection
    from search_core.plugin_api.index_documents import DocumentText
    from search_core.providers.demo_embedding import DemoEmbedding
    from search_core.providers.qdrant_index import QdrantRetrievalIndex
    from search_core.providers.sql_catalog import sqlite_catalog_repository

    class Converter:
        version = "business-v1"
        calls = 0

        def convert(self, card):
            self.calls += 1
            docs = [DocumentText("main", card.source_card["name"])]
            if card.source_card.get("question"):
                docs.append(DocumentText("question", card.source_card["question"]))
            return CardProjection(docs, {})

    class Model(DemoEmbedding):
        calls = 0

        def embed(self, text):
            self.calls += 1
            return super().embed(text)

    repository = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    converter, model = Converter(), Model()
    card = CardSnapshot(
        agent_id="demo",
        source_version="1",
        source_card={"name": "nutrition", "question": "food labels"},
    )
    repository.save(card, 0)
    for instance in ("first", "restored"):
        index = QdrantRetrievalIndex(
            path=str(tmp_path / instance), dimension=model.dimension
        )
        try:
            updates = CardUpdates(
                repository,
                model,
                index,
                embedding_key="demo/256",
                instance_id=instance,
                document_converter=converter,
            )
            assert updates.index_key == configuration_index_key(
                "demo/256", frozenset(), converter
            )
            updates.run_once()
            assert len(index.get_vector_ids()) == 2
            assert converter.calls == 1 and model.calls == 2
            if instance == "restored":
                repository.save(
                    card.model_copy(
                        update={
                            "source_version": "2",
                            "source_card": {"name": "railway"},
                        }
                    ),
                    1,
                )
                updates.run_once()
                assert len(index.get_vector_ids()) == 1
                assert converter.calls == 2 and model.calls == 3
                assert updates.status("demo")["local_applied_revision"] == 2
                assert (
                    len(updates.documents.load_by_source("demo")) == 2
                )  # removed Doc retained as a tombstone
                repository.delete("demo", 2)
                updates.run_once()
                assert not index.get_vector_ids()
                assert model.calls == 3
        finally:
            index.close()


def test_readiness_does_not_depend_on_optional_counts(tmp_path, monkeypatch):
    from search_core.providers.qdrant_index import QdrantRetrievalIndex

    def unavailable(self, index_key=""):
        raise OSError("count unavailable")

    monkeypatch.setattr(QdrantRetrievalIndex, "point_counts", unavailable)
    with TestClient(
        local_factory()(tmp_path, poll_seconds=0.05),
        client=("127.0.0.1", 50000),
        base_url="http://127.0.0.1",
    ) as client:
        saved = client.put(
            "/api/v2/agent-cards/demo", json={"card": {"name": "invoice"}}
        )
        applied(client, saved.json()["card_version"])
        assert client.get("/ready").status_code == 200
        result = client.get("/ready?include_counts=true")
        assert result.status_code == 503
        body = result.json()
        assert body["detail"] == "index count unavailable" and body["ready"] is False
        assert body["instance_id"] == "local" and body["index_key"]
        assert "index_counts" not in body


def test_ready_counts_report_actual_mismatched_payloads_without_new_endpoint(tmp_path):
    from search_core.plugin_api.vector_point import VectorPoint

    app = local_factory()(tmp_path, poll_seconds=0.05)
    with TestClient(
        app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1"
    ) as client:
        saved = client.put(
            "/api/v2/agent-cards/demo", json={"card": {"name": "invoice"}}
        )
        applied(client, saved.json()["card_version"])
        index = app.state.catalog_updates.index
        index.upsert(
            [
                VectorPoint(
                    id="wrong-version",
                    vector=[1.0] + [0.0] * 255,
                    payload={"index_key": "other"},
                ),
                VectorPoint(
                    id="missing-version", vector=[1.0] + [0.0] * 255, payload={}
                ),
            ]
        )
        result = client.get("/ready?include_counts=true")
        assert result.status_code == 200, result.text
        assert result.json()["index_key"] == app.state.index_key
        for kind in ("vector", "keyword"):
            assert result.json()["index_counts"][f"{kind}_points"] == 3
            assert result.json()["index_counts"][f"{kind}_matching_points"] == 1
            assert result.json()["index_counts"][f"{kind}_mismatched_points"] == 2
        assert "index_counts" not in client.get("/ready").json()
