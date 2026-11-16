"""Emergency default IDs from Cards; no query models or retrieval index calls."""

import asyncio

from search_core.api.errors import SearchUnavailable
from search_core.api.models import SearchItem, SearchResult
from search_core.application.cache import _key
from search_core.core.timing import stage_timing


class DefaultCandidates:
    def __init__(self, repository, catalog, cache, state, execution):
        self.repository, self.catalog = repository, catalog
        self.cache, self.state, self.execution = cache, state, execution
        # Only cold candidate-list refreshes serialize. No per-query lock map,
        # extra admission quota or background refresh job.
        self._refresh = asyncio.Lock()

    def _load(self, request):
        selected, after = [], ""
        while len(selected) < request.limit:
            page = self.repository.page(after, 100)
            if not page:
                break
            ids = [key for key, _ in page]
            cards = self.repository.get_many_cards(ids)
            for key in ids:
                card = cards.get(key)
                if card is None or card.deleted:
                    continue
                if not self.catalog.eligible(key, request.filters):
                    continue
                selected.append(key)
                if len(selected) == request.limit:
                    break
            after = page[-1][0]
            if len(page) < 100:
                break
        return tuple(selected)

    async def _load_ids(self, request):
        try:
            return await self.execution.call(self._load, request)
        except Exception as error:
            raise SearchUnavailable("default candidates unavailable") from error

    def _cached(self, request):
        revision = self.state.revision()
        key = _key(
            [
                "default_candidates",
                revision,
                request.limit,
                {k: sorted(v) for k, v in request.filters.items()},
            ]
        )
        hit = self.cache.get(key) if revision is not None else None
        if self.state.revision() != revision:
            hit = None
        return revision, key, hit

    async def _ids(self, request, stage):
        if not self.cache.enabled:
            stage["cache"] = "disabled"
            return await self._load_ids(request)
        _, _, hit = self._cached(request)
        if hit is not None:
            stage["cache"] = "hit"
            return hit
        # Hot hits never wait for an unrelated cold refresh. Waiters recheck
        # after the loader completes; the outer request deadline bounds waiting.
        async with self._refresh:
            revision, key, hit = self._cached(request)
            if hit is not None:
                stage["cache"] = "hit_after_wait"
                return hit
            stage["cache"] = "miss"
            ids = await self._load_ids(request)
            if revision is not None:
                self.state.put_result(self.cache, revision, key, ids)
            return ids

    async def retrieve(self, request):
        with stage_timing("default_candidates") as stage:
            if any(
                not v for v in request.filters.values()
            ):
                ids = ()
            else:
                ids = await self._ids(request, stage)
            stage["result_count"] = len(ids)
        return SearchResult(
            items=tuple(SearchItem(key, "", "", 0.0, "default") for key in ids),
            status="degraded",
            reasons=("default_candidates", "keyword_disabled", "vector_disabled"),
        )
