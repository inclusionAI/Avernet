"""Refresh the selected profile without deleting other profiles' vectors."""

from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
from src.infra.config.feature_flags import FeatureFlags


class _CheckedEmbedding:
    """Retain failures that the batch indexer otherwise logs per fragment."""

    def __init__(self, provider):
        self.provider = provider
        self.failed = False

    def embed(self, text):
        try:
            vector = self.provider.embed(text)
            if not vector:
                raise RuntimeError("embedding provider returned an empty vector")
            return vector
        except Exception:
            self.failed = True
            raise


def refresh_activated_profile_index(registry, worker, profile_id: str) -> None:
    """Publish the selected profile while retaining existing profile vectors.

    Persistence has already committed. Any failure must reach the caller so the
    same activation request can retry; it must not trigger activation rollback.
    """
    if not FeatureFlags.is_profile_embedding_index_enabled():
        return

    from src.interfaces.api.dependencies.fusion_dependencies import (
        _get_embedding_generator,
        _get_profile_embedding_store,
        _get_profile_source,
    )

    embedding = _get_embedding_generator()
    profile_store = _get_profile_embedding_store()
    source = _get_profile_source()
    runtime_store = registry.get("worker_runtime_state_store")
    if any(provider is None for provider in (embedding, profile_store, source, runtime_store)):
        raise RuntimeError("activation index providers unavailable")

    profile = source.get_profile(worker.id, profile_id)
    profile_key = f"{worker.id}:{profile_id}"
    if (
        profile is None
        or profile.profile_key != profile_key
        or profile.staff_id != worker.id
    ):
        raise RuntimeError("activated profile is unavailable from the composed source")

    runtime = runtime_store.get_runtime_state(worker.id)
    if isinstance(runtime, dict):
        runtime = runtime.get("state", "offline")
    elif runtime is None:
        runtime = "offline"
    else:
        runtime = getattr(runtime, "value", runtime)
    state = {
        "availability": worker.state.availability.value,
        "runtime_state": runtime,
    }
    checked_embedding = _CheckedEmbedding(embedding)
    indexer = ProfileEmbeddingIndexer(checked_embedding, profile_store)
    # Rebuilding the one target also makes retries independent of partial or
    # stale fragment caches left by a previously interrupted refresh.
    result = indexer.build_index([profile], worker_states={worker.id: state})
    if checked_embedding.failed or result.failed_count or result.indexed_count != 1:
        raise RuntimeError("activated profile indexing failed")
