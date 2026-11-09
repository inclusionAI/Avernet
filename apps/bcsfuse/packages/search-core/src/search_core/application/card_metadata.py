"""Batch-enrich candidates using an application projection and bounded cache."""

import asyncio
from copy import deepcopy
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
                    await self._fetch_missing(cards, missing, revision, stage)
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

    async def _load_batch(self, keys, revision):
        fetched = await self.execution.call(self.repository.get_many_cards, keys)
        cards = {}
        for key, card in fetched.items():
            cards[key] = self.project_card(card)
            self.state.put_result(self.cache, revision, key, cards[key])
        return cards

    async def _fetch_missing(self, cards, missing, revision, stage):
        if revision is None or not self.cache.enabled:
            cards.update(await self._load_batch(missing, revision))
            return

        pending = self.state.pending_card_metadata
        lookups = {key: pending.get((revision, key)) for key in missing}
        new_keys = [key for key, task in lookups.items() if task is None]
        stage["shared_loads"] = len(lookups) - len(new_keys)
        if new_keys:
            task = asyncio.create_task(self._load_batch(new_keys, revision))
            # Publish the in-flight batch before yielding to other requests.
            for key in new_keys:
                pending[revision, key] = lookups[key] = task

            def forget(completed):
                for key in new_keys:
                    pending.pop((revision, key))
                if not completed.cancelled():
                    completed.exception()  # The last waiter may have been cancelled.

            task.add_done_callback(forget)

        tasks = dict.fromkeys(lookups.values())
        outcomes = await asyncio.gather(
            *(asyncio.shield(task) for task in tasks), return_exceptions=True
        )
        loaded = dict(zip(tasks, outcomes))
        error = None
        for key, task in lookups.items():
            batch = loaded[task]
            if isinstance(batch, BaseException):
                error = batch
            elif key in batch:
                cards[key] = deepcopy(batch[key])
        if error is not None:
            raise error
