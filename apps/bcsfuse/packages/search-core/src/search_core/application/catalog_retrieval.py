"""Validate returned candidates against this instance's applied Card state."""

from dataclasses import replace
from typing import Protocol

from search_core.api.models import RetrievalRequest
from search_core.api.services import RetrievalService
from search_core.core.timing import stage_timing


class CatalogScope(Protocol):
    def eligible(self, agent_id, filters) -> bool: ...


class CatalogRetrieval:
    def __init__(self, retrieval: RetrievalService, catalog: CatalogScope):
        self.retrieval, self.catalog = retrieval, catalog

    async def retrieve(self, request: RetrievalRequest):
        result = await self.retrieval.retrieve(request)
        with stage_timing("eligibility") as stage:
            filtered = replace(
                result,
                items=tuple(
                    i
                    for i in result.items
                    if self.catalog.eligible(
                        i.item_id, request.filters
                    )
                ),
            )
            stage.update(
                input_count=len(result.items), result_count=len(filtered.items)
            )
            return filtered
