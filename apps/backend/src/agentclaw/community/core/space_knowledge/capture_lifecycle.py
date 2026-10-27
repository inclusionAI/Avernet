"""Validate capture configuration without probing the knowledge database schema.

Actual SQL operations surface schema/connection errors at runtime in the caller's
transaction. Startup and ordinary disabled-capture writes do not inspect the
outbox or infer configuration from retained history.
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
        self._validated = False

    def _validate_configuration(self) -> None:
        if self._validated:
            return
        if not self._enabled and self._sink.is_enabled():
            raise RuntimeError(
                "space knowledge delivery requires capture to be enabled"
            )
        self._validated = True

    def require_enabled(self, session: Session) -> None:
        self._validate_configuration()
        if not self._enabled:
            raise RuntimeError(
                "space knowledge producer requires capture to be enabled"
            )

    async def startup(self) -> None:
        self._validate_configuration()

    def lock_space(
        self, session: Session, *, space_id: int, environment: str
    ) -> SpaceModel | None:
        self._validate_configuration()
        return self._effects.lock_space(
            session, space_id=space_id, environment=environment
        )

    def record_membership(
        self, session: Session, space: SpaceModel, *, disabled: bool = False
    ) -> None:
        self._validate_configuration()
        self._effects.record_membership(session, space, disabled=disabled)

    def reserve_identity(self, session: Session, space: SpaceModel) -> None:
        self._validate_configuration()
        self._effects.reserve_identity(session, space)
