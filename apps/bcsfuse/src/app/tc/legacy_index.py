"""Temporary TC-only reader for the old Worker/Profile/vector persistence."""

from search_core.api.cards import CardRecord, CatalogConflict
from search_core.catalog.cards import CardSnapshot

from app.domain.services.vector_persistence_backend import (
    IncrementalVectorPersistenceBackend,
)
from app.tc.catalog import TcCatalog, TcDocumentConverter


class LegacyTcCatalog(TcCatalog):
    """Read unmigrated identities from old stores; all mutations target new Cards."""

    def __init__(self, repository, workers, profiles, states):
        super().__init__(repository)
        self.workers, self.profiles, self.states = workers, profiles, states

    def get(self, worker_id):
        current = self.repository.get(worker_id)
        if current is not None:
            return current if not current.card.deleted else None
        worker = self.workers.get_by_id(worker_id)
        if worker is None:
            return None
        active = worker.active_profile_key
        profile = (
            self.profiles.get_profile(worker_id, active[len(worker_id) + 1 :])
            if active and active.startswith(worker_id + ":")
            else self.profiles.get_active_profile_for_worker(worker_id)
        ) or {}
        state = self.states.get_runtime_state(worker_id)
        stored = worker.model_dump(mode="json")
        data = {
            "name": worker.identity.name,
            "handle": worker.identity.handle,
            "description": profile.get("description") or worker.identity.description,
            "type": stored["type"],
            "domains": worker.domains,
            "responsibilities": worker.responsibilities,
            "runtime_state": getattr(state, "value", state) or "offline",
            "availability": stored["state"]["availability"],
            "trust_level": stored["state"]["trust_level"],
            "lifecycle_state": stored["lifecycle_state"],
            "profile_id": profile.get("profile_id", "default"),
            "skills": profile.get("skill_sets") or [],
        }
        revision = worker.version
        contents = profile.get("contents") or {}
        return CardRecord(
            CardSnapshot(
                agent_id=worker_id, source_version=str(revision), source_card=data
            ),
            revision,
            0,
            None,
            "",
            profile={"source_revision": revision, "content": contents},
            config=worker.config.model_dump(mode="json"),
        )

    def rows(self):
        seen = set()
        for row in super().rows():
            seen.add(row.card.agent_id)
            yield row
        offset = 0
        while workers := self.workers.list(limit=100, offset=offset):
            for worker in workers:
                if worker.id not in seen:
                    row = self.get(worker.id)
                    if row is not None:
                        yield row
            offset += len(workers)

    def _import_current(self, worker_id):
        current = self.repository.get(worker_id)
        if current is not None:
            if current.config is not None or current.card.deleted:
                return False
            worker = self.workers.get_by_id(worker_id)
            if worker is None:
                return False
            config = worker.config.model_dump(mode="json")
        else:
            old = self.get(worker_id)
            if old is None:
                return False
            self.validate(old.card.source_card)
            # A concurrent sync must never be overwritten by this legacy snapshot.
            current = self.repository.save(old.card, expected_revision=0)
            config = old.config or {}
        if not self.repository.save_config(worker_id, current.revision, config):
            raise CatalogConflict("configuration changed during legacy import")
        return True

    def sync(self, worker_id, payload):
        self._import_current(worker_id)
        return super().sync(worker_id, payload)

    def update(self, worker_id, fields):
        self._import_current(worker_id)
        return super().update(worker_id, fields)

    def save_config(self, worker_id, fields, version=None):
        old = self.require(worker_id)
        if version is not None and old.revision != version:
            raise CatalogConflict("configuration version conflict")
        imported = self._import_current(worker_id)
        return super().save_config(worker_id, fields, None if imported else version)

    def delete(self, worker_id):
        self._import_current(worker_id)
        return super().delete(worker_id)


class LegacyTcIndex:
    """Translate old fragment payloads; enforce current TC labels before top-k."""

    def __init__(self, store, catalog, filters, allowed_ids):
        self.store, self.catalog = store, catalog
        self.filters, self.allowed_ids = filters, allowed_ids
        self.ids = frozenset(
            row.card.agent_id
            for row in catalog.rows()
            if self.eligible(row.card.agent_id, filters, allowed_ids)
        )

    def eligible(self, worker_id, filters, allowed_ids):
        row = self.catalog.get(worker_id)
        if row is None or (allowed_ids is not None and worker_id not in allowed_ids):
            return False
        labels = TcDocumentConverter().convert(row.card).filters
        return all(
            values and set(values).intersection(labels.get(key, ()))
            for key, values in filters.items()
        )

    def _search(self, query, limit, *, keyword):
        if not self.ids:
            return []
        method = self.store.text_search if keyword else self.store.search
        hits = method(query, limit, filters={"worker_id": list(self.ids)})
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
            if hit.payload.get("worker_id") in self.ids
        ]

    def vector_search(self, vector, limit, filters, allowed_ids):
        return self._search(vector, limit, keyword=False)

    def keyword_search(self, query, limit, filters, allowed_ids):
        return self._search(query, limit, keyword=True)


class LegacyTcSource:
    def __init__(self, repository, stores):
        self.workers, self.profiles, self.states, self.vectors = stores
        self.catalog = LegacyTcCatalog(
            repository, self.workers, self.profiles, self.states
        )

    def restore(self):
        self.vectors.rebuild_from_backend()

    def query_index(self, filters, allowed_ids):
        if isinstance(self.vectors._persistence, IncrementalVectorPersistenceBackend):
            self.vectors.sync_incremental()
        else:
            self.vectors.rebuild_from_backend()
        return LegacyTcIndex(self.vectors, self.catalog, filters, allowed_ids)

    def close(self):
        for store in (self.vectors, self.profiles, self.states, self.workers):
            close = getattr(store, "close", None)
            if close is not None:
                close()
