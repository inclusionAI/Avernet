"""Both OSS sync routes preserve runtime state on visibility-only updates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.models.worker import Availability, TrustLevel, Worker, WorkerIdentity, WorkerState, WorkerType
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
from src.infra.public.audit.in_memory_worker_audit_log_store import (
    InMemoryWorkerAuditLogStore,
)
from src.interfaces.api import worker_profile_parity_routes as routes
from src.interfaces.api.worker_profile_routes import analysis, common, vector_sync


@pytest.fixture
def sync_app(monkeypatch):
    from src.application.utils import drm_config_helper

    workers = InMemoryWorkerRegistryStore()
    runtime = InMemoryWorkerRuntimeStateStore()
    audit = InMemoryWorkerAuditLogStore()
    profiles = MagicMock()
    app = FastAPI()
    app.state.context = SimpleNamespace(registry={
        "worker_registry_store": workers,
        "worker_runtime_state_store": runtime,
        "worker_profile_content_store": profiles,
        "worker_audit_log_store": audit,
    })
    app.include_router(routes.api_router, prefix="/api/v1")
    app.include_router(routes.compat_router, prefix="/v1")
    app.include_router(routes.mgmt_router, prefix="/v1")
    monkeypatch.setattr(common, "_require_auth", lambda request: None)
    monkeypatch.setattr(vector_sync, "_sync_runtime_state_to_vector_store", MagicMock())
    availability_sync = MagicMock()
    monkeypatch.setattr(vector_sync, "_sync_availability_to_vector_store", availability_sync)
    monkeypatch.setattr(analysis, "_analyze_and_persist_async", AsyncMock())
    monkeypatch.setattr(
        drm_config_helper,
        "is_capability_verify_enabled",
        lambda: False,
    )
    with TestClient(app) as client:
        yield client, workers, runtime, profiles, availability_sync


def seed_worker(workers, runtime, state):
    workers.create(Worker(
        id="bot:owner", type=WorkerType.BOT,
        responsibilities=[], capabilities=[],
        identity=WorkerIdentity(name="Bot", handle="@bot:owner"),
        state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.UNVERIFIED, runtime_state=state),
    ))
    runtime.set_runtime_state("bot:owner", state, updated_by="user-publication")


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
@pytest.mark.parametrize("state", [WorkerRuntimeState.OFFLINE, WorkerRuntimeState.ONLINE])
@pytest.mark.parametrize("availability", ["public", "protected", "private"])
def test_visibility_sync_preserves_runtime_and_still_updates_profile(sync_app, monkeypatch, prefix, state, availability):
    client, workers, runtime, profiles, availability_sync = sync_app
    seed_worker(workers, runtime, state)
    runtime_write = MagicMock(wraps=runtime.set_runtime_state)
    monkeypatch.setattr(runtime, "set_runtime_state", runtime_write)
    response = client.post(f"{prefix}/workers/bot:owner/sync", json={
        "name": "Updated Bot", "availability": availability,
        "profile": {"profile_id": "default", "contents": {"profile": "Updated profile"}},
    })
    assert response.status_code == 200, response.text
    assert response.json()["runtime_state"] == state.value
    runtime_write.assert_not_called()
    assert runtime.get_runtime_state("bot:owner") == state
    assert workers.get_by_id("bot:owner").state.runtime_state == state
    assert workers.get_by_id("bot:owner").state.availability.value == availability
    profiles.upsert_profile.assert_called_once()
    availability_sync.assert_called_with("bot:owner", availability)


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
@pytest.mark.parametrize("explicit", [None, "offline", "online"])
def test_new_worker_defaults_online_and_explicit_runtime_is_honored(sync_app, prefix, explicit):
    client, workers, runtime, _, _ = sync_app
    payload = {"name": "New Bot", "availability": "private", "profile": {"profile_id": "default"}}
    if explicit is not None:
        payload["runtime_state"] = explicit
    response = client.post(f"{prefix}/workers/bot:owner/sync", json=payload)
    assert response.status_code == 200, response.text
    expected = explicit or "online"
    assert response.json()["created"] is True
    assert response.json()["runtime_state"] == expected
    assert workers.get_by_id("bot:owner").state.runtime_state.value == expected
    # Sync's in-memory adapter retains the mapping supplied by the handler.
    assert runtime.get_runtime_state("bot:owner")["state"] == expected
    again = client.post(f"{prefix}/workers/bot:owner/sync", json={"name": "Still here", "profile": {"profile_id": "default"}})
    assert again.status_code == 200, again.text
    assert again.json()["runtime_state"] == expected


def test_new_worker_remains_active_when_follow_up_lifecycle_write_fails(
    sync_app,
    monkeypatch,
):
    client, workers, _, _, _ = sync_app

    def fail_lifecycle_update(*_args, **_kwargs):
        raise RuntimeError("lifecycle update conflict")

    monkeypatch.setattr(
        workers,
        "update_lifecycle_state",
        fail_lifecycle_update,
    )

    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "Active Bot",
            "profile": {"profile_id": "default"},
        },
    )

    assert response.status_code == 200, response.text
    assert workers.get_by_id("bot:owner").lifecycle_state == WorkerLifecycleState.ACTIVE


@pytest.mark.parametrize("explicit", ["offline", "online"])
def test_explicit_runtime_updates_existing_worker(sync_app, explicit):
    client, workers, runtime, _, _ = sync_app
    seed_worker(workers, runtime, WorkerRuntimeState.OFFLINE if explicit == "online" else WorkerRuntimeState.ONLINE)
    response = client.post("/v1/workers/bot:owner/sync", json={"name": "Bot", "runtime_state": explicit, "profile": {"profile_id": "default"}})
    assert response.status_code == 200, response.text
    assert response.json()["runtime_state"] == explicit
    assert workers.get_by_id("bot:owner").state.runtime_state.value == explicit


def test_runtime_mirror_failure_rolls_back_sync_state(sync_app, monkeypatch):
    client, workers, runtime, _, _ = sync_app
    seed_worker(workers, runtime, WorkerRuntimeState.OFFLINE)
    original_update = workers.update
    update_calls = 0

    def fail_runtime_mirror_update(worker):
        nonlocal update_calls
        update_calls += 1
        if update_calls == 2:
            raise RuntimeError("worker runtime mirror write failed")
        return original_update(worker)

    monkeypatch.setattr(workers, "update", fail_runtime_mirror_update)

    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "Bot",
            "runtime_state": "online",
            "profile": {"profile_id": "default"},
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "SET_RUNTIME_STATE_FAILED"
    assert runtime.get_runtime_state("bot:owner") == WorkerRuntimeState.OFFLINE
    assert (
        workers.get_by_id("bot:owner").state.runtime_state
        == WorkerRuntimeState.OFFLINE
    )


def test_null_runtime_with_missing_runtime_record_preserves_registry_state(sync_app):
    client, workers, runtime, _, _ = sync_app
    seed_worker(workers, runtime, WorkerRuntimeState.OFFLINE)
    runtime._states.clear()
    response = client.post("/v1/workers/bot:owner/sync", json={"name": "Bot", "runtime_state": None, "profile": {"profile_id": "default"}})
    assert response.status_code == 200, response.text
    assert response.json()["runtime_state"] == "offline"
    assert runtime.get_runtime_state("bot:owner") is None
    assert workers.get_by_id("bot:owner").state.runtime_state == WorkerRuntimeState.OFFLINE


def test_new_worker_sync_preserves_registry_contract_fields(sync_app):
    client, workers, _, _, _ = sync_app

    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "Contract Bot",
            "responsibilities": ["review changes"],
            "domains": ["engineering"],
            "capabilities": [{"name": "code_review", "level": "expert"}],
            "skills": [
                {
                    "name": "review",
                    "source": "builtin",
                    "trust_level": "guarded",
                },
            ],
            "trust_level": "trusted",
            "profile": {"profile_id": "default"},
        },
    )

    assert response.status_code == 200, response.text
    worker = workers.get_by_id("bot:owner")
    assert worker.responsibilities == ["review changes"]
    assert worker.domains == ["engineering"]
    assert [capability.name for capability in worker.capabilities] == ["code_review"]
    assert [skill.name for skill in worker.skills] == ["review"]
    assert worker.state.trust_level == TrustLevel.TRUSTED


def test_existing_worker_sync_replaces_mutable_registry_fields(sync_app):
    client, workers, runtime, _, _ = sync_app
    seed_worker(workers, runtime, WorkerRuntimeState.ONLINE)
    existing = workers.get_by_id("bot:owner")
    existing.responsibilities = ["old responsibility"]
    existing.domains = ["old-domain"]
    existing.active_profile_key = "bot:owner:old"
    workers.update(existing)

    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "Updated Bot",
            "responsibilities": [],
            "domains": [],
            "profile": {"profile_id": "default"},
        },
    )

    assert response.status_code == 200, response.text
    worker = workers.get_by_id("bot:owner")
    assert worker.responsibilities == []
    assert worker.domains == []


def test_sync_keeps_internal_capability_verification_side_effects(
    sync_app,
    monkeypatch,
):
    from src.application.utils import drm_config_helper
    from src.domain import events
    from src.interfaces.api.dependencies import fusion_dependencies

    client, workers, _, _, _ = sync_app
    verify_service = SimpleNamespace(_running=False, start=AsyncMock())
    event_bus = MagicMock()
    monkeypatch.setattr(
        drm_config_helper,
        "is_capability_verify_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        fusion_dependencies,
        "get_capability_verify_service",
        lambda: verify_service,
    )
    monkeypatch.setattr(events, "get_event_bus", lambda: event_bus)

    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "Verification Bot",
            "trust_level": "trusted",
            "profile": {"profile_id": "default"},
        },
    )

    assert response.status_code == 200, response.text
    verify_service.start.assert_awaited_once()
    assert workers.get_by_id("bot:owner").state.trust_level == TrustLevel.UNVERIFIED
    published = event_bus.publish.call_args.args[0]
    assert isinstance(published, events.WorkerProfileCreatedEvent)
    assert published.worker_id == "bot:owner"


def test_profile_patch_rebuilds_vectors_when_embedding_content_changes(
    sync_app,
    monkeypatch,
):
    from src.interfaces.api.dependencies import fusion_dependencies

    client, _, _, profiles, _ = sync_app
    profiles.get_profile.return_value = {
        "worker_id": "bot:owner",
        "profile_id": "default",
        "display_name": "Bot",
        "soul_md": "old soul",
        "contents": {},
        "skill_sets": [],
        "metadata": {},
        "content_type": "api",
        "is_active": True,
        "version": 1,
    }
    rebuild = MagicMock(return_value=True)
    monkeypatch.setattr(
        fusion_dependencies,
        "_build_vector_index_for_worker",
        rebuild,
    )
    monkeypatch.delenv("ENABLE_EAGER_INDEXING", raising=False)

    response = client.patch(
        "/v1/workers/bot:owner/profiles/default",
        json={"contents": {"ecb_summary": {"capability": "code review"}}},
    )

    assert response.status_code == 200, response.text
    rebuild.assert_called_once_with("bot:owner")


def test_worker_config_update_preserves_audit_side_effect(sync_app):
    from src.domain.models.worker_audit_log import WorkerAuditAction

    client, workers, runtime, _, _ = sync_app
    seed_worker(workers, runtime, WorkerRuntimeState.ONLINE)

    response = client.put(
        "/v1/workers/bot:owner/config?updated_by=gateway",
        json={"fusion_enable": True},
    )

    assert response.status_code == 200, response.text
    audit_store = client.app.state.context.registry["worker_audit_log_store"]
    logs = audit_store.list_logs(worker_id="bot:owner")
    assert len(logs) == 1
    assert logs[0].action == WorkerAuditAction.CONFIG_CHANGED
    assert logs[0].old_value == "False"
    assert logs[0].new_value == "True"
    assert logs[0].performed_by == "gateway"


def test_sync_passes_complete_profile_snapshot_to_analysis(sync_app):
    client, _, _, _, _ = sync_app
    analyzer_task = analysis._analyze_and_persist_async
    analyzer_task.reset_mock()

    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "墨韵研发2号",
            "description": "研发协作机器人",
            "availability": "protected",
            "sync_llm": True,
            "profile": {
                "profile_id": "default",
                "display_name": "墨韵研发2号",
                "skill_sets": [
                    {"name": "code_review", "description": "代码评审"},
                ],
            },
        },
    )

    assert response.status_code == 200, response.text
    snapshot = analyzer_task.await_args.kwargs["profile_data"]
    assert snapshot["display_name"] == "墨韵研发2号"
    assert snapshot["description"] == "研发协作机器人"
    assert snapshot["skill_sets"] == [
        {"name": "code_review", "description": "代码评审"},
    ]


def test_sync_uses_worker_name_when_profile_display_name_is_omitted(sync_app):
    client, _, _, profiles, _ = sync_app

    response = client.post(
        "/v1/workers/bot:owner/sync",
        json={
            "name": "墨韵研发2号",
            "profile": {"profile_id": "default"},
        },
    )

    assert response.status_code == 200, response.text
    profile_data = profiles.upsert_profile.call_args.args[2]
    assert profile_data["display_name"] == "墨韵研发2号"


@pytest.mark.asyncio
async def test_background_analysis_preserves_profile_identity_and_skills(monkeypatch):
    import asyncio

    from src.application.services.profile_analyzer_service import ProfileAnalysisResult
    from src.interfaces.api import profile_routes
    from src.interfaces.api.dependencies import fusion_dependencies

    analyzed = []
    analyzer = MagicMock()

    def analyze(content):
        analyzed.append(content)
        return ProfileAnalysisResult(
            semantic_profile="有效画像",
            capability_tags=["代码评审"],
            llm_success=True,
            short_profile="研发协作",
        )

    analyzer.analyze.side_effect = analyze
    profile_store = MagicMock()
    running_loop = asyncio.get_running_loop()
    used_executors = []

    class CapturingLoop:
        def run_in_executor(self, executor, func, *args):
            used_executors.append(executor)
            return running_loop.run_in_executor(None, func, *args)

    context = SimpleNamespace(
        registry={"worker_profile_content_store": profile_store},
    )
    monkeypatch.setattr(analysis, "_get_profile_analyzer", lambda: analyzer)
    monkeypatch.setattr(fusion_dependencies, "get_app_context", lambda: context)
    monkeypatch.setattr(
        fusion_dependencies,
        "_build_vector_index_for_worker",
        lambda worker_id: True,
    )
    monkeypatch.setattr(profile_routes, "_trigger_index_sync", lambda worker_id: None)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: CapturingLoop())

    await analysis._analyze_and_persist_async(
        worker_id="20261008_j8u4z0l7:334018",
        profile_id="default",
        profile_data={
            "worker_id": "20261008_j8u4z0l7:334018",
            "profile_id": "default",
            "display_name": "墨韵研发2号",
            "description": "研发协作机器人",
            "soul_md": "负责研发协作",
            "contents": {},
            "skill_sets": [
                {"name": "code_review", "description": "代码评审"},
            ],
            "metadata": {"source": "bcs"},
        },
        max_retries=0,
    )

    assert len(analyzed) == 1
    content = analyzed[0]
    assert content.display_name == "墨韵研发2号"
    assert content.description == "研发协作机器人"
    assert [skill.name for skill in content.skill_sets] == ["code_review"]
    assert used_executors == [analysis._get_llm_analysis_executor()]
    persisted = profile_store.upsert_profile.call_args.args[2]
    assert persisted["contents"]["capabilities"] == ["代码评审"]
