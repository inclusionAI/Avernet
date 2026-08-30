"""Transactional blocker recheck and commit for recoverable Skill Offline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from injector import inject
from sqlalchemy import or_

from agentclaw.community.core.skill_center.space_skill_offline_service_protocol import (
    OfflineBlockerKind,
    OfflineImpactItem,
)
from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import (
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.space_skill_offline_types import (
    OfflineCommit,
    OfflineInspection,
    OfflineSkillIdentity,
)
from agentclaw.community.core.repository.protocols.space_skill_offline import (
    SpaceSkillOfflineRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    DraftNotFoundError,
    DraftRevisionConflictError,
    SpaceSkillGrantForbiddenError,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.plugin_api.database import DatabasePlugin


_ACTIVE_ATTEMPTS = (
    "PREPARING",
    "SC_SUBMITTING",
    "WAITING_SC",
    "MATERIALIZING",
    "RESULT_UNKNOWN",
)
_BLOCKER_ORDER = {kind: index for index, kind in enumerate(OfflineBlockerKind)}


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
            identity = self._identity(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
                lock=False,
            )
            blockers = self._db_blockers(session, identity=identity, env=env)
        return OfflineInspection(identity=identity, blockers=self._ordered(blockers))

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
            identity = self._identity(
                session,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
                lock=True,
            )
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

            guard(
                OfflineInspection(
                    identity=identity,
                    blockers=self._ordered(
                        self._db_blockers(session, identity=identity, env=env)
                    ),
                )
            )
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

    def _identity(
        self,
        session,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        env: str,
        lock: bool,
    ) -> OfflineSkillIdentity:
        # Lock the Skill row before reading or mutating any related fact.  The
        # membership/direct/default and Artifact-commit paths take this same lock.
        query = session.query(Skill).filter(Skill.id == skill_id, Skill.env == env)
        skill = (query.with_for_update() if lock else query).one_or_none()
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
        grant = (
            session.query(SkillGrant.id)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == actor_id,
                SkillGrant.env == env,
                SkillGrant.status == "ACTIVE",
                SkillGrant.role.in_(("OWNER", "MANAGER")),
            )
            .one_or_none()
        )
        if ownership is None or grant is None:
            raise SpaceSkillGrantForbiddenError("owner or manager required")
        latest_query = (
            session.query(SkillVersion)
            .filter(
                SkillVersion.skill_id == skill_id,
                SkillVersion.env == env,
                SkillVersion.status == "PUBLISHED",
            )
            .order_by(SkillVersion.version_ordinal.desc())
        )
        latest = (latest_query.with_for_update() if lock else latest_query).first()
        if latest is None:
            raise DraftNotFoundError("latest Published Version not found")
        return OfflineSkillIdentity(
            skill_id=int(skill.id),
            skill_uuid=str(skill.skill_uuid),
            name=str(skill.name),
            sc_team_id=ownership[1].sc_team_id,
            latest_version_id=int(latest.id),
            latest_version_ordinal=int(latest.version_ordinal),
            sc_version_number=str(latest.sc_version_number),
            offline_at=skill.offline_at,
            draft_target_version=skill.draft_target_version,
            draft_status=skill.draft_status,
            draft_locator=skill.zip_url,
        )

    def _db_blockers(self, session, *, identity: OfflineSkillIdentity, env: str):
        blockers: list[OfflineImpactItem] = []
        if identity.draft_status is not None:
            blockers.append(
                OfflineImpactItem(
                    kind=OfflineBlockerKind.DRAFT,
                    resource_id=str(identity.skill_id),
                    display_name=f"Draft V{identity.draft_target_version}",
                )
            )
        attempts = (
            session.query(SkillPublicationAttempt)
            .filter(
                SkillPublicationAttempt.skill_id == identity.skill_id,
                SkillPublicationAttempt.env == env,
                SkillPublicationAttempt.status.in_(_ACTIVE_ATTEMPTS),
            )
            .order_by(SkillPublicationAttempt.id.asc())
            .all()
        )
        blockers.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.PUBLICATION,
                resource_id=str(attempt.id),
                display_name=f"Publication V{attempt.target_version_ordinal}",
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
            .order_by(SkillSetSkill.id.asc())
            .all()
        )
        blockers.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.MEMBERSHIP,
                resource_id=str(membership.id),
                display_name=str(skill_set.name),
            )
            for membership, skill_set in memberships
        )
        installations = (
            session.query(BotSkillInstallation)
            .filter(
                BotSkillInstallation.skill_id == identity.skill_id,
                BotSkillInstallation.env == env,
            )
            .order_by(BotSkillInstallation.id.asc())
            .all()
        )
        blockers.extend(
            OfflineImpactItem(
                kind=OfflineBlockerKind.INSTALLATION,
                resource_id=str(installation.id),
                display_name=str(installation.bot_id),
            )
            for installation in installations
        )
        return blockers

    @staticmethod
    def _ordered(blockers):
        return tuple(
            sorted(
                blockers,
                key=lambda item: (
                    _BLOCKER_ORDER[item.kind],
                    item.resource_id,
                    item.display_name,
                ),
            )
        )

__all__ = ["SpaceSkillOfflineRepository"]
