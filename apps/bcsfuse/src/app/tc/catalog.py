"""One current TC Card per Worker, with legacy read projections for Fuse."""

from search_core.api.cards import CatalogConflict
from search_core.catalog.cards import business_card, label_filters
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import DocumentText

from app.domain.exceptions import WorkerNotFoundException
from app.domain.models.worker import Worker, WorkerIdentity, WorkerState, WorkerType
from app.domain.models.worker_config import WorkerConfig
from app.domain.models.worker_lifecycle_state import WorkerLifecycleState
from app.domain.models.worker_profile_content import (
    WorkerProfileContent,
    WorkerProfileContentList,
)

FILTER_FIELDS = frozenset(
    {
        "runtime_state",
        "availability",
        "trust_level",
        "domains",
        "type",
        "lifecycle_state",
    }
)


def worker_availability(card):
    # V2 Cards need no legacy state fields. Explicit labels also override old
    # Worker snapshots when projecting the legacy read/filter contract.
    values = card.labels.get("visibility")
    return values[0] if values else card.source_card.get("availability", "private")


class TcDocumentConverter:
    version = "tc-description-v2"

    def convert(self, card):
        data = business_card(card)
        # Skills and legacy MD are source data, never an implicit expertise claim.
        text = "\n".join(
            v.strip()
            for v in (data.get("name"), data.get("description"))
            if isinstance(v, str) and v.strip()
        )
        labels = {}
        worker_defaults = {
            "type": "bot",
            "runtime_state": "offline",
            "availability": "private",
            "trust_level": "guarded",
            "lifecycle_state": "active",
        }
        for key in FILTER_FIELDS:
            value = (
                worker_availability(card)
                if key == "availability"
                else data.get(key, worker_defaults.get(key))
            )
            values = [value] if isinstance(value, str) else value
            if values:
                labels[key] = tuple(values)
        # Bounded text fragments have stable keys; no semantic chunking platform.
        documents = [
            DocumentText(str(i), text[start : start + 8192])
            for i, start in enumerate(range(0, len(text), 8192))
        ]
        return CardProjection(documents, labels)


def sync_changes(current, payload):
    data = current.card.source_card if current else {}
    fields = {"name": payload["name"]}
    if "handle" in payload:
        fields["handle"] = payload["handle"]
    for key, default in {
        "type": "bot",
        "description": None,
        "domains": [],
        "responsibilities": [],
        "availability": "protected",
        "trust_level": "guarded",
        "runtime_state": "online",
        "lifecycle_state": "active",
    }.items():
        if key in payload and (key != "runtime_state" or payload[key] is not None):
            fields[key] = payload[key]
        elif current is None:
            fields[key] = default
    profile = payload.get("profile", {})
    fields["profile_id"] = (
        data.get("profile_id") or profile.get("profile_id") or "default"
    )
    if "skill_sets" in profile:
        fields["skills"] = profile["skill_sets"]
    elif "skills" in payload:
        fields["skills"] = [
            {k: skill[k] for k in ("name", "description") if k in skill}
            for skill in payload["skills"]
        ]
    elif current is None:
        fields["skills"] = []
    return fields


def matches_row(row, filters):
    if row is None:
        return False
    labels = {
        **TcDocumentConverter().convert(row.card).filters,
        **label_filters(row.card.labels),
    }
    return all(
        values and set(values).intersection(labels.get(key, ()))
        for key, values in filters.items()
    )


class TcCatalog:
    def __init__(self, repository):
        self.repository = repository

    def get(self, worker_id):
        row = self.repository.get(worker_id)
        return row if row is not None and not row.card.deleted else None

    def require(self, worker_id):
        row = self.get(worker_id)
        if row is None:
            raise WorkerNotFoundException(worker_id)
        return row

    def rows(self):
        after = ""
        while page := self.repository.page(after, 100):
            for key, _ in page:
                row = self.get(key)
                if row is not None:
                    yield row
            after = page[-1][0]

    def resolve(self, participant):
        # A complete Worker ID itself may contain colons. Prefer exact identity.
        row = self.get(participant)
        if row is not None:
            return row.card.agent_id
        # Both Worker and historical Profile IDs may themselves contain colons.
        for index in reversed([i for i, char in enumerate(participant) if char == ":"]):
            worker, profile = participant[:index], participant[index + 1 :]
            row = self.get(worker)
            if (
                row is not None
                and row.card.source_card.get("profile_id", "default") == profile
            ):
                return worker
        return None

    def sync(self, worker_id, payload):
        current = self.get(worker_id)
        data = current.card.source_card if current else {}
        fields = sync_changes(current, payload)
        self.validate({**data, **fields})
        row = self.repository.upsert(
            worker_id,
            fields,
            resource_type="agent",
            search_config={},
            labels={"visibility": [fields["availability"]]}
            if "availability" in fields
            else None,
        )
        return row, current is None

    def update(self, worker_id, fields):
        current = self.require(worker_id)
        data = {**current.card.source_card, **fields}
        self.validate(data)
        return self.repository.upsert(
            worker_id,
            fields,
            resource_type="agent",
            search_config={},
            labels={"visibility": [fields["availability"]]}
            if "availability" in fields
            else None,
        )

    @staticmethod
    def validate(data):
        WorkerType(data.get("type", "bot"))
        WorkerIdentity(
            name=data["name"], handle="validation", description=data.get("description")
        )
        for key in ("domains", "responsibilities"):
            if not isinstance(data.get(key, []), list) or not all(
                isinstance(value, str) for value in data.get(key, [])
            ):
                raise ValueError("invalid Worker list field")
        lifecycle = WorkerLifecycleState(data.get("lifecycle_state", "active"))
        state = WorkerState.model_validate(
            {"availability": "private", "trust_level": "guarded", **data}
        )
        if state.runtime_state == "online" and lifecycle != WorkerLifecycleState.ACTIVE:
            raise ValueError("inactive or disabled Worker cannot be online")
        WorkerProfileContent(
            worker_id="validation",
            profile_id=data.get("profile_id", "default"),
            description=data.get("description"),
            skill_sets=data.get("skills", []),
        )

    def save_config(self, worker_id, fields, version=None):
        for _ in range(8):
            row = self.require(worker_id)
            if version is not None and version != row.revision:
                raise CatalogConflict("configuration version conflict")
            config = {**(row.config or {}), **fields}
            config = WorkerConfig.model_validate(config).model_dump(mode="json")
            if config == row.config:
                return row
            if self.repository.save_config(worker_id, row.revision, config):
                return self.require(worker_id)
        raise CatalogConflict("configuration changed during update")

    def delete(self, worker_id):
        self.require(worker_id)
        self.repository.remove(worker_id)

    @staticmethod
    def profile_key(row):
        return (
            f"{row.card.agent_id}:{row.card.source_card.get('profile_id', 'default')}"
        )

    def profile(self, row):
        data = row.card.source_card
        generated = row.profile or {}
        contents = {}
        if generated.get("source_revision") == row.revision:
            text = generated.get("content", {}).get("profile")
            if isinstance(text, str) and text.strip():
                contents["profile"] = text
        return WorkerProfileContent(
            worker_id=row.card.agent_id,
            profile_id=data.get("profile_id", "default"),
            display_name=data["name"],
            description=data.get("description"),
            skill_sets=data.get("skills", []),
            contents=contents,
            is_active=True,
            version=row.revision,
        )

    def worker(self, row):
        data = row.card.source_card
        return Worker(
            id=row.card.agent_id,
            type=data.get("type", "bot"),
            identity={
                "name": data["name"],
                "handle": data.get("handle") or row.card.agent_id,
                "description": data.get("description"),
            },
            responsibilities=data.get("responsibilities", []),
            domains=data.get("domains", []),
            capabilities=[],
            state={
                "availability": worker_availability(row.card),
                "runtime_state": data.get("runtime_state", "offline"),
                "trust_level": data.get("trust_level", "guarded"),
            },
            lifecycle_state=data.get("lifecycle_state", "active"),
            active_profile_key=self.profile_key(row),
            config=row.config or {},
            version=row.revision,
        )


class CardWorkerStore:
    """Legacy Worker reads used by Fuse, backed only by TC Cards."""

    def __init__(self, catalog):
        self.catalog = catalog

    def get_by_id(self, worker_id):
        row = self.catalog.get(worker_id)
        return self.catalog.worker(row) if row else None

    def get_by_ids(self, worker_ids):
        return {
            key: worker
            for key in worker_ids
            if (worker := self.get_by_id(key)) is not None
        }

    def exists(self, worker_id):
        return self.catalog.get(worker_id) is not None

    def list(
        self,
        lifecycle_states=None,
        source_types=None,
        domains=None,
        limit=None,
        offset=None,
    ):
        workers = [self.catalog.worker(row) for row in self.catalog.rows()]
        if lifecycle_states:
            workers = [w for w in workers if w.lifecycle_state in lifecycle_states]
        if domains:
            workers = [w for w in workers if set(w.domains) & set(domains)]
        return workers[
            offset or 0 : (offset or 0) + limit if limit is not None else None
        ]

    def batch_get_configs(self, worker_ids):
        workers = self.get_by_ids(worker_ids)
        return {key: w.config for key, w in workers.items()}, [
            key for key in worker_ids if key not in workers
        ]


class CardProfileStore:
    """Single current content view for existing Fuse consumers. No activation state."""

    def __init__(self, catalog):
        self.catalog = catalog

    def get_active(self, worker_id):
        row = self.catalog.get(worker_id)
        return self.catalog.profile(row) if row else None

    def get(self, worker_id, profile_id):
        value = self.get_active(worker_id)
        return value if value and value.profile_id == profile_id else None

    def get_active_by_workers(self, worker_ids):
        return [
            value for key in worker_ids if (value := self.get_active(key)) is not None
        ]

    def get_all_active(self):
        return [self.catalog.profile(row) for row in self.catalog.rows()]

    def list_by_worker(self, worker_id):
        value = self.get_active(worker_id)
        return WorkerProfileContentList(
            items=[value] if value else [], total=int(value is not None)
        )
