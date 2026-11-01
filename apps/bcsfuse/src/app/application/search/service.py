"""Opt-in common API adapter. Existing TC v1 recommendation is unchanged."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Callable

from search_core.api.errors import (
    SearchOverloaded,
    SearchUnavailable,
    UnsupportedSearchOption,
)
from search_core.api.models import (
    SearchCommand,
    SearchItem,
    SearchResult,
    VerifiedIdentity,
)

from app.domain.models.retrieval_mode import RetrievalMode
from app.domain.services.worker_candidate_recommendation_service import (
    WorkerCandidateRecommendationService,
)
from app.infra.public.observability.search_result_cache_metrics import (
    record_search_result_cache_eviction,
    record_search_result_cache_hit,
    record_search_result_cache_miss,
    set_search_result_cache_entries,
)


class TcSearchApplication:
    """The host owns the executor; permits follow actual work, not HTTP waits.

    Group rewriting and Fuse remain available through TC v1. This initial v2
    adapter supports the default search plus a typed rerank toggle only. It
    projects TC's graceful-degradation diagnostics into the shared API status.
    """

    def __init__(
        self,
        candidates: WorkerCandidateRecommendationService,
        *,
        executor: ThreadPoolExecutor,
        max_inflight: int = 4,
        index_version_provider: Callable[[], int] | None = None,
        max_entries: int = 1000,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_inflight <= 0:
            raise ValueError("max_inflight must be positive")
        if max_entries <= 0:
            raise ValueError("result cache max_entries must be positive")
        if ttl_seconds < 0:
            raise ValueError("result cache ttl_seconds cannot be negative")
        self._candidates = candidates
        self._executor = executor
        self._permits = threading.BoundedSemaphore(max_inflight)
        self._index_version_provider = index_version_provider
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, SearchResult]] = OrderedDict()
        self._inflight: dict[str, threading.Event] = {}
        self._cache_lock = threading.RLock()
        self._cache_hits = 0
        self._cache_misses = 0

    @staticmethod
    def _degradation_reasons(metadata: dict) -> tuple[str, ...]:
        reasons: list[str] = []
        if metadata.get("keyword_unavailable") is True:
            reasons.append("keyword_unavailable")
        if metadata.get("semantic_unavailable") is True:
            reasons.append("semantic_unavailable")
        if (
            metadata.get("semantic_unavailable") is True
            and metadata.get("keyword_search_used") is True
        ):
            reasons.append("keyword_fallback_used")
        if (
            metadata.get("literal_fallback_used") is True
            or metadata.get("score_source") == "regex_fallback"
        ):
            reasons.append("literal_fallback")
        if metadata.get("rerank_degraded") is True:
            reasons.append("rerank_degraded")
        return tuple(reasons)

    @property
    def result_cache_stats(self) -> dict[str, float | int]:
        with self._cache_lock:
            return {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl_seconds,
            }

    def _cache_enabled(self) -> bool:
        return self._index_version_provider is not None and self._ttl_seconds > 0

    def _cache_key(
        self,
        command: SearchCommand,
        caller: VerifiedIdentity,
        *,
        index_version: int,
    ) -> str:
        normalized_query = " ".join(
            unicodedata.normalize("NFKC", command.query).split()
        )
        material = json.dumps(
            {
                "operation": command.operation,
                "query": normalized_query,
                "limit": command.limit,
                "filters": {
                    key: sorted(values) for key, values in command.filters.items()
                },
                "extensions": {
                    key: command.extensions[key] for key in sorted(command.extensions)
                },
                "subject": caller.subject,
                "allowed_ids": None
                if caller.allowed_ids is None
                else sorted(caller.allowed_ids),
                "index_version": index_version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _cached_result(self, key: str) -> SearchResult | None:
        if not self._cache_enabled():
            return None
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            now = self._clock()
            if now < expires_at:
                self._cache.move_to_end(key)
                self._cache_hits += 1
                record_search_result_cache_hit()
                return result
            self._cache.pop(key, None)
            record_search_result_cache_eviction()
            set_search_result_cache_entries(len(self._cache))
            return None

    def _store_result(self, key: str, result: SearchResult) -> None:
        if not self._cache_enabled() or result.status != "ok":
            return
        with self._cache_lock:
            self._cache[key] = (self._clock() + self._ttl_seconds, result)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
                record_search_result_cache_eviction()
            set_search_result_cache_entries(len(self._cache))

    def _record_miss(self) -> None:
        with self._cache_lock:
            self._cache_misses += 1
        record_search_result_cache_miss()

    @staticmethod
    def _build_result(  # noqa: PLR0913 - the values come from a single command.
        response: object,
        command: SearchCommand,
        identity: VerifiedIdentity,
    ) -> SearchResult:
        reasons = TcSearchApplication._degradation_reasons(
            getattr(response, "metadata", {}) or {}
        )
        items = tuple(
            SearchItem(
                candidate.profile_key,
                "",
                candidate.short_profile,
                candidate.score,
                "tc_recommendation",
                {
                    "worker_id": candidate.worker_id,
                    "profile_key": candidate.profile_key,
                },
            )
            for candidate in response.recommendations
            if identity.allowed_ids is None
            or candidate.profile_key in identity.allowed_ids
        )[: command.limit]
        return SearchResult(
            items=items,
            status="degraded" if reasons else "ok",
            reasons=reasons,
        )

    async def execute(
        self, command: SearchCommand, identity: VerifiedIdentity
    ) -> SearchResult:
        if command.scene != "default" or command.extensions.keys() - {"enable_rerank"}:
            raise UnsupportedSearchOption("unsupported TC search context")
        rerank = command.extensions.get("enable_rerank", True)
        if type(rerank) is not bool:
            raise UnsupportedSearchOption("enable_rerank must be boolean")
        if command.filters.keys() - {
            "availability",
            "runtime_state",
            "domains",
            "trust_level",
        }:
            raise UnsupportedSearchOption("unsupported TC filter")
        if identity.allowed_ids == frozenset() or any(
            not values for values in command.filters.values()
        ):
            return SearchResult()

        filters = {key: list(values) for key, values in command.filters.items()}
        filters.setdefault("availability", ["protected", "public"])
        if identity.allowed_ids is not None:
            filters["profile_key"] = sorted(identity.allowed_ids)
        config = {"expand_factor": 10}
        if not rerank:
            config["reranker_model"] = None

        key: str | None = None
        in_flight_event: threading.Event | None = None
        if self._cache_enabled():
            try:
                index_version = int(self._index_version_provider())
                if index_version < 0:
                    raise ValueError("index_version must not be negative")
            except Exception as error:
                raise SearchUnavailable("index revision unavailable") from error
            key = self._cache_key(command, identity, index_version=index_version)
            cached = self._cached_result(key)
            if cached is not None:
                return cached
            self._record_miss()
            while True:
                with self._cache_lock:
                    existing = self._inflight.get(key)
                    if existing is None:
                        in_flight_event = threading.Event()
                        self._inflight[key] = in_flight_event
                        break
                await asyncio.to_thread(existing.wait)
                cached = self._cached_result(key)
                if cached is not None:
                    return cached

        if not self._permits.acquire(blocking=False):
            self._clear_in_flight(key, in_flight_event)
            raise SearchOverloaded("TC search capacity is occupied")
        try:
            pending = self._executor.submit(
                partial(
                    self._candidates.recommend,
                    question=command.query,
                    mode=RetrievalMode.EXPERT_DIAGNOSIS,
                    participants=None,
                    max_candidates=command.limit,
                    filters=filters,
                    runtime_config=config,
                )
            )
        except Exception as error:
            self._permits.release()
            raise SearchUnavailable("TC executor unavailable") from error
        # The done callback releases capacity only after the synchronous
        # candidate work has actually finished.
        pending.add_done_callback(lambda _: self._permits.release())

        try:
            response = await asyncio.wrap_future(pending)
        except Exception as error:
            raise SearchUnavailable("TC recommendation failed") from error
        finally:
            self._clear_in_flight(key, in_flight_event)

        result = self._build_result(response, command, identity)
        if key is not None:
            self._store_result(key, result)
        return result

    def _clear_in_flight(
        self, key: str | None, event: threading.Event | None
    ) -> None:
        if key is None or event is None:
            return
        with self._cache_lock:
            if self._inflight.get(key) is event:
                self._inflight.pop(key, None)
        event.set()
