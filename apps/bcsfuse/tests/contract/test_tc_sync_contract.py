"""The surviving sparse-sync and lifecycle contract after removing draft state."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name

import pytest

from tests.contract.test_tc_catalog import runtime, sync  # noqa: F401


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
@pytest.mark.parametrize("activate", [None, False, True])
def test_activate_variants_update_one_current_content(runtime, prefix, activate):
    client, registry = runtime
    profile = {"profile_id": "legacy:current"}
    if activate is not None:
        profile["activate"] = activate
    for name in ("First", "Current"):
        response = client.post(
            f"{prefix}/workers/bot:owner/sync", json={"name": name, "profile": profile}
        )
        assert response.status_code == 200, response.text
        assert response.json()["profile_activated"] is True
    assert (
        registry.require("tc_catalog").resolve("bot:owner:legacy:current")
        == "bot:owner"
    )
    assert (
        client.get("/v1/workers/bot:owner/profiles").json()["items"][0]["display_name"]
        == "Current"
    )


@pytest.mark.parametrize("worker_type", ["human", "bot"])
def test_sync_preserves_worker_type_and_rejects_invalid_type_before_write(
    runtime, worker_type
):
    client, registry = runtime
    sync(client, type=worker_type)
    assert (
        registry.require("tc_catalog").require("bot:owner").card.source_card["type"]
        == worker_type
    )
    response = client.post(
        "/v1/workers/invalid/sync",
        json={"name": "Invalid", "type": "unknown", "profile": {}},
    )
    assert response.status_code == 422
    assert registry.require("tc_catalog").get("invalid") is None


@pytest.mark.parametrize("state", ["online", "offline"])
@pytest.mark.parametrize("runtime_field", [{}, {"runtime_state": None}])
def test_sparse_sync_preserves_existing_runtime_state(runtime, state, runtime_field):
    client, registry = runtime
    sync(client, runtime_state=state)
    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={"name": "Updated", "profile": {}, **runtime_field},
    )
    assert response.status_code == 200, response.text
    assert response.json()["runtime_state"] == state
    assert (
        registry.require("tc_catalog")
        .require("bot:owner")
        .card.source_card["runtime_state"]
        == state
    )


def test_replay_does_not_change_revision_and_config_conflicts_are_visible(runtime):
    client, registry = runtime
    sync(client)
    original = registry.require("tc_catalog").require("bot:owner").revision
    sync(client)
    assert registry.require("tc_catalog").require("bot:owner").revision == original
    client.put(
        "/v1/workers/bot:owner/config", json={"fusion_enable": True}
    ).raise_for_status()
    response = client.put(
        "/v1/workers/bot:owner/config",
        json={"fusion_enable": False, "version": original},
    )
    assert response.status_code == 409
    assert client.get("/v1/workers/bot:owner/config").json()["fusion_enable"] is True


def test_failed_doc_publication_does_not_claim_success(runtime, monkeypatch):
    client, registry = runtime

    def fail(text):
        raise RuntimeError("controlled embedding failure")

    monkeypatch.setattr(registry.require("embedding_provider"), "embed", fail)
    response = client.post(
        "/v1/workers/pending/sync", json={"name": "Pending", "profile": {}}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "INDEX_NOT_READY"
    assert registry.require("tc_catalog").get("pending") is not None


def test_all_missing_batch_has_the_old_empty_response(runtime):
    client, _ = runtime
    body = client.post("/v1/workers/batch", json={"worker_ids": ["missing"]}).json()
    assert body["data"] == {}
    assert body["workers"] == []
    assert body["not_found_ids"] == ["missing"]
    assert body["total"] == 0
