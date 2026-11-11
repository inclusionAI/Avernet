"""One leased producer per Card revision; every replica restores durable artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from contextlib import nullcontext
from dataclasses import replace
from threading import Lock
from time import monotonic
from uuid import uuid4

from search_core.api.cards import CatalogConflict, IndexTargetMismatch
from search_core.api.index_builds import IndexBuildConflict
from search_core.catalog.cards import CardSnapshot, is_label_filter, label_filters
from search_core.catalog.conversion import DefaultCardDocumentConverter
from search_core.catalog.documents import build_documents, load_group, source_id
from search_core.catalog.inspection import inspect_documents, inspect_index
from search_core.config.index_sync import IndexSyncControl
from search_core.core.card_management import CardManagementService
from search_core.core.index_sync import apply_snapshot_pages
from search_core.core.observation import events
from search_core.core.preparation_lease import PreparationLease
from search_core.core.source_rebuild import rebuild_source
from search_core.plugin_api.card_documents import CardDocumentConverter
from search_core.plugin_api.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)
CONVERSION_VERSION = "default-card-v1"


def configuration_index_key(
    embedding_key,
    filter_fields,
    converter=None,
    *,
    conversion_version="",
):
    """Legacy implicit identity, retained for existing library consumers only."""
    conversion_version = conversion_version or (
        converter.version if converter is not None else CONVERSION_VERSION
    )
    if converter is not None and (
        not isinstance(converter.version, str) or not converter.version.strip()
    ):
        raise ValueError("custom document converter requires an explicit version")
    key = f"{conversion_version}|{embedding_key}|{','.join(sorted(filter_fields))}"
    if converter is not None:
        key += "|converter:" + converter.version
    return hashlib.sha256(key.encode()).hexdigest()


class _IndexApplyFailure(RuntimeError):
    pass


class CardUpdates(CardManagementService):
    def __init__(
        self,
        repository,
        embedding: EmbeddingProvider,
        index,
        *,
        embedding_key: str,
        instance_id: str,
        filter_fields: frozenset[str] = frozenset(),
        lease_seconds: float = 60.0,
        on_change=nullcontext,
        document_converter: CardDocumentConverter | None = None,
        sync_control: IndexSyncControl | None = None,
        index_key: str = "",
        conversion_version: str = "",
    ):
        if not embedding_key.strip() or not instance_id.strip():
            raise ValueError("embedding_key and instance_id are required")
        if not math.isfinite(lease_seconds) or lease_seconds < 0.1:
            raise ValueError("lease must be at least 0.1 seconds")
        for field in filter_fields:
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", field) or field in {
                "source_id",
                "search_text",
                "rerank_text",
                "source_version",
                "index_key",
            }:
                raise ValueError(f"unsupported filter field: {field}")
        super().__init__(repository)
        self.embedding, self.index = embedding, index
        self.embedding_key, self.instance_id = embedding_key, instance_id
        self.filter_fields = filter_fields
        self.conversion_version = conversion_version or (
            document_converter.version if document_converter else CONVERSION_VERSION
        )
        self.document_converter = document_converter or DefaultCardDocumentConverter(
            filter_fields=filter_fields
        )
        if index_key and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", index_key
        ):
            raise ValueError("invalid index key")
        self.index_key = index_key or configuration_index_key(
            embedding_key,
            filter_fields,
            document_converter,
            conversion_version=self.conversion_version,
        )
        self.documents = repository.index_documents(self.index_key)
        self.lease_seconds = lease_seconds
        self._applied, self._errors, self._operations_observed = {}, {}, {}
        self._document_versions = {}
        self._source_docs = {}
        self._active = frozenset()
        self._eligibility = {}
        self._checkpoint = 0.0
        self._loaded = self.ready = False
        self._lock = Lock()
        self._on_change = on_change
        self.sync_control = sync_control or IndexSyncControl()
        self._last_reconcile = None  # No successful full check yet.
        # Stable 0-10% delay spreads replicas without delaying startup recovery.
        self._schedule_factor = (
            1
            + int(hashlib.sha256(instance_id.encode()).hexdigest()[:8], 16)
            / 0xFFFFFFFF
            * 0.1
        )

    def rebuild(self, agent_id, index_key):
        if index_key != self.index_key:
            raise IndexTargetMismatch("target index is not loaded on this instance")

        def prepare(row):
            documents = self._prepare_documents(replace(row, prepared=None), [])
            documents[0].metadata["conversion_version"] = self.conversion_version
            return documents

        try:
            return rebuild_source(
                self.repository,
                agent_id,
                index_key=index_key,
                read_source=self.repository.read_card,
                prepare_documents=prepare,
                lease_seconds=self.lease_seconds,
            )
        except IndexBuildConflict as error:
            raise CatalogConflict(str(error)) from error

    def _prepare_documents(self, row, previous):
        return build_documents(
            row,
            self.embedding,
            self.embedding_key,
            self.filter_fields,
            self.document_converter,
            previous,
        )

    def _previous_documents(self, key, previous):
        if previous is not None:
            return load_group(self.documents, key)
        return self.documents.load_vector_candidates(key, self.embedding_key)

    def _restore_auxiliary(self, document):
        """Applications may restore prepared business state without model calls."""

    def save(self, card: CardSnapshot, expected_revision: int):
        # Business projection belongs to the leased builder, not management.
        return self.repository.save(card, expected_revision)

    def delete(self, agent_id: str, expected_revision: int):
        return self.repository.delete(agent_id, expected_revision)

    def searchable_ids(self):
        return self._active

    def eligible(self, agent_id, filters):
        entry = self._eligibility.get(agent_id)
        return (
            agent_id in self._active
            and entry is not None
            and not entry[1]
            and all(
                values and set(values).intersection(entry[2].get(key, ()))
                for key, values in filters.items()
            )
        )

    def _observe_eligibility(self, card, revision, filters):
        previous = self._eligibility.get(card.agent_id)
        entry = (revision, card.deleted, filters)
        if previous != entry and (previous is None or revision >= previous[0]):
            with self._on_change():
                self._eligibility = {**self._eligibility, card.agent_id: entry}

    def status(self, agent_id):
        row = self.read(agent_id)
        stored = self.documents.load_by_source(agent_id)
        doc = next((item for item in stored if item.id == agent_id), None)
        documents, fields = inspect_documents(stored, self.filter_fields)
        return {
            "agent_id": agent_id,
            "source_version": row.card.source_version,
            "saved_revision": row.revision,
            "deleted": row.card.deleted,
            "prepared": doc is not None and doc.source_revision == row.revision,
            "preparation_error": (
                row.error_code if row.preparation_key == self.index_key else ""
            ),
            "instance_id": self.instance_id,
            "environment": self.repository.environment,
            "index_key": self.index_key,
            "local_applied_revision": self._applied.get(agent_id, 0),
            "document_version": doc.version if doc is not None else 0,
            "local_applied_document_version": self._document_versions.get(agent_id, 0),
            "local_error": self._errors.get(agent_id, ""),
            "operations_text_revision": self._operations_observed.get(agent_id, 0),
            "documents": documents,
            "qdrant": inspect_index(self.index, agent_id, fields),
        }

    def _apply_documents(self, documents):
        documents = [
            d for d in documents if self._document_versions.get(d.id, 0) < d.version
        ]
        if not documents:
            return
        with self._on_change():
            self._apply_changed_documents(documents)

    def _apply_changed_documents(self, changed):
        for source in dict.fromkeys(source_id(d) for d in changed):
            try:
                documents = load_group(self.documents, source)
                if not documents:
                    raise ValueError("document group missing")
                main = documents[0]
                revision = main.source_revision
                filters = (
                    main.filters
                    if main.filters is not None
                    else main.metadata.get("filters", {})
                )
                previous = self._eligibility.get(source)
                entry = (revision, main.is_deleted, filters)
                if previous is None or revision >= previous[0]:
                    self._eligibility = {**self._eligibility, source: entry}
                self._restore_auxiliary(main)
                self._operations_observed[source] = revision
                # Readiness need not wait for preparation, but saved deletion must
                # already hide an older artifact when restoration makes it visible.
                current = self.repository.read_card(source)
                if current is not None:
                    self._observe_pending(current)
                if main.is_deleted:
                    self._active = self._active - {source}
                current_ids = {d.id for d in documents if not d.is_deleted}
                removed = self._source_docs.get(source, set()) - current_ids
                removed |= {
                    d.id
                    for d in changed
                    if source_id(d) == source
                    and d.is_deleted
                    and d.id not in current_ids
                }
                removed |= {d.id for d in documents if d.is_deleted}
                filter_fields = set(self.filter_fields) | set(
                    main.metadata.get("filters", {})
                )
                points = []
                for document in documents:
                    if document.is_deleted:
                        continue
                    point = document.to_index_point(filter_fields)
                    points.append(
                        point.model_copy(
                            update={
                                "payload": {
                                    **point.payload,
                                    "index_key": self.index_key,
                                }
                            }
                        )
                    )
                if points:
                    self.index.upsert(points)
                if removed:
                    self.index.delete(list(removed))
            except Exception as error:
                self._errors[source] = "INDEX_APPLY_FAILED"
                raise _IndexApplyFailure("local index apply failed") from error
            self._source_docs[source] = current_ids
            self._applied[source] = revision
            for d in documents:
                self._document_versions[d.id] = d.version
            for d in changed:
                if source_id(d) == source and d.id not in current_ids:
                    self._document_versions[d.id] = d.version
            self._errors.pop(source, None)
            if not main.is_deleted and not self._eligibility[source][1]:
                self._active = self._active | {source}

    def _advance(self, value):
        self._checkpoint = value

    def _restore(self):
        checkpoint = self.documents.get_last_modified_time()
        ids = getattr(self.index, "get_vector_ids", None)
        stale = set(ids()) if ids is not None else set()
        apply_snapshot_pages(
            self.documents.iter_version_batches(100), 100, self._apply_documents, stale
        )
        stale.difference_update(self._document_versions)
        if stale:
            self.index.delete(list(stale))
        self._checkpoint = checkpoint
        self._loaded = True

    def _sync(self):
        changed = 0
        for page in self.documents.iter_change_versions_since(self._checkpoint, 100):
            changed += sum(
                self._document_versions.get(d.id, 0) < d.version for d in page
            )
            self._apply_documents(page)
            # Do not advance after a partially failed group/page.
            self._advance(max(self._checkpoint, page[-1].modified_at))
        if changed:
            events.record(
                "search_index_sync", "incremental", "applied", documents=changed
            )

    def _reconcile_if_due(self):
        interval = (
            self.sync_control.current.reconcile_interval_ms
            / 1000
            * self._schedule_factor
        )
        if (
            self._last_reconcile is not None
            and monotonic() - self._last_reconcile < interval
        ):
            return
        # A transaction may commit after a newer timestamp was consumed. Scan
        # only IDs/versions, loading vectors solely for missing/stale documents.
        scanned = changed = 0
        for page in self.documents.iter_version_batches(100):
            scanned += len(page)
            changed += sum(
                self._document_versions.get(d.id, 0) < d.version for d in page
            )
            self._apply_documents(page)
        self._last_reconcile = monotonic()
        events.record(
            "search_index_sync",
            "reconcile",
            "applied",
            scanned=scanned,
            changed=changed,
        )

    def _observe_pending(self, row):
        key = row.card.agent_id
        if self._operations_observed.get(key, 0) >= row.revision:
            return
        with self._on_change():
            self._publish_pending(row)

    def _publish_pending(self, row):
        key = row.card.agent_id
        # Explicit source labels are authoritative as soon as observed. Keep
        # converter-derived filters until the next document projection arrives.
        previous = self._eligibility.get(key)
        filters = (
            {k: v for k, v in previous[2].items() if not is_label_filter(k)}
            if previous
            else {}
        )
        filters.update(label_filters(row.card.labels))
        self._observe_eligibility(row.card, row.revision, filters)
        if row.card.deleted:
            self._active = self._active - {key}
        self._operations_observed[key] = row.revision

    def _prepare(self, row, previous):
        key, revision, token = row.card.agent_id, row.revision, uuid4().hex
        if not self.repository.claim_preparation(
            key, revision, self.index_key, token, self.lease_seconds
        ):
            return
        try:
            with PreparationLease(
                self.repository,
                key,
                revision,
                self.index_key,
                token,
                self.lease_seconds,
            ) as lease:
                previous_group = self._previous_documents(key, previous)
                documents = self._prepare_documents(row, previous_group)
                documents[0].metadata["conversion_version"] = self.conversion_version
                if not lease.lost.is_set():
                    self.repository.publish_documents(documents, self.index_key, token)
        except Exception:  # noqa: BLE001 -- sanitized retry state; no success acknowledgement
            self.repository.release_preparation(
                key, revision, self.index_key, token, "PREPARATION_FAILED"
            )
            return
        self.repository.release_preparation(key, revision, self.index_key, token, "")

    def run_once(self):
        with self._lock:
            try:
                startup = not self._loaded
                preparation_attempted = False
                # A failed index apply does not prevent central preparation.
                try:
                    if not self._loaded:
                        self._restore()
                    self._sync()
                    # Restored artifacts can serve while pending Cards are prepared.
                    self.ready = self._loaded and not self._errors
                except _IndexApplyFailure:
                    self.ready = False
                after = ""
                while True:
                    rows = self.repository.page(after, 100)
                    for key, revision in rows:
                        if self._applied.get(key) == revision:
                            continue
                        document = self.documents.load_by_id(key)
                        if (
                            document is not None
                            and document.source_revision == revision
                        ):
                            # Central publication completed but the local cursor
                            # may have passed its timestamp. Apply it directly.
                            try:
                                self._apply_documents([document])
                            except _IndexApplyFailure:
                                self.ready = False
                            continue
                        row = self.repository.get(key)
                        if row is not None:
                            self._observe_pending(row)
                            preparation_attempted = True
                            self._prepare(row, document)
                    if len(rows) < 100:
                        break
                    after = rows[-1][0]
                try:
                    if not self._loaded:
                        self._restore()
                    if startup or preparation_attempted:
                        self._sync()
                    self._reconcile_if_due()
                except _IndexApplyFailure:
                    self.ready = False
                    return
                self.ready = self._loaded and not self._errors
            except Exception:
                self.ready = False
                raise

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001 -- keep retrying without exposing DB credentials
                logger.warning("Card catalog poll failed; will retry")
            completed = monotonic()
            while not stop.is_set():
                interval = (
                    self.sync_control.current.incremental_interval_ms
                    / 1000
                    * self._schedule_factor
                )
                remaining = completed + interval - monotonic()
                if remaining <= 0:
                    break
                try:
                    # Re-read DRM within one second without touching the database.
                    await asyncio.wait_for(stop.wait(), min(1.0, remaining))
                except TimeoutError:
                    pass
