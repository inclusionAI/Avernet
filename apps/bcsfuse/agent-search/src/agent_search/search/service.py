"""Minimal Agent business service; production policies are added separately."""

from dataclasses import replace

from avernet_search.api.errors import UnsupportedSearchOption
from avernet_search.api.models import (
    RetrievalRequest,
    SearchCommand,
    SearchResult,
    VerifiedIdentity,
)
from avernet_search.api.services import RetrievalService


class AgentSearchApplication:
    def __init__(
        self,
        retrieval: RetrievalService,
        *,
        filter_fields: frozenset[str] = frozenset(),
    ):
        self._retrieval = retrieval
        self._filter_fields = filter_fields

    async def execute(
        self, command: SearchCommand, identity: VerifiedIdentity
    ) -> SearchResult:
        if command.scene != "default" or command.extensions:
            raise UnsupportedSearchOption("only the default Agent policy is configured")
        if command.filters.keys() - self._filter_fields:
            raise UnsupportedSearchOption("unsupported Agent filter")
        if identity.allowed_ids == frozenset() or any(
            not values for values in command.filters.values()
        ):
            return SearchResult()
        result = await self._retrieval.retrieve(
            RetrievalRequest(
                command.query,
                command.limit,
                dict(command.filters),
                identity.allowed_ids,
            )
        )
        # Defense in depth for provider bugs. Providers still MUST prefilter;
        # filtering after top-k alone would under-fill valid results.
        items = tuple(
            item
            for item in result.items
            if identity.allowed_ids is None or item.item_id in identity.allowed_ids
        )
        return replace(result, items=items[: command.limit])
