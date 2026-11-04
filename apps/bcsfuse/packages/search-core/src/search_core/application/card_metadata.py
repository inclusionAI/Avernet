"""Batch-enrich candidates using an application projection and bounded cache."""

from dataclasses import replace

from search_core.core.observation import events
from search_core.core.timing import stage_timing


def card_metadata(card):
    name = card.source_card.get("name")
    description = card.source_card.get("description")
    return {
        "name": name if isinstance(name, str) else "",
        "description": description if isinstance(description, str) else "",
        "deleted": card.deleted,
    }


def search_item(item, metadata):
    return replace(item, name=metadata["name"], summary=metadata["description"])


class CardMetadataRetrieval:
    def __init__(
        self,
        retrieval,
        repository,
        cache,
        state,
        execution,
        *,
        project_card=card_metadata,
        project_item=search_item,
    ):
        self.retrieval, self.repository = retrieval, repository
        self.cache, self.state, self.execution = cache, state, execution
        self.project_card, self.project_item = project_card, project_item

    async def retrieve(self, request):
        result = await self.retrieval.retrieve(request)
        with stage_timing("card_metadata") as stage:
            stage["input_count"] = len(result.items)
            enriched = await self._enrich(result, stage)
            stage["result_count"] = len(enriched.items)
            if "card_metadata_unavailable" in enriched.reasons:
                stage["outcome"] = "unavailable"
            elif "card_metadata_missing" in enriched.reasons:
                stage["outcome"] = "missing"
            return enriched

    async def _enrich(self, result, stage):
        if not result.items:
            return result
        revision = self.state.revision()
        cards, missing = {}, []
        for item in result.items:
            cached = self.cache.get(item.item_id) if revision is not None else None
            if cached is None:
                missing.append(item.item_id)
            else:
                cards[item.item_id] = cached
        stage.update(cache_hits=len(cards), cache_misses=len(missing))
        try:
            if missing:
                with stage_timing("card_fetch"):
                    fetched = await self.execution.call(
                        self.repository.get_many_cards, missing
                    )
                for key, card in fetched.items():
                    value = self.project_card(card)
                    cards[key] = value
                    self.state.put_result(self.cache, revision, key, value)
        except Exception as error:  # noqa: BLE001 -- preserve valid cached Cards
            events.record(
                "search_stage_failure",
                "card_metadata",
                "unavailable",
                error_type=type(error).__name__,
            )
            # A callable Card is required now; never return bare IDs as if
            # they contained valid invocation data after an enrichment failure.
            result = replace(
                result,
                status="degraded",
                reasons=tuple(
                    dict.fromkeys((*result.reasons, "card_metadata_unavailable"))
                ),
            )
        items = tuple(
            self.project_item(item, cards[item.item_id])
            for item in result.items
            if item.item_id in cards and not cards[item.item_id]["deleted"]
        )
        if "card_metadata_unavailable" not in result.reasons and any(
            item.item_id not in cards for item in result.items
        ):
            return replace(
                result,
                items=items,
                status="degraded",
                reasons=tuple(
                    dict.fromkeys((*result.reasons, "card_metadata_missing"))
                ),
            )
        return replace(result, items=items)
