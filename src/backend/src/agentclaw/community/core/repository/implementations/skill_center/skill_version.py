"""ORM reads for immutable, consumable Skill Versions."""

from __future__ import annotations

from injector import inject
from sqlalchemy import and_, func

from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SkillVersionRecord,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SkillVersionRepository(SkillVersionRepositoryProtocol):
    """Tenant-guarded, env-scoped reads from ``ac_skill_version``."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_latest_published(
        self, *, env: str, skill_ids: tuple[int, ...]
    ) -> tuple[SkillVersionRecord, ...]:
        if not skill_ids:
            return ()
        ordered_ids = tuple(dict.fromkeys(skill_ids))
        with self._db.orm_session() as session:
            latest = (
                session.query(
                    SkillVersion.skill_id.label("skill_id"),
                    func.max(SkillVersion.version_ordinal).label("version_ordinal"),
                )
                .filter(
                    SkillVersion.env == env,
                    SkillVersion.status == "PUBLISHED",
                    SkillVersion.skill_id.in_(ordered_ids),
                )
                .group_by(SkillVersion.skill_id)
                .subquery()
            )
            rows = (
                session.query(SkillVersion)
                .join(
                    latest,
                    and_(
                        latest.c.skill_id == SkillVersion.skill_id,
                        latest.c.version_ordinal == SkillVersion.version_ordinal,
                    ),
                )
                .filter(
                    SkillVersion.env == env,
                    SkillVersion.status == "PUBLISHED",
                )
                .all()
            )
            by_skill_id = {int(row.skill_id): self._record(row) for row in rows}
            return tuple(
                by_skill_id[skill_id]
                for skill_id in ordered_ids
                if skill_id in by_skill_id
            )

    def get_exact_published(
        self, *, env: str, skill_id: int, skill_version_id: int
    ) -> SkillVersionRecord | None:
        with self._db.orm_session() as session:
            row = (
                session.query(SkillVersion)
                .filter(
                    SkillVersion.id == skill_version_id,
                    SkillVersion.skill_id == skill_id,
                    SkillVersion.env == env,
                    SkillVersion.status == "PUBLISHED",
                )
                .one_or_none()
            )
            return self._record(row) if row is not None else None

    @staticmethod
    def _record(row: SkillVersion) -> SkillVersionRecord:
        return SkillVersionRecord(
            id=int(row.id),
            skill_id=int(row.skill_id),
            version_ordinal=int(row.version_ordinal),
            status=row.status,
            sc_version_number=row.sc_version_number,
            sc_skill_id=int(row.sc_skill_id) if row.sc_skill_id is not None else None,
            sc_version_id=(
                int(row.sc_version_id) if row.sc_version_id is not None else None
            ),
            name=row.name,
            description=row.description,
            metadata_json=row.metadata_json,
            published_at=row.published_at,
        )


__all__ = ["SkillVersionRepository"]
