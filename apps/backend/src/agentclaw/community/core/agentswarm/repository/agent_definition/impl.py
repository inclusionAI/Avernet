"""AgentDefinitionRepository — ac_agent_swarm_agent_definition persistence.

One version is one immutable row. A replace inserts the successor and retires
the predecessor by writing the predecessor's own ``id`` into ``is_deleted``
(design D6, the ``baas_bot`` multi-version pattern). That tombstone write is
what frees both live-row unique slots, so the database — not this module — is
the arbiter of a concurrent edit.
"""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository.agent_definition import (
    model as om,
)
from agentclaw.community.core.agentswarm.repository.agent_definition.protocol import (
    AgentDefinitionRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


class DefinitionConflictError(RuntimeError):
    """A live-row unique slot was taken: another editor got there first."""


class AgentDefinitionRepository(AgentDefinitionRepositoryProtocol):

    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    # ── write ────────────────────────────────────────────────────────────────

    def create(self, definition: dm.AgentDefinition) -> dm.AgentDefinition:
        try:
            with self._db.orm_session() as session:
                row = self._to_row(definition)
                session.add(row)
                session.flush()
                return self._to_domain(row)
        except IntegrityError as exc:
            raise DefinitionConflictError(
                f"a live definition already occupies this name or id: "
                f"{definition.definition_id}/{definition.name}"
            ) from exc

    def replace_live(self, definition: dm.AgentDefinition,
                     expected_row_id: int) -> dm.AgentDefinition:
        """Retire ``expected_row_id`` and insert its successor, in one go.

        **Retire before insert.** The successor reuses the same
        ``definition_id``, so while the predecessor is still live it would
        occupy the same ``uk_def_live`` slot as the new row and the insert
        would fail. Writing the tombstone first frees both live slots —
        ``(definition_id, 0)`` and ``(owner_id, name, 0)`` — and the insert
        then takes them.

        Both statements share one transaction, so a refused replace rolls the
        tombstone back; a replace whose predecessor is not live mutates nothing.
        The database's unique keys, not this method, remain the arbiter of a
        concurrent edit.
        """
        try:
            with self._db.orm_session() as session:
                retired = session.execute(
                    update(om.AcAgentSwarmAgentDefinitionModel)
                    .where(
                        om.AcAgentSwarmAgentDefinitionModel.id == expected_row_id,
                        om.AcAgentSwarmAgentDefinitionModel.env == get_current_env(),
                        om.AcAgentSwarmAgentDefinitionModel.tenant == definition.tenant,
                        om.AcAgentSwarmAgentDefinitionModel.owner_id == definition.owner_id,
                        om.AcAgentSwarmAgentDefinitionModel.is_deleted == 0,
                    )
                    .values(is_deleted=om.AcAgentSwarmAgentDefinitionModel.id)
                    .execution_options(synchronize_session=False)
                )
                if retired.rowcount != 1:
                    # Not live (already superseded, deleted, or another owner's):
                    # refuse without writing anything.
                    raise DefinitionConflictError(
                        f"definition row {expected_row_id} is no longer live"
                    )
                row = self._to_row(definition)
                session.add(row)
                session.flush()
                session.refresh(row)
                return self._to_domain(row)
        except IntegrityError as exc:
            raise DefinitionConflictError(
                f"concurrent edit on definition {definition.definition_id}"
            ) from exc

    def retire_live(self, definition_id: dm.AgentDefinitionId,
                    tenant: dm.TenantId, owner_id: str) -> int:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmAgentDefinitionModel)
                .where(
                    om.AcAgentSwarmAgentDefinitionModel.definition_id == definition_id,
                    om.AcAgentSwarmAgentDefinitionModel.tenant == tenant,
                    om.AcAgentSwarmAgentDefinitionModel.owner_id == owner_id,
                    om.AcAgentSwarmAgentDefinitionModel.env == get_current_env(),
                    om.AcAgentSwarmAgentDefinitionModel.is_deleted == 0,
                )
                .values(is_deleted=om.AcAgentSwarmAgentDefinitionModel.id)
                .execution_options(synchronize_session=False)
            )
            return int(result.rowcount or 0)

    # ── read ─────────────────────────────────────────────────────────────────

    def get_live(self, definition_id: dm.AgentDefinitionId, tenant: dm.TenantId,
                 owner_id: str) -> dm.AgentDefinition | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmAgentDefinitionModel).where(
                    om.AcAgentSwarmAgentDefinitionModel.definition_id == definition_id,
                    om.AcAgentSwarmAgentDefinitionModel.tenant == tenant,
                    om.AcAgentSwarmAgentDefinitionModel.owner_id == owner_id,
                    om.AcAgentSwarmAgentDefinitionModel.env == get_current_env(),
                    om.AcAgentSwarmAgentDefinitionModel.is_deleted == 0,
                )
            ).scalars().first()
            return self._to_domain(row) if row else None

    def get_row(self, row_id: int, tenant: dm.TenantId,
                owner_id: str) -> dm.AgentDefinition | None:
        """One exact version, live or retired, scoped to its owner."""
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmAgentDefinitionModel).where(
                    om.AcAgentSwarmAgentDefinitionModel.id == row_id,
                    om.AcAgentSwarmAgentDefinitionModel.tenant == tenant,
                    om.AcAgentSwarmAgentDefinitionModel.owner_id == owner_id,
                    om.AcAgentSwarmAgentDefinitionModel.env == get_current_env(),
                )
            ).scalars().first()
            return self._to_domain(row) if row else None

    def get_many_live(self, definition_ids: list[dm.AgentDefinitionId],
                      tenant: dm.TenantId,
                      owner_id: str) -> list[dm.AgentDefinition]:
        if not definition_ids:
            return []
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmAgentDefinitionModel).where(
                    om.AcAgentSwarmAgentDefinitionModel.definition_id.in_(
                        list(definition_ids)
                    ),
                    om.AcAgentSwarmAgentDefinitionModel.tenant == tenant,
                    om.AcAgentSwarmAgentDefinitionModel.owner_id == owner_id,
                    om.AcAgentSwarmAgentDefinitionModel.env == get_current_env(),
                    om.AcAgentSwarmAgentDefinitionModel.is_deleted == 0,
                )
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    def list_live_page(self, tenant: dm.TenantId, owner_id: str,
                       name_filter: str | None = None,
                       page: int = 1, page_size: int = 20
                       ) -> dm.AgentDefinitionPage:
        # Ordered by the stable definition id so adjacent pages partition the
        # set deterministically (the spec depends on a fixed order).
        with self._db.orm_session() as session:
            predicates = [
                om.AcAgentSwarmAgentDefinitionModel.tenant == tenant,
                om.AcAgentSwarmAgentDefinitionModel.owner_id == owner_id,
                om.AcAgentSwarmAgentDefinitionModel.env == get_current_env(),
                om.AcAgentSwarmAgentDefinitionModel.is_deleted == 0,
            ]
            if name_filter:
                predicates.append(
                    om.AcAgentSwarmAgentDefinitionModel.name.like(f"%{name_filter}%")
                )
            total = session.execute(
                select(func.count()).select_from(
                    om.AcAgentSwarmAgentDefinitionModel
                ).where(*predicates)
            ).scalar_one()
            rows = session.execute(
                select(om.AcAgentSwarmAgentDefinitionModel)
                .where(*predicates)
                .order_by(om.AcAgentSwarmAgentDefinitionModel.definition_id)
                .offset(max(page - 1, 0) * page_size)
                .limit(page_size)
            ).scalars().all()
            return dm.AgentDefinitionPage(
                items=tuple(self._to_domain(r) for r in rows),
                total=int(total),
                page=page,
                page_size=page_size,
            )

    def list_versions(self, definition_id: dm.AgentDefinitionId,
                      tenant: dm.TenantId,
                      owner_id: str) -> list[dm.AgentDefinition]:
        # Newest first by id, the baas_bot ordering — retired versions included.
        with self._db.orm_session() as session:
            rows = session.execute(
                select(om.AcAgentSwarmAgentDefinitionModel)
                .where(
                    om.AcAgentSwarmAgentDefinitionModel.definition_id == definition_id,
                    om.AcAgentSwarmAgentDefinitionModel.tenant == tenant,
                    om.AcAgentSwarmAgentDefinitionModel.owner_id == owner_id,
                    om.AcAgentSwarmAgentDefinitionModel.env == get_current_env(),
                )
                .order_by(om.AcAgentSwarmAgentDefinitionModel.id.desc())
            ).scalars().all()
            return [self._to_domain(r) for r in rows]

    # ── mapping ──────────────────────────────────────────────────────────────

    @staticmethod
    def _to_row(definition: dm.AgentDefinition) -> om.AcAgentSwarmAgentDefinitionModel:
        return om.AcAgentSwarmAgentDefinitionModel(
            definition_id=definition.definition_id,
            tenant=definition.tenant,
            env=get_current_env(),
            name=definition.name,
            role=definition.role,
            instructions=definition.instructions,
            kind=definition.kind.value if hasattr(definition.kind, "value")
            else str(definition.kind),
            model=definition.model,
            engine=definition.engine,
            bot_type=definition.bot_type,
            manifest_document=definition.manifest_document,
            manifest_digest=definition.manifest_digest,
            owner_id=definition.owner_id,
            creator_id=definition.creator_id,
            modifier_id=definition.modifier_id,
            is_deleted=0,
        )

    @staticmethod
    def _to_domain(row: om.AcAgentSwarmAgentDefinitionModel) -> dm.AgentDefinition:
        return dm.AgentDefinition(
            definition_id=dm.AgentDefinitionId(row.definition_id),
            tenant=dm.TenantId(row.tenant),
            owner_id=row.owner_id or "",
            creator_id=row.creator_id or "",
            modifier_id=row.modifier_id or "",
            name=row.name,
            role=row.role or "",
            instructions=row.instructions or "",
            kind=dm.AgentKind(row.kind),
            model=row.model,
            engine=row.engine or "",
            bot_type=row.bot_type or "personal",
            manifest_document=row.manifest_document or "",
            manifest_digest=row.manifest_digest or "",
            row_id=int(row.id or 0),
            is_live=int(row.is_deleted or 0) == 0,
            created_at=dm.EpochMillis(
                int(row.gmt_create.timestamp() * 1000) if row.gmt_create else 0
            ),
        )
