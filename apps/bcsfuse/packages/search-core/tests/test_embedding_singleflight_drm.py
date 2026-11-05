"""Emergency updates switch coalescing independently from embedding cache TTL."""

import asyncio

import pytest
from search_core.api.models import RetrievalRequest, VerifiedIdentity
from search_core.application.emergency import EmergencyPolicy
from search_core.bootstrap.catalog import create_catalog_search_app
from search_core.providers.demo_embedding import DemoEmbedding
from search_core.providers.qdrant_index import QdrantRetrievalIndex
from search_core.providers.sql_catalog import sqlite_catalog_repository
from test_embedding_singleflight import Model


def config(**changes):
    return {
        "max_inflight_requests": 32,
        "retrieval": {
            "keyword_enabled": True,
            "vector_enabled": True,
            "rerank_enabled": True,
        },
        "embedding_timeout_ms": 1000,
        "reranker_timeout_ms": 1500,
        **changes,
    }


@pytest.mark.parametrize("bad", [None, "false", 0, 1])
def test_singleflight_switch_requires_a_boolean(bad):
    with pytest.raises(ValueError):
        EmergencyPolicy.model_validate(config(embedding_singleflight_enabled=bad))


@pytest.mark.parametrize("cache_enabled", [True, False])
def test_emergency_toggle_works_without_cache_and_preserves_inflight_snapshot(
    tmp_path, cache_enabled
):
    async def run():
        class RecordingModel(Model):
            def __init__(self):
                super().__init__()
                self.three_calls = asyncio.Event()

            async def embed(self, text):
                # Count model entries before either await or release.
                if len(self.calls) == 2:
                    self.three_calls.set()
                return await super().embed(text)

        model = RecordingModel()
        index = QdrantRetrievalIndex(path=str(tmp_path / "index"), dimension=2)

        async def auth(request):
            return VerifiedIdentity("test")

        app = create_catalog_search_app(
            sqlite_catalog_repository(str(tmp_path / "cards.db")),
            index,
            DemoEmbedding(2),
            auth,
            auth,
            embedding_key="model/2",
            instance_id="test",
            query_embedding=model,
        )
        try:
            async with app.router.lifespan_context(app):
                control = app.state.search_emergency
                assert control.update_cache({"enabled": cache_enabled})
                assert not control.current.policy.cache.query_embedding.enabled
                request = RetrievalRequest("same", 1, {})

                async def burst():
                    return await asyncio.gather(
                        *(control.retrieve(request) for _ in range(3))
                    )

                first = asyncio.create_task(burst())
                await model.started.wait()
                # Do not discard the old snapshot's pending shared call.
                assert control.update(config(embedding_singleflight_enabled=False))
                assert not control.update(
                    config(embedding_singleflight_enabled="false")
                )
                assert not control.current.policy.embedding_singleflight_enabled
                second = asyncio.create_task(burst())
                await model.three_calls.wait()
                model.release.set()
                results = await asyncio.gather(first, second)
                assert all(
                    result.status == "ok" for group in results for result in group
                )
                assert model.calls == ["same"] * 4  # Old shared call + three new calls.

                assert control.update(config())  # Omitted switch restores default on.
                await burst()
                assert model.calls == ["same"] * 5
                await burst()  # No result/vector caching was silently enabled.
                assert model.calls == ["same"] * 6
        finally:
            index.close()

    asyncio.run(asyncio.wait_for(run(), 5))
