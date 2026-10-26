"""Opt-in common API adapter. Existing TC v1 recommendation is unchanged."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from avernet_search.api.errors import (
    SearchOverloaded,
    SearchUnavailable,
    UnsupportedSearchOption,
)
from avernet_search.api.models import (
    SearchCommand,
    SearchItem,
    SearchResult,
    VerifiedIdentity,
)

from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.services.worker_candidate_recommendation_service import (
    WorkerCandidateRecommendationService,
)


class TcSearchApplication:
    """The host owns the executor; permits follow actual work, not HTTP waits.

    Group rewriting and Fuse remain available through TC v1. This initial v2
    adapter supports the default search plus a typed rerank toggle only.
    """

    def __init__(
        self,
        candidates: WorkerCandidateRecommendationService,
        *,
        executor: ThreadPoolExecutor,
        max_inflight: int = 4,
    ):
        if max_inflight <= 0:
            raise ValueError("max_inflight must be positive")
        self._candidates = candidates
        self._executor = executor
        self._permits = threading.BoundedSemaphore(max_inflight)

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
        if not self._permits.acquire(blocking=False):
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
        pending.add_done_callback(lambda _: self._permits.release())
        try:
            result = await asyncio.wrap_future(pending)
        except Exception as error:
            raise SearchUnavailable("TC recommendation failed") from error
        return SearchResult(
            items=tuple(
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
                for candidate in result.recommendations
                if identity.allowed_ids is None
                or candidate.profile_key in identity.allowed_ids
            )[: command.limit]
        )
