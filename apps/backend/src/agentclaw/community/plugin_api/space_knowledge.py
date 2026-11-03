"""Plugin API for canonical knowledge assets and trusted Space synchronization.

ensure_synced must verify current authoritative membership has been durably
acknowledged in this environment, including continuous removal/role-change
synchronization. No timestamp-derived revision, request-local snapshot, browser
revision, or cached acknowledgement without a currentness check is sufficient.
Unavailable, pending, or stale creation synchronization raises SpaceKnowledgeError.
get_context is a read-only lookup, does not activate or deliver snapshots, and
returns None for a never-activated space. Activated reads use provider-side ACL
with explicitly eventual delivery after TC membership mutations.

Provider owns durable (environment, space, request_id) idempotency, actor and
revision checks, KB permissions and provisioning. Retrying an uncertain create
must preserve request_id and immutable content. Automatic initialization recovery
is outside this contract; neither replay nor reads request a retry or replacement KB.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class SpaceKnowledgeError(RuntimeError):
    """Safe machine-readable failure; never contains upstream diagnostics."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SyncedSpace:
    space_id: str
    environment: str
    revision: int


@dataclass(frozen=True)
class KnowledgeBaseState:
    kb_id: str
    tenant_id: str
    name: str
    description: str
    status: str
    created_by: str
    owner_id: str


@dataclass(frozen=True)
class KnowledgeBasePage:
    knowledge_bases: list[KnowledgeBaseState]
    next_cursor: str | None = None


@dataclass(frozen=True)
class KnowledgeBaseResult:
    state: KnowledgeBaseState
    accepted: bool = False


@runtime_checkable
class SpaceKnowledgeSync(Plugin, Protocol):
    def get_context(self, *, space_id: int, environment: str) -> SyncedSpace | None: ...

    def ensure_synced(self, *, space_id: int, environment: str) -> SyncedSpace: ...


@runtime_checkable
class SpaceKnowledgeProvider(Plugin, Protocol):
    def create(
        self,
        *,
        space: SyncedSpace,
        actor_id: str,
        request_id: str,
        name: str,
        description: str,
    ) -> KnowledgeBaseResult: ...

    def get(
        self, *, space: SyncedSpace, actor_id: str, kb_id: str
    ) -> KnowledgeBaseResult: ...

    def list_knowledge_bases(
        self,
        *,
        space: SyncedSpace,
        actor_id: str,
        page_size: int = 50,
        cursor: str = "",
    ) -> KnowledgeBasePage: ...
