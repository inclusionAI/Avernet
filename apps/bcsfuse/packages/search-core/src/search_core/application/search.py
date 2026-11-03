"""Default catalog search policy and request execution."""

import math
import time
from contextlib import nullcontext
from dataclasses import dataclass, field, replace

from search_core.api.errors import UnsupportedSearchOption
from search_core.api.models import (
    EffectiveRetrieval,
    EffectiveSearch,
    RetrievalRequest,
    SearchCommand,
    SearchResult,
    VerifiedIdentity,
)
from search_core.api.services import RetrievalService
from search_core.application.emergency import EmergencyControl
from search_core.core.observation import events, request_budget


@dataclass(frozen=True)
class CatalogSearchCommand(SearchCommand):
    retrieval: dict[str, bool] = field(default_factory=dict)


class CatalogSearchApplication:
    def __init__(
        self,
        retrieval: RetrievalService,
        *,
        filter_fields: frozenset[str] = frozenset(),
        request_timeout: float = 4.0,
        emergency: EmergencyControl | None = None,
    ):
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("positive finite request timeout required")
        self._budget = request_timeout
        self._retrieval = retrieval
        self._filter_fields = filter_fields
        self._emergency = emergency

    async def execute(
        self, command: SearchCommand, identity: VerifiedIdentity
    ) -> SearchResult:
        started = time.monotonic()
        snapshot = self._emergency.snapshot() if self._emergency is not None else None
        if isinstance(command, CatalogSearchCommand) and command.retrieval:
            if self._emergency is None:
                raise UnsupportedSearchOption("request retrieval switches unavailable")
            snapshot = self._emergency.for_request(command.retrieval)
        maximum = snapshot.policy.max_results if snapshot is not None else 20
        if command.limit > maximum:
            events.record(
                "search_request",
                "validation",
                "limit_clamped",
                requested_limit=command.limit,
                effective_limit=maximum,
            )
            command = replace(command, limit=maximum)
        binding = (
            self._emergency.bind(snapshot)
            if self._emergency is not None
            else nullcontext()
        )
        with request_budget(self._budget), binding:
            try:
                result = await self._validated_execute(command, identity)
            except Exception as error:
                events.record(
                    "search_request",
                    "request",
                    type(error).__name__,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                )
                raise
            if result.status == "degraded":
                for reason in result.reasons:
                    events.record(
                        "search_degraded",
                        "request",
                        reason,
                        elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                        result_count=len(result.items),
                    )
            # Attach after retrieval/cache lookup, using this request's snapshot.
            # Enabled switches describe policy, not model execution or success.
            return replace(
                result,
                effective=EffectiveSearch(
                    limit=command.limit,
                    retrieval=(
                        EffectiveRetrieval(**snapshot.policy.retrieval.model_dump())
                        if snapshot is not None
                        else None
                    ),
                ),
            )

    async def _validated_execute(self, command, identity):
        if command.scene != "default" or command.extensions:
            raise UnsupportedSearchOption(
                "only the default catalog policy is configured"
            )
        if command.filters.keys() - self._filter_fields:
            raise UnsupportedSearchOption("unsupported catalog filter")
        if identity.allowed_ids == frozenset() or any(
            not values for values in command.filters.values()
        ):
            return SearchResult()
        return await self._execute(command, identity)

    async def _execute(self, command, identity):
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
