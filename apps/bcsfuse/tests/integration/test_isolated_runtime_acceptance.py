"""Restart TC against real SQLite/Qdrant Local; no running service or live models."""

from fastapi.testclient import TestClient

from app.bootstrap.opensource_app import create_opensource_app


def test_current_card_config_and_search_survive_restart_without_reembedding(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "test")
    monkeypatch.setenv("BCSFUSE_TC_V1_STORAGE", "card")
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "tc.sqlite3"))
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "qdrant"))
    headers = {"Authorization": "Bearer test-token"}
    first = create_opensource_app("test")
    with TestClient(first, headers=headers) as client:
        saved = client.post(
            "/v1/workers/bot:owner/sync",
            json={
                "name": "Database expert",
                "description": "MySQL migration support",
                "availability": "public",
                "profile": {"profile_id": "legacy", "activate": False},
            },
        )
        assert saved.status_code == 200, saved.text
        client.put(
            "/v1/workers/bot:owner/config", json={"fusion_enable": True}
        ).raise_for_status()
        revision = client.get("/v1/workers/bot:owner").json()["version"]
    second = create_opensource_app("test")
    embedding = second.state.context.registry.require("embedding_provider")
    original = embedding.embed

    def forbidden_embed(text):
        raise AssertionError("Restart must restore persisted Docs without model calls")

    monkeypatch.setattr(embedding, "embed", forbidden_embed)
    with TestClient(second, headers=headers) as client:
        assert client.get("/ready").status_code == 200
        assert client.get("/v1/workers/bot:owner").json()["version"] == revision
        assert (
            client.get("/v1/workers/bot:owner/config").json()["fusion_enable"] is True
        )
        monkeypatch.setattr(embedding, "embed", original)
        result = client.post("/api/v1/recommend", json={"question": "MySQL"})
        assert result.status_code == 200, result.text
        assert result.json()["recommendations"][0]["profile_key"] == "bot:owner:legacy"
        client.delete("/api/v1/workers/bot:owner").raise_for_status()
    with TestClient(create_opensource_app("test"), headers=headers) as client:
        assert client.get("/v1/workers/bot:owner").status_code == 404
        assert (
            client.post("/api/v1/recommend", json={"question": "MySQL"}).json()[
                "recommendations"
            ]
            == []
        )
