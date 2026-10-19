"""Projection and bounded recovery queries for one-table security observations."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select

from agentclaw.community.core.models.local_skill_security_observation import (
    LocalSkillSecurityObservationModel as ObservationRow,
)
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.skill_center.local_skill_security_observation import (
    LocalSkillSecurityHandoffAttempt,
    LocalSkillSecurityIdentity,
    LocalSkillSecurityObservation,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _observation(row: ObservationRow) -> LocalSkillSecurityObservation:
    return LocalSkillSecurityObservation(
        observation_id=row.observation_id,
        upload_order=int(row.upload_order or 0),
        provider_skill_id=row.provider_skill_id,
        skill_id=row.skill_id,
        incarnation_id=row.incarnation_id,
        env=row.env,
        bot_id=row.bot_id,
        owner_id=row.owner_id,
        modifier_id=row.modifier_id,
        name=row.name or "",
        description=row.description or "",
        sha256=row.sha256,
        snapshot_ref=row.snapshot_ref,
        asset_deleted=row.handoff_stage == "DELETED",
        external_artifact=json.loads(row.external_artifact_json)
        if row.external_artifact_json else None,
        check_id=row.check_id,
        processing_status=row.processing_status,
        risk_status=row.risk_status,
        report=json.loads(row.report_json) if row.report_json else None,
        detail_url=row.detail_url,
        error_code=row.error_code,
        error_message=row.error_message,
        scan_started_at=row.scan_started_at,
        scan_completed_at=row.scan_completed_at,
        created_at=row.gmt_created,
    )


def _handoff(row: ObservationRow) -> LocalSkillSecurityHandoffAttempt:
    return LocalSkillSecurityHandoffAttempt(
        observation_id=row.observation_id,
        upload_order=int(row.upload_order or 0),
        order_known=row.upload_order is not None,
        skill_id=int(row.skill_id),
        incarnation_id=row.incarnation_id,
        owner_id=row.owner_id,
        bot_id=row.bot_id,
        actor_id=row.modifier_id,
        name=row.name,
        description=row.description,
        creator_id=row.creator_id,
        sha256=row.sha256,
        snapshot_ref=row.snapshot_ref,
        stage=row.handoff_stage,
        snapshot_verified=bool(row.snapshot_verified),
        env=row.env,
    )


def _identity(row: ObservationRow) -> LocalSkillSecurityIdentity:
    observed = row.handoff_stage in {"OBSERVED", "DELETED"}
    return LocalSkillSecurityIdentity(
        provider_skill_id=row.provider_skill_id,
        skill_id=row.skill_id,
        incarnation_id=row.incarnation_id,
        env=row.env,
        bot_id=row.bot_id,
        owner_id=row.owner_id,
        creator_id=row.creator_id,
        current_observation_id=row.observation_id if observed else None,
        sync_target_observation_id=row.observation_id if observed else None,
        sync_completed_observation_id=(
            row.observation_id if row.sync_status == "COMPLETED" else None
        ),
        sync_status=row.sync_status,
        sync_error_code=row.sync_error_code,
        sync_error_message=row.sync_error_message,
        created_at=row.gmt_created,
    )


def _live_local_asset_match(row: ObservationRow):
    skill = Skill.__table__.c
    return and_(
        skill.id == row.skill_id,
        skill.avernet_tenant == row.avernet_tenant,
        skill.env == row.env,
        skill.bolt_id == row.bot_id,
        skill.user_id == row.owner_id,
        skill.git_path.like("local://%"),
    )


class LocalSkillSecurityRecoveryQueries:
    """Background queries that share the observation repository's DB plugin."""

    _db: DatabasePlugin
    _PENDING_GRACE_SECONDS = 30

    def list_recoverable_scans(
        self, *, env: str, after_observation_id: str | None, limit: int
    ) -> tuple[LocalSkillSecurityObservation, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            query = (
                session.query(ObservationRow, Skill.id)
                .outerjoin(Skill, _live_local_asset_match(ObservationRow))
                .filter(
                    ObservationRow.avernet_tenant == tenant,
                    ObservationRow.env == env,
                    ObservationRow.handoff_stage == "OBSERVED",
                    or_(Skill.id.isnot(None), ObservationRow.check_id.isnot(None)),
                    or_(
                        ObservationRow.processing_status.in_(
                            ("PENDING", "ARTIFACT_READY", "CHECK_SUBMITTED", "SUBMITTING")
                        ),
                        and_(
                            ObservationRow.processing_status == "FAILED",
                            ObservationRow.error_code.in_(
                                ("SNAPSHOT_UNAVAILABLE", "ARTIFACT_EXPOSE_FAILED",
                                 "REPORT_POLL_FAILED", "TASK_ENQUEUE_FAILED")
                            ),
                        ),
                    ),
                )
            )
            cursor = after_observation_id
            result: list[LocalSkillSecurityObservation] = []
            latest_identity: dict[tuple[str, str, int], ObservationRow | None] = {}
            while len(result) < limit:
                page = query
                if cursor is not None:
                    page = page.filter(ObservationRow.observation_id > cursor)
                rows = page.order_by(ObservationRow.observation_id).limit(limit).all()
                if not rows:
                    break
                for row, live_skill_id in rows:
                    key = (row.bot_id, row.owner_id, int(row.skill_id))
                    if key not in latest_identity:
                        latest_identity[key] = self._latest_row(
                            session, tenant, env, row.bot_id, row.owner_id,
                            int(row.skill_id),
                        )
                    latest = latest_identity[key]
                    deleted_scan = row.check_id is not None and self._latest_row(
                        session, tenant, env, row.bot_id, row.owner_id,
                        int(row.skill_id), row.incarnation_id,
                    ).handoff_stage == "DELETED"
                    if (
                        (latest is not None and latest.incarnation_id == row.incarnation_id
                         and latest.provider_skill_id == row.provider_skill_id
                         and live_skill_id is not None)
                        or deleted_scan
                    ):
                        result.append(_observation(row))
                        if len(result) == limit:
                            break
                cursor = rows[-1][0].observation_id
                if len(rows) < limit:
                    break
            return tuple(result)

    def list_pending_sync_identities(
        self, *, env: str, after_provider_skill_id: str | None, limit: int
    ) -> tuple[LocalSkillSecurityIdentity, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            query = (
                session.query(ObservationRow)
                .outerjoin(Skill, _live_local_asset_match(ObservationRow))
                .filter(
                    ObservationRow.avernet_tenant == tenant,
                    ObservationRow.env == env,
                    or_(
                        ObservationRow.handoff_stage == "DELETED",
                        and_(
                            ObservationRow.handoff_stage == "OBSERVED",
                            Skill.id.isnot(None),
                        ),
                    ),
                    ObservationRow.sync_status.in_(("PENDING", "FAILED")),
                    or_(
                        ObservationRow.handoff_stage == "DELETED",
                        ObservationRow.external_artifact_json.isnot(None),
                    ),
                )
            )
            result: list[LocalSkillSecurityIdentity] = []
            cursor = after_provider_skill_id
            while len(result) < limit:
                page = query.with_entities(ObservationRow.provider_skill_id)
                if cursor is not None:
                    page = page.filter(ObservationRow.provider_skill_id > cursor)
                provider_ids = [
                    provider_id for (provider_id,) in page.distinct().order_by(
                        ObservationRow.provider_skill_id
                    ).limit(limit).all()
                ]
                if not provider_ids:
                    break
                for provider_id in provider_ids:
                    cursor = provider_id
                    row = query.filter(
                        ObservationRow.provider_skill_id == provider_id
                    ).order_by(ObservationRow.upload_order.desc()).first()
                    if row is None:
                        continue
                    latest = self._latest_row(
                        session, tenant, env, row.bot_id, row.owner_id,
                        int(row.skill_id), row.incarnation_id if row.handoff_stage == "DELETED" else None,
                    )
                    if latest is not None and latest.observation_id == row.observation_id:
                        result.append(_identity(row))
                        if len(result) == limit:
                            break
                if len(provider_ids) < limit:
                    break
            return tuple(result)

    def list_recovery_tenants(self, *, env: str) -> tuple[str, ...]:
        """Discover work across tenants before entering each worker scope."""
        observation = ObservationRow.__table__.c
        with self._db.transactional_orm_session() as session:
            tenants = {
                tenant
                for (tenant,) in session.execute(
                    select(observation.avernet_tenant)
                    .where(observation.env == env)
                    .distinct()
                    .execution_options(skip_avernet_tenant_guard=True)
                )
            }
        return tuple(sorted(tenants))

    def list_repairable_handoffs(
        self, *, env: str, after_observation_id: str | None, limit: int
    ) -> tuple[LocalSkillSecurityHandoffAttempt, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            db_now = session.execute(select(func.now())).scalar_one()
            cutoff = db_now - timedelta(seconds=self._PENDING_GRACE_SECONDS)
            query = session.query(ObservationRow).filter(
                ObservationRow.avernet_tenant == tenant,
                ObservationRow.env == env,
                ObservationRow.handoff_stage.notin_((
                    "OBSERVED", "DELETED", "ASSET_INCARNATION_STALE",
                    "REPAIR_SNAPSHOT_MISSING", "REPAIR_SNAPSHOT_MISMATCH",
                    "SNAPSHOT_DIGEST_MISMATCH",
                )),
                ObservationRow.gmt_created <= cutoff,
            )
            if after_observation_id is not None:
                query = query.filter(ObservationRow.observation_id > after_observation_id)
            rows = query.order_by(ObservationRow.observation_id).limit(limit).all()
            return tuple(_handoff(row) for row in rows)

    def reopen_verified_handoff(
        self, *, env: str, observation_id: str, verified_sha256: str
    ) -> bool:
        tenant = get_current_avernet_tenant()
        with self._db.transactional_orm_session() as session:
            updated = session.query(ObservationRow).filter_by(
                avernet_tenant=tenant, env=env, observation_id=observation_id,
                sha256=verified_sha256,
            ).filter(ObservationRow.handoff_stage.notin_(("OBSERVED", "DELETED"))).update(
                {ObservationRow.repair_claimed: 1,
                 ObservationRow.snapshot_verified: 1,
                 ObservationRow.handoff_stage: "PENDING"},
                synchronize_session=False,
            )
            return bool(updated)
