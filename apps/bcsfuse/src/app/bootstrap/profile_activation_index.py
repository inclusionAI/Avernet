"""Coalesced indexing for durable profile activation requests."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from app.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
from app.infra.config.feature_flags import FeatureFlags
from app.infra.worker_profiles.sources.api_profile_source import APIProfileSource


class _CheckedEmbedding:
    """Reject malformed model responses instead of persisting empty vectors."""

    def __init__(self, provider):
        self.provider = provider

    def embed_batch(self, texts):
        vectors = self.provider.embed_batch(texts)
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise RuntimeError("embedding provider returned invalid vectors")
        return vectors

    def embed(self, text):
        vector = self.provider.embed(text)
        if not vector:
            raise RuntimeError("embedding provider returned an empty vector")
        return vector


@dataclass
class _ActivationIndexRequest:
    registry: object
    worker: object
    profile_id: str
    event: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


@dataclass
class _ResolvedActivation:
    worker_id: str
    profile: object
    worker_state: dict
    requests: list[_ActivationIndexRequest]
    error: BaseException | None = None


class ProfileActivationIndexCoordinator:
    """Coalesce concurrent activation index refreshes into model batches.

    Profile activation remains explicit: a request succeeds only after its
    own profile has a complete index and successful stale cleanup. Concurrent
    requests do not make repeated model calls for identical work and one
    profile's independent failure does not suppress another profile's success.
    """

    def __init__(
        self,
        *,
        batch_size: int = 32,
        coalesce_seconds: float = 0.02,
    ) -> None:
        self._batch_size = batch_size
        self._coalesce_seconds = coalesce_seconds
        self._lock = threading.Lock()
        self._queue: deque[_ActivationIndexRequest] = deque()
        self._leader_active = False

    def refresh(self, registry, worker, profile_id: str) -> None:
        """Index one activation and propagate its batch-specific result."""
        request = _ActivationIndexRequest(
            registry=registry,
            worker=worker,
            profile_id=profile_id,
        )

        with self._lock:
            self._queue.append(request)
            is_leader = not self._leader_active
            self._leader_active = True

        if not is_leader:
            request.event.wait()
            if request.error is not None:
                raise request.error
            return

        try:
            self._drain_queue()
        finally:
            with self._lock:
                self._leader_active = False

            # The leader owns the only call path that never waits on its event.
            # Check its batch result as well: an indexing failure must surface
            # to the activation route instead of being converted into HTTP 200.
            if request.error is not None:
                raise request.error

    def _drain_queue(self) -> None:
        while True:
            # Keep the coalescing interval short. It only needs to absorb an
            # activation burst, never to introduce an artificial startup delay.
            time.sleep(self._coalesce_seconds)
            with self._lock:
                batch: list[_ActivationIndexRequest] = []
                while self._queue and len(batch) < self._batch_size:
                    batch.append(self._queue.popleft())

            if not batch:
                with self._lock:
                    if not self._queue:
                        return
                continue

            self._process_batch(batch)
            self._complete_requests(batch)

    def _complete_requests(
        self,
        batch: list[_ActivationIndexRequest],
    ) -> None:
        for request in batch:
            request.event.set()

    def _resolve_batch(
        self,
        batch: list[_ActivationIndexRequest],
    ) -> tuple[
        list[_ResolvedActivation],
        _CheckedEmbedding,
        object | None,
        BaseException | None,
    ]:
        from app.adapters.http.dependencies.fusion_dependencies import (
            _get_embedding_generator,
            _get_profile_embedding_store,
        )

        embedding = _get_embedding_generator()
        profile_store = _get_profile_embedding_store()
        provider_error = None
        if embedding is None or profile_store is None:
            provider_error = RuntimeError("activation index providers unavailable")

        resolved: list[_ResolvedActivation] = []
        by_profile_key: dict[str, _ResolvedActivation] = {}
        for request in batch:
            entry = self._resolve_request(request)
            if entry is None:
                continue
            existing = by_profile_key.get(entry.profile.profile_key)
            if existing is None:
                resolved.append(entry)
                by_profile_key[entry.profile.profile_key] = entry
            else:
                existing.requests.append(request)

        return resolved, _CheckedEmbedding(embedding), profile_store, provider_error

    def _resolve_request(self, request: _ActivationIndexRequest) -> _ResolvedActivation | None:
        try:
            content_store = request.registry.get("worker_profile_content_store")
            runtime_store = request.registry.get("worker_runtime_state_store")
            if content_store is None or runtime_store is None:
                raise RuntimeError("activation index providers unavailable")

            worker = request.worker
            source = APIProfileSource(content_store=content_store)
            profile = source.get_profile(worker.id, request.profile_id)
            profile_key = f"{worker.id}:{request.profile_id}"
            if (
                profile is None
                or profile.profile_key != profile_key
                or profile.staff_id != worker.id
            ):
                raise RuntimeError("activated profile is unavailable from the content store")

            runtime = runtime_store.get_runtime_state(worker.id)
            if isinstance(runtime, dict):
                runtime = runtime.get("state", "offline")
            elif runtime is None:
                runtime = "offline"
            else:
                runtime = getattr(runtime, "value", runtime)

            return _ResolvedActivation(
                worker_id=worker.id,
                profile=profile,
                worker_state={
                    "availability": worker.state.availability.value,
                    "runtime_state": runtime,
                },
                requests=[request],
            )
        except Exception as error:
            request.error = error
            return None

    def _process_batch(self, batch: list[_ActivationIndexRequest]) -> None:
        try:
            resolved, checked_embedding, profile_store, provider_error = self._resolve_batch(batch)
        except Exception as error:
            for request in batch:
                request.error = error
            return

        if provider_error is not None:
            for request in batch:
                request.error = provider_error
            return

        profiles = [entry.profile for entry in resolved]
        worker_states = {
            entry.worker_id: entry.worker_state
            for entry in resolved
        }
        result = ProfileEmbeddingIndexer(
            embedding_provider=checked_embedding,
            profile_store=profile_store,
            batch_size=self._batch_size,
        ).build_index(profiles, worker_states=worker_states)

        successful_keys = set(result.indexed_profile_keys)

        for entry in resolved:
            if entry.profile.profile_key not in successful_keys:
                entry.error = entry.error or RuntimeError(
                    "activated profile indexing failed"
                )
            for request in entry.requests:
                request.error = request.error or entry.error

_activation_index_coordinator = ProfileActivationIndexCoordinator()


def get_activation_index_coordinator() -> ProfileActivationIndexCoordinator:
    """Return the process-local coordinator used by the activation route."""

    return _activation_index_coordinator


def reset_activation_index_coordinator_for_tests() -> None:
    """Recreate the singleton coordinator between isolated contract tests."""

    global _activation_index_coordinator
    _activation_index_coordinator = ProfileActivationIndexCoordinator()


def refresh_activated_profile_index(registry, worker, profile_id: str) -> None:
    """Publish the selected profile while retaining other profile vectors.

    Persistence has already committed. Any failure reaches the caller so the
    same activation request can retry; the call runs in a worker thread and
    therefore must not block the HTTP event loop.
    """

    if not FeatureFlags.is_profile_embedding_index_enabled():
        return

    get_activation_index_coordinator().refresh(registry, worker, profile_id)
