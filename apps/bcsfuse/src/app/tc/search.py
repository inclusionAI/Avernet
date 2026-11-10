"""TC candidate policy around shared recall, source aggregation, RRF and reranking."""

import asyncio
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from search_core.api.errors import SearchUnavailable
from search_core.api.models import RetrievalRequest, SearchItem, SearchResult
from search_core.application.cache import CachedEmbedding, CachedRetrieval, SearchCaches
from search_core.application.catalog_retrieval import CatalogRetrieval
from search_core.application.emergency import SearchCachePolicy
from search_core.catalog.cards import is_label_filter
from search_core.core.execution import DeadlineExecutor
from search_core.core.retrieval import HybridRetrievalService

from app.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from app.domain.models.context_fragment import ContextFragment
from app.domain.models.retrieval_mode import RetrievalMode
from app.domain.models.worker_profile import WorkerProfile, WorkerProfileScanResult
from app.tc.card_profiles import TcCardUpdates, current_profile_summary
from app.tc.catalog import FILTER_FIELDS, TcDocumentConverter, matches_row


class InvalidTcFilters(ValueError):
    pass


def _name_score(query, name, domains):
    name = unicodedata.normalize("NFKC", name).strip().casefold()
    if name == query:
        return 1.0
    words = re.findall(r"[a-z0-9]+", name)
    if query in words or any(
        unicodedata.normalize("NFKC", domain).strip().casefold() == query
        for domain in domains
    ):
        return 0.95
    if any(word.startswith(query) for word in words):
        return 0.9
    return 0.0


class TcSearch:
    def __init__(
        self,
        catalog,
        index,
        embedding,
        reranker,
        *,
        embedding_key,
        instance_id,
        updates=None,
        retrieval_index=None,
        profile_gateway=None,
    ):
        self.catalog, self.index, self.reranker = catalog, index, reranker
        self.storage_kind = "legacy" if updates is not None else "card"
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tc-search")
        self.caches = SearchCaches(
            result_entries=1024,
            result_bytes=16 * 1024 * 1024,
            embedding_entries=512,
            embedding_bytes=64 * 1024 * 1024,
        )
        result_cache, embedding_cache, _ = self.caches.configure(
            SearchCachePolicy(
                result={"ttl_ms": 30000},
                query_embedding={"ttl_ms": 600000},
            )
        )
        self.result_cache = result_cache
        self.embedding = CachedEmbedding(
            embedding, embedding_cache, embedding_key, singleflight_enabled=False
        )
        self.updates = (
            updates
            if updates is not None
            else TcCardUpdates(
                catalog.repository,
                embedding,
                index,
                embedding_key=embedding_key,
                instance_id=instance_id,
                filter_fields=FILTER_FIELDS,
                document_converter=TcDocumentConverter(),
                on_change=self.caches.changing,
                profile_gateway=profile_gateway,
            )
        )
        if updates is not None:
            updates.on_change = self.caches.changing
        self.retrieval_index = retrieval_index if retrieval_index is not None else index
        self.execution = {
            name: DeadlineExecutor(self.pool, name=name, timeout_seconds=timeout)
            for name, timeout in (
                ("embedding", 10),
                ("vector", 5),
                ("keyword", 5),
                ("reranker", 20),
            )
        }

    async def retrieve(
        self,
        question,
        limit,
        *,
        filters=None,
        rerank=True,
        expand_factor=10,
        reranker_model=None,
        legacy_defaults=True,
        short_names=True,
    ):
        filters = dict(filters or {})
        if legacy_defaults:
            filters.setdefault("runtime_state", ["online"])
            filters.setdefault("availability", ["protected", "public"])
            filters.setdefault("lifecycle_state", ["active"])
        extra = filters.keys() - FILTER_FIELDS - {"profile_key", "worker_id"}
        if any(legacy_defaults or not is_label_filter(key) for key in extra):
            raise InvalidTcFilters("unsupported TC filter")
        for field, values in filters.items():
            if isinstance(values, str):
                filters[field] = [values]
            elif not isinstance(values, (list, tuple)) or any(
                not isinstance(value, str) for value in values
            ):
                raise InvalidTcFilters(
                    "TC filter values must be strings or string lists"
                )
        # Legacy identities restrict a query; they do not introduce a TC ACL.
        scope = None
        for field in ("profile_key", "worker_id"):
            if field in filters:
                keys = frozenset(
                    key
                    for value in filters.pop(field)
                    if (key := self.catalog.resolve(value)) is not None
                )
                scope = keys if scope is None else scope & keys
        if scope is not None:
            filters["source_id"] = sorted(scope)
        request = RetrievalRequest(
            question, limit, {k: tuple(v) for k, v in filters.items()}
        )
        result = None
        vector_enabled = True
        if short_names and self.storage_kind == "card" and limit > 0:
            name_query = unicodedata.normalize("NFKC", question).strip()
            if re.fullmatch(r"[A-Za-z]{3,6}", name_query):
                try:
                    result = await self.execution["keyword"].call(
                        self._search_names, name_query.casefold(), request
                    )
                except TimeoutError as error:
                    raise SearchUnavailable("TC name search timed out") from error
                if result is None and name_query.isupper():
                    vector_enabled = rerank = False
        if result is None:
            result = await self._hybrid_retrieve(
                request, rerank, expand_factor, reranker_model, vector_enabled
            )
        # TC exact identity priority never bypasses visibility or lifecycle rules.
        exact = self.catalog.get(question.strip())
        if exact and self.eligible(exact.card.agent_id, request.filters):
            item = SearchItem(
                exact.card.agent_id, exact.card.source_card["name"], "", 1.0, "exact_id"
            )
            result = replace(
                result,
                items=(item, *(i for i in result.items if i.item_id != item.item_id))[
                    :limit
                ],
            )
        items = []
        for item in result.items:
            row = self.catalog.get(item.item_id)
            if row is not None:
                data = row.card.source_card
                items.append(
                    replace(
                        item,
                        name=data["name"],
                        summary=data.get("description") or "",
                    )
                )
        return replace(result, items=tuple(items))

    def _search_names(self, query, request):
        candidates = sorted(
            (-score, key)
            for key, (name, domains) in self.updates.search_names.copy().items()
            if (score := _name_score(query, name, domains))
        )
        items = []
        for _, key in candidates:
            if not self.eligible(key, request.filters):
                continue
            row = self.catalog.get(key)
            if row is None:
                continue
            data = row.card.source_card
            score = _name_score(query, data["name"], data.get("domains") or ())
            if score:
                items.append(SearchItem(key, data["name"], "", score, "name_match"))
            if len(items) == request.limit:
                break
        return SearchResult(items=tuple(items)) if items else None

    async def _hybrid_retrieve(
        self, request, rerank, expand_factor, reranker_model, vector_enabled
    ):
        selected_reranker = self.reranker
        if reranker_model and rerank:
            from app.infra.reranker.http_reranker import get_reranker

            selected_reranker = get_reranker(reranker_model)
        pipeline = HybridRetrievalService(
            index=self.retrieval_index,
            embedding=self.embedding,
            reranker=selected_reranker if rerank else None,
            group_by_source=True,
            vector_enabled=vector_enabled,
            candidate_limit=max(30, request.limit),
            recall_limit=max(60, request.limit * expand_factor),
            allow_partial=True,
            execution=self.execution,
        )
        retrieval = CachedRetrieval(
            CatalogRetrieval(pipeline, self),
            self.result_cache,
            self.caches,
            namespace=(rerank, expand_factor, reranker_model, vector_enabled),
        )
        return await retrieval.retrieve(request)

    def eligible(self, worker_id, filters):
        if "source_id" in filters and worker_id not in filters["source_id"]:
            return False
        labels = {key: values for key, values in filters.items() if key != "source_id"}
        return self.updates.eligible(worker_id, labels) and matches_row(
            self.catalog.get(worker_id), labels
        )

    def candidate(self, row, score, *, supplement=False, reasons=None):
        data = row.card.source_card
        return CandidateRecommendation(
            profile_key=self.catalog.profile_key(row),
            worker_id=row.card.agent_id,
            score=score,
            reasons=reasons or [],
            short_profile=(
                current_profile_summary(row) or data.get("description") or ""
            )[:120],
            trust_level=data.get("trust_level", "guarded"),
            is_supplement=supplement,
        )

    async def search(self, question, limit, **options):
        result = await self.retrieve(question, limit, **options)
        candidates = []
        for item in result.items:
            row = self.catalog.get(item.item_id)
            if row:
                candidates.append(
                    self.candidate(
                        row, item.score, reasons=[{"score_source": item.score_source}]
                    )
                )
        return candidates, result

    def recommend(
        self,
        question,
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        participants=None,
        max_candidates=5,
        filters=None,
        runtime_config=None,
        strict_participants=False,
        **ignored,
    ):
        explicit = []
        for participant in participants or []:
            key = self.catalog.resolve(participant)
            if key and key not in {c.worker_id for c in explicit}:
                explicit.append(self.candidate(self.catalog.require(key), 1.0))
        candidates = explicit
        if (
            not (strict_participants and participants)
            and len(explicit) < max_candidates
        ):
            config = runtime_config or {}
            rerank = not (
                "reranker_model" in config and config["reranker_model"] is None
            )
            recalled, _ = asyncio.run(
                self.search(
                    question,
                    min(100, max_candidates + len(explicit)),
                    filters=filters,
                    rerank=rerank,
                    expand_factor=config.get("expand_factor", 10),
                    reranker_model=config.get("reranker_model"),
                    short_names=False,
                )
            )
            selected = {c.worker_id for c in explicit}
            candidates = [
                *explicit,
                *(
                    c.model_copy(update={"is_supplement": True})
                    for c in recalled
                    if c.worker_id not in selected
                ),
            ]
        candidates = candidates[:max_candidates]
        return CandidateRecommendationResponse(
            question=question,
            mode=mode,
            recommendations=candidates,
            participants_given=bool(participants),
            participants_sufficient=len(explicit) >= 3,
            total_candidates=len(candidates),
            selected_candidates=len(candidates),
            metadata={
                "min_score_compat_mode": "deprecated_ignored",
                "candidate_source": f"tc_{self.storage_kind}",
            },
        )


class CardProfileSource:
    """Fuse reads current TC content without filesystem scans or MD enrichment."""

    def __init__(self, catalog):
        self.catalog = catalog

    def get_by_participant(self, participant_id):
        key = self.catalog.resolve(participant_id)
        row = self.catalog.get(key) if key else None
        return self._profile(row) if row is not None else None

    def get_profile(self, staff_id, profile_id):
        row = self.catalog.get(staff_id)
        if (
            row is None
            or row.card.source_card.get("profile_id", "default") != profile_id
        ):
            return None
        return self._profile(row)

    def _profile(self, row):
        content = self.catalog.profile(row)
        text = content.contents.get("profile") or content.description or ""
        return WorkerProfile(
            staff_id=row.card.agent_id,
            profile_id=content.profile_id,
            profile_type="bot"
            if row.card.source_card.get("type", "bot") == "bot"
            else "default",
            source_type="api",
            source_root="tc-card",
            searchable_text=text,
            short_profile=(content.description or "")[:120],
            context_fragments=[
                ContextFragment(
                    filename="description", content=text, source_path="tc-card"
                )
            ],
        )

    def scan(self):
        return WorkerProfileScanResult(
            profiles=[
                self.get_profile(
                    row.card.agent_id, row.card.source_card.get("profile_id", "default")
                )
                for row in self.catalog.rows()
            ]
        )

    def scan_for_workers(self, worker_ids):
        profiles = []
        for key in worker_ids:
            resolved = self.catalog.resolve(key)
            row = self.catalog.get(resolved) if resolved else None
            if row:
                profiles.append(
                    self.get_profile(
                        resolved, row.card.source_card.get("profile_id", "default")
                    )
                )
        return WorkerProfileScanResult(profiles=profiles)

    def get_profiles_by_staff(self, staff_id):
        return self.scan_for_workers([staff_id]).profiles
