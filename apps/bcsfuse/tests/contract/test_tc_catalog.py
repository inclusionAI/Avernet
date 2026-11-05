"""TC v1 consumers over one durable Card and the public retrieval pipeline."""

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.opensource import build_opensource_provider_registry


@pytest.fixture
def runtime(tmp_path, monkeypatch):
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


def test_tc_index_source_cutover_keeps_old_queries_and_state_filters(
    tmp_path, monkeypatch
):
    from search_core.providers.demo_embedding import DemoEmbedding

    from app.domain.models.vector_point import VectorPoint
    from app.domain.models.worker import Worker, WorkerIdentity, WorkerState
    from app.domain.models.worker_profile_content import WorkerProfileContent
    from app.infra.public.stores.sqlite_worker_profile_content_store import (
        SQLiteWorkerProfileContentStore,
    )
    from app.infra.public.stores.sqlite_worker_registry_store import (
        SQLiteWorkerRegistryStore,
    )
    from app.infra.public.stores.sqlite_worker_runtime_state_store import (
        SQLiteWorkerRuntimeStateStore,
    )
    from app.infra.public.vectorstores.qdrant_sqlite_vector_store import (
        QdrantSQLiteVectorStore,
    )

    old_db = str(tmp_path / "legacy.sqlite")
    workers = SQLiteWorkerRegistryStore(old_db)
    profiles = SQLiteWorkerProfileContentStore(old_db)
    states = SQLiteWorkerRuntimeStateStore(old_db)
    workers.create(
        Worker(
            id="legacy:owner",
            type="bot",
            identity=WorkerIdentity(
                name="Legacy worker",
                handle="legacy",
                description="Old MySQL description",
            ),
            capabilities=[],
            responsibilities=[],
            state=WorkerState(
                runtime_state="online", availability="public", trust_level="guarded"
            ),
        )
    )
    profiles.save(
        WorkerProfileContent(worker_id="legacy:owner", profile_id="old", is_active=True)
    )
    from app.domain.models.worker_runtime_state import WorkerRuntimeState

    states.set_runtime_state("legacy:owner", WorkerRuntimeState.ONLINE)
    old_vectors = QdrantSQLiteVectorStore(
        dimension=256, db_path=old_db, qdrant_path=str(tmp_path / "old-qdrant")
    )
    old_vectors.upsert(
        [
            VectorPoint(
                id="legacy:owner:old:full",
                vector=DemoEmbedding(256).embed("MySQL legacy source"),
                payload={"worker_id": "legacy:owner", "content": "MySQL legacy source"},
            )
        ]
    )
    workers.create(
        Worker(
            id="aaa:hidden",
            type="bot",
            capabilities=[],
            responsibilities=[],
            identity=WorkerIdentity(name="Hidden", handle="hidden"),
            state=WorkerState(
                runtime_state="online", availability="private", trust_level="guarded"
            ),
        )
    )
    states.set_runtime_state("aaa:hidden", WorkerRuntimeState.ONLINE)
    # More hidden fragments than the recall limit must not crowd out the visible Worker.
    old_vectors.upsert(
        [
            VectorPoint(
                id=f"aaa:hidden:old:{i}",
                vector=DemoEmbedding(256).embed("MySQL"),
                payload={"worker_id": "aaa:hidden", "content": "MySQL " * 20},
            )
            for i in range(65)
        ]
    )
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "cards.sqlite"))
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "new-qdrant"))
    monkeypatch.setenv("BCSFUSE_TC_INDEX_SOURCE", "legacy")
    registry = build_opensource_provider_registry("test")
    registry.register(
        "tc_legacy_storage_factory", lambda: (workers, profiles, states, old_vectors)
    )
    app = create_bcsfuse_app(
        ApplicationContext(mode="test", startup_profile="opensource", registry=registry)
    )
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["index_source"] == "legacy"
        legacy = client.post("/v1/search", json={"query": "MySQL"})
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["results"][0]["worker_id"] == "legacy:owner"
        scoped = client.post(
            "/api/v1/workers/profiles/search",
            json={"query": "MySQL", "filters": {"worker_id": ["aaa:hidden"]}},
        )
        assert scoped.status_code == 200, scoped.text
        assert scoped.json()["results"] == []
        response = client.post(
            "/v1/workers/legacy:owner/sync",
            json={
                "name": "Current worker",
                "description": "PostgreSQL current source",
                "runtime_state": "online",
                "availability": "public",
                "profile": {"activate": False},
            },
        )
        assert response.status_code == 200, response.text
        assert (
            registry.require("tc_catalog_repository")
            .get("legacy:owner")
            .card.source_card["description"]
            == "PostgreSQL current source"
        )
        for path, body in (
            ("offline", {}),
            ("online", {}),
            ("availability", {"availability": "private"}),
            ("availability", {"availability": "public"}),
        ):
            assert (
                client.put(f"/v1/workers/legacy:owner/{path}", json=body).status_code
                == 200
            )
            result = client.post("/api/v1/search", json={"query": "MySQL"})
            assert result.status_code == 200, result.text
            assert bool(result.json()["results"]) == (
                path == "online" or body.get("availability") == "public"
            )
        # Import/state changes do not overwrite the old persistence source.
        assert (
            old_vectors.get("legacy:owner:old:full").payload["content"]
            == "MySQL legacy source"
        )
    monkeypatch.setenv("BCSFUSE_TC_INDEX_SOURCE", "card")
    next_registry = build_opensource_provider_registry("test")
    next_registry.register(
        "tc_legacy_storage_factory",
        lambda: pytest.fail("card mode must not open old storage"),
    )
    next_app = create_bcsfuse_app(
        ApplicationContext(
            mode="test", startup_profile="opensource", registry=next_registry
        )
    )
    with TestClient(next_app, headers={"Authorization": "Bearer test-token"}) as client:
        assert client.get("/ready").json()["index_source"] == "card"
        current = client.post("/api/v1/search", json={"query": "PostgreSQL"})
        assert current.status_code == 200, current.text
        assert current.json()["results"][0]["worker_id"] == "legacy:owner"
        assert client.get("/v1/workers/legacy:owner/profiles/active").status_code == 200
        recommendation = client.post("/v1/recommend", json={"question": "PostgreSQL"})
        assert recommendation.status_code == 200, recommendation.text
        assert (
            recommendation.json()["recommendations"][0]["worker_id"] == "legacy:owner"
        )
        assert (
            client.put(
                "/v1/workers/legacy:owner/availability",
                json={"availability": "private"},
            ).status_code
            == 200
        )
        assert (
            client.post("/v1/search", json={"query": "PostgreSQL"}).json()["results"]
            == []
        )


def test_invalid_tc_index_source_is_rejected_before_storage(monkeypatch):
    monkeypatch.setenv("BCSFUSE_TC_INDEX_SOURCE", "unexpected")
    registry = build_opensource_provider_registry("test")
    registry.register(
        "tc_storage_factory", lambda: pytest.fail("must not open storage")
    )
    with pytest.raises(ValueError, match="legacy or card"):
        create_bcsfuse_app(
            ApplicationContext(
                mode="test", startup_profile="opensource", registry=registry
            )
        )
