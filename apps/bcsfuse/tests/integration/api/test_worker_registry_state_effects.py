"""TC state changes are durable and affect current Fuse retrieval."""

import os

from app.adapters.http.dependencies import fusion_dependencies as dependencies
from app.domain.models.retrieval_mode import RetrievalMode
from search_core.providers.sql_catalog import sqlite_catalog_repository

from tests.contract.test_tc_catalog import runtime as tc_runtime  # noqa: F401
from tests.contract.test_tc_catalog import sync


def test_create_worker_defaults_to_offline(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    response = client.post("/v1/workers", json={"id": "bot:owner", "name": "State test"})
    assert response.status_code == 201
    assert client.get("/v1/workers/bot:owner").json()["runtime_state"] == "offline"


def test_online_state_enables_current_fuse_candidates(tc_runtime):  # noqa: F811
    client, registry = tc_runtime
    sync(client, runtime_state="offline")
    service = dependencies.get_candidate_recommendation_service()
    assert service.recommend("MySQL").recommendations == []
    client.put("/v1/workers/bot:owner/online").raise_for_status()
    assert registry.require("worker_runtime_state_store").get_runtime_state("bot:owner") == "online"
    assert [candidate.worker_id for candidate in service.recommend("MySQL").recommendations] == ["bot:owner"]


def test_offline_state_removes_current_fuse_profiles_without_deleting_worker(tc_runtime):  # noqa: F811
    client, _registry = tc_runtime
    sync(client)
    service = dependencies.get_profile_retrieval_service()
    assert service.retrieve("MySQL", RetrievalMode.EXPERT_DIAGNOSIS).results
    client.put("/v1/workers/bot:owner/offline").raise_for_status()
    assert service.retrieve("MySQL", RetrievalMode.EXPERT_DIAGNOSIS).results == []
    assert client.get("/v1/workers/bot:owner").status_code == 200


def test_state_survives_a_fresh_repository_read(tc_runtime):  # noqa: F811
    client, registry = tc_runtime
    sync(client)
    client.put("/v1/workers/bot:owner/offline").raise_for_status()
    repository = registry.require("tc_catalog_repository")
    reader = sqlite_catalog_repository(
        os.environ["WORKER_REGISTRY_SQLITE_DB_PATH"], environment=repository.environment,
        table=repository.table, document_table=repository.document_table,
    )
    assert reader.get("bot:owner").card.source_card["runtime_state"] == "offline"
