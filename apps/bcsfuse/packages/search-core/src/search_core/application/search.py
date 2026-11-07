"""Default catalog search policy and request execution."""

import logging
import math
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, replace

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
from search_core.catalog.cards import is_label_filter
from search_core.core.observation import (
    events,
    logger,
    request_budget,
    search_transport,
)
from search_core.core.timing import request_timing, stage_timing

# Keep request logs readable without copying whole prompts or common identifiers.
_sensitive_text = re.compile(
    r"(?i)\bbearer\s+[a-z0-9._~+/=-]+|(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)"
)


def _log_text(value):
    return _sensitive_text.sub("[redacted]", value)[:256]


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
        trace_id_provider: Callable[[], str] | None = None,
    ):
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("positive finite request timeout required")
        self._trace_id_provider = trace_id_provider
        self._budget = request_timeout
        self._retrieval = retrieval
        self._filter_fields = filter_fields
        self._emergency = emergency

    async def execute(
        self, command: SearchCommand, identity: VerifiedIdentity
    ) -> SearchResult:
        trace_id = self._trace_id_provider() if self._trace_id_provider else ""
        with (
            request_budget(self._budget),
            request_timing(command.operation, trace_id) as timing,
        ):
            if logger.isEnabledFor(logging.INFO):
                events.record_progress(
                    "received",
                    trace_id,
                    transport=search_transport.get(),
                    operation=command.operation,
                    query=_log_text(command.query),
                    limit=command.limit,
                    filters={
                        field: [_log_text(value) for value in values[:20]]
                        for field, values in command.filters.items()
                        if field in self._filter_fields
                    },
                    retrieval={
                        key: value
                        for key, value in getattr(command, "retrieval", {}).items()
                        if key
                        in {"keyword_enabled", "vector_enabled", "rerank_enabled"}
                        and isinstance(value, bool)
                    },
                )
            started = time.monotonic()
            with stage_timing("configuration"):
                snapshot = (
                    self._emergency.snapshot() if self._emergency is not None else None
                )
                if isinstance(command, CatalogSearchCommand) and command.retrieval:
                    if self._emergency is None:
                        raise UnsupportedSearchOption(
                            "request retrieval switches unavailable"
                        )
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
                if snapshot is not None:
                    timing.update(
                        config_version=snapshot.version,
                        retrieval=snapshot.policy.retrieval.model_dump(),
                    )
            binding = (
                self._emergency.bind(snapshot)
                if self._emergency is not None
                else nullcontext()
            )
            with binding:
                try:
                    result = await self._validated_execute(command)
                except Exception as error:
                    events.record(
                        "search_request",
                        "request",
                        type(error).__name__,
                        elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                    )
                    raise
                failures = tuple(
                    reason
                    for reason in result.reasons
                    if reason
                    not in {
                        "keyword_disabled",
                        "vector_disabled",
                        "reranker_disabled",
                        "default_candidates",
                    }
                )
                outcome = result.status
                if result.status == "degraded" and result.reasons and not failures:
                    outcome = "ok"
                if result.status == "degraded":
                    for reason in failures:
                        events.record(
                            "search_degraded",
                            "request",
                            reason,
                            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                            result_count=len(result.items),
                        )
                timing.update(
                    outcome=outcome,
                    result_count=len(result.items),
                    reasons=result.reasons,
                    effective_limit=command.limit,
                )
                if logger.isEnabledFor(logging.INFO):
                    timing["results"] = [
                        {
                            "rank": rank,
                            "item_id": item.item_id[:256],
                            "score": item.score,
                            "score_source": item.score_source,
                            "score_details": (
                                asdict(item.score_details)
                                if item.score_details is not None
                                else None
                            ),
                        }
                        for rank, item in enumerate(result.items[:20], 1)
                    ]
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

    async def _validated_execute(self, command):
        with stage_timing("validation"):
            if command.scene != "default" or command.extensions:
                raise UnsupportedSearchOption(
                    "only the default catalog policy is configured"
                )
            if any(
                not is_label_filter(k)
                for k in command.filters.keys() - self._filter_fields
            ):
                raise UnsupportedSearchOption("unsupported catalog filter")
            if any(not values for values in command.filters.values()):
                return SearchResult()
        return await self._execute(command)

    async def _execute(self, command):
        result = await self._retrieval.retrieve(
            RetrievalRequest(
                command.query,
                command.limit,
                dict(command.filters),
            )
        )
        return replace(result, items=result.items[: command.limit])
