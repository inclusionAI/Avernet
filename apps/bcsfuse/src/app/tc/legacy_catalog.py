"""TC-only current-profile operations against the original Worker/Profile tables."""

from contextlib import nullcontext
from threading import RLock

from search_core.api.cards import CardRecord, CatalogConflict
from search_core.catalog.cards import CardSnapshot

from app.domain.models.worker import Worker, WorkerIdentity, WorkerState
from app.domain.models.worker_config import WorkerConfig
from app.domain.models.worker_profile_content import WorkerProfileContent
from app.domain.models.worker_runtime_state import WorkerRuntimeState
from app.tc.catalog import TcCatalog, sync_changes


class LegacyCatalog(TcCatalog):
    """Reuse TC projections only; this is not a public Card repository."""

    def __init__(self, workers, profiles, states, transaction=nullcontext):
        self.workers, self.profiles, self.states = workers, profiles, states
        self.dirty = set()
        self.lock = RLock()
        self.transaction = transaction

    def _profile(self, worker):
        key = worker.active_profile_key
        if key and key.startswith(worker.id + ":"):
            return self.profiles.get(worker.id, key[len(worker.id) + 1 :])
        return self.profiles.get_active(worker.id)

    def get(self, worker_id):
        worker = self.workers.get_by_id(worker_id)
        if worker is None:
            return None
        content = self._profile(worker)
        stored = worker.model_dump(mode="json")
        state = self.states.get_runtime_state(worker_id)
        data = {
            "name": worker.identity.name,
            "handle": worker.identity.handle,
            "description": content.description
            if content and content.description is not None
            else worker.identity.description,
            "type": stored["type"],
            "domains": worker.domains,
            "responsibilities": worker.responsibilities,
            "runtime_state": getattr(state, "value", state)
            or stored["state"].get("runtime_state")
            or "offline",
            "availability": stored["state"]["availability"],
            "trust_level": stored["state"]["trust_level"],
            "lifecycle_state": stored["lifecycle_state"],
            "profile_id": content.profile_id if content else "default",
            "skills": content.model_dump(mode="json")["skill_sets"] if content else [],
        }
        return CardRecord(
            CardSnapshot(
                agent_id=worker_id, source_version=str(worker.version), source_card=data
            ),
            worker.version,
            0,
            None,
            "",
            profile={
                "source_revision": worker.version,
                "content": content.contents if content else {},
                "tc_managed": bool(
                    content and content.metadata.get("_tc_worker_revision")
                ),
            },
            config=worker.config.model_dump(mode="json"),
        )

    def rows(self):
        offset = 0
        while workers := self.workers.list(limit=100, offset=offset):
            for worker in workers:
                row = self.get(worker.id)
                if row is not None:
                    yield row
            offset += len(workers)

    def candidate_ids(self, filters):
        """Read filter labels in batches; retrieval never needs Profile/MD data."""
        scope = filters.get("source_id")
        labels_filter = {key: values for key, values in filters.items() if key != "source_id"}
        offset = 0
        while True:
            if scope is None:
                workers = self.workers.list(limit=100, offset=offset)
                if not workers:
                    break
            else:
                ids = scope[offset : offset + 100]
                if not ids:
                    break
                workers = list(self.workers.get_by_ids(ids).values())
            states = self.states.batch_get_runtime_states([w.id for w in workers])
            for worker in workers:
                state = states.get(worker.id, worker.state.runtime_state)
                data = {
                    **worker.state.model_dump(mode="json"),
                    "runtime_state": getattr(state, "value", state),
                    "type": worker.type,
                    "domains": worker.domains,
                    "lifecycle_state": worker.lifecycle_state,
                }
                labels = {
                    key: [value] if isinstance(value, str) else value
                    for key, value in data.items()
                }
                if all(
                    values and set(values).intersection(labels.get(key, ()))
                    for key, values in labels_filter.items()
                ):
                    yield worker.id
            offset += 100

    def worker(self, row):
        worker = self.workers.get_by_id(row.card.agent_id)
        data = row.card.source_card
        return worker.model_copy(
            update={
                "identity": worker.identity.model_copy(
                    update={
                        "name": data["name"],
                        "description": data.get("description"),
                    }
                ),
                "state": WorkerState.model_validate(
                    {
                        **worker.state.model_dump(mode="json"),
                        "runtime_state": data["runtime_state"],
                        "availability": data["availability"],
                        "trust_level": data["trust_level"],
                    }
                ),
            }
        )

    def profile(self, row):
        worker = self.workers.get_by_id(row.card.agent_id)
        content = self._profile(worker)
        return content if content is not None else super().profile(row)

    def sync(self, worker_id, payload):
        with self.lock, self.transaction():
            current = self.get(worker_id)
            changes = sync_changes(current, payload)
            data = {**(current.card.source_card if current else {}), **changes}
            self._save(worker_id, data, current.revision if current else 0)
            return self.require(worker_id), current is None

    def update(self, worker_id, fields):
        with self.lock, self.transaction():
            current = self.require(worker_id)
            self._save(
                worker_id, {**current.card.source_card, **fields}, current.revision
            )
            return self.require(worker_id)

    def _save(self, worker_id, data, revision):
        self.validate(data)
        original = self.workers.get_by_id(worker_id)
        if (original.version if original else 0) != revision:
            raise CatalogConflict("legacy Worker changed; retry")
        profile = self._profile(original) if original else None
        identity = WorkerIdentity(
            name=data["name"],
            handle=data.get("handle") or worker_id,
            description=data.get("description"),
        )
        state = WorkerState.model_validate(data)
        if original is None:
            worker = Worker(
                id=worker_id,
                type=data["type"],
                identity=identity,
                responsibilities=data["responsibilities"],
                domains=data["domains"],
                capabilities=[],
                state=state,
                lifecycle_state=data["lifecycle_state"],
            )
        else:
            worker = Worker.model_validate(
                {
                    **original.model_dump(mode="json"),
                    "type": data["type"],
                    "identity": identity,
                    "state": state,
                    "domains": data["domains"],
                    "responsibilities": data["responsibilities"],
                    "lifecycle_state": data["lifecycle_state"],
                }
            )
        worker.active_profile_key = f"{worker_id}:{data['profile_id']}"
        self.dirty.add(worker_id)
        if original is None:
            worker = self.workers.create(worker)
        else:
            worker = self.workers.update(worker)
        if profile is None:
            profile = WorkerProfileContent(
                worker_id=worker_id, profile_id=data["profile_id"], is_active=True
            )
        skills = profile.model_dump(mode="json")["skill_sets"]
        contents = dict(profile.contents)
        if profile.description != data.get("description") or skills != data["skills"]:
            contents.pop("profile", None)
        profile = WorkerProfileContent.model_validate(
            {
                **profile.model_dump(mode="json"),
                "display_name": data["name"],
                "description": data.get("description"),
                "skill_sets": data["skills"],
                "contents": contents,
                "metadata": {**profile.metadata, "_tc_worker_revision": worker.version},
                "is_active": True,
            }
        )
        self.profiles.save(profile)
        if not self.states.set_runtime_state(
            worker_id, WorkerRuntimeState(data["runtime_state"])
        ):
            raise RuntimeError("legacy runtime state write failed")

    def save_config(self, worker_id, fields, version=None):
        with self.lock, self.transaction():
            self.require(worker_id)
            worker = self.workers.get_by_id(worker_id)
            if version is not None and worker.version != version:
                raise CatalogConflict("legacy configuration version conflict")
            config = WorkerConfig.model_validate(
                {**worker.config.model_dump(mode="json"), **fields}
            )
            if config != worker.config:
                self.dirty.add(worker_id)
                worker.config = config
                self.workers.update(worker)
            return self.require(worker_id)

    def delete(self, worker_id):
        with self.lock, self.transaction():
            self.require(worker_id)
            self.dirty.add(worker_id)
            self.workers.delete(worker_id)
