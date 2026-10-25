"""Explicit disabled defaults, not a mock knowledge engine or sync producer."""

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBasePage,
    KnowledgeBaseResult,
    SpaceKnowledgeError,
    SpaceKnowledgeProvider,
    SpaceKnowledgeSync,
    SyncedSpace,
)


class UnavailableSpaceKnowledge:
    def get_context(self, *, space_id: int, environment: str) -> SyncedSpace | None:
        raise SpaceKnowledgeError("SPACE_SYNC_UNAVAILABLE")

    def ensure_synced(self, *, space_id: int, environment: str) -> SyncedSpace:
        raise SpaceKnowledgeError("SPACE_SYNC_UNAVAILABLE")

    def create(
        self,
        *,
        space: SyncedSpace,
        actor_id: str,
        request_id: str,
        name: str,
        description: str,
    ) -> KnowledgeBaseResult:
        raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")

    def get(
        self, *, space: SyncedSpace, actor_id: str, kb_id: str
    ) -> KnowledgeBaseResult:
        raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")

    def list_knowledge_bases(
        self,
        *,
        space: SyncedSpace,
        actor_id: str,
        page_size: int = 50,
        cursor: str = "",
    ) -> KnowledgeBasePage:
        raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.STUB,
    rationale="Explicitly unavailable; no knowledge engine",
)
class UnavailableSpaceKnowledgeProvider(
    UnavailableSpaceKnowledge, SpaceKnowledgeProvider
):
    pass


@plugin_impl(
    mode=Mode.PROD, rationale="Disabled provider until deployment adapter is configured"
)
class DisabledSpaceKnowledgeProvider(UnavailableSpaceKnowledgeProvider):
    pass


@plugin_impl(
    mode=Mode.LOCAL, flavor=Flavor.STUB, rationale="No membership producer; fail closed"
)
class UnavailableSpaceKnowledgeSync(UnavailableSpaceKnowledge, SpaceKnowledgeSync):
    pass


@plugin_impl(
    mode=Mode.PROD,
    rationale="No continuous membership producer; fail closed, never simulate sync",
)
class DisabledSpaceKnowledgeSync(UnavailableSpaceKnowledgeSync):
    pass


@plugin_impl(
    mode=Mode.PROD, rationale="Membership delivery disabled until a sink is configured"
)
class DisabledSpaceMembershipSink(SpaceMembershipSink):
    def is_enabled(self) -> bool:
        return False

    def apply_snapshot(self, snapshot) -> None:
        raise SpaceKnowledgeError("SPACE_SYNC_UNAVAILABLE")
