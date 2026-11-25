"""Runtime-only capture guard; startup performs no configuration probing.

Actual SQL operations surface schema/connection errors at runtime in the caller's
transaction. Startup and ordinary disabled-capture writes do not inspect the
outbox or infer configuration from retained history. A capture-disabled
deployment with an enabled sink starts normally; producer operations still
require enabled capture at runtime.
"""

from sqlalchemy.orm import Session

from agentclaw.community.core.repository.protocols.space_persistence_effects import (
    SpacePersistenceEffects,
)
from agentclaw.community.core.space_knowledge.capture_guard import (
    SpaceKnowledgeCaptureGuard,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink


class SpaceKnowledgeCaptureLifecycle(
    LifecycleBase, SpacePersistenceEffects, SpaceKnowledgeCaptureGuard
):
    def __init__(
        self,
        database: DatabasePlugin,
        effects: SpacePersistenceEffects,
        sink: SpaceMembershipSink,
        *,
        enabled: bool,
        environment: str,
    ) -> None:
        # Keep database/environment constructor arguments compatible with callers.
        self._effects = effects
        self._sink = sink
        self._enabled = enabled

    def require_enabled(self, session: Session) -> None:
        if not self._enabled:
            raise RuntimeError(
                "space knowledge producer requires capture to be enabled"
            )

    def lock_space(
        self, session: Session, *, space_id: int, environment: str
    ) -> SpaceModel | None:
        return self._effects.lock_space(
            session, space_id=space_id, environment=environment
        )

    def record_membership(
        self, session: Session, space: SpaceModel, *, disabled: bool = False
    ) -> None:
        self._effects.record_membership(session, space, disabled=disabled)

    def reserve_identity(self, session: Session, space: SpaceModel) -> None:
        self._effects.reserve_identity(session, space)
