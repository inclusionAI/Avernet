"""ORM reads for immutable, consumable Skill Versions."""

from __future__ import annotations

from datetime import datetime

from injector import inject
from sqlalchemy import and_, func

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionMaterializationRepositoryProtocol,
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SkillVersionRecord,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    MaterializingSkillVersion,
    PublishedMaterializedSkillVersion,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class SkillVersionRepository(
    SkillVersionRepositoryProtocol,
    SkillVersionMaterializationRepositoryProtocol,
):
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

    def get_materialization_target(
        self, *, env: str, skill_id: int, skill_version_id: int
    ) -> MaterializingSkillVersion | None:
        with self._db.orm_session() as session:
            result = (
                session.query(SkillVersion, Skill)
                .join(Skill, Skill.id == SkillVersion.skill_id)
                .filter(
                    SkillVersion.id == skill_version_id,
                    SkillVersion.skill_id == skill_id,
                    SkillVersion.env == env,
                    Skill.env == env,
                    SkillVersion.status.in_(("MATERIALIZING", "PUBLISHED")),
                )
                .one_or_none()
            )
            if result is None:
                return None
            version, skill = result
            skill_uuid = skill.skill_uuid
            git_path = skill.git_path or ""
            if (
                not isinstance(skill_uuid, str)
                or not skill_uuid
                or not git_path.startswith("center://")
                or not git_path[len("center://") :]
            ):
                raise RuntimeError(
                    "Center Version has no stable Asset identity"
                )
            return self._materializing(
                version,
                skill_uuid=skill_uuid,
                skill_code=git_path[len("center://") :],
            )

    def publish_materialized(
        self,
        *,
        env: str,
        skill_id: int,
        skill_version_id: int,
        metadata_json: str,
        description: str,
        sc_sha256: str,
        published_at: datetime,
    ) -> PublishedMaterializedSkillVersion:
        with self._db.orm_session() as session:
            result = (
                session.query(SkillVersion, Skill)
                .join(Skill, Skill.id == SkillVersion.skill_id)
                .filter(
                    SkillVersion.id == skill_version_id,
                    SkillVersion.skill_id == skill_id,
                    SkillVersion.env == env,
                    Skill.env == env,
                )
                .with_for_update()
                .one_or_none()
            )
            if result is None:
                raise RuntimeError("materializing Skill Version not found")
            version, skill = result
            if version.status == "PUBLISHED":
                if (
                    version.metadata_json != metadata_json
                    or version.description != description
                    or version.sc_sha256 != sc_sha256
                    or version.published_at is None
                ):
                    raise RuntimeError(
                        "PUBLISHED Skill Version conflicts with materialized facts"
                    )
            elif version.status == "MATERIALIZING":
                version.metadata_json = metadata_json
                version.description = description
                version.sc_sha256 = sc_sha256
                version.published_at = published_at
                version.status = "PUBLISHED"
                skill.description = description
                skill.status = "PUBLISHED"
                session.flush()
            else:
                raise RuntimeError("Skill Version is not MATERIALIZING")
            skill_uuid = skill.skill_uuid
            if not isinstance(skill_uuid, str) or not skill_uuid:
                raise RuntimeError("Center Version has no stable skill_uuid")
            assert version.published_at is not None
            return PublishedMaterializedSkillVersion(
                skill_version_id=int(version.id),
                skill_id=int(version.skill_id),
                version_ordinal=int(version.version_ordinal),
                status="PUBLISHED",
                skill_uuid=skill_uuid,
                sc_version_number=version.sc_version_number,
                name=version.name,
                description=version.description,
                metadata_json=version.metadata_json,
                published_at=version.published_at,
            )

    @staticmethod
    def _materializing(
        row: SkillVersion, *, skill_uuid: str, skill_code: str
    ) -> MaterializingSkillVersion:
        return MaterializingSkillVersion(
            skill_version_id=int(row.id),
            skill_id=int(row.skill_id),
            version_ordinal=int(row.version_ordinal),
            status=row.status,
            skill_uuid=skill_uuid,
            skill_code=skill_code,
            sc_version_number=row.sc_version_number,
            sc_sha256=row.sc_sha256,
            name=row.name,
            description=row.description,
            metadata_json=row.metadata_json,
            published_at=row.published_at,
        )

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
