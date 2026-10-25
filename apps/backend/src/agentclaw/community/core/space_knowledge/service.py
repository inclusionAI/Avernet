"""Transport-free orchestration; does not store knowledge assets or snapshots."""

from injector import inject

from agentclaw.community.core.space_knowledge.protocols import (
    SpaceKnowledgeServiceProtocol,
)
from agentclaw.community.core.spaces.space_service_protocol import (
    SpaceAccessServiceProtocol,
)
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBasePage,
    KnowledgeBaseResult,
    SpaceKnowledgeError,
    SpaceKnowledgeProvider,
    SpaceKnowledgeSync,
    SyncedSpace,
)


class SpaceKnowledgeService(SpaceKnowledgeServiceProtocol):
    @inject
    def __init__(
        self,
        access: SpaceAccessServiceProtocol,
        sync: SpaceKnowledgeSync,
        provider: SpaceKnowledgeProvider,
    ) -> None:
        self._access = access
        self._sync = sync
        self._provider = provider

    def _context(
        self, space_id: int, actor_id: str, *, initialize: bool = False
    ) -> SyncedSpace | None:
        if not actor_id or actor_id != actor_id.strip():
            raise SpaceKnowledgeError("IDENTITY_REQUIRED")
        space, _ = self._access.require_space_member(
            space_id=space_id, user_id=actor_id
        )
        resolve = self._sync.ensure_synced if initialize else self._sync.get_context
        synced = resolve(space_id=space.id, environment=space.env)
        if synced is None:
            if initialize:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            return None
        if (
            synced.space_id != str(space.id)
            or synced.environment != space.env
            or type(synced.revision) is not int
            or not 1 <= synced.revision <= 9223372036854775807
        ):
            raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
        # Do not admit a member revoked while the synchronizer was running.
        self._access.require_space_member(space_id=space_id, user_id=actor_id)
        return synced

    def create(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        name: str,
        description: str = "",
    ) -> KnowledgeBaseResult:
        return self._provider.create(
            space=self._context(space_id, actor_id, initialize=True),
            actor_id=actor_id,
            request_id=request_id,
            name=name,
            description=description,
        )

    def get(self, *, space_id: int, actor_id: str, kb_id: str) -> KnowledgeBaseResult:
        space = self._context(space_id, actor_id)
        if space is None:
            raise SpaceKnowledgeError("RESOURCE_NOT_FOUND")
        return self._provider.get(space=space, actor_id=actor_id, kb_id=kb_id)

    def list_knowledge_bases(
        self, *, space_id: int, actor_id: str, page_size: int = 50, cursor: str = ""
    ) -> KnowledgeBasePage:
        space = self._context(space_id, actor_id)
        if space is None:
            return KnowledgeBasePage([])
        return self._provider.list_knowledge_bases(
            space=space,
            actor_id=actor_id,
            page_size=page_size,
            cursor=cursor,
        )
