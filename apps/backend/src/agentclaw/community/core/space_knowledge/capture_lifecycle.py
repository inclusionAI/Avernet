"""Validate capture policy after DB bootstrap and before source transactions commit.

Writers may also be used by startup participants or library callers that do not
run HTTP lifespan. The same idempotent validation is performed on first use in
that case; failure is propagated, never interpreted as missing history. There is
no DB I/O in DI construction and no dependency on private database engines.
"""

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from agentclaw.community.core.repository.protocols.space_persistence_effects import (
    SpacePersistenceEffects,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink


class SpaceKnowledgeCaptureLifecycle(LifecycleBase):
    def __init__(
        self,
        database: DatabasePlugin,
        effects: SpacePersistenceEffects,
        sink: SpaceMembershipSink,
        *,
        enabled: bool,
        environment: str,
    ) -> None:
        self._database = database
        self._effects = effects
        self._sink = sink
        self._enabled = enabled
        self._environment = environment
        self._validated = False

    def _validate(self, session: Session) -> None:
        if self._validated:
            return
        if not self._enabled and self._sink.is_enabled():
            raise RuntimeError(
                "space knowledge delivery requires capture to be enabled"
            )
        schema = inspect(session.connection())
        if not schema.has_table(Snapshot.__tablename__):
            if self._enabled:
                raise RuntimeError(
                    "space knowledge capture requires ac_space_knowledge_sync migration"
                )
            self._validated = True
            return
        columns = {
            column["name"] for column in schema.get_columns(Snapshot.__tablename__)
        }
        if set(Snapshot.__table__.columns.keys()) - columns:
            raise RuntimeError(
                "ac_space_knowledge_sync schema is incompatible; migrate before startup"
            )
        primary_key = schema.get_pk_constraint(Snapshot.__tablename__)[
            "constrained_columns"
        ]
        if primary_key != ["env", "space_id", "revision"]:
            raise RuntimeError("ac_space_knowledge_sync primary key is incompatible")
        if not self._enabled:
            # Source Spaces and retired SQLite identities are shared across app
            # owners. Another app's same-environment history is not safe to ignore.
            history = session.execute(
                select(Snapshot.space_id)
                .where(Snapshot.env == self._environment)
                .limit(1)
            ).first()
            if history is not None:
                raise RuntimeError(
                    "space knowledge history exists; keep capture enabled when pausing delivery"
                )
        self._validated = True

    async def startup(self) -> None:
        with self._database.orm_session() as session:
            self._validate(session)

    def lock_space(
        self, session: Session, *, space_id: int, environment: str
    ) -> SpaceModel | None:
        self._validate(session)
        return self._effects.lock_space(
            session, space_id=space_id, environment=environment
        )

    def record_membership(
        self, session: Session, space: SpaceModel, *, disabled: bool = False
    ) -> None:
        self._validate(session)
        self._effects.record_membership(session, space, disabled=disabled)

    def reserve_identity(self, session: Session, space: SpaceModel) -> None:
        self._validate(session)
        self._effects.reserve_identity(session, space)
