"""First-use confirmation and ordered, idempotent membership delivery."""

from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.plugin_api.space_knowledge import (
    SpaceKnowledgeError,
    SyncedSpace,
)
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink


class SpaceMembershipSynchronizer:
    def __init__(
        self,
        repository: SpaceKnowledgeSyncRepositoryProtocol,
        sink: SpaceMembershipSink,
        app: str,
    ):
        self._repository, self._sink, self._app = repository, sink, app

    def is_enabled(self):
        return self._sink.is_enabled()

    def _enabled(self):
        if not self.is_enabled():
            raise SpaceKnowledgeError("SPACE_SYNC_UNAVAILABLE")

    def get_context(self, *, space_id, environment):
        self._enabled()
        progress = self._repository.progress(
            space_id=space_id, environment=environment, app=self._app
        )
        if progress is None:
            return None
        revision, acknowledged, status = progress
        if status != "active":
            raise SpaceKnowledgeError("RESOURCE_NOT_FOUND")
        if acknowledged < 1:
            raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
        # Reads do not activate a space or push snapshots. Final ACL lives at the provider.
        return SyncedSpace(str(space_id), environment, acknowledged)

    def deliver_next(self, *, space_id, environment):
        self._enabled()
        snapshot = self._repository.next_snapshot(
            space_id=space_id, environment=environment, app=self._app
        )
        if snapshot is not None:
            self._sink.apply_snapshot(snapshot)
            self._repository.acknowledge(snapshot=snapshot, app=self._app)
        return snapshot is not None

    def ensure_synced(self, *, space_id, environment):
        self._enabled()
        self._repository.activate(
            space_id=space_id, environment=environment, app=self._app
        )
        # Bound request work; a larger backlog is drained by the durable worker.
        for attempt in range(5):
            progress = self._repository.progress(
                space_id=space_id, environment=environment, app=self._app
            )
            revision, acknowledged, status = progress
            if status != "active":
                raise SpaceKnowledgeError("RESOURCE_NOT_FOUND")
            if revision == acknowledged:
                return SyncedSpace(str(space_id), environment, acknowledged)
            if attempt < 4:
                self.deliver_next(space_id=space_id, environment=environment)
        raise SpaceKnowledgeError("SPACE_NOT_SYNCED")

    def progress(self, *, space_id, environment):
        return self._repository.progress(
            space_id=space_id, environment=environment, app=self._app
        )

    def repair_pending(self, *, environment, after_space_id=0):
        self._enabled()
        return self._repository.repair_pending(
            environment=environment,
            app=self._app,
            after_space_id=after_space_id,
            limit=32,
        )
