"""One-table persistence for exact Local Skill upload security observations."""

from __future__ import annotations

import json
from threading import Event
from typing import Any
from uuid import uuid4

from injector import inject
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentclaw.community.core.models.local_skill_security_observation import (
    LocalSkillSecurityObservationModel as ObservationRow,
)
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.repository.implementations.skill_center.local_skill_security_recovery import (
    LocalSkillSecurityRecoveryQueries,
    _handoff,
    _identity,
    _observation,
    _utc_now,
)
from agentclaw.community.core.repository.protocols.local_skill_security_observation import (
    AssetIncarnationMismatchError,
    LocalSkillSecurityObservationRepositoryProtocol,
    ObservationHandoffCancelledError,
)
from agentclaw.community.core.skill_center.local_skill_security_observation import (
    LocalSkillSecurityHandoffAttempt,
    LocalSkillSecurityIdentity,
    LocalSkillSecurityObservation,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def _upload_order_conflict(error: IntegrityError) -> bool:
    detail = str(error.orig).lower()
    return "uk_local_security_upload_order" in detail or (
        "unique constraint failed" in detail and "upload_order" in detail
    )


class LocalSkillSecurityObservationRepository(
    LocalSkillSecurityRecoveryQueries, LocalSkillSecurityObservationRepositoryProtocol
):
    """One security row owns upload order, identity, scan, and sync facts."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    @staticmethod
    def _asset_rows(session: Session, tenant: str, env: str, bot_id: str,
                    owner_id: str, skill_id: int):
        return session.query(ObservationRow).filter_by(
            avernet_tenant=tenant, env=env, bot_id=bot_id,
            owner_id=owner_id, skill_id=skill_id,
        )

    def _latest_row(self, session: Session, tenant: str, env: str,
                    bot_id: str, owner_id: str, skill_id: int,
                    incarnation_id: str | None = None) -> ObservationRow | None:
        query = self._asset_rows(session, tenant, env, bot_id, owner_id, skill_id)
        if incarnation_id is not None:
            query = query.filter(ObservationRow.incarnation_id == incarnation_id)
        return query.order_by(
            ObservationRow.upload_order.desc(), ObservationRow.id.desc()
        ).first()

    @staticmethod
    def _formal_matches(session: Session, tenant: str, env: str, bot_id: str,
                        owner_id: str, skill_id: int) -> bool:
        query = session.query(Skill.id).filter(
            Skill.id == skill_id,
            Skill.avernet_tenant == tenant,
            Skill.env == env,
            Skill.bolt_id == bot_id,
            Skill.user_id == owner_id,
            Skill.git_path.like("local://%"),
        )
        return query.first() is not None

    def _assert_incarnation(self, session: Session, *, tenant: str, env: str,
                            bot_id: str, owner_id: str, skill_id: int,
                            incarnation_id: str, allow_new: bool = False) -> None:
        if not self._formal_matches(
            session, tenant, env, bot_id, owner_id, skill_id
        ):
            raise AssetIncarnationMismatchError(
                "formal Local Skill no longer matches the security identity"
            )
        latest = self._latest_row(session, tenant, env, bot_id, owner_id, skill_id)
        if latest is None and allow_new:
            return
        if (latest is not None and latest.handoff_stage == "DELETED"
                and latest.incarnation_id != incarnation_id and allow_new):
            return
        if latest is None or latest.handoff_stage == "DELETED" or latest.incarnation_id != incarnation_id:
            raise AssetIncarnationMismatchError("security identity is no longer current")

    def get_observed_incarnation_id(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int
    ) -> str | None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            if not self._formal_matches(session, tenant, env, bot_id, owner_id, skill_id):
                return None
            latest = self._latest_row(session, tenant, env, bot_id, owner_id, skill_id)
            return latest.incarnation_id if latest is not None and latest.handoff_stage != "DELETED" else None

    def record_asset_deleted(
        self, *, env: str, bot_id: str, owner_id: str,
        skill_id: int, actor_id: str,
    ) -> LocalSkillSecurityIdentity | None:
        for attempt in range(3):
            try:
                return self._record_asset_deleted_once(
                    env=env, bot_id=bot_id, owner_id=owner_id,
                    skill_id=skill_id, actor_id=actor_id,
                )
            except IntegrityError as exc:
                if attempt == 2 or not _upload_order_conflict(exc):
                    raise
        raise AssertionError("unreachable upload-order retry state")

    def _record_asset_deleted_once(
        self, *, env: str, bot_id: str, owner_id: str,
        skill_id: int, actor_id: str,
    ) -> LocalSkillSecurityIdentity | None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            latest = self._latest_row(session, tenant, env, bot_id, owner_id, skill_id)
            if latest is None:
                return None
            if latest.handoff_stage == "DELETED":
                return _identity(latest)
            deleted = ObservationRow(
                observation_id=str(uuid4()),
                upload_order=int(latest.upload_order) + 1,
                provider_skill_id=latest.provider_skill_id,
                skill_id=skill_id, incarnation_id=latest.incarnation_id,
                owner_id=owner_id, bot_id=bot_id,
                creator_id=latest.creator_id, modifier_id=actor_id,
                name=latest.name, description=latest.description,
                sha256=latest.sha256, snapshot_ref=latest.snapshot_ref,
                handoff_stage="DELETED", observation_committed=1,
                snapshot_verified=latest.snapshot_verified,
                processing_status="SKIPPED",
                sync_status="PENDING",
                external_artifact_json=None,
                avernet_tenant=tenant, env=env,
            )
            session.add(deleted)
            session.flush()
            return _identity(deleted)

    def ensure_for_asset(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        creator_id: str | None, incarnation_id: str,
    ) -> LocalSkillSecurityIdentity:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            self._assert_incarnation(
                session, tenant=tenant, env=env, bot_id=bot_id,
                owner_id=owner_id, skill_id=skill_id,
                incarnation_id=incarnation_id,
            )
            row = self._latest_row(
                session, tenant, env, bot_id, owner_id, skill_id, incarnation_id
            )
            if row is None:
                raise ValueError("security handoff must be reserved before identity lookup")
            return _identity(row)

    def get_identity_for_asset(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        incarnation_id: str,
    ) -> LocalSkillSecurityIdentity | None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            row = self._latest_row(
                session, tenant, env, bot_id, owner_id, skill_id, incarnation_id
            )
            if row is None:
                return None
            if row.handoff_stage != "DELETED":
                current = self._latest_row(session, tenant, env, bot_id, owner_id, skill_id)
                if current is None or current.id != row.id or not self._formal_matches(
                    session, tenant, env, bot_id, owner_id, skill_id
                ):
                    return None
            return _identity(row)

    def reserve_handoff(
        self, *, env: str, bot_id: str, owner_id: str, actor_id: str,
        skill_id: int, observation_id: str, sha256: str, snapshot_ref: str,
        cancelled: Event, name: str, description: str,
        creator_id: str | None = None, incarnation_id: str,
    ) -> int:
        for attempt in range(3):
            try:
                return self._reserve_handoff_once(
                    env=env, bot_id=bot_id, owner_id=owner_id,
                    actor_id=actor_id, skill_id=skill_id,
                    observation_id=observation_id, sha256=sha256,
                    snapshot_ref=snapshot_ref, cancelled=cancelled,
                    name=name, description=description,
                    creator_id=creator_id, incarnation_id=incarnation_id,
                )
            except IntegrityError as exc:
                if attempt == 2 or not _upload_order_conflict(exc):
                    raise
        raise AssertionError("unreachable upload-order retry state")

    def _reserve_handoff_once(
        self, *, env: str, bot_id: str, owner_id: str, actor_id: str,
        skill_id: int, observation_id: str, sha256: str, snapshot_ref: str,
        cancelled: Event, name: str, description: str,
        creator_id: str | None = None, incarnation_id: str,
    ) -> int:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            if cancelled.is_set():
                raise ObservationHandoffCancelledError()
            self._assert_incarnation(
                session, tenant=tenant, env=env, bot_id=bot_id,
                owner_id=owner_id, skill_id=skill_id,
                incarnation_id=incarnation_id, allow_new=True,
            )
            # Device uploads use the Bot edit guard. Manifest deletion may
            # interleave, so the caller retries this table's unique-order race.
            previous_order = self._asset_rows(
                session, tenant, env, bot_id, owner_id, skill_id
            ).with_entities(func.max(ObservationRow.upload_order)).scalar()
            upload_order = int(previous_order or 0) + 1
            if creator_id is None:
                first_known_creator = self._asset_rows(
                    session, tenant, env, bot_id, owner_id, skill_id
                ).filter(
                    ObservationRow.incarnation_id == incarnation_id,
                    ObservationRow.creator_id.isnot(None),
                ).order_by(ObservationRow.upload_order).first()
                if first_known_creator is not None:
                    creator_id = first_known_creator.creator_id
            row = ObservationRow(
                observation_id=observation_id,
                upload_order=upload_order,
                provider_skill_id=f"tc:{incarnation_id}",
                skill_id=skill_id, incarnation_id=incarnation_id,
                owner_id=owner_id, bot_id=bot_id,
                creator_id=creator_id, modifier_id=actor_id,
                name=name, description=description,
                sha256=sha256, snapshot_ref=snapshot_ref,
                handoff_stage="PENDING", snapshot_verified=0,
                processing_status="PENDING", sync_status="NOT_READY",
                avernet_tenant=tenant, env=env,
            )
            session.add(row)
            session.flush()
            if cancelled.is_set():
                raise ObservationHandoffCancelledError()
            return upload_order

    def get_latest_handoff_for_asset(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        incarnation_id: str,
    ) -> LocalSkillSecurityHandoffAttempt | None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            if not self._formal_matches(
                session, tenant, env, bot_id, owner_id, skill_id
            ):
                return None
            row = self._latest_row(session, tenant, env, bot_id, owner_id, skill_id)
            if row is None or row.incarnation_id != incarnation_id or row.handoff_stage == "DELETED":
                return None
            return _handoff(row) if row is not None else None

    def get_handoff_failure(
        self, *, env: str, observation_id: str
    ) -> LocalSkillSecurityHandoffAttempt | None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            row = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id
            ).one_or_none()
            if row is None or row.handoff_stage in {"PENDING", "OBSERVED"}:
                return None
            return _handoff(row)

    def record_handoff_failure(
        self, *, env: str, bot_id: str, owner_id: str, actor_id: str,
        skill_id: int, observation_id: str, sha256: str, snapshot_ref: str,
        stage: str, snapshot_verified: bool, name: str | None = None,
        description: str | None = None, creator_id: str | None = None,
        upload_order: int | None = None, incarnation_id: str,
    ) -> None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            row = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id
            ).with_for_update().one_or_none()
            if row is None:
                # No durable reservation means no trustworthy ordering token.
                # A late timeout must not insert an older upload *after* a
                # newer one and thereby displace its current result.
                raise ValueError(
                    "security handoff row is absent; failure cannot be persisted"
                )
            if row.handoff_stage == "OBSERVED" and stage in {
                "SNAPSHOT_UNCERTAIN", "HANDOFF_TIMEOUT",
            }:
                return
            if (
                row.skill_id != skill_id or row.incarnation_id != incarnation_id
                or row.owner_id != owner_id or row.bot_id != bot_id
                or row.sha256 != sha256 or row.snapshot_ref != snapshot_ref
            ):
                raise ValueError("handoff failure ID was reused for another upload")
            if upload_order is not None and row.upload_order != upload_order:
                raise ValueError("handoff upload order differs from reserved row")
            if stage == "HANDOFF_TIMEOUT" and row.repair_claimed:
                return
            row.handoff_stage = stage
            row.snapshot_verified = int(bool(row.snapshot_verified) or snapshot_verified)
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            if creator_id is not None:
                row.creator_id = creator_id
            row.processing_status = "FAILED"
            row.error_code = stage
            row.sync_status = "FAILED"
            row.sync_error_code = stage

    def record_observation(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        observation_id: str, sha256: str, snapshot_ref: str,
        modifier_id: str, name: str,
        description: str, upload_order: int,
        cancelled: Event | None = None, incarnation_id: str,
    ) -> LocalSkillSecurityObservation:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            if cancelled is not None and cancelled.is_set():
                raise ObservationHandoffCancelledError()
            self._assert_incarnation(
                session, tenant=tenant, env=env, bot_id=bot_id,
                owner_id=owner_id, skill_id=skill_id,
                incarnation_id=incarnation_id,
            )
            row = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id
            ).with_for_update().one_or_none()
            if row is None:
                raise ValueError("observation requires a reserved handoff row")
            if row.handoff_stage == "HANDOFF_TIMEOUT" and not row.repair_claimed:
                raise ObservationHandoffCancelledError()
            if (
                row.skill_id != skill_id or row.incarnation_id != incarnation_id
                or row.upload_order != upload_order or row.sha256 != sha256
                or row.snapshot_ref != snapshot_ref
            ):
                raise ValueError("observation ID was reused for another upload")
            row.name = name
            row.description = description
            row.modifier_id = modifier_id
            row.handoff_stage = "OBSERVED"
            row.observation_committed = 1
            row.snapshot_verified = 1
            row.processing_status = "PENDING"
            row.error_code = None
            row.error_message = None
            row.sync_status = "PENDING"
            row.sync_error_code = None
            row.sync_error_message = None
            session.flush()
            if cancelled is not None and cancelled.is_set():
                raise ObservationHandoffCancelledError()
            return _observation(row)

    def get_current_for_asset(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        incarnation_id: str,
    ) -> LocalSkillSecurityObservation | None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            if not self._formal_matches(
                session, tenant, env, bot_id, owner_id, skill_id
            ):
                return None
            row = self._latest_row(session, tenant, env, bot_id, owner_id, skill_id)
            if (
                row is None
                or row.incarnation_id != incarnation_id
                or row.handoff_stage != "OBSERVED" or not row.observation_committed
            ):
                return None
            return _observation(row)

    def get_by_observation_id(
        self, *, env: str, observation_id: str
    ) -> LocalSkillSecurityObservation | None:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            row = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id,
                observation_committed=1,
            ).one_or_none()
            return _observation(row) if row is not None else None

    def list_for_asset(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        incarnation_id: str,
    ) -> tuple[LocalSkillSecurityObservation, ...]:
        _, items = self.page_for_asset(
            env=env, bot_id=bot_id, owner_id=owner_id, skill_id=skill_id,
            incarnation_id=incarnation_id, page=1, page_size=500,
        )
        return items

    def page_for_asset(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        page: int, page_size: int, incarnation_id: str,
    ) -> tuple[int, tuple[LocalSkillSecurityObservation, ...]]:
        if page < 1 or not 1 <= page_size <= 500:
            raise ValueError("invalid observation page")
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            query = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, bot_id=bot_id,
                owner_id=owner_id, skill_id=skill_id,
                incarnation_id=incarnation_id, observation_committed=1,
            )
            total = query.count()
            rows = query.order_by(
                ObservationRow.upload_order.desc(), ObservationRow.id.desc()
            ).offset((page - 1) * page_size).limit(page_size).all()
            return total, tuple(_observation(row) for row in rows)

    def _mutate(self, env: str, observation_id: str, values: dict[str, Any]) -> LocalSkillSecurityObservation:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            query = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id,
                handoff_stage="OBSERVED",
            )
            # Queue leases can overlap. The database predicate makes the first
            # terminal report win even when a former worker writes late.
            query.filter(ObservationRow.processing_status != "COMPLETED").update(
                {getattr(ObservationRow, key): value for key, value in values.items()},
                synchronize_session=False,
            )
            return _observation(query.one())

    def save_external_artifact(
        self, *, env: str, observation_id: str, artifact: dict[str, Any]
    ) -> LocalSkillSecurityObservation:
        return self._mutate(env, observation_id, {
            "external_artifact_json": json.dumps(artifact, ensure_ascii=False),
            "processing_status": "ARTIFACT_READY", "error_code": None,
            "error_message": None,
        })

    def claim_scan_submission(self, *, env: str, observation_id: str) -> bool:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            row = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id,
            ).one_or_none()
            if row is None or row.skill_id is None:
                return False
            latest = self._latest_row(
                session, tenant, env, row.bot_id, row.owner_id, int(row.skill_id),
            )
            if latest is None or latest.id != row.id:
                return False
            updated = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id,
                handoff_stage="OBSERVED", check_id=None,
            ).filter(ObservationRow.processing_status.in_(
                ("PENDING", "ARTIFACT_READY", "FAILED")
            )).filter(session.query(Skill.id).filter(
                Skill.id == ObservationRow.skill_id,
                Skill.avernet_tenant == ObservationRow.avernet_tenant,
                Skill.env == ObservationRow.env,
                Skill.bolt_id == ObservationRow.bot_id,
                Skill.user_id == ObservationRow.owner_id,
                Skill.git_path.like("local://%"),
            ).exists()).update({
                ObservationRow.processing_status: "SUBMITTING",
                ObservationRow.scan_started_at: _utc_now(),
                ObservationRow.error_code: None,
                ObservationRow.error_message: None,
            }, synchronize_session=False)
            return bool(updated)

    def save_check_submission(
        self, *, env: str, observation_id: str, check_id: str
    ) -> LocalSkillSecurityObservation:
        return self._mutate(env, observation_id, {
            "check_id": check_id, "processing_status": "CHECK_SUBMITTED"
        })

    def save_in_progress_report(
        self, *, env: str, observation_id: str, check_id: str,
        risk_status: str, report: dict[str, Any], detail_url: str | None,
    ) -> LocalSkillSecurityObservation:
        return self._mutate(env, observation_id, {
            "check_id": check_id, "risk_status": risk_status,
            "report_json": json.dumps(report, ensure_ascii=False),
            "detail_url": detail_url, "processing_status": "CHECK_SUBMITTED",
        })

    def save_report(
        self, *, env: str, observation_id: str, check_id: str,
        risk_status: str, report: dict[str, Any], detail_url: str | None,
    ) -> LocalSkillSecurityObservation:
        return self._mutate(env, observation_id, {
            "check_id": check_id, "risk_status": risk_status,
            "report_json": json.dumps(report, ensure_ascii=False),
            "detail_url": detail_url, "processing_status": "COMPLETED",
            "scan_completed_at": _utc_now(), "error_code": None,
            "error_message": None,
        })

    def mark_scan_error(
        self, *, env: str, observation_id: str, error_code: str,
        error_message: str,
    ) -> LocalSkillSecurityObservation:
        return self._mutate(env, observation_id, {
            "processing_status": "FAILED", "error_code": error_code,
            "error_message": error_message,
        })

    def mark_submit_unknown(
        self, *, env: str, observation_id: str, error_code: str,
        error_message: str,
    ) -> LocalSkillSecurityObservation:
        return self._mutate(env, observation_id, {
            "processing_status": "SUBMIT_UNKNOWN", "error_code": error_code,
            "error_message": error_message,
        })

    def _mark_sync(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        observation_id: str, incarnation_id: str, status: str,
        error_code: str | None = None, error_message: str | None = None,
    ) -> bool:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            latest = self._latest_row(
                session, tenant, env, bot_id, owner_id, skill_id, incarnation_id
            )
            if latest is None or latest.observation_id != observation_id:
                return False
            if latest.handoff_stage != "DELETED":
                current = self._latest_row(session, tenant, env, bot_id, owner_id, skill_id)
                if current is None or current.id != latest.id:
                    return False
            query = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, bot_id=bot_id,
                owner_id=owner_id, skill_id=skill_id,
                incarnation_id=incarnation_id, observation_id=observation_id,
            ).filter(ObservationRow.handoff_stage.in_(
                ("OBSERVED", "DELETED")
            ))
            if status == "FAILED":
                query = query.filter(ObservationRow.sync_status != "COMPLETED")
            updated = query.update({
                ObservationRow.sync_status: status,
                ObservationRow.sync_error_code: error_code,
                ObservationRow.sync_error_message: error_message,
            }, synchronize_session=False)
            return bool(updated)

    def mark_sync_completed(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        observation_id: str, incarnation_id: str,
    ) -> bool:
        return self._mark_sync(
            env=env, bot_id=bot_id, owner_id=owner_id, skill_id=skill_id,
            observation_id=observation_id, incarnation_id=incarnation_id,
            status="COMPLETED",
        )

    def mark_sync_error(
        self, *, env: str, bot_id: str, owner_id: str, skill_id: int,
        observation_id: str, error_code: str, error_message: str,
        incarnation_id: str,
    ) -> bool:
        return self._mark_sync(
            env=env, bot_id=bot_id, owner_id=owner_id, skill_id=skill_id,
            observation_id=observation_id, incarnation_id=incarnation_id,
            status="FAILED", error_code=error_code,
            error_message=error_message,
        )
