"""First-use confirmation and ordered, idempotent membership delivery."""

from agentclaw.community.core.repository.protocols.platform import (
    TaskQueueRepositoryProtocol,
)
from agentclaw.community.log import get_logger

from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.plugin_api.space_knowledge import (
    SpaceKnowledgeError,
    SyncedSpace,
)
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink


logger = get_logger()
TASK_TYPE = "space.membership.sync"


class SpaceMembershipSynchronizer:
    def __init__(
        self,
        repository: SpaceKnowledgeSyncRepositoryProtocol,
        sink: SpaceMembershipSink,
        app: str,
        queue: TaskQueueRepositoryProtocol,
    ):
        self._repository, self._sink, self._app = repository, sink, app
        self._queue = queue

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
        pending = self._repository.pending_revisions(
            environment=environment,
            app=self._app,
            after_space_id=after_space_id,
            limit=32,
        )
        for space_id, revision in pending:
            try:
                progress = self._repository.progress(
                    space_id=space_id, environment=environment, app=self._app
                )
                if progress is None or revision <= progress[1]:
                    continue
                if revision != progress[1] + 1:
                    raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
                # Dispatch metadata only. The worker validates the exact head's
                # payload and hash before any Sink call; corrupt heads retry.
                # The outbox already committed with the source mutation. Queue
                # failures are retried from it; no distributed transaction or
                # queue history/retention exception is required.
                self._queue.enqueue(
                    task_type=TASK_TYPE,
                    payload={"_space_id": space_id, "revision": revision},
                    env=environment,
                    app=self._app,
                    delay_seconds=0,
                    deadline_seconds=7 * 24 * 3600,
                    idempotency_key=f"space:{self._app}:{space_id}:membership:{revision}",
                )
            except Exception:
                logger.warning("membership dispatch pending; no payload logged")
        return pending[-1][0] if len(pending) == 32 else 0
