"""Published-only Version and consumable Space Skill reads."""

from __future__ import annotations

from injector import inject
from sqlalchemy import and_, func, or_

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillSpaceBinding, SkillVersion
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillVersionReadRepository as Protocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    ConsumableSpaceSkillRecord,
    SpaceSkillVersionRecord,
)
from agentclaw.community.core.skill_center.errors import DraftNotFoundError
from agentclaw.community.plugin_api.database import DatabasePlugin


class SpaceSkillVersionReadRepository(Protocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def list_published(
        self, *, space_id: int, skill_id: int, env: str, offset: int, limit: int
    ) -> tuple[int, list[SpaceSkillVersionRecord]]:
        with self._db.orm_session() as session:
            query = self._published_query(
                session, space_id=space_id, skill_id=skill_id, env=env
            )
            total = query.count()
            rows = (
                query.order_by(SkillVersion.version_ordinal.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, [
                self._version_record(skill, version) for skill, version in rows
            ]

    def get_published_ordinal(
        self, *, space_id: int, skill_id: int, version: int, env: str
    ) -> SpaceSkillVersionRecord:
        with self._db.orm_session() as session:
            row = (
                self._published_query(
                    session, space_id=space_id, skill_id=skill_id, env=env
                )
                .filter(SkillVersion.version_ordinal == version)
                .one_or_none()
            )
            if row is None:
                raise DraftNotFoundError("published version not found")
            return self._version_record(row[0], row[1])

    def list_consumable_candidates(
        self,
        *,
        space_id: int,
        env: str,
        keyword: str | None,
        offset: int,
        limit: int,
    ) -> tuple[int, list[ConsumableSpaceSkillRecord]]:
        with self._db.orm_session() as session:
            latest = (
                session.query(
                    SkillVersion.skill_id.label("skill_id"),
                    func.max(SkillVersion.version_ordinal).label("version_ordinal"),
                )
                .filter(SkillVersion.env == env, SkillVersion.status == "PUBLISHED")
                .group_by(SkillVersion.skill_id)
                .subquery()
            )
            query = (
                session.query(Skill, SkillVersion)
                .join(
                    SkillSpaceBinding,
                    and_(
                        SkillSpaceBinding.skill_id == Skill.id,
                        SkillSpaceBinding.env == Skill.env,
                    ),
                )
                .join(latest, latest.c.skill_id == Skill.id)
                .join(
                    SkillVersion,
                    and_(
                        SkillVersion.skill_id == latest.c.skill_id,
                        SkillVersion.version_ordinal == latest.c.version_ordinal,
                        SkillVersion.env == env,
                        SkillVersion.status == "PUBLISHED",
                    ),
                )
                .filter(
                    SkillSpaceBinding.space_id == space_id,
                    SkillSpaceBinding.env == env,
                    Skill.env == env,
                    Skill.offline_at.is_(None),
                )
            )
            if keyword is not None:
                pattern = f"%{keyword.lower()}%"
                query = query.filter(
                    or_(
                        func.lower(Skill.name).like(pattern),
                        func.lower(Skill.description).like(pattern),
                    )
                )
            total = query.count()
            rows = (
                query.order_by(Skill.gmt_modified.desc(), Skill.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, [
                {
                    "skill_id": skill.id,
                    "skill_uuid": skill.skill_uuid,
                    "name": version.name,
                    "description": version.description,
                    "version_ordinal": version.version_ordinal,
                    "sc_version_number": version.sc_version_number,
                    "published_at": version.published_at,
                }
                for skill, version in rows
            ]

    @staticmethod
    def _published_query(session, *, space_id: int, skill_id: int, env: str):
        return (
            session.query(Skill, SkillVersion)
            .join(
                SkillSpaceBinding,
                and_(
                    SkillSpaceBinding.skill_id == Skill.id,
                    SkillSpaceBinding.env == Skill.env,
                ),
            )
            .join(
                SkillVersion,
                and_(
                    SkillVersion.skill_id == Skill.id,
                    SkillVersion.env == env,
                    SkillVersion.status == "PUBLISHED",
                ),
            )
            .filter(
                Skill.id == skill_id,
                Skill.env == env,
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.env == env,
            )
        )

    @staticmethod
    def _version_record(skill: Skill, version: SkillVersion) -> SpaceSkillVersionRecord:
        return {
            "id": version.id,
            "skill_id": skill.id,
            "skill_uuid": skill.skill_uuid,
            "version_ordinal": version.version_ordinal,
            "status": "PUBLISHED",
            "sc_version_number": version.sc_version_number,
            "sc_skill_id": version.sc_skill_id,
            "sc_version_id": version.sc_version_id,
            "name": version.name,
            "description": version.description,
            "metadata_json": version.metadata_json,
            "published_at": version.published_at,
        }
