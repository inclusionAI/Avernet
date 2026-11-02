"""P0 contract tests for profile activation indexing capacity."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.bootstrap.profile_activation_index import (
    ProfileActivationIndexCoordinator,
)
from app.bootstrap import profile_activation_index
from app.adapters.http.dependencies import fusion_dependencies
from app.domain.models.profile_fragment import ProfileFragment
from app.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
from app.infra.config.feature_flags import FeatureFlags


class RecordingEmbeddingProvider:
    def __init__(self, *, fail_texts: set[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_texts = fail_texts or set()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if any(text in self.fail_texts for text in texts):
            raise RuntimeError("embedding failed")
        return [[float(len(text))] for text in texts]


class Fragment(ProfileFragment):
    def __init__(self, fragment_id: str, text: str) -> None:
        super().__init__(fragment_type=fragment_id, content=text)



class Decomposer:
    def __init__(self, fragments_by_profile: dict[str, list[Fragment]]) -> None:
        self.fragments_by_profile = fragments_by_profile

    def decompose(self, profile):
        return self.fragments_by_profile[profile.profile_key]


def make_profile(worker_id: str, title: str):
    from app.domain.models.worker_profile import ProfileType, WorkerProfile

    return WorkerProfile(
        staff_id=worker_id,
        profile_id="default",
        profile_type=ProfileType.DEFAULT,
        source_root="api",
        searchable_text=title,
        short_profile=title,
    )


@pytest.fixture(autouse=True)
def enable_profile_index(monkeypatch):
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        lambda: True,
    )


def test_indexer_deduplicates_repeated_texts_in_one_batch():
    provider = RecordingEmbeddingProvider()
    store = MagicMock()
    indexer = ProfileEmbeddingIndexer(provider, store, batch_size=8)
    profile_a = make_profile("worker-a", "Shared title")
    profile_b = make_profile("worker-b", "Shared title")
    indexer._fragment_decomposer = Decomposer({
        "worker-a:default": [Fragment("full", "shared embedding text")],
        "worker-b:default": [Fragment("full", "shared embedding text")],
    })

    result = indexer.build_index([profile_a, profile_b])

    assert result.failed_count == 0
    assert result.indexed_count == 2
    assert len(provider.calls) == 1
    # Each full fragment contains the same trailing text. The indexer must pass
    # unique texts to the model provider while retaining per-profile metadata.
    assert provider.calls[0] == ["shared embedding text"]
    assert len(store.upsert_embeddings.call_args.args[0]) == 2


def test_indexer_batches_profiles_into_one_model_request(monkeypatch):
    provider = RecordingEmbeddingProvider()
    store = MagicMock()
    profiles = [make_profile(f"worker-{i}", f"Profile {i}") for i in range(4)]
    fragments = {
        profile.profile_key: [
            Fragment("full", f"worker_id: {profile.staff_id}\nProfile {profile.profile_key}")
        ]
        for profile in profiles
    }
    indexer = ProfileEmbeddingIndexer(provider, store, batch_size=8)
    indexer._fragment_decomposer = Decomposer(fragments)

    result = indexer.build_index(profiles)

    assert result.failed_count == 0
    assert result.indexed_count == 4
    assert len(provider.calls) == 1
    assert len(provider.calls[0]) == 4
    assert store.upsert_embeddings.call_count >= 1


def test_one_profile_failure_does_not_suppress_successful_profiles():
    provider = RecordingEmbeddingProvider()
    store = MagicMock()
    good_profile = make_profile("worker-good", "Good profile")
    bad_profile = make_profile("worker-bad", "Bad profile")
    indexer = ProfileEmbeddingIndexer(provider, store, batch_size=8)
    indexer._fragment_decomposer = Decomposer({
        "worker-good:default": [Fragment("full", "worker_id: worker-good")],
        "worker-bad:default": [],
    })

    result = indexer.build_index([good_profile, bad_profile])

    assert result.indexed_count == 1
    assert result.failed_count == 1
    assert result.indexed_profile_keys == ["worker-good:default"]
    assert result.failed_profile_keys == ["worker-bad:default"]
    assert len(store.upsert_embeddings.call_args.args[0]) == 1


def prepare_activation_environment(monkeypatch, profiles: dict[str, tuple[str, str]]):
    """Return coordinator inputs and a content store containing each profile."""
    from app.infra.public.stores.in_memory_worker_profile_content_store import (
        InMemoryWorkerProfileContentStore,
    )

    content_store = InMemoryWorkerProfileContentStore()
    workers: dict[str, SimpleNamespace] = {}
    for worker_id, (profile_id, title) in profiles.items():
        content_store.upsert_profile(worker_id, profile_id, {
            "worker_id": worker_id,
            "profile_id": profile_id,
            "soul_md": title,
        })
        workers[worker_id] = SimpleNamespace(
            id=worker_id,
            state=SimpleNamespace(availability=SimpleNamespace(value="public")),
        )

    registry = MagicMock()
    registry.get = lambda key: {
        "worker_profile_content_store": content_store,
        "worker_runtime_state_store": None,
    }.get(key)
    runtime_store = MagicMock()
    runtime_store.get_runtime_state.return_value = None
    registry.get = lambda key: {
        "worker_profile_content_store": content_store,
        "worker_runtime_state_store": runtime_store,
    }.get(key)

    provider = RecordingEmbeddingProvider()
    vector_store = MagicMock()
    monkeypatch.setattr(fusion_dependencies, "_get_embedding_generator", lambda: provider)
    monkeypatch.setattr(fusion_dependencies, "_get_profile_embedding_store", lambda: vector_store)
    return content_store, registry, workers, provider, vector_store


def test_concurrent_activations_coalesce_into_one_model_call(monkeypatch):
    profiles = {
        f"worker-{i}": ("default", f"Concurrent profile {i}")
        for i in range(4)
    }
    _content, registry, workers, provider, vector_store = prepare_activation_environment(monkeypatch, profiles)
    coordinator = ProfileActivationIndexCoordinator(
        coalesce_seconds=0.05,
        batch_size=32,
    )
    start = threading.Barrier(len(profiles))
    errors: dict[str, BaseException | None] = {}

    def refresh(worker_id: str) -> None:
        try:
            start.wait(timeout=2)
            coordinator.refresh(registry, workers[worker_id], "default")
            errors[worker_id] = None
        except BaseException as error:  # noqa: BLE001 - propagated to test result
            errors[worker_id] = error

    threads = []
    for worker_id in profiles:
        thread = threading.Thread(target=refresh, args=(worker_id,))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join(timeout=5)

    assert errors == {worker_id: None for worker_id in profiles}
    assert len(provider.calls) == 1
    assert len(provider.calls[0]) == len(profiles)
    assert vector_store.upsert_embeddings.call_count == 1
    assert vector_store.delete_stale_fragments.call_count == len(profiles)


def test_activation_route_runs_index_refresh_in_threadpool(monkeypatch):
    """The production activation route must not run indexing on its event loop."""

    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.bootstrap import oss_worker_lifecycle_routes

    refresh_calls: list[tuple] = []

    async def fake_run_in_threadpool(func, *args):
        refresh_calls.append((func, args))
        return None

    monkeypatch.setattr(oss_worker_lifecycle_routes, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(oss_worker_lifecycle_routes, "require_oss_auth", lambda request: None)
    monkeypatch.setattr(oss_worker_lifecycle_routes, "_ensure_writable_index", lambda request: None)

    profile_store = MagicMock()
    worker_store = MagicMock()
    binding_store = MagicMock()
    registry = MagicMock()
    registry.get = lambda key: {
        "worker_profile_content_store": profile_store,
        "worker_profile_binding_store": binding_store,
        "worker_registry_store": worker_store,
        "worker_runtime_state_store": MagicMock(),
    }.get(key)

    worker = MagicMock()
    worker.active_profile_key = "worker-1:default"
    worker.version = "test"
    worker.state.availability.value = "public"
    worker_store.get_by_id.return_value = worker
    worker_store.update.return_value = worker
    profile_store.get.return_value = MagicMock(profile_id="default")
    profile_store.get_active.return_value = MagicMock(profile_id="default")
    binding_store.get_active_binding.return_value = None
    profile_store.activate.return_value = MagicMock()

    app = FastAPI()
    app.include_router(oss_worker_lifecycle_routes.router)
    app.state.context = MagicMock()
    app.state.context.registry = registry

    client = TestClient(app)
    response = client.put("/v1/workers/worker-1/profiles/default/activate")

    assert response.status_code == 200, response.text
    assert refresh_calls == [
        (
            profile_activation_index.refresh_activated_profile_index,
            (registry, worker, "default"),
        )
    ]
