"""Consumers call these contracts; implementations are composed per app."""

from typing import Protocol

from search_core.api.models import (
    RecallResult,
    RetrievalRequest,
    SearchCommand,
    SearchResult,
    VerifiedIdentity,
)


class SearchApplication(Protocol):
    async def execute(
        self, command: SearchCommand, identity: VerifiedIdentity
    ) -> SearchResult:
        """Validate business options, enforce scope, and return ranked items."""
        ...


class RetrievalService(Protocol):
    async def retrieve(self, request: RetrievalRequest) -> SearchResult:
        """Apply filters AND allowed_ids before limiting. Never ignore a filter.

        Return ok/empty only for a successful no-match query; dependency failures
        raise SearchUnavailable, or return degraded with a documented reason.
        Startup/cleanup belongs to the composing application's lifespan.
        """
        ...


class StagedRetrievalService(Protocol):
    """Recall once, optionally inspect query_vector, then rank without re-embedding."""

    async def recall(self, request: RetrievalRequest) -> RecallResult: ...
    async def rank(
        self, request: RetrievalRequest, recall: RecallResult
    ) -> SearchResult: ...
