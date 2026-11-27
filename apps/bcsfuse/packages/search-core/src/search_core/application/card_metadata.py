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


def _cache_key(key, scope):
    return key if scope is None else (scope, key)


class CardMetadataRetrieval:
    def __init__(
        self,
        retrieval,
        repository,
        cache,
        state,
        execution,
        *,
        read_scope=None,
        project_card=card_metadata,
        project_item=search_item,
        project_timeout=None,
        metadata_dependencies=lambda metadata: (),
    ):
        self.retrieval, self.repository = retrieval, repository
        self.cache, self.state, self.execution = cache, state, execution
        self.project_card, self.project_item = project_card, project_item
        self.project_timeout = project_timeout
        self.metadata_dependencies = metadata_dependencies
        self.read_scope = read_scope

    async def retrieve(self, request):
        result = await self.retrieval.retrieve(request)
        return await self.enrich(result, request=request)

    async def enrich(self, result, *, request=None, allow_timeout_projection=True):
        with stage_timing("card_metadata") as stage:
            stage["input_count"] = len(result.items)
            scope = (
                self.read_scope(request.filters)
                if self.read_scope and request
                else None
            )
            enriched = await self._enrich(
                result, stage, allow_timeout_projection, scope
            )
            stage["result_count"] = len(enriched.items)
            if "card_metadata_unavailable" in enriched.reasons:
                stage["outcome"] = "unavailable"
            elif "card_metadata_missing" in enriched.reasons:
                stage["outcome"] = "missing"
            return enriched

    async def _enrich(self, result, stage, allow_timeout_projection, scope):
        if not result.items:
            return result
        revision = self.state.metadata_revision()
        cards, missing, timed_out = {}, [], set()
        stale_hits = 0
        for item in result.items:
            cached, stale = self.cache.get_with_stale(_cache_key(item.item_id, scope))
            if stale:
                refresh = self.state.pending_card_metadata.get(
                    (revision, scope, item.item_id)
                )
                if refresh is None or refresh.done():
                    cached = None  # This request refreshes; followers can use old data.
                else:
                    stale_hits += 1
            if cached is None:
                missing.append(item.item_id)
            else:
                cards[item.item_id] = cached
        stage.update(
            cache_hits=len(cards), cache_misses=len(missing), stale_hits=stale_hits
        )
        try:
            if missing:
                with stage_timing("card_fetch"):
                    await self._fetch_missing(
                        cards, missing, revision, stage, timed_out, scope
                    )
        except Exception as error:  # noqa: BLE001 -- preserve valid cached Cards
            events.record(
                "search_stage_failure",
                "card_metadata",
                "unavailable",
                error_type=type(error).__name__,
            )
            reasons = (*result.reasons, "card_metadata_unavailable")
            if timed_out:
                reasons += ("card_metadata_timeout",)
            result = replace(
                result,
                status="degraded",
                reasons=tuple(dict.fromkeys(reasons)),
            )
        timeout_items = {}
        if allow_timeout_projection and timed_out and self.project_timeout is not None:
            timeout_items = await self._project_timeouts(
                [item for item in result.items if item.item_id in timed_out], scope
            )
        items = []
        for item in result.items:
            if item.item_id in cards:
                if not cards[item.item_id]["deleted"]:
                    items.append(self.project_item(item, cards[item.item_id]))
            elif item.item_id in timeout_items:
                items.append(timeout_items[item.item_id])
        items = tuple(items)
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

    async def _project_timeouts(self, items, scope):
        return {item.item_id: self.project_timeout(item) for item in items}

    async def _load_batch(self, keys, revision, scope):
        read = self.repository.get_many_cards
        if scope is None:
            fetched = await self.execution.call(read, keys)
        else:
            fetched = await self.execution.call(read, keys, scope)
        cards = {}
        for key, card in fetched.items():
            cards[key] = self.project_card(card)
            dependencies = self.metadata_dependencies(cards[key])
            if scope is not None:
                dependencies = (
                    key,
                    *dependencies,
                )  # Catalog invalidations use bare IDs.
            self.state.put_result(
                self.cache,
                revision,
                _cache_key(key, scope),
                cards[key],
                dependencies=dependencies,
            )
        for key in set(keys) - fetched.keys():
            self.state.discard_metadata(self.cache, revision, _cache_key(key, scope))
        return cards

    async def _fetch_missing(self, cards, missing, revision, stage, timed_out, scope):
        if revision is None or not self.cache.enabled:
            try:
                cards.update(await self._load_batch(missing, revision, scope))
            except TimeoutError:
                timed_out.update(missing)
                raise
            return

        pending = self.state.pending_card_metadata
        lookups = {key: pending.get((revision, scope, key)) for key in missing}
        new_keys = [key for key, task in lookups.items() if task is None]
        stage["shared_loads"] = len(lookups) - len(new_keys)
        if new_keys:
            task = asyncio.create_task(self._load_batch(new_keys, revision, scope))
            # Publish the in-flight batch before yielding to other requests.
            for key in new_keys:
                pending[revision, scope, key] = lookups[key] = task

            def forget(completed):
                for key in new_keys:
                    pending.pop((revision, scope, key))
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
                if isinstance(batch, TimeoutError):
                    timed_out.add(key)
            elif key in batch:
                cards[key] = deepcopy(batch[key])
        if error is not None:
            raise error
