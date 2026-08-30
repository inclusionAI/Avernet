"""Transactional persistence for Space Skill Publication Attempts."""

from __future__ import annotations

from datetime import datetime

from injector import inject
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.models.skill import (
    BotSkillInstallation,
    Skill,
    SkillSet,
    SkillSetSkill,
)
from agentclaw.community.core.models.space_skill import (
    SkillDraftEditLease,
    SkillDraftUpgradeRequest,
    SkillGrant,
    SkillPublicationAttempt,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.protocols.space_skill_publication import (
    SpaceSkillPublicationRepositoryProtocol,
)
from agentclaw.community.core.skill_center.errors import (
    DraftEditLeaseConflictError,
    DraftNotFoundError,
    PublicationAttemptNotFoundError,
    PublicationInProgressError,
    PublicationRecoveryNotAvailableError,
    PublicationRequiresNewAttemptError,
    PublicationResultUnknownError,
    SpaceSkillGrantForbiddenError,
    SpaceSkillIdempotencyConflictError,
)
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptCreation,
    PublicationAttemptRecord,
    PublicationAttemptStatus,
    PublicationImpactCandidate,
    PublicationRecovery,
    PublicationRecoveryKind,
    PublicationRecoveryState,
    PublicationRetryResult,
    PublicationSubmissionClaim,
    PublicationWork,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


_ACTIVE_STATUSES = (
    "PREPARING",
    "SC_SUBMITTING",
    "WAITING_SC",
    "MATERIALIZING",
    "RESULT_UNKNOWN",
)


class SpaceSkillPublicationRepository(SpaceSkillPublicationRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create_or_replay_attempt(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
        env: str,
    ) -> PublicationAttemptCreation:
        request_id = request_id.strip() if isinstance(request_id, str) else ""
        if not request_id or len(request_id) > 128:
            raise ValueError("Idempotency-Key must contain 1..128 characters")
        try:
            with self._db.transactional_orm_session() as session:
                replay = self._attempt_by_request(
                    session, request_id=request_id, env=env, lock=True
                )
                if replay is not None:
                    self._require_attempt_scope(
                        session,
                        attempt=replay,
                        space_id=space_id,
                        skill_id=skill_id,
                        actor_id=actor_id,
                        env=env,
                    )
                    return PublicationAttemptCreation(
                        attempt=self._record(replay), created=False
                    )
                row = (
                    session.query(Skill, SpaceModel)
                    .join(
                        SkillSpaceBinding,
                        (SkillSpaceBinding.skill_id == Skill.id)
                        & (SkillSpaceBinding.env == Skill.env),
                    )
                    .join(
                        SpaceModel,
                        (SpaceModel.id == SkillSpaceBinding.space_id)
                        & (SpaceModel.env == SkillSpaceBinding.env),
                    )
                    .filter(
                        Skill.id == skill_id,
                        Skill.env == env,
                        SkillSpaceBinding.space_id == space_id,
                        SpaceModel.deleted_at.is_(None),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if row is None:
                    raise DraftNotFoundError("draft not found")
                skill, space = row
                self._require_publisher(
                    session,
                    skill_id=skill_id,
                    actor_id=actor_id,
                    env=env,
                )
                if skill.draft_status != "EDITING" or not skill.zip_url:
                    if skill.draft_status == "FROZEN":
                        active = (
                            session.query(SkillPublicationAttempt)
                            .filter(
                                SkillPublicationAttempt.skill_id == skill_id,
                                SkillPublicationAttempt.env == env,
                                SkillPublicationAttempt.status.in_(_ACTIVE_STATUSES),
                            )
                            .order_by(SkillPublicationAttempt.id.desc())
                            .first()
                        )
                        if active is not None and active.status == "RESULT_UNKNOWN":
                            raise PublicationResultUnknownError(
                                "publication result is unknown"
                            )
                        if active is not None:
                            raise PublicationInProgressError(
                                "publication is already in progress"
                            )
                    raise DraftNotFoundError("editable draft not found")
                active = (
                    session.query(SkillPublicationAttempt)
                    .filter(
                        SkillPublicationAttempt.skill_id == skill_id,
                        SkillPublicationAttempt.env == env,
                        SkillPublicationAttempt.status.in_(_ACTIVE_STATUSES),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if active is not None:
                    if active.status == "RESULT_UNKNOWN":
                        raise PublicationResultUnknownError(
                            "publication result is unknown"
                        )
                    raise PublicationInProgressError(
                        "publication is already in progress"
                    )
                if space.space_type == "TEAM":
                    lease = (
                        session.query(SkillDraftEditLease)
                        .filter(
                            SkillDraftEditLease.skill_id == skill_id,
                            SkillDraftEditLease.env == env,
                        )
                        .with_for_update()
                        .one_or_none()
                    )
                    if lease is not None and lease.holder_user_id not in (
                        None,
                        actor_id,
                    ):
                        raise DraftEditLeaseConflictError(
                            "draft lease is held by another actor"
                        )
                target_version = int(skill.draft_target_version or 0)
                if target_version < 1:
                    raise DraftNotFoundError("draft target version is missing")
                attempt = SkillPublicationAttempt(
                    skill_id=skill_id,
                    request_id=request_id,
                    active_skill_key=self._active_key(env=env, skill_id=skill_id),
                    target_version_ordinal=target_version,
                    sc_version_number=f"{target_version}.0.0",
                    status="PREPARING",
                    recovery_state="AUTO_RETRYING",
                    recovery_kind="PREPARATION",
                    created_by=actor_id,
                    avernet_tenant=get_current_avernet_tenant(),
                    env=env,
                )
                skill.draft_status = "FROZEN"
                session.add(attempt)
                session.flush()
                return PublicationAttemptCreation(
                    attempt=self._record(attempt), created=True
                )
        except IntegrityError:
            with self._db.orm_session() as session:
                replay = self._attempt_by_request(
                    session, request_id=request_id, env=env, lock=False
                )
                if replay is None:
                    raise
                self._require_attempt_scope(
                    session,
                    attempt=replay,
                    space_id=space_id,
                    skill_id=skill_id,
                    actor_id=actor_id,
                    env=env,
                )
                return PublicationAttemptCreation(
                    attempt=self._record(replay), created=False
                )

    def get_attempt(
        self, *, space_id: int, skill_id: int, attempt_id: int, env: str
    ) -> PublicationAttemptRecord:
        with self._db.orm_session() as session:
            attempt = (
                session.query(SkillPublicationAttempt)
                .join(
                    SkillSpaceBinding,
                    (SkillSpaceBinding.skill_id == SkillPublicationAttempt.skill_id)
                    & (SkillSpaceBinding.env == SkillPublicationAttempt.env),
                )
                .filter(
                    SkillPublicationAttempt.id == attempt_id,
                    SkillPublicationAttempt.skill_id == skill_id,
                    SkillPublicationAttempt.env == env,
                    SkillSpaceBinding.space_id == space_id,
                )
                .one_or_none()
            )
            if attempt is None:
                raise PublicationAttemptNotFoundError("publication attempt not found")
            return self._record(attempt)

    def list_attempts(
        self,
        *,
        space_id: int,
        skill_id: int,
        env: str,
        offset: int,
        limit: int,
    ) -> tuple[int, list[PublicationAttemptRecord]]:
        with self._db.orm_session() as session:
            binding = (
                session.query(SkillSpaceBinding.id)
                .filter(
                    SkillSpaceBinding.skill_id == skill_id,
                    SkillSpaceBinding.space_id == space_id,
                    SkillSpaceBinding.env == env,
                )
                .one_or_none()
            )
            if binding is None:
                raise PublicationAttemptNotFoundError("publication history not found")
            query = session.query(SkillPublicationAttempt).filter(
                SkillPublicationAttempt.skill_id == skill_id,
                SkillPublicationAttempt.env == env,
            )
            total = query.count()
            rows = (
                query.order_by(
                    SkillPublicationAttempt.gmt_created.desc(),
                    SkillPublicationAttempt.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return total, [self._record(row) for row in rows]

    def require_publisher(
        self, *, space_id: int, skill_id: int, actor_id: str, env: str
    ) -> None:
        with self._db.orm_session() as session:
            binding = (
                session.query(SkillSpaceBinding.id)
                .filter(
                    SkillSpaceBinding.skill_id == skill_id,
                    SkillSpaceBinding.space_id == space_id,
                    SkillSpaceBinding.env == env,
                )
                .one_or_none()
            )
            if binding is None:
                raise SpaceSkillGrantForbiddenError("owner or manager required")
            self._require_publisher(
                session, skill_id=skill_id, actor_id=actor_id, env=env
            )

    def list_impact_candidates(
        self, *, skill_id: int, env: str
    ) -> tuple[PublicationImpactCandidate, ...]:
        """Return an over-approximation; the Service confirms each via Reader."""
        with self._db.orm_session() as session:
            pairs = {
                (str(row.owner_id), str(row.bot_id))
                for row in session.query(BotSkillInstallation)
                .filter(
                    BotSkillInstallation.skill_id == skill_id,
                    BotSkillInstallation.env == env,
                )
                .all()
            }
            pairs.update(
                (str(row.user_id), str(row.bolt_id))
                for row in session.query(SkillSet)
                .join(
                    SkillSetSkill,
                    (SkillSetSkill.skill_set_id == SkillSet.id)
                    & (SkillSetSkill.env == SkillSet.env),
                )
                .filter(
                    SkillSetSkill.skill_id == skill_id,
                    SkillSetSkill.env == env,
                    SkillSet.is_active.is_(True),
                    SkillSet.is_default.is_(False),
                    SkillSet.user_id.is_not(None),
                    SkillSet.bolt_id.is_not(None),
                )
                .all()
            )
            if not pairs:
                return ()
            predicate = or_(
                *(
                    and_(BotModel.owner_id == owner_id, BotModel.bot_id == bot_id)
                    for owner_id, bot_id in pairs
                )
            )
            bots = (
                session.query(BotModel)
                .filter(
                    BotModel.env == env,
                    BotModel.is_delete == 0,
                    predicate,
                )
                .order_by(BotModel.bot_id.asc(), BotModel.owner_id.asc())
                .all()
            )
            return tuple(
                PublicationImpactCandidate(
                    owner_id=str(bot.owner_id),
                    bot_id=str(bot.bot_id),
                    bot_name=bot.bot_name,
                    bot=bot.to_dict(),
                )
                for bot in bots
            )

    def get_work(self, *, attempt_id: int, env: str) -> PublicationWork:
        with self._db.orm_session() as session:
            return self._work(session, attempt_id=attempt_id, env=env, lock=False)

    def mark_prepared(
        self, *, attempt_id: int, package_url: str, env: str
    ) -> PublicationWork:
        if not package_url:
            raise ValueError("package_url is required")
        with self._db.transactional_orm_session() as session:
            work = self._work(session, attempt_id=attempt_id, env=env, lock=True)
            if work.attempt.status is not PublicationAttemptStatus.PREPARING:
                return work
            skill = session.query(Skill).filter(Skill.id == work.attempt.skill_id).one()
            skill.package_url = package_url
            session.flush()
            return self._work(session, attempt_id=attempt_id, env=env, lock=False)

    def claim_sc_submission(
        self, *, attempt_id: int, started_at: datetime, env: str
    ) -> PublicationSubmissionClaim:
        with self._db.transactional_orm_session() as session:
            work = self._work(session, attempt_id=attempt_id, env=env, lock=True)
            attempt = self._attempt(session, attempt_id=attempt_id, env=env)
            may_submit = False
            if attempt.status == "PREPARING" and attempt.sc_post_started_at is None:
                if not work.package_url:
                    raise RuntimeError("Publication package is not prepared")
                attempt.sc_post_started_at = started_at
                attempt.status = "SC_SUBMITTING"
                attempt.recovery_state = "AUTO_RETRYING"
                attempt.recovery_kind = "SC_STATUS_CHECK"
                may_submit = True
                session.flush()
                work = self._work(session, attempt_id=attempt_id, env=env, lock=False)
            return PublicationSubmissionClaim(work=work, may_submit=may_submit)

    def mark_waiting_sc(
        self, *, attempt_id: int, accepted_at: datetime, env: str
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            if attempt.status == "SC_SUBMITTING":
                attempt.status = "WAITING_SC"
                attempt.sc_accepted_at = accepted_at
                attempt.recovery_state = "AUTO_RETRYING"
                attempt.recovery_kind = "SC_STATUS_CHECK"
                attempt.error_code = None
                attempt.error_message = None
                session.flush()
            return self._record(attempt)

    def mark_result_unknown(
        self,
        *,
        attempt_id: int,
        error_code: str,
        error_message: str,
        recovery_available: bool,
        env: str,
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            if attempt.status not in ("SUCCEEDED", "FAILED", "MATERIALIZING"):
                attempt.status = "RESULT_UNKNOWN"
                attempt.error_code = error_code
                attempt.error_message = error_message
                attempt.recovery_state = (
                    "AVAILABLE" if recovery_available else "AUTO_RETRYING"
                )
                attempt.recovery_kind = "SC_STATUS_CHECK"
                session.flush()
            return self._record(attempt)

    def mark_failed(
        self,
        *,
        attempt_id: int,
        error_code: str,
        error_message: str,
        completed_at: datetime,
        env: str,
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            if attempt.status == "SUCCEEDED":
                return self._record(attempt)
            if attempt.skill_version_id is not None:
                raise RuntimeError("a materializing Version cannot become FAILED")
            skill = (
                session.query(Skill)
                .filter(Skill.id == attempt.skill_id, Skill.env == env)
                .with_for_update()
                .one()
            )
            if skill.draft_status == "FROZEN":
                skill.draft_status = "EDITING"
            attempt.status = "FAILED"
            attempt.active_skill_key = None
            attempt.error_code = error_code
            attempt.error_message = error_message
            attempt.recovery_state = "NOT_AVAILABLE"
            attempt.recovery_kind = None
            attempt.completed_at = completed_at
            session.flush()
            return self._record(attempt)

    def begin_materialization(
        self,
        *,
        attempt_id: int,
        sc_skill_id: int,
        sc_version_id: int,
        sc_sha256: str | None,
        env: str,
    ) -> PublicationWork:
        if sc_skill_id < 1 or sc_version_id < 1:
            raise ValueError("exact SC ids must be positive integers")
        with self._db.transactional_orm_session() as session:
            work = self._work(session, attempt_id=attempt_id, env=env, lock=True)
            attempt = self._attempt(session, attempt_id=attempt_id, env=env)
            if attempt.status == "MATERIALIZING":
                version = self._version_for_attempt(session, attempt, env=env)
                self._require_exact_version(
                    version,
                    sc_skill_id=sc_skill_id,
                    sc_version_id=sc_version_id,
                    sc_sha256=sc_sha256,
                )
                return work
            if attempt.status not in (
                "SC_SUBMITTING",
                "WAITING_SC",
                "RESULT_UNKNOWN",
            ):
                raise RuntimeError("Attempt cannot begin materialization")
            skill = (
                session.query(Skill)
                .filter(Skill.id == attempt.skill_id, Skill.env == env)
                .with_for_update()
                .one()
            )
            expected_locator = f"center://{skill.skill_uuid}"
            if skill.git_path not in (None, "", expected_locator):
                raise RuntimeError("Space Skill has a conflicting Center locator")
            skill.git_path = expected_locator
            version = SkillVersion(
                skill_id=attempt.skill_id,
                publication_attempt_id=attempt.id,
                version_ordinal=attempt.target_version_ordinal,
                status="MATERIALIZING",
                sc_version_number=attempt.sc_version_number,
                sc_skill_id=sc_skill_id,
                sc_version_id=sc_version_id,
                sc_sha256=sc_sha256,
                name=skill.name,
                description=skill.draft_description,
                metadata_json=None,
                published_at=None,
                created_by=attempt.created_by,
                avernet_tenant=get_current_avernet_tenant(),
                env=env,
            )
            session.add(version)
            session.flush()
            attempt.skill_version_id = version.id
            attempt.status = "MATERIALIZING"
            attempt.recovery_state = "AUTO_RETRYING"
            attempt.recovery_kind = "MATERIALIZATION"
            attempt.error_code = None
            attempt.error_message = None
            session.flush()
            return self._work(session, attempt_id=attempt_id, env=env, lock=False)

    def complete_success(
        self,
        *,
        attempt_id: int,
        skill_version_id: int,
        completed_at: datetime,
        env: str,
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            if attempt.status == "SUCCEEDED":
                if int(attempt.skill_version_id or 0) != skill_version_id:
                    raise RuntimeError("SUCCEEDED Attempt points at another Version")
                return self._record(attempt)
            if (
                attempt.status != "MATERIALIZING"
                or int(attempt.skill_version_id or 0) != skill_version_id
            ):
                raise RuntimeError("Attempt is not materializing this Version")
            version = (
                session.query(SkillVersion)
                .filter(
                    SkillVersion.id == skill_version_id,
                    SkillVersion.skill_id == attempt.skill_id,
                    SkillVersion.env == env,
                )
                .with_for_update()
                .one()
            )
            if version.status != "PUBLISHED":
                raise RuntimeError("Version is not PUBLISHED")
            skill = (
                session.query(Skill)
                .filter(Skill.id == attempt.skill_id, Skill.env == env)
                .with_for_update()
                .one()
            )
            if skill.draft_status == "FROZEN":
                if int(skill.draft_target_version or 0) != int(
                    attempt.target_version_ordinal
                ):
                    raise RuntimeError("Frozen Draft target changed")
                skill.zip_url = None
                skill.package_url = None
                skill.draft_target_version = None
                skill.draft_status = None
                skill.draft_description = None
                skill.draft_source_kind = None
                session.query(SkillDraftUpgradeRequest).filter(
                    SkillDraftUpgradeRequest.skill_id == skill.id,
                    SkillDraftUpgradeRequest.env == env,
                    SkillDraftUpgradeRequest.status == "ACTIVE",
                    SkillDraftUpgradeRequest.target_version_ordinal
                    == attempt.target_version_ordinal,
                ).update(
                    {SkillDraftUpgradeRequest.status: "SPENT"},
                    synchronize_session=False,
                )
            elif skill.draft_status is not None:
                raise RuntimeError("Publication Draft is no longer frozen")
            attempt.status = "SUCCEEDED"
            attempt.active_skill_key = None
            attempt.recovery_state = "NOT_AVAILABLE"
            attempt.recovery_kind = None
            attempt.error_code = None
            attempt.error_message = None
            attempt.completed_at = completed_at
            session.flush()
            return self._record(attempt)

    def mark_recovery_available(
        self,
        *,
        attempt_id: int,
        kind: PublicationRecoveryKind,
        error_code: str,
        error_message: str,
        env: str,
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            if attempt.status not in ("SUCCEEDED", "FAILED"):
                attempt.recovery_state = "AVAILABLE"
                attempt.recovery_kind = kind.value
                attempt.error_code = error_code
                attempt.error_message = error_message
                session.flush()
            return self._record(attempt)

    def restart_recovery(
        self,
        *,
        space_id: int,
        skill_id: int,
        attempt_id: int,
        actor_id: str,
        env: str,
    ) -> PublicationRetryResult:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            self._require_attempt_scope(
                session,
                attempt=attempt,
                space_id=space_id,
                skill_id=skill_id,
                actor_id=actor_id,
                env=env,
            )
            if attempt.status == "SUCCEEDED":
                return PublicationRetryResult(
                    attempt=self._record(attempt), task_required=False
                )
            if attempt.status == "FAILED":
                raise PublicationRequiresNewAttemptError(
                    "failed publication requires a new attempt"
                )
            if attempt.recovery_state != "AVAILABLE":
                raise PublicationRecoveryNotAvailableError(
                    "publication recovery is not available"
                )
            if attempt.recovery_kind is None:
                raise RuntimeError("AVAILABLE recovery has no kind")
            attempt.recovery_state = "AUTO_RETRYING"
            attempt.error_code = None
            attempt.error_message = None
            session.flush()
            return PublicationRetryResult(
                attempt=self._record(attempt), task_required=True
            )

    def _work(
        self, session, *, attempt_id: int, env: str, lock: bool
    ) -> PublicationWork:
        query = (
            session.query(SkillPublicationAttempt, Skill, SkillSpaceBinding, SpaceModel)
            .join(
                Skill,
                (Skill.id == SkillPublicationAttempt.skill_id)
                & (Skill.env == SkillPublicationAttempt.env),
            )
            .join(
                SkillSpaceBinding,
                (SkillSpaceBinding.skill_id == Skill.id)
                & (SkillSpaceBinding.env == Skill.env),
            )
            .join(
                SpaceModel,
                (SpaceModel.id == SkillSpaceBinding.space_id)
                & (SpaceModel.env == SkillSpaceBinding.env),
            )
            .filter(
                SkillPublicationAttempt.id == attempt_id,
                SkillPublicationAttempt.env == env,
            )
        )
        if lock:
            query = query.with_for_update()
        row = query.one_or_none()
        if row is None:
            raise PublicationAttemptNotFoundError("publication attempt not found")
        attempt, skill, binding, space = row
        return PublicationWork(
            attempt=self._record(attempt),
            space_id=int(binding.space_id),
            space_type=space.space_type,
            sc_team_id=str(space.sc_team_id) if space.sc_team_id else None,
            skill_uuid=str(skill.skill_uuid or ""),
            skill_name=skill.name,
            draft_description=skill.draft_description or skill.description or "",
            draft_locator=skill.zip_url,
            package_url=skill.package_url,
        )

    @staticmethod
    def _attempt(session, *, attempt_id: int, env: str, lock: bool = False):
        query = session.query(SkillPublicationAttempt).filter(
            SkillPublicationAttempt.id == attempt_id,
            SkillPublicationAttempt.env == env,
        )
        if lock:
            query = query.with_for_update()
        attempt = query.one_or_none()
        if attempt is None:
            raise PublicationAttemptNotFoundError("publication attempt not found")
        return attempt

    @staticmethod
    def _version_for_attempt(session, attempt, *, env: str):
        if attempt.skill_version_id is None:
            raise RuntimeError("MATERIALIZING Attempt has no Version")
        return (
            session.query(SkillVersion)
            .filter(
                SkillVersion.id == attempt.skill_version_id,
                SkillVersion.skill_id == attempt.skill_id,
                SkillVersion.env == env,
            )
            .one()
        )

    @staticmethod
    def _require_exact_version(
        version,
        *,
        sc_skill_id: int,
        sc_version_id: int,
        sc_sha256: str | None,
    ) -> None:
        if (
            int(version.sc_skill_id or 0) != sc_skill_id
            or int(version.sc_version_id or 0) != sc_version_id
            or version.sc_sha256 != sc_sha256
        ):
            raise RuntimeError("Attempt already materializes another exact Version")

    @staticmethod
    def _attempt_by_request(session, *, request_id: str, env: str, lock: bool):
        query = session.query(SkillPublicationAttempt).filter(
            SkillPublicationAttempt.request_id == request_id,
            SkillPublicationAttempt.env == env,
        )
        if lock:
            query = query.with_for_update()
        rows = query.limit(2).all()
        if len(rows) > 1:
            raise RuntimeError("Publication Idempotency-Key is not globally unique")
        return rows[0] if rows else None

    @staticmethod
    def _require_attempt_scope(
        session,
        *,
        attempt: SkillPublicationAttempt,
        space_id: int,
        skill_id: int,
        actor_id: str,
        env: str,
    ) -> None:
        if int(attempt.skill_id) != skill_id:
            raise SpaceSkillIdempotencyConflictError(
                "publication request belongs to another Skill"
            )
        binding = (
            session.query(SkillSpaceBinding.id)
            .filter(
                SkillSpaceBinding.skill_id == skill_id,
                SkillSpaceBinding.space_id == space_id,
                SkillSpaceBinding.env == env,
            )
            .one_or_none()
        )
        if binding is None:
            raise SpaceSkillIdempotencyConflictError(
                "publication request belongs to another Space"
            )
        SpaceSkillPublicationRepository._require_publisher(
            session, skill_id=skill_id, actor_id=actor_id, env=env
        )

    @staticmethod
    def _require_publisher(session, *, skill_id: int, actor_id: str, env: str) -> None:
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
        if grant is None:
            raise SpaceSkillGrantForbiddenError("owner or manager required")

    @staticmethod
    def _active_key(*, env: str, skill_id: int) -> str:
        return f"{get_current_avernet_tenant()}:{env}:{skill_id}"

    @staticmethod
    def _record(attempt: SkillPublicationAttempt) -> PublicationAttemptRecord:
        state = PublicationRecoveryState(attempt.recovery_state or "NOT_AVAILABLE")
        kind = (
            PublicationRecoveryKind(attempt.recovery_kind)
            if attempt.recovery_kind is not None
            else None
        )
        return PublicationAttemptRecord(
            attempt_id=int(attempt.id),
            skill_id=int(attempt.skill_id),
            target_version=int(attempt.target_version_ordinal),
            status=PublicationAttemptStatus(attempt.status),
            sc_version_number=attempt.sc_version_number,
            recovery=PublicationRecovery(state=state, kind=kind),
            error_code=attempt.error_code,
            error_message=attempt.error_message,
            skill_version_id=(
                int(attempt.skill_version_id)
                if attempt.skill_version_id is not None
                else None
            ),
            created_by=attempt.created_by,
            gmt_created=attempt.gmt_created,
            gmt_modified=attempt.gmt_modified,
            sc_post_started_at=attempt.sc_post_started_at,
            sc_accepted_at=attempt.sc_accepted_at,
            completed_at=attempt.completed_at,
        )


__all__ = ["SpaceSkillPublicationRepository"]
