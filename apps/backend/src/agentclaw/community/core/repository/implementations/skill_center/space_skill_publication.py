"""Transactional persistence for Space Skill Publication Attempts."""

from __future__ import annotations

from datetime import datetime

from injector import inject
from sqlalchemy import and_, func, or_
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
from agentclaw.community.core.repository.implementations.skill_center.skill_version_lock import (
    lock_skill_row,
    lock_skill_then_exact_version,
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
    ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES,
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
                    session,
                    request_id=request_id,
                    env=env,
                    lock=False,
                )
                if replay is not None:
                    replay = self._attempt_by_request(
                        session,
                        request_id=request_id,
                        env=env,
                        lock=True,
                    )
                    if replay is None:
                        raise RuntimeError("Publication Attempt disappeared")
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
                skill = lock_skill_row(session, env=env, skill_id=skill_id)
                if skill is None:
                    raise DraftNotFoundError("draft not found")
                ownership = (
                    session.query(SkillSpaceBinding, SpaceModel)
                    .join(
                        SpaceModel,
                        (SpaceModel.id == SkillSpaceBinding.space_id)
                        & (SpaceModel.env == SkillSpaceBinding.env),
                    )
                    .filter(
                        SkillSpaceBinding.skill_id == skill_id,
                        SkillSpaceBinding.env == env,
                        SkillSpaceBinding.space_id == space_id,
                        SpaceModel.deleted_at.is_(None),
                    )
                    .one_or_none()
                )
                if ownership is None:
                    raise DraftNotFoundError("draft not found")
                _binding, space = ownership
                self._require_publisher(
                    session,
                    skill_id=skill_id,
                    actor_id=actor_id,
                    env=env,
                )
                replay = self._attempt_by_request(
                    session,
                    request_id=request_id,
                    env=env,
                    lock=False,
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
                if skill.draft_status != "EDITING" or not skill.zip_url:
                    if skill.draft_status == "FROZEN":
                        active = (
                            session.query(SkillPublicationAttempt)
                            .filter(
                                SkillPublicationAttempt.skill_id == skill_id,
                                SkillPublicationAttempt.env == env,
                                SkillPublicationAttempt.status.in_(
                                    ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES
                                ),
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
                        SkillPublicationAttempt.status.in_(
                            ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES
                        ),
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
                latest_failed = (
                    session.query(SkillPublicationAttempt)
                    .filter(
                        SkillPublicationAttempt.skill_id == skill_id,
                        SkillPublicationAttempt.env == env,
                        SkillPublicationAttempt.status == "FAILED",
                    )
                    .order_by(SkillPublicationAttempt.id.desc())
                    .first()
                )
                if latest_failed is not None:
                    frozen = latest_failed.frozen_draft_locator
                    if not frozen or frozen == skill.zip_url:
                        raise PublicationRequiresNewAttemptError(
                            "failed publication Draft must be modified before a new Attempt"
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
                    frozen_draft_locator=skill.zip_url,
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
                    session,
                    request_id=request_id,
                    env=env,
                    lock=False,
                )
                if replay is None:
                    active = (
                        session.query(SkillPublicationAttempt)
                        .filter(
                            SkillPublicationAttempt.skill_id == skill_id,
                            SkillPublicationAttempt.env == env,
                            SkillPublicationAttempt.status.in_(
                                ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES
                            ),
                        )
                        .first()
                    )
                    if active is not None:
                        if active.status == "RESULT_UNKNOWN":
                            raise PublicationResultUnknownError(
                                "publication result is unknown"
                            )
                        raise PublicationInProgressError(
                            "publication is already in progress"
                        )
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
            return self._work(session, attempt_id=attempt_id, env=env)

    def mark_prepared(
        self, *, attempt_id: int, package_url: str, env: str
    ) -> PublicationWork:
        if not package_url:
            raise ValueError("package_url is required")
        with self._db.transactional_orm_session() as session:
            attempt, skill, _version = self._lock_attempt_aggregate(
                session, attempt_id=attempt_id, env=env
            )
            work = self._work(session, attempt_id=attempt_id, env=env)
            if attempt.status != "PREPARING":
                return work
            skill.package_url = package_url
            session.flush()
            return self._work(session, attempt_id=attempt_id, env=env)

    def claim_sc_submission(
        self, *, attempt_id: int, env: str
    ) -> PublicationSubmissionClaim:
        with self._db.transactional_orm_session() as session:
            attempt, _skill, _version = self._lock_attempt_aggregate(
                session, attempt_id=attempt_id, env=env
            )
            work = self._work(session, attempt_id=attempt_id, env=env)
            may_submit = False
            if attempt.status == "PREPARING" and attempt.sc_post_started_at is None:
                if not work.package_url:
                    raise RuntimeError("Publication package is not prepared")
                attempt.sc_post_started_at = func.now()
                attempt.status = "SC_SUBMITTING"
                attempt.recovery_state = "AUTO_RETRYING"
                attempt.recovery_kind = "SC_STATUS_CHECK"
                may_submit = True
                session.flush()
                session.refresh(attempt)
                work = self._work(session, attempt_id=attempt_id, env=env)
            return PublicationSubmissionClaim(work=work, may_submit=may_submit)

    def mark_waiting_sc(
        self, *, attempt_id: int, env: str
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
            if attempt.status in ("SC_SUBMITTING", "RESULT_UNKNOWN"):
                attempt.status = "WAITING_SC"
                attempt.sc_accepted_at = attempt.sc_accepted_at or func.now()
                attempt.recovery_state = "AUTO_RETRYING"
                attempt.recovery_kind = "SC_STATUS_CHECK"
                attempt.error_code = None
                attempt.error_message = None
                session.flush()
                session.refresh(attempt)
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
        env: str,
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt, skill, version = self._lock_attempt_aggregate(
                session,
                attempt_id=attempt_id,
                env=env,
                allow_version_appearance=True,
            )
            if attempt.status == "SUCCEEDED":
                return self._record(attempt)
            if version is not None or attempt.skill_version_id is not None:
                raise RuntimeError("a materializing Version cannot become FAILED")
            if skill.draft_status == "FROZEN":
                skill.draft_status = "EDITING"
            skill.package_url = None
            attempt.status = "FAILED"
            attempt.active_skill_key = None
            attempt.error_code = error_code
            attempt.error_message = error_message
            attempt.recovery_state = "NOT_AVAILABLE"
            attempt.recovery_kind = None
            attempt.completed_at = func.now()
            session.flush()
            session.refresh(attempt)
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
            attempt, skill, locked_version = self._lock_attempt_aggregate(
                session, attempt_id=attempt_id, env=env
            )
            work = self._work(session, attempt_id=attempt_id, env=env)
            if attempt.status == "MATERIALIZING":
                if locked_version is None:
                    raise RuntimeError("MATERIALIZING Attempt has no locked Version")
                self._require_exact_version(
                    locked_version,
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
            return self._work(session, attempt_id=attempt_id, env=env)

    def complete_success(
        self,
        *,
        attempt_id: int,
        skill_version_id: int,
        env: str,
    ) -> PublicationAttemptRecord:
        with self._db.transactional_orm_session() as session:
            attempt, skill, version = self._lock_attempt_aggregate(
                session, attempt_id=attempt_id, env=env
            )
            located_version_id = (
                int(attempt.skill_version_id)
                if attempt.skill_version_id is not None
                else None
            )
            if located_version_id is None or version is None:
                raise RuntimeError("Attempt is not materializing a Version")
            if attempt.status == "SUCCEEDED":
                if located_version_id != skill_version_id:
                    raise RuntimeError("SUCCEEDED Attempt points at another Version")
                return self._record(attempt)
            if (
                attempt.status != "MATERIALIZING"
                or located_version_id != skill_version_id
            ):
                raise RuntimeError("Attempt is not materializing this Version")
            if version.status != "PUBLISHED":
                raise RuntimeError("Version is not PUBLISHED")
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
            attempt.completed_at = func.now()
            session.flush()
            session.refresh(attempt)
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
            if attempt.recovery_state == "AUTO_RETRYING":
                return PublicationRetryResult(
                    attempt=self._record(attempt), task_required=True
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

    def _work(self, session, *, attempt_id: int, env: str) -> PublicationWork:
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
        row = query.one_or_none()
        if row is None:
            raise PublicationAttemptNotFoundError("publication attempt not found")
        attempt, skill, binding, space = row
        database_now = session.query(func.now()).scalar()
        if not isinstance(database_now, datetime):
            raise RuntimeError("database CURRENT_TIMESTAMP is not a datetime")
        if (
            attempt.status in ACTIVE_SKILL_PUBLICATION_ATTEMPT_STATUSES
            and not attempt.frozen_draft_locator
        ):
            raise RuntimeError(
                "active Publication Attempt has no frozen Draft Revision"
            )
        return PublicationWork(
            attempt=self._record(attempt),
            space_id=int(binding.space_id),
            space_type=space.space_type,
            sc_team_id=str(space.sc_team_id) if space.sc_team_id else None,
            skill_uuid=str(skill.skill_uuid or ""),
            skill_name=skill.name,
            draft_description=skill.draft_description or skill.description or "",
            package_url=skill.package_url,
            database_now=database_now,
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
    def _attempt_version_identity(
        session, *, attempt_id: int, env: str
    ) -> tuple[int, int | None]:
        row = (
            session.query(
                SkillPublicationAttempt.skill_id,
                SkillPublicationAttempt.skill_version_id,
            )
            .filter(
                SkillPublicationAttempt.id == attempt_id,
                SkillPublicationAttempt.env == env,
            )
            .one_or_none()
        )
        if row is None:
            raise PublicationAttemptNotFoundError("publication attempt not found")
        return int(row[0]), int(row[1]) if row[1] is not None else None

    def _lock_attempt_aggregate(
        self,
        session,
        *,
        attempt_id: int,
        env: str,
        allow_version_appearance: bool = False,
    ) -> tuple[SkillPublicationAttempt, Skill, SkillVersion | None]:
        """Lock one Publication aggregate as Skill [-> Version] -> Attempt."""
        skill_id, located_version_id = self._attempt_version_identity(
            session,
            attempt_id=attempt_id,
            env=env,
        )
        version = None
        if located_version_id is None:
            skill = lock_skill_row(session, env=env, skill_id=skill_id)
        else:
            locked = lock_skill_then_exact_version(
                session,
                env=env,
                skill_id=skill_id,
                skill_version_id=located_version_id,
            )
            if locked is None:
                skill = lock_skill_row(session, env=env, skill_id=skill_id)
            else:
                skill, version = locked
        if skill is None:
            raise RuntimeError("Publication Skill not found")
        attempt = self._attempt(session, attempt_id=attempt_id, env=env, lock=True)
        self._require_attempt_version_identity(
            attempt,
            skill_id=skill_id,
            skill_version_id=located_version_id,
            allow_version_appearance=allow_version_appearance,
        )
        return attempt, skill, version

    @staticmethod
    def _require_attempt_version_identity(
        attempt: SkillPublicationAttempt,
        *,
        skill_id: int,
        skill_version_id: int | None,
        allow_version_appearance: bool = False,
    ) -> None:
        current_version_id = (
            int(attempt.skill_version_id)
            if attempt.skill_version_id is not None
            else None
        )
        version_matches = current_version_id == skill_version_id
        if (
            allow_version_appearance
            and skill_version_id is None
            and current_version_id is not None
        ):
            version_matches = True
        if int(attempt.skill_id) != skill_id or not version_matches:
            raise RuntimeError("Attempt identity changed while acquiring locks")

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
            frozen_draft_locator=attempt.frozen_draft_locator,
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
