"""ORM reads for immutable, consumable Skill Versions."""

from __future__ import annotations

from injector import inject
from sqlalchemy import and_, func

from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import SkillSpaceBinding, SkillVersion
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillVersionMaterializationRepositoryProtocol,
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_types import (
    SkillVersionRecord,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version_lock import (
    lock_skill_then_exact_version,
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

    def get_published_by_ordinal(
        self, *, env: str, skill_id: int, version_ordinal: int
    ) -> SkillVersionRecord | None:
        with self._db.orm_session() as session:
            row = (
                session.query(SkillVersion)
                .filter(
                    SkillVersion.skill_id == skill_id,
                    SkillVersion.version_ordinal == version_ordinal,
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
                raise RuntimeError("Center Version has no stable Asset identity")
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
        name: str,
        metadata_json: str,
        description: str,
    ) -> PublishedMaterializedSkillVersion:
        with self._db.orm_session() as session:
            locked = lock_skill_then_exact_version(
                session,
                env=env,
                skill_id=skill_id,
                skill_version_id=skill_version_id,
            )
            if locked is None:
                raise RuntimeError("materializing Skill Version not found")
            skill, version = locked
            if version.status == "PUBLISHED":
                if (
                    version.name != name
                    or version.metadata_json != metadata_json
                    or version.description != description
                    or version.published_at is None
                ):
                    raise RuntimeError(
                        "PUBLISHED Skill Version conflicts with materialized facts"
                    )
            elif version.status == "MATERIALIZING":
                self._converge_public_manifest_name(
                    session,
                    skill=skill,
                    version=version,
                    env=env,
                    manifest_name=name,
                )
                version.metadata_json = metadata_json
                version.description = description
                version.published_at = func.now()
                version.status = "PUBLISHED"
                skill.description = description
                skill.status = "PUBLISHED"
                session.flush()
                session.refresh(version)
            else:
                raise RuntimeError("Skill Version is not MATERIALIZING")
            skill_uuid = skill.skill_uuid
            if not isinstance(skill_uuid, str) or not skill_uuid:
                raise RuntimeError("Center Version has no stable skill_uuid")
            if version.sc_skill_id is None or version.sc_version_id is None:
                raise RuntimeError("Center Version has incomplete exact SC identity")
            assert version.published_at is not None
            return PublishedMaterializedSkillVersion(
                skill_version_id=int(version.id),
                skill_id=int(version.skill_id),
                version_ordinal=int(version.version_ordinal),
                status="PUBLISHED",
                skill_uuid=skill_uuid,
                sc_version_number=version.sc_version_number,
                sc_skill_id=int(version.sc_skill_id),
                sc_version_id=int(version.sc_version_id),
                name=version.name,
                description=version.description,
                metadata_json=version.metadata_json,
                published_at=version.published_at,
            )

    @staticmethod
    def _converge_public_manifest_name(
        session,
        *,
        skill: Skill,
        version: SkillVersion,
        env: str,
        manifest_name: str,
    ) -> None:
        """Freeze the exact package name for an unconsumed SC Public Asset.

        Public catalogue metadata is available before the exact package and
        may use a presentation name unrelated to ``SKILL.md.name``.  The row
        is still provisional while its first Version is MATERIALIZING.  A
        one-time convergence is safe only before any PUBLISHED Version or Bot
        relationship exists; every later mismatch fails closed.
        """
        if skill.name == manifest_name and version.name == manifest_name:
            return
        git_path = skill.git_path or ""
        if (
            not manifest_name
            or not git_path.startswith("center://")
            or not git_path[len("center://") :]
            or version.publication_attempt_id is not None
        ):
            raise RuntimeError("materialized SKILL.md name changed")
        has_space = (
            session.query(SkillSpaceBinding.id)
            .filter(
                SkillSpaceBinding.skill_id == int(skill.id),
                SkillSpaceBinding.env == env,
            )
            .first()
            is not None
        )
        has_published = (
            session.query(SkillVersion.id)
            .filter(
                SkillVersion.skill_id == int(skill.id),
                SkillVersion.env == env,
                SkillVersion.status == "PUBLISHED",
            )
            .first()
            is not None
        )
        has_membership = (
            session.query(SkillSetSkill.id)
            .filter(SkillSetSkill.skill_id == int(skill.id), SkillSetSkill.env == env)
            .first()
            is not None
        )
        has_installation = (
            session.query(BotSkillInstallation.id)
            .filter(
                BotSkillInstallation.skill_id == int(skill.id),
                BotSkillInstallation.env == env,
            )
            .first()
            is not None
        )
        if has_space or has_published or has_membership or has_installation:
            raise RuntimeError("materialized SKILL.md name changed")
        skill.name = manifest_name
        version.name = manifest_name

    @staticmethod
    def _materializing(
        row: SkillVersion, *, skill_uuid: str, skill_code: str
    ) -> MaterializingSkillVersion:
        if row.sc_skill_id is None or row.sc_version_id is None:
            raise RuntimeError("Center Version has incomplete exact SC identity")
        return MaterializingSkillVersion(
            skill_version_id=int(row.id),
            skill_id=int(row.skill_id),
            version_ordinal=int(row.version_ordinal),
            status=row.status,
            skill_uuid=skill_uuid,
            skill_code=skill_code,
            sc_version_number=row.sc_version_number,
            sc_skill_id=int(row.sc_skill_id),
            sc_version_id=int(row.sc_version_id),
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
