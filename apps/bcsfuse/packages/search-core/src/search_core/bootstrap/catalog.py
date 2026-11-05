"""Compose the minimal durable Agent catalog and shared search application."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from itertools import product

from fastapi import Response
from pydantic import BaseModel

from search_core.adapters.http.catalog_cards import create_card_router
from search_core.adapters.http.index_builds import create_index_build_router
from search_core.adapters.http.routes import Authenticator
from search_core.api.services import RetrievalService, StagedRetrievalService
from search_core.application.cache import CachedEmbedding, CachedRetrieval, SearchCaches
from search_core.application.card_metadata import CardMetadataRetrieval
from search_core.application.catalog_retrieval import CatalogRetrieval
from search_core.application.default_candidates import DefaultCandidates
from search_core.application.emergency import (
    EmergencyControl,
    EmergencyPolicy,
    RetrievalSwitches,
)
from search_core.bootstrap.http import create_search_app
from search_core.catalog.updates import CardUpdates
from search_core.config.index_sync import IndexSyncControl
from search_core.config.search import DEFAULT_SEARCH, SearchSettings
from search_core.core.execution import DeadlineExecutor
from search_core.core.index_builds import IndexBuildService
from search_core.core.observation import events
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.card_documents import CardDocumentConverter
from search_core.plugin_api.embedding import AsyncEmbeddingProvider, EmbeddingProvider
from search_core.plugin_api.reranker import AsyncReranker, Reranker
from search_core.plugin_api.retrieval import IndexStatistics


class IndexCounts(BaseModel):
    vector_points: int
    vector_matching_points: int
    vector_mismatched_points: int
    keyword_points: int
    keyword_matching_points: int
    keyword_mismatched_points: int


class ReadyStatus(BaseModel):
    ready: bool
    instance_id: str
    index_key: str
    active_index: str
    index_counts: IndexCounts | None = None
    detail: str | None = None


def create_catalog_search_app(
    repository,
    index,
    embedding: EmbeddingProvider,
    authenticate: Authenticator,
    authorize_operator: Authenticator,
    *,
    embedding_key: str,
    instance_id: str,
    filter_fields: frozenset[str] = frozenset(),
    poll_seconds: float = 5.0,
    reranker: Reranker | AsyncReranker | None = None,
    query_embedding: AsyncEmbeddingProvider | None = None,
    settings: SearchSettings = DEFAULT_SEARCH,
    retrieval_extension: Callable[..., RetrievalService] | None = None,
    document_converter: CardDocumentConverter | None = None,
    index_key: str = "",
    active_index: str = "",
    index_builds: IndexBuildService | None = None,
    updates_factory=CardUpdates,
    metadata_factory=CardMetadataRetrieval,
    http_app_factory=create_search_app,
):
    """All dependencies are injected; host owns and closes model/index clients.

    This lifespan owns only the background task and waits for in-flight work on
    shutdown. Model/database calls MUST have finite provider-level deadlines.
    Each application must use a dedicated index, not TC or another replica's.
    """
    filter_fields = filter_fields | {"resource_type"}
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if query_embedding is not None and query_embedding.dimension != embedding.dimension:
        raise ValueError("query and preparation embedding dimensions must match")
    # One runtime-managed pool, with no per-stage admission or worker settings.
    executor = ThreadPoolExecutor(thread_name_prefix="search-call")
    calls = {
        name: DeadlineExecutor(executor, name=name, timeout_seconds=timeout)
        for name, timeout in {
            "embedding": settings.embedding_timeout,
            "vector": settings.index_timeout,
            "keyword": settings.index_timeout,
            "reranker": settings.reranker_timeout,
            "probe": 2 * settings.index_timeout,
            "card_metadata": settings.index_timeout,
            "default_candidates": settings.index_timeout,
        }.items()
    }
    caches = SearchCaches(
        result_entries=settings.result_cache_max_entries,
        result_bytes=settings.result_cache_max_bytes,
        embedding_entries=settings.embedding_cache_max_entries,
        embedding_bytes=settings.embedding_cache_max_bytes,
    )
    sync_control = IndexSyncControl(incremental_interval_ms=round(poll_seconds * 1000))
    updates = updates_factory(
        repository,
        embedding,
        index,
        embedding_key=embedding_key,
        instance_id=instance_id,
        filter_fields=filter_fields,
        on_change=caches.changing,
        document_converter=document_converter,
        sync_control=sync_control,
        index_key=index_key,
    )

    async def flush_events(stop):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except TimeoutError:
                await asyncio.to_thread(events.flush)
                await asyncio.to_thread(caches.log_stats)
        await asyncio.to_thread(events.flush, force=True)

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        task = asyncio.create_task(updates.run(stop))
        log_task = asyncio.create_task(flush_events(stop))
        build_task = (
            asyncio.create_task(index_builds.run(stop, poll_seconds))
            if index_builds is not None
            else None
        )
        try:
            yield
        finally:
            stop.set()
            try:
                await task  # no detached writer after lifespan exits
            finally:
                if build_task is not None:
                    await build_task
                await log_task
                await asyncio.to_thread(
                    executor.shutdown, wait=True, cancel_futures=True
                )

    def build_retrieval(policy, cache_views=None):
        result_cache, embedding_cache, metadata_cache = (
            caches.configure(policy.cache) if cache_views is None else cache_views
        )
        if not policy.retrieval.keyword_enabled and not policy.retrieval.vector_enabled:
            return metadata_factory(
                DefaultCandidates(
                    repository,
                    updates,
                    caches.candidate_ids,
                    caches,
                    calls["default_candidates"],
                ),
                repository,
                metadata_cache,
                caches,
                calls["card_metadata"],
            )
        online_embedding = query_embedding if query_embedding is not None else embedding
        if embedding_cache.enabled or policy.embedding_singleflight_enabled:
            online_embedding = CachedEmbedding(
                online_embedding,
                embedding_cache,
                embedding_key,
                singleflight_enabled=policy.embedding_singleflight_enabled,
            )
        execution = dict(calls)
        for name, timeout in (
            ("embedding", policy.embedding_timeout_ms / 1000),
            ("reranker", policy.reranker_timeout_ms / 1000),
        ):
            execution[name] = DeadlineExecutor(
                executor, name=name, timeout_seconds=timeout
            )
        candidate_limit = max(30, policy.max_results)
        retrieval: StagedRetrievalService = HybridRetrievalService(
            index=index,
            embedding=online_embedding,
            group_by_source=True,
            candidate_limit=candidate_limit,
            recall_limit=max(settings.document_recall_limit, candidate_limit),
            vector_min_score=settings.vector_min_score,
            keyword_min_score=settings.keyword_min_score,
            reranker=reranker if policy.retrieval.rerank_enabled else None,
            allow_partial=True,
            execution=execution,
            keyword_enabled=policy.retrieval.keyword_enabled,
            vector_enabled=policy.retrieval.vector_enabled,
        )
        if retrieval_extension is not None:
            retrieval = retrieval_extension(retrieval, updates, executor)
        if result_cache.enabled:
            retrieval = CachedRetrieval(
                retrieval,
                result_cache,
                caches,
                namespace=tuple(policy.retrieval.model_dump().values()),
            )
        return metadata_factory(
            retrieval, repository, metadata_cache, caches, calls["card_metadata"]
        )

    def build_variants(policy):
        # Seven valid combinations at most. All share providers and bounded caches;
        # request handling only selects a prebuilt pipeline, never creates clients.
        cache_views = caches.configure(policy.cache)
        variants = {}
        limits = tuple(policy.retrieval.model_dump().values())
        for switches in product((False, True), repeat=3):
            keyword, vector, rerank = switches
            if any(value and not allowed for value, allowed in zip(switches, limits)):
                continue
            if rerank and not (keyword or vector):
                continue
            variant_policy = policy.model_copy(
                update={
                    "retrieval": RetrievalSwitches(
                        keyword_enabled=keyword,
                        vector_enabled=vector,
                        rerank_enabled=rerank,
                    )
                }
            )
            variants[switches] = build_retrieval(variant_policy, cache_views)
        return variants

    emergency = EmergencyControl(
        EmergencyPolicy(
            max_inflight_requests=settings.max_inflight_requests,
            retrieval={
                "keyword_enabled": True,
                "vector_enabled": True,
                "rerank_enabled": True,
            },
            embedding_timeout_ms=settings.embedding_timeout * 1000,
            reranker_timeout_ms=settings.reranker_timeout * 1000,
        ),
        build_retrieval,
        build_variants=build_variants,
    )
    retrieval = CatalogRetrieval(emergency, updates)
    app = http_app_factory(
        retrieval,
        authenticate,
        filter_fields=filter_fields,
        lifespan=lifespan,
        timeout_seconds=settings.request_timeout,
        max_inflight_requests=settings.max_inflight_requests,
        emergency=emergency,
        operations=("search", "recommend"),
    )
    app.state.catalog_updates = updates
    app.state.search_emergency = emergency
    app.state.index_sync = sync_control
    app.state.index_key = updates.index_key
    app.state.active_index = active_index
    app.include_router(create_card_router(updates, authorize_operator))
    if index_builds is not None:
        app.include_router(create_index_build_router(index_builds, authorize_operator))

    def check_index(switches):
        filters = {"source_id": ("__catalog_readiness_probe__",)}
        if switches.keyword_enabled:
            index.keyword_search("catalog readiness", 1, filters)
        if switches.vector_enabled:
            index.vector_search([0.0] * embedding.dimension, 1, filters)
        if not switches.keyword_enabled and not switches.vector_enabled:
            repository.page("", 1)  # Default candidates require the source DB.

    @app.get(
        "/ready",
        response_model=ReadyStatus,
        response_model_exclude_none=True,
        responses={503: {"model": ReadyStatus}, 501: {"model": ReadyStatus}},
    )
    async def ready(response: Response, include_counts: bool = False):
        result = ReadyStatus(
            ready=updates.ready,
            instance_id=instance_id,
            index_key=updates.index_key,
            active_index=active_index,
        )
        if not updates.ready:
            result.detail = "catalog initial load or poll unavailable"
        else:
            try:
                await calls["probe"].call(
                    check_index, emergency.current.policy.retrieval
                )
            except Exception as error:  # noqa: BLE001 - report provider failures as readiness diagnostics
                events.record(
                    "search_readiness",
                    "index",
                    "unavailable",
                    error_type=type(error).__name__,
                )
                result.ready = False
                result.detail = "catalog index unavailable"
        if include_counts:
            if not isinstance(index, IndexStatistics):
                response.status_code = 501
                result.ready = False
                result.detail = "index statistics unsupported"
            else:
                try:
                    counts = await calls["probe"].call(
                        index.point_counts, updates.index_key
                    )
                    result.index_counts = IndexCounts(**counts)
                except Exception as error:  # noqa: BLE001 - preserve identity on optional count failures
                    events.record(
                        "search_readiness",
                        "counts",
                        "unavailable",
                        error_type=type(error).__name__,
                    )
                    result.ready = False
                    result.detail = "index count unavailable"
        if not result.ready and response.status_code != 501:
            response.status_code = 503
        return result

    return app
