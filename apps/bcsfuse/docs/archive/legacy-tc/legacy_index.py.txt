"""TC old-table vector writes and retrieval; no Card/Doc repository emulation."""

import asyncio
import logging

from app.domain.models.vector_point import VectorPoint
from app.domain.services.vector_persistence_backend import (
    IncrementalVectorPersistenceBackend,
    WorkerScopedVectorPersistenceBackend,
)
from app.tc.catalog import TcDocumentConverter

logger = logging.getLogger(__name__)


def matches(catalog, worker_id, filters):
    row = catalog.get(worker_id)
    if row is None:
        return False
    labels = TcDocumentConverter().convert(row.card).filters
    return all(
        values and set(values).intersection(labels.get(key, ()))
        for key, values in filters.items()
    )


class LegacyUpdates:
    def __init__(self, catalog, vectors, embedding):
        self.catalog, self.vectors, self.embedding = catalog, vectors, embedding
        self.index_key = f"legacy:{vectors.collection_name}"
        self.ready = False
        self.applied = {}
        self._lock = catalog.lock
        self.on_change = None

    def restore(self):
        self.vectors.rebuild_from_backend()
        # Reconcile the original durable stores after a failed write or crash.
        # This never reads or writes Card/Doc storage.
        revisions = {}
        for page in self.vectors._persistence.iter_batches(100):
            for point in page:
                worker_id = point.payload.get("worker_id")
                if worker_id:
                    revisions.setdefault(worker_id, set()).add(
                        point.payload.get("_tc_revision")
                    )
        for row in self.catalog.rows():
            worker_id = row.card.agent_id
            persisted = revisions.pop(worker_id, set())
            if not persisted or (
                row.profile["tc_managed"] and persisted != {row.revision}
            ):
                self.catalog.dirty.add(worker_id)
            else:
                self.applied[worker_id] = row.revision
        self.catalog.dirty.update(revisions)  # Deleted owners with orphan vectors.
        self.ready = not self.catalog.dirty

    def worker_points(self, worker_id):
        backend = self.vectors._persistence
        if isinstance(backend, WorkerScopedVectorPersistenceBackend):
            return backend.load_by_worker(worker_id)
        return [
            point
            for page in backend.iter_batches(100)
            for point in page
            if point.payload.get("worker_id") == worker_id
        ]

    def run_once(self):
        with self._lock:
            for worker_id in self.catalog.dirty.copy():
                self.ready = False
                row = self.catalog.get(worker_id)
                previous = self.worker_points(worker_id)
                if row is None:
                    with self.catalog.transaction(worker_id):
                        if self.catalog.get(worker_id) is not None:
                            continue
                        if previous:
                            self.vectors.delete([p.id for p in previous])
                        self.applied[worker_id] = 0
                        self.catalog.dirty.discard(worker_id)
                    continue
                projection = TcDocumentConverter().convert(row.card)
                # The original durable format has no Doc table. Keep one current text,
                # with the same bounded text policy, and retire old fragments on update.
                points = []
                for i, text in enumerate(projection.documents):
                    key = f"{worker_id}:{row.card.source_card['profile_id']}:{'full' if i == 0 else i}"
                    reused = next(
                        (
                            p.vector
                            for p in previous
                            if p.payload.get("content") == text.search_text
                            and len(p.vector) == self.embedding.dimension
                        ),
                        None,
                    )
                    vector = (
                        reused
                        if reused is not None
                        else self.embedding.embed(text.search_text)
                    )
                    payload = {
                        **row.card.source_card,
                        "worker_id": worker_id,
                        "profile_key": self.catalog.profile_key(row),
                        "content": text.search_text,
                        "_tc_revision": row.revision,
                    }
                    payload.pop("skills", None)
                    points.append(VectorPoint(id=key, vector=vector, payload=payload))
                with self.catalog.transaction(worker_id):
                    latest = self.catalog.get(worker_id)
                    if (
                        latest is None
                        or latest.revision != row.revision
                        or latest.card.source_card != row.card.source_card
                    ):
                        continue
                    self.vectors.upsert(points)
                    stale = {p.id for p in previous} - {p.id for p in points}
                    if stale:
                        self.vectors.delete(list(stale))
                    self.applied[worker_id] = row.revision
                    self.catalog.dirty.discard(worker_id)
            self.ready = not self.catalog.dirty

    def status(self, worker_id):
        row = self.catalog.get(worker_id)
        return {
            "saved_revision": row.revision if row else 0,
            "local_applied_revision": self.applied.get(worker_id, 0),
        }

    def eligible(self, worker_id, filters):
        return worker_id not in self.catalog.dirty and matches(
            self.catalog, worker_id, filters
        )

    async def run(self, stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.run_once)
                if isinstance(
                    self.vectors._persistence, IncrementalVectorPersistenceBackend
                ):
                    changed = await asyncio.to_thread(self.vectors.sync_incremental)
                    if any(changed.values()) and self.on_change is not None:
                        with self.on_change():
                            pass
            except Exception:
                self.ready = False
                logger.exception("TC old-table index synchronization failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass


class LegacyRetrievalIndex:
    def __init__(self, catalog, vectors, updates):
        self.catalog, self.vectors, self.updates = catalog, vectors, updates

    def _search(self, query, limit, filters, *, keyword):
        ids = [
            worker_id
            for worker_id in self.catalog.candidate_ids(filters)
            if worker_id not in self.catalog.dirty
        ]
        if not ids:
            return []
        method = self.vectors.text_search if keyword else self.vectors.search
        hits = method(query, limit, filters={"worker_id": ids})
        return [
            hit.model_copy(
                update={
                    "payload": {
                        **hit.payload,
                        "source_id": hit.payload["worker_id"],
                        "search_text": hit.payload.get("content")
                        or hit.payload.get("searchable_text")
                        or "",
                    }
                }
            )
            for hit in hits
            if hit.payload.get("worker_id") in ids
        ]

    def vector_search(self, vector, limit, filters):
        return self._search(vector, limit, filters, keyword=False)

    def keyword_search(self, query, limit, filters):
        return self._search(query, limit, filters, keyword=True)
