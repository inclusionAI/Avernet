"""TC mapping is opt-in; builds and selection cannot overwrite the legacy index."""
# ruff: noqa: F811 -- imported fixture

from time import monotonic, sleep

import pytest

from tests.contract.test_tc_catalog import runtime, sync  # noqa: F401

PATH = "/api/v2/agent-cards/bot:owner"


def test_tc_mapping_update_labels_clear_and_legacy_config_preservation(runtime):
    client, registry = runtime
    sync(client)
    updates = registry.require("tc_card_search").updates
    repo = registry.require("tc_catalog_repository")
    old_key = updates.index_key
    original = updates.documents.load_by_id("bot:owner")
    card = repo.read_card("bot:owner").card.source_card
    rule = {
        "search_text_template": "mapped {name}",
        "rerank_text_template": "{description}",
    }
    response = client.put(PATH, json={"document_mapping": rule})
    assert response.status_code == 202, response.text
    updates.run_once()
    doc = updates.documents.load_by_id("bot:owner")
    assert doc.point.payload["search_text"] == "mapped Database specialist"
    assert doc.point.payload["rerank_text"] == card["description"]
    assert repo.read_card("bot:owner").card.source_card == card
    assert updates.index_key == old_key

    vector = doc.point.vector
    assert (
        client.put(PATH, json={"labels": {"user_visibility": ["public"]}}).status_code
        == 202
    )
    updates.run_once()
    doc = updates.documents.load_by_id("bot:owner")
    assert doc.point.vector == vector
    assert doc.filters["runtime_state"] == ("online",)
    assert doc.filters["availability"] == ("public",)
    assert doc.filters["labels.user_visibility"] == ("public",)
    assert (
        client.put(
            "/api/v2/workers/bot:owner/config", json={"config": {"fusion_enable": True}}
        ).status_code
        == 200
    )
    assert repo.read_card("bot:owner").config["document_mapping"] == rule
    assert client.put(PATH, json={"document_mapping": None}).status_code == 202
    updates.run_once()
    assert (
        updates.documents.load_by_id("bot:owner").point.payload["search_text"]
        == original.point.payload["search_text"]
    )
    assert repo.read_card("bot:owner").config["fusion_enable"] is True


def test_tc_invalid_mapping_and_card_edit_are_rejected_before_persistence(runtime):
    client, registry = runtime
    sync(client)
    assert (
        client.put(
            PATH,
            json={
                "card": {"custom": "ok"},
                "document_mapping": {"search_text_template": "{custom}"},
            },
        ).status_code
        == 202
    )
    repo = registry.require("tc_catalog_repository")
    before = repo.read_card("bot:owner")
    response = client.put(PATH, json={"card": {"custom": {"bad": 1}}})
    assert response.status_code == 422, response.text
    assert repo.read_card("bot:owner").revision == before.revision
    response = client.put(
        PATH, json={"document_mapping": {"filter_fields": {"availability": "name"}}}
    )
    assert response.status_code == 422
    assert repo.read_card("bot:owner").config == before.config
    # Legacy configuration writes must not bypass mapping validation/revisioning.
    with pytest.raises(ValueError, match="Card endpoint"):
        registry.require("tc_card_catalog").save_config(
            "bot:owner", {"document_mapping": {"search_text_template": "{missing}"}}
        )
    with pytest.raises(ValueError):
        registry.require("tc_card_catalog").update("bot:owner", {"custom": {"bad": 1}})
    assert repo.read_card("bot:owner").revision == before.revision


def test_tc_build_uses_isolated_docs_and_preserves_current_index(runtime):
    client, registry = runtime
    sync(client)
    updates = registry.require("tc_card_search").updates
    builds = registry.require("tc_index_builds")
    repo = registry.require("tc_catalog_repository")
    before = updates.documents.load_by_id("bot:owner")
    before_key = updates.index_key
    model, dimension = updates.embedding_key.rsplit("/", 1)
    body = {
        "embedding_model": model,
        "embedding_dimension": int(dimension),
        "document_mapping": {
            "search_text_template": "new {name}",
            "filter_fields": {"topic": "domains[]"},
        },
        "validation_card_ids": ["bot:owner"],
    }
    response = client.put("/api/v2/index-builds/tc-next", json=body)
    assert response.status_code == 202, response.text
    deadline = monotonic() + 10
    while (
        builds.status("tc-next").status in {"pending", "running"}
        and monotonic() < deadline
    ):
        sleep(0.02)
    assert builds.status("tc-next").status == "completed"
    new_doc = repo.index_documents("tc-next").load_by_id("bot:owner")
    assert new_doc.point.payload["search_text"] == "new Database specialist"
    assert new_doc.filters["availability"] == ("public",)
    assert updates.documents.load_by_id("bot:owner") == before
    assert updates.index_key == before_key
    assert client.get("/api/v2/index-version").json()["active_index"] == "default"
    for protected in ("default", before_key, "a" * 64):
        assert (
            client.put(f"/api/v2/index-builds/{protected}", json=body).status_code
            == 409
        )
    response = client.put(
        "/api/v2/index-builds/bad-policy",
        json={**body, "document_mapping": {"filter_fields": {"runtime_state": "name"}}},
    )
    assert response.status_code == 422
    assert builds.builds.get("bad-policy") is None
    assert builds.select("tc-next").index_key == "tc-next"
    with pytest.raises(ValueError):
        builds.select("missing")


def test_selected_generation_restart_and_rollback_leave_old_index_intact(
    tmp_path, monkeypatch
):
    import json

    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.opensource import build_opensource_provider_registry
    from fastapi.testclient import TestClient

    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "cards.sqlite"))
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("index_version", "")

    def application():
        registry = build_opensource_provider_registry("test")
        app = create_bcsfuse_app(
            ApplicationContext(
                mode="test", startup_profile="opensource", registry=registry
            )
        )
        return TestClient(app, headers={"Authorization": "Bearer test-token"}), registry

    old, old_registry = application()
    with old:
        sync(old, domains=["database"])
        old_updates = old_registry.require("tc_card_search").updates
        old_doc = old_updates.documents.load_by_id("bot:owner")
        old_points = old.get(PATH + "/index-status").json()["qdrant"]
        old_key = old_updates.index_key
        model, dimension = old_updates.embedding_key.rsplit("/", 1)
        builds = old_registry.require("tc_index_builds")
        builds.create(
            "tc-next",
            model + "-new",
            int(dimension) // 2,
            document_mapping={
                "search_text_template": "generation {name}",
                "filter_fields": {"topic": "domains[]"},
            },
        )
        builds.run_once(build_id="tc-next")
        assert builds.status("tc-next").status == "completed"
        monkeypatch.setenv("index_version", json.dumps({"active_index": "tc-next"}))
        assert old.get("/api/v2/index-version").json()["restart_required"]
        selected, new_registry = application()
        with selected:
            status = selected.get("/api/v2/index-version").json()
            assert status["index_key"] == status["active_index"] == "tc-next"
            assert not status["restart_required"]
            assert selected.get("/ready").status_code == 200
            assert (
                new_registry.require("tc_card_search").embedding.dimension
                == int(dimension) // 2
            )
            response = selected.post(
                "/api/v2/search",
                json={"query": "Database", "filters": {"topic": ["database"]}},
            )
            assert response.status_code == 200, response.text
            assert response.json()["items"][0]["id"] == "bot:owner"
            assert (
                new_registry.require("tc_card_search").updates.index
                is not old_updates.index
            )
            assert old_updates.documents.load_by_id("bot:owner") == old_doc
            assert old.get("/api/v2/index-version").json()["index_key"] == old_key
            old_result = old.post("/api/v2/search", json={"query": "Database"})
            assert old_result.status_code == 200
            assert old_result.json()["items"][0]["id"] == "bot:owner"
            assert old.get(PATH + "/index-status").json()["qdrant"] == old_points
        monkeypatch.setenv("index_version", "")
        rollback, _ = application()
        with rollback:
            assert rollback.get("/api/v2/index-version").json()["index_key"] == old_key
            assert old_updates.documents.load_by_id("bot:owner") == old_doc
        monkeypatch.setenv("index_version", json.dumps({"active_index": "missing"}))
        invalid, _ = application()
        with pytest.raises(ValueError, match="incomplete"), invalid:
            pass
        assert old_updates.documents.load_by_id("bot:owner") == old_doc


def test_failed_build_cannot_be_selected_or_change_serving_documents(runtime):
    client, registry = runtime
    sync(client)
    updates = registry.require("tc_card_search").updates
    before = updates.documents.load_by_id("bot:owner")
    builds = registry.require("tc_index_builds")
    model, dimension = updates.embedding_key.rsplit("/", 1)
    # Syntax is valid, but this Card has no value for the required search text.
    builds.create(
        "tc-failed",
        model,
        int(dimension),
        document_mapping={"search_text_template": "{missing}"},
    )
    builds.run_once(build_id="tc-failed")
    assert builds.status("tc-failed").status == "failed"
    with pytest.raises(ValueError, match="incomplete"):
        builds.select("tc-failed")
    assert updates.documents.load_by_id("bot:owner") == before
    assert client.get("/api/v2/index-version").json()["active_index"] == "default"
    assert client.post("/api/v2/search", json={"query": "Database"}).status_code == 200


def test_profile_defaults_survive_mapping_and_templates_read_raw_card(
    runtime, monkeypatch
):
    client, registry = runtime
    sync(client, description="raw description")
    updates = registry.require("tc_card_search").updates
    monkeypatch.setattr(updates, "_prepare_profile", lambda row: "derived profile")
    updates.rebuild("bot:owner", updates.index_key)
    original = updates.documents.load_by_id("bot:owner").point.payload["search_text"]
    assert original == "Database specialist\nderived profile"
    assert (
        client.put(
            PATH, json={"document_mapping": {"rerank_text_template": "{description}"}}
        ).status_code
        == 202
    )
    updates.run_once()
    payload = updates.documents.load_by_id("bot:owner").point.payload
    assert payload["search_text"] == original
    assert payload["rerank_text"] == "raw description"
    assert client.get(PATH).json()["card"]["description"] == "raw description"
    assert client.put(PATH, json={"document_mapping": None}).status_code == 202
    updates.run_once()
    assert (
        updates.documents.load_by_id("bot:owner").point.payload["search_text"]
        == original
    )
