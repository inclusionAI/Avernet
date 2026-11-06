"""One new TC build, one address: v1 switches whole backends, v2 stays new."""

import sqlite3
import time
from contextlib import contextmanager
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.opensource import build_opensource_provider_registry


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "test")
    monkeypatch.setenv("BCSFUSE_TRUST_GATEWAY", "false")
    monkeypatch.setenv("BCSFUSE_DATABASE_SQLITE_PATH", str(tmp_path / "old.sqlite"))
    monkeypatch.setenv(
        "BCSFUSE_VECTOR_SQLITE_PATH", str(tmp_path / "old-vectors.sqlite")
    )
    monkeypatch.setenv("WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "new.sqlite"))
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "new-index"))
    monkeypatch.setenv("ENABLE_REAL_LLM", "false")
    monkeypatch.setenv("LLM_ENABLED", "false")

    @contextmanager
    def start(mode=None):
        if mode is None:
            monkeypatch.delenv("BCSFUSE_TC_V1_STORAGE", raising=False)
        else:
            monkeypatch.setenv("BCSFUSE_TC_V1_STORAGE", mode)
        registry = build_opensource_provider_registry("test")
        if mode == "card":
            registry.register(
                "tc_legacy_storage_factory",
                lambda: pytest.fail("card must not open old stores"),
            )
        app = create_bcsfuse_app(
            ApplicationContext(
                mode="test", startup_profile="opensource", registry=registry
            )
        )
        with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
            yield client, registry

    return start, tmp_path


def old_sync(client, key, description):
    response = client.post(
        f"/v1/workers/{key}/sync",
        json={
            "name": key,
            "description": description,
            "runtime_state": "online",
            "availability": "public",
            "profile": {"profile_id": "current", "activate": False},
        },
    )
    assert response.status_code == 200, response.text


def new_card(client, key, **fields):
    response = client.put(
        f"/api/v2/agent-cards/{key}",
        json={
            "card": fields,
            "labels": {"visibility": ["public"], "user_visibility": ["public"]},
        },
    )
    assert response.status_code == 202, response.text
    version = response.json()["card_version"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.get(f"/api/v2/agent-cards/{key}/index-status")
        assert status.status_code == 200, status.text
        if (
            status.json()["indexed_card_version"] >= version
            and status.json()["doc_ready"]
        ):
            return
        time.sleep(0.01)
    pytest.fail("new Card/Doc did not become indexed")


def rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def test_same_new_instance_switches_all_v1_reads_and_writes_and_keeps_v2_new(service):
    start, path = service
    with start() as (client, registry):
        assert client.get("/ready").json().get("v1_storage") == "legacy"
        old_sync(client, "same:owner", "LEGACY_MYSQL")
        old_sync(client, "old:only", "LEGACY_ONLY")
        assert registry.require("tc_catalog_repository").get("same:owner") is None
        assert client.get("/api/v2/agent-cards/old:only").status_code == 404
        new_card(
            client,
            "same:owner",
            name="same:owner",
            description="CARD_POSTGRES",
            runtime_state="online",
            availability="public",
            profile_id="current",
        )
        new_card(
            client,
            "new:only",
            name="new:only",
            description="CARD_ONLY",
            runtime_state="online",
            availability="public",
        )
        assert client.get("/v1/workers/new:only").status_code == 404
        assert (
            client.get("/v1/workers/same:owner").json()["description"] == "LEGACY_MYSQL"
        )
        assert (
            client.get("/api/v2/agent-cards/same:owner").json()["card"]["description"]
            == "CARD_POSTGRES"
        )
        assert (
            client.put(
                "/v1/workers/same:owner/config", json={"fusion_enable": False}
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/v2/workers/same:owner/config", json={"fusion_enable": True}
            ).status_code
            == 200
        )
        assert (
            client.get("/v1/workers/same:owner/config").json()["fusion_enable"] is False
        )
        assert (
            client.get("/api/v2/workers/same:owner/config").json()["fusion_enable"]
            is True
        )
        new_before = rows(path / "new.sqlite", "bcsfuse_agent_cards")
        for action, body in (
            ("offline", {}),
            ("online", {}),
            ("availability", {"availability": "private"}),
        ):
            result = client.put(f"/api/v1/workers/same:owner/{action}", json=body)
            assert result.status_code == 200, result.text
        assert rows(path / "new.sqlite", "bcsfuse_agent_cards") == new_before
        filtered = client.post(
            "/v1/search",
            json={"query": "LEGACY_MYSQL", "filters": {"worker_id": ["same:owner"]}},
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["results"] == []
        assert (
            client.put(
                "/v1/workers/same:owner/availability", json={"availability": "public"}
            ).status_code
            == 200
        )
        old_before = rows(path / "old.sqlite", "bcsfuse_workers")
        new_card(client, "same:owner", description="CARD_UPDATED")
        assert rows(path / "old.sqlite", "bcsfuse_workers") == old_before
        old_result = client.post("/v1/search", json={"query": "LEGACY_MYSQL"})
        new_result = client.post("/api/v2/search", json={"query": "CARD_UPDATED"})
        assert old_result.status_code == new_result.status_code == 200, (
            old_result.text,
            new_result.text,
        )
        assert "new:only" not in [i["worker_id"] for i in old_result.json()["results"]]
        assert "old:only" not in [i["id"] for i in new_result.json()["items"]]
        fused = client.post(
            "/api/v1/groups/same-group/fuse",
            json={"question": "Recovery", "participants": ["same:owner:current"]},
        )
        assert fused.status_code == 200, fused.text
        assert "LEGACY_MYSQL" in fused.text and "CARD_UPDATED" not in fused.text
        assert client.delete("/v1/workers/old:only").status_code == 200
        assert len(rows(path / "old.sqlite", "bcsfuse_workers")) == 1
        old_frozen = rows(path / "old.sqlite", "bcsfuse_workers")

    with start("card") as (client, registry):
        assert client.get("/ready").json()["v1_storage"] == "card"
        assert registry.require("tc_search") is registry.require("tc_card_search")
        assert (
            client.get("/v1/workers/same:owner").json()["description"] == "CARD_UPDATED"
        )
        assert (
            client.get("/v1/workers/same:owner/config").json()["fusion_enable"] is True
        )
        old_sync(client, "after:switch", "CARD_AFTER_SWITCH")
        assert client.get("/api/v2/agent-cards/after:switch").status_code == 200
        assert client.put("/v1/workers/same:owner/offline").status_code == 200
        assert (
            client.get("/api/v2/agent-cards/same:owner").json()["card"]["runtime_state"]
            == "offline"
        )
        new_card(client, "same:owner", description="CARD_FINAL", runtime_state="online")
        assert (
            client.get("/v1/workers/same:owner").json()["description"] == "CARD_FINAL"
        )
        assert rows(path / "old.sqlite", "bcsfuse_workers") == old_frozen
        fused = client.post(
            "/api/v1/groups/same-group/fuse",
            json={"question": "Recovery", "participants": ["same:owner:current"]},
        )
        assert fused.status_code == 200 and "CARD_FINAL" in fused.text, fused.text
        assert client.delete("/v1/workers/new:only").status_code == 200
        assert client.get("/api/v2/agent-cards/new:only").status_code == 404


def test_invalid_storage_selection_fails_instead_of_picking_a_backend(service):
    start, _path = service
    with pytest.raises(ValueError, match="legacy.*card"), start("invalid"):
        pass


def test_each_api_deletes_only_its_selected_store_for_the_same_id(service):
    start, _path = service
    with start() as (client, _registry):
        old_sync(client, "both:delete", "old delete target")
        saved = client.put(
            "/api/v2/agent-cards/both:delete",
            json={
                "card": {"name": "new delete target"},
                "labels": {"visibility": ["private"], "user_visibility": ["private"]},
            },
        )
        assert saved.status_code == 202, saved.text
        assert client.delete("/api/v1/workers/both:delete").status_code == 200
        assert client.get("/api/v2/agent-cards/both:delete").status_code == 200
        old_sync(client, "both:delete", "old recreated target")
        assert client.delete("/api/v2/agent-cards/both:delete").status_code == 202
        assert (
            client.get("/v1/workers/both:delete").json()["description"]
            == "old recreated target"
        )
        assert client.get("/api/v2/agent-cards/both:delete").status_code == 404


def test_old_index_failure_cannot_succeed_using_new_doc_status(service, monkeypatch):
    start, path = service
    with start() as (client, registry):
        old_sync(client, "legacy:owner", "old text")
        vectors = registry.require("tc_legacy_vectors")
        original = vectors.upsert

        def fail(_points):
            raise RuntimeError("old index unavailable")

        monkeypatch.setattr(vectors, "upsert", fail)
        failed = client.post(
            "/v1/workers/legacy:owner/sync",
            json={"name": "Changed", "description": "updated text", "profile": {}},
        )
        assert failed.status_code == 500, failed.text
        assert registry.require("tc_catalog_repository").get("legacy:owner") is None
        assert rows(path / "new.sqlite", "bcsfuse_agent_cards") == []
        monkeypatch.setattr(vectors, "upsert", original)
        old_sync(client, "legacy:owner", "updated text")
        assert client.get("/ready").status_code == 200


def test_legacy_index_consumer_cannot_clear_pending_before_worker_commits(
    service, monkeypatch
):
    start, _path = service
    with start() as (_client, registry):
        catalog = registry.require("tc_catalog")
        updates = registry.require("tc_search").updates
        create = catalog.workers.create
        entered = Event()

        def consume():
            entered.set()
            updates.run_once()

        consumer = Thread(target=consume)

        def delayed_create(worker):
            consumer.start()
            assert entered.wait(1)
            consumer.join(0.1)
            return create(worker)

        monkeypatch.setattr(catalog.workers, "create", delayed_create)
        catalog.sync("racing:owner", {"name": "Race", "description": "Committed"})
        consumer.join(2)
        assert not consumer.is_alive()
        updates.run_once()
        assert updates.status("racing:owner")["local_applied_revision"] > 0
        assert updates.worker_points("racing:owner")


@pytest.mark.parametrize("operation", ["sync", "delete"])
def test_legacy_restart_recovers_failed_index_write(service, monkeypatch, operation):
    start, _path = service
    with start() as (client, registry):
        old_sync(client, "restart:owner", "before failure")
        vectors = registry.require("tc_legacy_vectors")

        def fail(_points):
            raise RuntimeError("old index unavailable")

        monkeypatch.setattr(
            vectors, "upsert" if operation == "sync" else "delete", fail
        )
        if operation == "sync":
            response = client.post(
                "/v1/workers/restart:owner/sync",
                json={
                    "name": "Updated",
                    "description": "after recovery",
                    "profile": {},
                },
            )
        else:
            response = client.delete("/v1/workers/restart:owner")
        assert response.status_code == 500, response.text

    with start() as (client, registry):
        updates = registry.require("tc_search").updates
        points = updates.worker_points("restart:owner")
        if operation == "sync":
            assert points and all(
                "after recovery" in p.payload["content"] for p in points
            )
            status = updates.status("restart:owner")
            assert status["local_applied_revision"] == status["saved_revision"]
        else:
            assert not points
        assert client.get("/ready").status_code == 200


def test_legacy_restart_preserves_historical_unmarked_vectors(service, monkeypatch):
    from app.infra.public.vectorstores.qdrant_sqlite_vector_store import (
        QdrantSQLiteVectorStore,
    )

    start, _path = service
    with start() as (client, registry):
        old_sync(client, "history:owner", "historical text")
        catalog = registry.require("tc_catalog")
        profile = catalog.profiles.get_active("history:owner")
        profile.metadata.pop("_tc_worker_revision")
        catalog.profiles.save(profile)
        updates = registry.require("tc_search").updates
        points = updates.worker_points("history:owner")
        for point in points:
            point.payload.pop("_tc_revision")
        registry.require("tc_legacy_vectors").upsert(points)

    def fail(*args, **kwargs):
        pytest.fail("legacy startup must reuse historical vectors without rewriting")

    monkeypatch.setattr(QdrantSQLiteVectorStore, "upsert", fail)
    with start() as (client, _registry):
        assert client.get("/ready").status_code == 200
        result = client.put(
            "/v1/workers/history:owner/config", json={"fusion_enable": False}
        )
        assert result.status_code == 200, result.text


@pytest.mark.parametrize("recreate", [False, True])
def test_late_legacy_embedding_cannot_overwrite_another_updaters_newer_revision(
    service, monkeypatch, recreate
):
    from app.tc.legacy_catalog import LegacyCatalog
    from app.tc.legacy_index import LegacyUpdates

    start, _path = service
    with start() as (_client, registry):
        first = registry.require("tc_search").updates
        catalog = first.catalog
        second_catalog = LegacyCatalog(
            catalog.workers, catalog.profiles, catalog.states
        )
        second = LegacyUpdates(second_catalog, first.vectors, first.embedding)
        embed = first.embedding.embed
        advanced = False

        def delayed_embed(text):
            nonlocal advanced
            if not advanced:
                advanced = True
                if recreate:
                    second_catalog.delete("late:owner")
                    second.run_once()
                second_catalog.sync(
                    "late:owner", {"name": "Newer", "description": "newer revision"}
                )
                second.run_once()
            return embed(text)

        monkeypatch.setattr(first.embedding, "embed", delayed_embed)
        catalog.sync("late:owner", {"name": "Older", "description": "older revision"})
        first.run_once()
        points = second.worker_points("late:owner")
        version = second_catalog.require("late:owner").revision
        assert all(p.payload["_tc_revision"] == version for p in points)
        assert all("newer revision" in p.payload["content"] for p in points)


def test_api_migration_imports_and_reconciles_only_new_storage(service):
    import json

    from app.tc.migration import migrate_rows
    from app.tc.migration_api import ApiMigrationRepository

    start, _path = service
    with start() as (client, registry):
        old_sync(client, "import:owner", "authoritative old source")
        environment = client.get("/ready").json()["environment"]
        with pytest.raises(ValueError, match="environment"):
            ApiMigrationRepository(client, "wrong-environment")
        repository = ApiMigrationRepository(client, environment)
        row = {
            "env": environment,
            "bot_uuid": "import:owner",
            "actor_kind": "bot",
            "visibility": "public",
            "status": "online",
            "is_deleted": "0",
            "bot_info": json.dumps(
                {"name": "Imported", "summary": "migrated description"}
            ),
        }
        report = migrate_rows(
            [row],
            repository,
            environment=environment,
            dry_run=False,
            legacy_lookup=lambda *_: {"config": {"fusion_enable": True}},
        )
        assert report.counts["migrated"] == 1
        assert (
            registry.require("tc_catalog")
            .require("import:owner")
            .card.source_card["description"]
            == "authoritative old source"
        )
        new = repository.read_card("import:owner")
        assert new.card.source_card["description"] == "migrated description"
        assert new.config["fusion_enable"] is True
        # Replaying the identical source/config is idempotent.
        migrate_rows([row], repository, environment=environment, dry_run=False)
        assert repository.read_card("import:owner").revision == new.revision
        migrate_rows(
            [{**row, "is_deleted": "1"}],
            repository,
            environment=environment,
            dry_run=False,
        )
        assert repository.read_card("import:owner").card.deleted
        skipped = migrate_rows(
            [row], repository, environment=environment, dry_run=False
        )
        assert skipped.counts["skipped_tombstone"] == 1
        assert client.get("/v1/workers/import:owner").status_code == 200


def test_legacy_filter_excludes_hidden_fragments_before_top_k(service):
    from app.domain.models.vector_point import VectorPoint

    start, _path = service
    with start() as (client, registry):
        old_sync(client, "visible:owner", "needle visible")
        old_sync(client, "hidden:owner", "needle")
        assert (
            client.put(
                "/v1/workers/hidden:owner/availability",
                json={"availability": "private"},
            ).status_code
            == 200
        )
        vector = registry.require("embedding_provider").embed("needle")
        registry.require("tc_legacy_vectors").upsert(
            [
                VectorPoint(
                    id=f"hidden:owner:current:{i}",
                    vector=vector,
                    payload={"worker_id": "hidden:owner", "content": "needle " * 20},
                )
                for i in range(65)
            ]
        )
        result = client.post("/api/v1/search", json={"query": "needle", "top_k": 1})
        assert result.status_code == 200, result.text
        assert [i["worker_id"] for i in result.json()["results"]] == ["visible:owner"]


def test_card_mode_needs_no_old_worker_or_vector_tables(service):
    start, path = service
    with start("card") as (client, _registry):
        old_sync(client, "new:owner", "card only")
        assert client.get("/api/v2/agent-cards/new:owner").status_code == 200
        with sqlite3.connect(path / "old.sqlite") as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "bcsfuse_workers" not in tables
        assert "bcsfuse_worker_profile_contents" not in tables
        assert not (path / "old-vectors.sqlite").exists()
        unauthorized = client.put(
            "/api/v2/agent-cards/forbidden",
            json={"card": {"name": "x"}},
            headers={"Authorization": "Bearer bad"},
        )
        assert unauthorized.status_code == 401
        invalid = client.put(
            "/api/v2/agent-cards/incomplete",
            json={"card": {"description": "missing name"}},
        )
        assert invalid.status_code == 400, invalid.text


@pytest.mark.parametrize("scoped", [False, True])
def test_legacy_recall_batches_filter_labels_without_loading_profiles(
    service, monkeypatch, scoped
):
    from app.domain.models.worker_runtime_state import WorkerRuntimeState

    start, _path = service
    with start() as (client, registry):
        old_sync(client, "visible:batch", "needle database")
        old_sync(client, "offline:batch", "needle database")
        catalog = registry.require("tc_catalog")
        # Runtime state is authoritative even if Worker/vector payloads are older.
        catalog.states.set_runtime_state("offline:batch", WorkerRuntimeState.OFFLINE)

        def fail(*args, **kwargs):
            pytest.fail("recall filtering must not enumerate full Card/Profile views")

        monkeypatch.setattr(catalog, "rows", fail)
        monkeypatch.setattr(catalog.profiles, "get", fail)
        monkeypatch.setattr(catalog.profiles, "get_active", fail)
        if scoped:
            monkeypatch.setattr(catalog.workers, "list", fail)
        filters = {"runtime_state": ("online",), "availability": ("public",)}
        if scoped:
            filters["source_id"] = ("visible:batch", "offline:batch")
        hits = registry.require("tc_search").retrieval_index.keyword_search(
            "needle",
            1,
            filters,
        )
        assert [hit.payload["source_id"] for hit in hits] == ["visible:batch"]


def test_native_search_projects_tc_name_and_description(service):
    start, _path = service
    with start("card") as (client, _registry):
        old_sync(client, "display:owner", "MySQL database backup")
        result = client.post(
            "/api/v2/search",
            json={"query": "MySQL", "filters": {"worker_id": ["display:owner"]}},
        )
        assert result.status_code == 200, result.text
        item = result.json()["items"][0]
        assert item["name"] == "display:owner"
        assert item["summary"] == "MySQL database backup"


def test_private_v2_card_is_discoverable_without_changing_v1_defaults(service):
    start, _ = service
    with start("card") as (client, registry):
        path = "/api/v2/agent-cards/private:one"
        data = {"name": "invoice", "description": "invoice lookup"}
        saved = client.put(
            path,
            json={
                "card": data,
                "labels": {"visibility": ["private"], "user_visibility": ["public"]},
            },
        )
        assert saved.status_code == 202, saved.text
        registry.require("tc_card_search").updates.run_once()
        assert client.get(path).json()["card"] == data
        for route in ("search", "recommend"):
            for visibility, expected in [("private", ["private:one"]), ("public", [])]:
                response = client.post(
                    "/api/v2/" + route,
                    json={
                        "query": "private:one",
                        "filters": {
                            "resource_type": ["agent"],
                            "labels.visibility": [visibility],
                        },
                    },
                )
                assert response.status_code == 200, response.text
                assert [item["id"] for item in response.json()["items"]] == expected
        response = client.post("/v1/search", json={"query": "private:one"})
        assert response.status_code == 200 and not response.json()["results"]


@pytest.mark.parametrize("mode", ["legacy", "card"])
def test_old_visibility_updates_follow_storage_switch_and_preserve_user_label(
    service, mode
):
    start, _ = service
    with start(mode) as (client, _registry):
        key = "visibility:owner"
        old_sync(client, key, "invoice lookup")
        new_card(client, key, name=key, description="invoice lookup")
        path = f"/api/v2/agent-cards/{key}"
        client.put(
            path, json={"labels": {"user_visibility": ["protected"]}}
        ).raise_for_status()
        for operation in ("availability", "sync"):
            if operation == "availability":
                response = client.put(
                    f"/v1/workers/{key}/availability", json={"availability": "private"}
                )
            else:
                response = client.post(
                    f"/v1/workers/{key}/sync",
                    json={"name": key, "availability": "protected", "profile": {}},
                )
            assert response.status_code == 200, response.text
            expected = (
                "public"
                if mode == "legacy"
                else ("private" if operation == "availability" else "protected")
            )
            labels = client.get(path).json()["labels"]
            assert labels == {
                "visibility": [expected],
                "user_visibility": ["protected"],
            }
        before = client.get(path).json()["labels"]
        for state in ("offline", "online"):
            client.put(f"/v1/workers/{key}/{state}").raise_for_status()
            assert client.get(path).json()["labels"] == before
        client.post(
            f"/v1/workers/{key}/sync", json={"name": "Renamed", "profile": {}}
        ).raise_for_status()
        assert client.get(path).json()["labels"] == before
        # Missing user visibility is preserved as unknown for old-only imports.
        old_sync(client, "unlabeled:owner", "invoice lookup")
        if mode == "card":
            assert client.get("/api/v2/agent-cards/unlabeled:owner").json()[
                "labels"
            ] == {"visibility": ["public"]}


def test_v2_visibility_updates_old_reads_filters_and_fuse_without_changing_native_card(
    service,
):
    start, _ = service
    with start("card") as (client, registry):
        key = "native:visibility"
        path = f"/api/v2/agent-cards/{key}"
        card = {"name": "Invoice assistant", "description": "invoice lookup"}
        new_card(client, key, **card)
        client.put(f"/v1/workers/{key}/online").raise_for_status()
        # Only the old online operation adds a compatibility runtime state.
        stored = client.get(path).json()["card"]
        assert stored == {**card, "runtime_state": "online"}
        for value, visible in (
            ("public", True),
            ("private", False),
            ("protected", True),
        ):
            response = client.put(path, json={"labels": {"visibility": [value]}})
            assert response.status_code == 202, response.text
            registry.require("tc_card_search").updates.run_once()
            assert client.get(path).json()["card"] == stored
            assert client.get(f"/v1/workers/{key}").json()["availability"] == value
            batch = client.post("/v1/workers/batch", json={"worker_ids": [key]}).json()
            assert batch["data"][key]["availability"] == value
            worker = registry.require("worker_registry_store").get_by_id(key)
            assert worker.state.availability.value == value
            old = client.post("/v1/search", json={"query": key})
            assert old.status_code == 200, old.text
            assert bool(old.json()["results"]) is visible
            for route in ("search", "recommend"):
                new = client.post(
                    f"/api/v2/{route}",
                    json={"query": key, "filters": {"labels.visibility": [value]}},
                )
                assert new.status_code == 200, new.text
                assert [item["id"] for item in new.json()["items"]] == [key]


def test_v2_rejects_conflicting_visibility_and_keeps_legacy_card_fields_consistent(
    service,
):
    start, _ = service
    with start("card") as (client, registry):
        key = "imported:visibility"
        old_sync(client, key, "invoice lookup")
        path = f"/api/v2/agent-cards/{key}"
        before = client.get(path).json()
        response = client.put(
            path,
            json={
                "card": {"availability": "private"},
                "labels": {"visibility": ["public"]},
            },
        )
        assert response.status_code == 400, response.text
        assert client.get(path).json() == before
        response = client.put(path, json={"labels": {"visibility": ["private"]}})
        assert response.status_code == 202, response.text
        assert client.get(path).json()["card"]["availability"] == "private"
        response = client.put(path, json={"card": {"availability": "protected"}})
        assert response.status_code == 202, response.text
        registry.require("tc_card_search").updates.run_once()
        saved = client.get(path).json()
        assert saved["labels"] == {"visibility": ["protected"]}
        assert saved["card"]["availability"] == "protected"
