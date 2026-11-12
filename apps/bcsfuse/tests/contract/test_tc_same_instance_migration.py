"""TC V1 and V2 share the same Card and search service."""

import sqlite3
import time
from contextlib import contextmanager

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
    def start():
        registry = build_opensource_provider_registry("test")
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


def test_v1_and_v2_read_and_write_the_same_card(service):
    start, _ = service
    with start() as (client, registry):
        assert client.get("/ready").json()["v1_storage"] == "card"
        assert registry.require("tc_search") is registry.require("tc_card_search")
        new_card(
            client,
            "same:owner",
            name="Test Worker",
            description="CARD_UPDATED",
            profile_id="current",
        )
        assert (
            client.get("/v1/workers/same:owner").json()["description"] == "CARD_UPDATED"
        )
        client.put(
            "/v1/workers/same:owner/config", json={"fusion_enable": True}
        ).raise_for_status()
        assert (
            client.get("/api/v2/workers/same:owner/config").json()["fusion_enable"]
            is True
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
        fused = client.post(
            "/api/v1/groups/same-group/fuse",
            json={"question": "Recovery", "participants": ["same:owner:current"]},
        )
        assert fused.status_code == 200 and "CARD_FINAL" in fused.text, fused.text
        assert client.delete("/v1/workers/same:owner").status_code == 200
        assert client.get("/api/v2/agent-cards/same:owner").status_code == 404


def test_api_migration_import_is_visible_to_v1(service):
    import json

    from app.tc.migration import migrate_rows
    from app.tc.migration_api import ApiMigrationRepository

    start, _path = service
    with start() as (client, _registry):
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
            client.get("/v1/workers/import:owner").json()["description"]
            == "migrated description"
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
        assert client.get("/v1/workers/import:owner").status_code == 404


def test_card_mode_needs_no_old_worker_or_vector_tables(service):
    start, path = service
    with start() as (client, _registry):
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


def test_native_search_projects_tc_name_and_description(service):
    start, _path = service
    with start() as (client, _registry):
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
    with start() as (client, registry):
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


def test_v1_visibility_updates_preserve_user_label(service):
    start, _ = service
    with start() as (client, _registry):
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
            expected = "private" if operation == "availability" else "protected"
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
        assert client.get("/api/v2/agent-cards/unlabeled:owner").json()["labels"] == {
            "visibility": ["public"]
        }


def test_v2_visibility_updates_old_reads_filters_and_fuse_without_changing_native_card(
    service,
):
    start, _ = service
    with start() as (client, registry):
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
    with start() as (client, registry):
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
