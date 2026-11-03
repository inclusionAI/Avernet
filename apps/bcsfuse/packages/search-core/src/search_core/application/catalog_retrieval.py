"""Validate returned candidates against this instance's applied Card state."""

from dataclasses import replace
from typing import Protocol

from search_core.api.models import RetrievalRequest
from search_core.api.services import RetrievalService


class CatalogScope(Protocol):
    def eligible(self, agent_id, filters, allowed_ids) -> bool: ...


class CatalogRetrieval:
    def __init__(self, retrieval: RetrievalService, catalog: CatalogScope):
        self.retrieval, self.catalog = retrieval, catalog

    async def retrieve(self, request: RetrievalRequest):
        result = await self.retrieval.retrieve(request)
        return replace(
            result,
            items=tuple(
                i
                for i in result.items
                if self.catalog.eligible(
                    i.item_id, request.filters, request.allowed_ids
                )
            ),
        )
