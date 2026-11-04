"""Shapes consumed by existing TC callers, through the composed application."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name

import pytest

from tests.contract.test_tc_catalog import runtime, sync  # noqa: F401


def test_worker_and_profile_reads_keep_flat_contract_and_diagnostic_items(runtime):
    client, _ = runtime
    sync(client)
    worker = client.get("/v1/workers/bot:owner").json()
    assert worker["id"] == "bot:owner"
    assert worker["name"] == "Database specialist"
    assert worker["runtime_state"] == "online"
    assert client.get("/v1/workers").json()["items"][0]["id"] == "bot:owner"
    profiles = client.get("/v1/workers/bot:owner/profiles").json()
    assert (
        profiles["items"][0]["profile_id"] == profiles["active_profile_id"] == "legacy"
    )
    assert profiles["items"][0]["is_active"] is True
    assert (
        profiles["items"][0]["contents"]["short_profile"] == "MySQL database migrations"
    )
    assert "profile" not in profiles["items"][0]["contents"]
    assert (
        client.get("/v1/workers/bot:owner/profiles/active").json()["profile_id"]
        == "legacy"
    )
    assert (
        client.get("/v1/workers/bot:owner/profiles/legacy").json()["worker_id"]
        == "bot:owner"
    )


def test_profile_and_worker_writes_update_single_current_card(runtime):
    client, registry = runtime
    sync(client)
    response = client.patch(
        "/v1/workers/bot:owner", json={"description": "Redis monitoring"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "Redis monitoring"
    changed = client.patch(
        "/v1/workers/bot:owner/profiles/legacy",
        json={
            "description": "Redis recovery",
            "skill_sets": [{"name": "redis", "description": "cache recovery"}],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["is_active"] is True
    assert changed.json()["skill_sets"][0]["name"] == "redis"
    row = registry.require("tc_catalog_repository").get("bot:owner")
    assert row.card.source_card["description"] == "Redis recovery"
    assert client.get("/v1/workers/bot:owner/profiles").json()["total"] == 1
    assert (
        client.put(
            "/v1/workers/bot:owner/profiles/another", json={"display_name": "Renamed"}
        ).status_code
        == 200
    )
    assert (
        registry.require("tc_catalog").profile_key(
            registry.require("tc_catalog").require("bot:owner")
        )
        == "bot:owner:legacy"
    )


def test_legacy_search_paths_use_current_retrieval_without_threshold(runtime):
    client, _ = runtime
    sync(client)
    response = client.post(
        "/api/v1/search", json={"query": "MySQL", "top_k": 5, "min_score": 0.99}
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["worker_id"] == "bot:owner"
    assert response.json()["results_count"] == 1
    response = client.post(
        "/v1/workers/profiles/search", json={"query": "MySQL", "min_score": 0.99}
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["profile_key"] == "bot:owner:legacy"
    active = client.get("/v1/workers/profiles/active-profiles")
    assert active.status_code == 200, active.text
    assert active.json()["profiles"][0]["profile_key"] == "bot:owner:legacy"


def test_batch_and_state_inputs_keep_boundary_validation(runtime):
    client, registry = runtime
    sync(client)
    for route in ("/v1/workers/batch", "/v1/workers/config/batch"):
        assert client.post(route, json={"worker_ids": []}).status_code == 422
        assert client.post(route, json={"worker_ids": ["a"] * 101}).status_code == 422
    invalid = client.put(
        "/v1/workers/bot:owner/availability", json={"availability": "bogus"}
    )
    assert invalid.status_code == 422
    assert (
        registry.require("tc_catalog")
        .require("bot:owner")
        .card.source_card["availability"]
        == "public"
    )
    assert client.get("/v1/workers?offset=-1").status_code == 422
    assert (
        client.post(
            "/api/v1/recommend",
            json={"question": "MySQL", "filters": {"unexpected": ["x"]}},
        ).status_code
        == 400
    )


def test_authentication_is_required_on_legacy_aliases(runtime):
    client, _ = runtime
    for method, path, body in (
        ("GET", "/v1/workers", None),
        ("POST", "/api/v1/recommend", {"question": "test"}),
        ("POST", "/api/v1/search", {"query": "test"}),
    ):
        result = client.request(
            method, path, json=body, headers={"Authorization": "Bearer invalid"}
        )
        assert result.status_code == 401


@pytest.fixture
def admin_mode(monkeypatch):
    monkeypatch.setenv("BCSFUSE_EXPOSE_ADMIN", "true")


@pytest.fixture
def admin_runtime(admin_mode, runtime):
    return runtime


def test_conditional_management_aliases_keep_current_content_and_disabled_analysis(
    admin_runtime,
):
    client, _registry = admin_runtime
    created = client.post(
        "/v1/admin/workers",
        json={
            "worker_id": "managed",
            "name": "Managed",
            "description": "Service support",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["runtime_state"] == "offline"
    assert created.json()["availability"] == "private"
    client.put(
        "/v1/admin/workers/managed/config", json={"fusion_enable": True}
    ).raise_for_status()
    batch = client.post(
        "/v1/admin/workers/config/batch",
        json={"updates": [{"worker_id": "managed", "fusion_enable": False}]},
    )
    assert batch.status_code == 200, batch.text
    assert batch.json()["updated"] == ["managed"]
    assert (
        client.get("/v1/workers/managed/profiles/quality").json()["profile_count"] == 1
    )
    assert client.get("/v1/workers/managed/profiles/default/quality").status_code == 200
    assert (
        client.get("/v1/workers/config/by-source?source=api").json()["workers"][0]["id"]
        == "managed"
    )
    assert (
        client.post("/v1/verify/batch", json={"worker_ids": ["managed"]}).status_code
        == 503
    )
    assert (
        client.post(
            "/v1/admin/workers/managed/profiles/default/analyze", json={}
        ).status_code
        == 503
    )
    client.delete("/v1/admin/workers/managed/profiles/default").raise_for_status()
    assert client.get("/v1/workers/managed").json()["description"] is None
