"""TC v1 consumers over one durable Card and the public retrieval pipeline."""

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.opensource import build_opensource_provider_registry


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("BCSFUSE_TC_V1_STORAGE", "card")
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "test")
    monkeypatch.setenv("WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "tc.sqlite3"))
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("QDRANT_MODE", "local")
    registry = build_opensource_provider_registry("test")
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="test",
            startup_profile="opensource",
            registry=registry,
        )
    )
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        yield client, registry


def sync(client, worker="bot:owner", **changes):
    body = {
        "name": "Database specialist",
        "description": "MySQL database migrations",
        "availability": "public",
        "runtime_state": "online",
        "profile": {"profile_id": "legacy", "activate": False},
    }
    body.update(changes)
    response = client.post(f"/v1/workers/{worker}/sync", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_sync_writes_one_card_and_never_imports_md_or_synthetic_capabilities(runtime):
    client, registry = runtime
    response = sync(
        client,
        profile={
            "profile_id": "legacy",
            "activate": False,
            "soul_md": "NOISY_MD_EXPERT",
            "contents": {"profile": "STALE_PROFILE"},
            "skill_sets": [
                {"name": "default_tool", "description": "NOISY_DEFAULT_SKILL"}
            ],
        },
        capabilities=[{"name": "unrelated", "level": "expert"}],
    )
    assert response["profile_activated"] is True
    repository = registry.require("tc_catalog_repository")
    row = repository.get("bot:owner")
    assert row.card.source_card["description"] == "MySQL database migrations"
    assert row.card.source_card["skills"][0]["description"] == "NOISY_DEFAULT_SKILL"
    assert "soul_md" not in row.card.source_card
    assert "capabilities" not in row.card.source_card
    sync(
        client,
        description="PostgreSQL maintenance",
        profile={"profile_id": "another", "activate": True},
    )
    profiles = client.get("/v1/workers/bot:owner/profiles").json()
    assert profiles["total"] == 1
    assert repository.get("bot:owner").card.source_card["profile_id"] == "legacy"
    assert (
        registry.require("worker_profile_content_store").get_active("bot:owner").soul_md
        is None
    )


def test_sparse_sync_preserves_fields_and_explicit_empty_clears(runtime):
    client, registry = runtime
    sync(
        client, profile={"skill_sets": [{"name": "migration", "description": "MySQL"}]}
    )
    client.post(
        "/api/v1/workers/bot:owner/sync", json={"name": "Renamed", "profile": {}}
    ).raise_for_status()
    row = registry.require("tc_catalog_repository").get("bot:owner").card.source_card
    assert row["description"] == "MySQL database migrations"
    assert row["skills"] == [{"name": "migration", "description": "MySQL"}]
    client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "Renamed",
            "description": "",
            "profile": {"skill_sets": []},
        },
    ).raise_for_status()
    row = registry.require("tc_catalog_repository").get("bot:owner").card.source_card
    assert row["description"] == ""
    assert row["skills"] == []


def test_recommend_ignores_legacy_threshold_and_obeys_runtime_and_visibility(runtime):
    client, _registry = runtime
    sync(client)
    sync(client, "private", availability="private")
    sync(client, "offline", runtime_state="offline")
    for enabled in (False, True):
        results = []
        for threshold in (0.0, 0.99):
            response = client.post(
                "/api/v1/recommend",
                json={
                    "question": "MySQL database migration",
                    "topK": 5,
                    "min_score": threshold,
                    "enable_rerank": enabled,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            results.append([r["worker_id"] for r in body["recommendations"]])
            assert body["metadata"]["min_score_compat_mode"] == "deprecated_ignored"
            assert body["recommendations"][0]["profile_key"] == "bot:owner:legacy"
        assert results == [["bot:owner"], ["bot:owner"]]
    for suffix in ("offline", "online"):
        client.put(f"/v1/workers/bot:owner/{suffix}").raise_for_status()
        result = client.post("/api/v1/recommend", json={"question": "MySQL"}).json()
        assert bool(result["recommendations"]) is (suffix == "online")


def test_config_batch_and_delete_preserve_old_contracts(runtime):
    client, registry = runtime
    sync(client)
    config = client.get("/v1/workers/bot:owner/config").json()
    assert config["fusion_enable"] is False
    client.put(
        "/v1/workers/bot:owner/config", json={"fusion_enable": True}
    ).raise_for_status()
    batch = client.post(
        "/v1/workers/config/batch", json={"worker_ids": ["bot:owner", "missing"]}
    ).json()
    assert batch["data"]["bot:owner"]["fusion_enable"] is True
    assert batch["not_found_ids"] == ["missing"]
    workers = client.post(
        "/v1/workers/batch", json={"worker_ids": ["bot:owner"]}
    ).json()
    assert workers["data"]["bot:owner"]["profile_tags"]["trust_level"] == "guarded"
    client.delete("/v1/workers/bot:owner").raise_for_status()
    assert client.get("/v1/workers/bot:owner").status_code == 404
    assert (
        registry.require("worker_profile_content_store").get_active("bot:owner") is None
    )
    assert not client.post("/api/v1/recommend", json={"question": "MySQL"}).json()[
        "recommendations"
    ]


def test_write_failure_is_reported_without_claiming_sync_success(runtime, monkeypatch):
    client, registry = runtime
    repository = registry.require("tc_catalog_repository")

    def fail(*args, **kwargs):
        raise RuntimeError("controlled persistence failure")

    monkeypatch.setattr(repository, "upsert", fail)
    response = client.post(
        "/v1/workers/failing/sync", json={"name": "Failure", "profile": {}}
    )
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "SYNC_WORKER_ERROR"


def test_invalid_state_is_rejected_before_writing_and_disabled_cannot_go_online(
    runtime,
):
    client, registry = runtime
    invalid = client.post(
        "/v1/workers/invalid/sync",
        json={
            "name": "Invalid",
            "availability": "invalid",
            "profile": {},
        },
    )
    assert invalid.status_code in (400, 422)
    assert registry.require("tc_catalog_repository").get("invalid") is None
    sync(client)
    registry.require("tc_catalog").update(
        "bot:owner", {"lifecycle_state": "disabled", "runtime_state": "offline"}
    )
    assert client.put("/v1/workers/bot:owner/online").status_code == 400


def test_state_and_config_updates_reuse_embedding_and_do_not_leak_private_results(
    runtime, monkeypatch
):
    client, registry = runtime
    embedding = registry.require("embedding_provider")
    original = embedding.embed
    calls = []

    def observed(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(embedding, "embed", observed)
    sync(client)
    indexed = len(calls)
    client.put("/v1/workers/bot:owner/offline").raise_for_status()
    client.put("/v1/workers/bot:owner/online").raise_for_status()
    client.put(
        "/v1/workers/bot:owner/config", json={"fusion_enable": True}
    ).raise_for_status()
    assert len(calls) == indexed
    client.put(
        "/v1/workers/bot:owner/availability", json={"availability": "private"}
    ).raise_for_status()
    assert not client.post("/api/v1/recommend", json={"question": "MySQL"}).json()[
        "recommendations"
    ]


def test_legacy_identity_is_resolved_for_fuse_without_md(runtime):
    client, registry = runtime
    sync(client)
    source = registry.require("worker_profile_source")
    # Existing perspective providers may split a complete Worker ID at its first colon.
    assert source.get_by_participant("bot:owner").staff_id == "bot:owner"
    assert source.get_by_participant("bot:owner:legacy").staff_id == "bot:owner"
    assert (
        source.scan_for_workers(["bot:owner:legacy"]).profiles[0].staff_id
        == "bot:owner"
    )


def test_explicit_card_backend_ignores_removed_index_source_setting(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "new.sqlite"))
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "new-qdrant"))
    monkeypatch.setenv("BCSFUSE_TC_INDEX_SOURCE", "legacy")
    monkeypatch.setenv("BCSFUSE_TC_V1_STORAGE", "card")
    registry = build_opensource_provider_registry("test")

    def old_storage():
        raise AssertionError("new deployment must never open the old storage")

    registry.register("tc_legacy_storage_factory", old_storage)
    app = create_bcsfuse_app(
        ApplicationContext(mode="test", startup_profile="opensource", registry=registry)
    )
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        response = client.post(
            "/v1/workers/new:owner/sync",
            json={
                "name": "New database worker",
                "description": "Database recovery",
                "runtime_state": "online",
                "availability": "public",
                "profile": {},
            },
        )
        assert response.status_code == 200, response.text
        result = client.post("/v1/search", json={"query": "Database recovery"})
        assert result.status_code == 200, result.text
        assert result.json()["results"][0]["worker_id"] == "new:owner"
        assert registry.require("tc_catalog_repository").get("new:owner") is not None
