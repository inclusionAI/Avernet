"""Transactional blocker recheck and commit for recoverable Skill Offline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from injector import inject
from sqlalchemy import or_

from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import (
    ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES,
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.space_skill_offline_types import (
    OfflineCommit,
    OfflineInspection,
    OfflineInstallationFact,
    OfflineMembershipFact,
    OfflinePublicationAttemptFact,
    OfflineSkillIdentity,
)
from agentclaw.community.core.repository.protocols.space_skill_offline import (
    SpaceSkillOfflineRepositoryProtocol,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version_lock import (
    lock_skill_then_latest_published_version,
)
from agentclaw.community.core.skill_center.errors import (
    DraftNotFoundError,
    DraftRevisionConflictError,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.plugin_api.database import DatabasePlugin


class SpaceSkillOfflineRepository(SpaceSkillOfflineRepositoryProtocol):
    @inject
    def __init__(
        self,
        db: DatabasePlugin,
    ) -> None:
        self._db = db

    def inspect(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> OfflineInspection:
        with self._db.orm_session() as session:
            inspection = self._inspection(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
                lock=False,
            )
        return inspection

    def commit(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_version_id: int,
        target_version: int,
        new_locator: str,
        new_description: str | None,
        env: str,
        guard: Callable[[OfflineInspection], None],
    ) -> OfflineCommit:
        with self._db.transactional_orm_session() as session:
            inspection = self._inspection(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
                lock=True,
            )
            identity = inspection.identity
            skill = session.query(Skill).filter(Skill.id == skill_id).one()
            if (
                skill.offline_at is not None
                and skill.draft_status is not None
                and skill.zip_url
                and skill.draft_target_version is not None
            ):
                return OfflineCommit(
                    changed=False,
                    target_version=int(skill.draft_target_version),
                    status=str(skill.draft_status),
                    locator=str(skill.zip_url),
                )

            guard(inspection)
            if identity.latest_version_id != expected_version_id:
                raise DraftRevisionConflictError("latest Published Version changed")
            if target_version != identity.latest_version_ordinal + 1:
                raise DraftRevisionConflictError("Offline Draft target changed")

            skill.offline_at = datetime.now(timezone.utc).replace(tzinfo=None)
            skill.offline_by = actor_id
            skill.zip_url = new_locator
            skill.draft_target_version = target_version
            skill.draft_status = "EDITING"
            skill.draft_description = new_description
            skill.draft_source_kind = "PUBLISHED_VERSION"
            session.flush()
            return OfflineCommit(
                changed=True,
                target_version=target_version,
                status="EDITING",
                locator=new_locator,
            )

    def _inspection(
        self,
        session,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        env: str,
        lock: bool,
    ) -> OfflineInspection:
        # Lock the Skill row before reading or mutating any related fact.  The
        # membership/direct/default and Artifact-commit paths take this same lock.
        locked_latest = None
        if lock:
            locked_latest = lock_skill_then_latest_published_version(
                session,
                env=env,
                skill_id=skill_id,
            )
            skill = locked_latest[0] if locked_latest is not None else None
        else:
            skill = (
                session.query(Skill)
                .filter(Skill.id == skill_id, Skill.env == env)
                .one_or_none()
            )
        if skill is None or not skill.skill_uuid:
            raise DraftNotFoundError("space skill not found")
        ownership = (
            session.query(SkillSpaceBinding, SpaceModel)
            .join(
                SpaceModel,
                (SpaceModel.id == SkillSpaceBinding.space_id)
                & (SpaceModel.env == SkillSpaceBinding.env),
            )
            .filter(
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.env == env,
                SpaceModel.deleted_at.is_(None),
            )
            .one_or_none()
        )
        actor_roles = tuple(
            str(row[0])
            for row in session.query(SkillGrant.role)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == actor_id,
                SkillGrant.env == env,
                SkillGrant.status == "ACTIVE",
            )
            .all()
        )
        if lock:
            assert locked_latest is not None
            latest = locked_latest[1]
        else:
            latest = (
                session.query(SkillVersion)
                .filter(
                    SkillVersion.skill_id == skill_id,
                    SkillVersion.env == env,
                    SkillVersion.status == "PUBLISHED",
                )
                .order_by(SkillVersion.version_ordinal.desc())
                .first()
            )
        if latest is None:
            raise DraftNotFoundError("latest Published Version not found")
        identity = OfflineSkillIdentity(
            skill_id=int(skill.id),
            skill_uuid=str(skill.skill_uuid),
            name=str(skill.name),
            sc_team_id=ownership[1].sc_team_id if ownership is not None else None,
            latest_version_id=int(latest.id),
            latest_version_ordinal=int(latest.version_ordinal),
            sc_version_number=str(latest.sc_version_number),
            offline_at=skill.offline_at,
            draft_target_version=skill.draft_target_version,
            draft_status=skill.draft_status,
            draft_locator=skill.zip_url,
        )
        attempts = (
            session.query(SkillPublicationAttempt)
            .filter(
                SkillPublicationAttempt.skill_id == identity.skill_id,
                SkillPublicationAttempt.env == env,
                SkillPublicationAttempt.status.in_(
                    ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES
                ),
            )
            .all()
        )
        publication_attempts = tuple(
            OfflinePublicationAttemptFact(
                id=int(attempt.id),
                target_version_ordinal=int(attempt.target_version_ordinal),
                status=str(attempt.status),
            )
            for attempt in attempts
        )
        identity_predicates = [SkillSetSkill.skill_id == identity.skill_id]
        if identity.skill_uuid:
            identity_predicates.append(SkillSetSkill.skill_uuid == identity.skill_uuid)
        memberships = (
            session.query(SkillSetSkill, SkillSet)
            .join(SkillSet, SkillSet.id == SkillSetSkill.skill_set_id)
            .filter(
                SkillSetSkill.env == env,
                SkillSet.env == env,
                or_(*identity_predicates),
            )
            .all()
        )
        membership_facts = tuple(
            OfflineMembershipFact(
                id=int(membership.id),
                skill_set_name=str(skill_set.name),
            )
            for membership, skill_set in memberships
        )
        installations = (
            session.query(BotSkillInstallation)
            .filter(
                BotSkillInstallation.skill_id == identity.skill_id,
                BotSkillInstallation.env == env,
            )
            .all()
        )
        installation_facts = tuple(
            OfflineInstallationFact(
                id=int(installation.id),
                bot_id=str(installation.bot_id),
            )
            for installation in installations
        )
        return OfflineInspection(
            identity=identity,
            space_bound=ownership is not None,
            actor_roles=actor_roles,
            publication_attempts=publication_attempts,
            memberships=membership_facts,
            installations=installation_facts,
        )

__all__ = ["SpaceSkillOfflineRepository"]
