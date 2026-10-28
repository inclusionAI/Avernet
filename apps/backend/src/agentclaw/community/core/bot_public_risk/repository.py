"""Transactional persistence for public risk applications.

Current eligibility is an explicit unique key, not an ordering inferred from IDs.
No external call is made while a transaction is held.
"""

from dataclasses import fields
from datetime import datetime
from hashlib import sha256
import json

from injector import inject
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Index,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    false,
    or_,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_public_risk.repository_protocol import RiskCheck, RequestConflict
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.avernet_tenant_guard import register_avernet_tenant_guard


class RiskCheckModel(Base):
    __tablename__ = "ac_bot_public_risk_check"
    risk_check_id = Column(String(128), primary_key=True)
    avernet_tenant = Column(String(64), nullable=False, server_default="teamclaw")
    env = Column(String(20), nullable=False)
    bot_pk = Column(BigInteger, nullable=False)
    owner_id = Column(String(128), nullable=False)
    operator_id = Column(String(128), nullable=False)
    client_scope = Column(String(128), nullable=False)
    client_request_id = Column(String(128), nullable=False)
    public_scope = Column(String(32), nullable=False)
    target_json = Column(Text, nullable=False)
    request_hash = Column(String(64), nullable=False)
    input_json = Column(Text, nullable=False)
    input_hash = Column(String(64), nullable=False)
    phase = Column(String(32), nullable=False)
    security_status = Column(String(32))
    auto_poll_paused = Column(Boolean, nullable=False, server_default=false())
    next_action_at = Column(TIMESTAMP)
    auto_poll_until = Column(TIMESTAMP)
    check_id = Column(String(128))
    approval_biz_id = Column(String(128), nullable=False)
    approval_puid = Column(String(128))
    detail_url = Column(String(2048))
    notification_due_at = Column(TIMESTAMP)
    workflow_json = Column(Text, nullable=False)
    current_target_key = Column(String(64))
    row_version = Column(BigInteger, nullable=False, default=1)
    gmt_create = Column(
        TIMESTAMP, nullable=False, server_default=func.current_timestamp()
    )
    gmt_modified = Column(
        TIMESTAMP,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
    __table_args__ = (
        UniqueConstraint(
            "avernet_tenant",
            "env",
            "client_scope",
            "client_request_id",
            name="uk_risk_request",
        ),
        UniqueConstraint(
            "avernet_tenant", "env", "current_target_key", name="uk_risk_current"
        ),
        Index("idx_risk_notice_due", "env", "notification_due_at", "avernet_tenant"),
        Index(
            "idx_risk_recovery",
            "env",
            "phase",
            "auto_poll_paused",
            "next_action_at",
            "avernet_tenant",
        ),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"},
    )


register_avernet_tenant_guard(RiskCheckModel)


def _view(row: RiskCheckModel) -> RiskCheck:
    return RiskCheck(**{f.name: getattr(row, f.name) for f in fields(RiskCheck)})


def _target_key(bot_pk: int, public_scope: str) -> str:
    return sha256(f"{bot_pk}:{public_scope}".encode()).hexdigest()


class BotPublicRiskRepository:
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create_or_replay(self, record: RiskCheck) -> tuple[RiskCheck, bool]:
        # Concurrent new requests can race for an initially vacant target. The
        # losing transaction rolls back completely and retries against the winner.
        for attempt in range(3):
            try:
                return self._create_or_replay(record)
            except IntegrityError as exc:
                detail = str(exc.orig).lower()
                is_race = (
                    "uk_risk_request" in detail
                    or "uk_risk_current" in detail
                    or (
                        "unique constraint failed" in detail
                        and (
                            "client_request_id" in detail
                            or "current_target_key" in detail
                        )
                    )
                )
                if attempt == 2 or not is_race:
                    raise
        raise AssertionError("unreachable")

    def _create_or_replay(self, record: RiskCheck) -> tuple[RiskCheck, bool]:
        tenant = get_current_avernet_tenant()
        key = _target_key(record.bot_pk, record.public_scope)
        with self._db.transactional_orm_session() as session:
            previous_request = (
                session.query(RiskCheckModel)
                .filter_by(
                    avernet_tenant=tenant,
                    env=record.env,
                    client_scope=record.client_scope,
                    client_request_id=record.client_request_id,
                )
                .one_or_none()
            )
            if previous_request is not None:
                if (
                    previous_request.bot_pk != record.bot_pk
                    or previous_request.public_scope != record.public_scope
                    or previous_request.request_hash != record.request_hash
                ):
                    raise RequestConflict("request ID reused with different intent")
                return _view(previous_request), False
            previous = session.query(RiskCheckModel).filter_by(
                avernet_tenant=tenant,
                env=record.env,
                current_target_key=key,
            ).with_for_update().one_or_none()
            if previous is not None:
                workflow = json.loads(previous.workflow_json)
                workflow["invalidation_reason"] = "REPLACED"
                previous.workflow_json = json.dumps(workflow)
                previous.current_target_key = None
                previous.row_version += 1
                previous.next_action_at = None
                if previous.phase != "PUBLISHED":
                    previous.phase = "CLOSED"
                session.flush()
            values = {
                f.name: getattr(record, f.name)
                for f in fields(RiskCheck)
                if f.name
                not in {
                    "current_target_key",
                    "gmt_create",
                    "gmt_modified",
                    "row_version",
                }
            }
            row = RiskCheckModel(
                **values, avernet_tenant=tenant, current_target_key=key, row_version=1
            )
            session.add(row)
            session.flush()
            return _view(row), True

    def get_by_request(
        self, *, env: str, client_scope: str, client_request_id: str
    ) -> RiskCheck | None:
        """Look up an idempotent request within the current tenant and environment."""
        with self._db.orm_session() as session:
            row = (
                session.query(RiskCheckModel)
                .filter_by(env=env, client_scope=client_scope, client_request_id=client_request_id)
                .one_or_none()
            )
            return _view(row) if row else None

    def get(self, *, env: str, risk_check_id: str) -> RiskCheck | None:
        with self._db.orm_session() as session:
            row = (
                session.query(RiskCheckModel)
                .filter_by(env=env, risk_check_id=risk_check_id)
                .one_or_none()
            )
            return _view(row) if row else None

    def get_current(
        self, *, env: str, bot_pk: int, public_scope: str
    ) -> RiskCheck | None:
        with self._db.orm_session() as session:
            row = (
                session.query(RiskCheckModel)
                .filter_by(
                    env=env, current_target_key=_target_key(bot_pk, public_scope)
                )
                .one_or_none()
            )
            return _view(row) if row else None

    def find_approval(self, *, env: str, puid: str) -> RiskCheck | None:
        """Resolve a callback using saved PUID or the stable botpublic business ID.

        The latter applies only to the existing enterprise PUID shape; callers
        must not enable risk publication until that shape is confirmed end to end.
        Historical records are included so a superseded approval is not treated
        as an unrelated legacy callback.
        """
        if not puid:
            raise ValueError("approval PUID is required")
        prefix = "antprocess-agentclaw_botpublic_"
        biz_id = puid[len(prefix):].split("_", 1)[0] if puid.startswith(prefix) else ""
        with self._db.orm_session() as session:
            query = session.query(RiskCheckModel).filter_by(
                env=env, avernet_tenant=get_current_avernet_tenant()
            )
            if biz_id:
                query = query.filter(or_(RiskCheckModel.approval_puid == puid,
                                         RiskCheckModel.approval_biz_id == biz_id))
            else:
                query = query.filter(RiskCheckModel.approval_puid == puid)
            rows = query.limit(2).all()
            if len(rows) > 1:
                raise ValueError("approval PUID has ambiguous risk associations")
            return _view(rows[0]) if rows else None

    def update_current(
        self, *, env: str, risk_check_id: str, row_version: int, changes: dict
    ) -> bool:
        allowed = {
            "phase",
            "security_status",
            "auto_poll_paused",
            "next_action_at",
            "auto_poll_until",
            "check_id",
            "approval_puid",
            "detail_url",
            "notification_due_at",
            "workflow_json",
            "input_json",
            "input_hash",
        }
        if not changes or not set(changes) <= allowed:
            raise ValueError("invalid risk update fields")
        with self._db.transactional_orm_session() as session:
            count = (
                session.query(RiskCheckModel)
                .filter_by(
                    env=env,
                    risk_check_id=risk_check_id,
                    row_version=row_version,
                )
                .filter(RiskCheckModel.current_target_key.isnot(None))
                .update(
                    {**changes, "row_version": RiskCheckModel.row_version + 1},
                    synchronize_session=False,
                )
            )
            return count == 1

    def invalidate_current(self, *, env: str, bot_pk: int, public_scope: str) -> bool:
        """Withdraw current eligibility without modifying historical outcome."""
        row = self.get_current(env=env, bot_pk=bot_pk, public_scope=public_scope)
        if row is None:
            return False
        return self.invalidate_if_current(env=env, risk_check_id=row.risk_check_id, row_version=row.row_version)

    def invalidate_if_current(
        self, *, env: str, risk_check_id: str, row_version: int
    ) -> bool:
        """Invalidate the observed current record only, never a newer request."""
        with self._db.transactional_orm_session() as session:
            row = (
                session.query(RiskCheckModel)
                .filter_by(env=env, risk_check_id=risk_check_id, row_version=row_version)
                .filter(RiskCheckModel.current_target_key.isnot(None))
                .one_or_none()
            )
            if row is None:
                return False
            workflow = json.loads(row.workflow_json)
            workflow["invalidation_reason"] = "WITHDRAWN"
            count = (
                session.query(RiskCheckModel)
                .filter_by(
                    env=env, risk_check_id=risk_check_id, row_version=row_version
                )
                .filter(RiskCheckModel.current_target_key.isnot(None))
                .update(
                    {
                        RiskCheckModel.current_target_key: None,
                        RiskCheckModel.row_version: RiskCheckModel.row_version + 1,
                        RiskCheckModel.workflow_json: json.dumps(workflow),
                        RiskCheckModel.next_action_at: None,
                        RiskCheckModel.phase: "PUBLISHED" if row.phase == "PUBLISHED" else "CLOSED",
                    },
                    synchronize_session=False,
                )
            )
            return count == 1

    def list_due(
        self, *, env: str, now: datetime, after_id: str = "", limit: int = 100
    ) -> tuple[RiskCheck, ...]:
        """Page current due work within the caller's tenant; paused work stays paused."""
        if not 1 <= limit <= 500:
            raise ValueError("invalid page size")
        with self._db.orm_session() as session:
            rows = (
                session.query(RiskCheckModel)
                .filter(
                    RiskCheckModel.env == env,
                    RiskCheckModel.risk_check_id > after_id,
                    RiskCheckModel.current_target_key.isnot(None),
                    RiskCheckModel.auto_poll_paused.is_(False),
                    RiskCheckModel.next_action_at.isnot(None),
                    RiskCheckModel.next_action_at <= now,
                )
                .order_by(RiskCheckModel.risk_check_id)
                .limit(limit)
                .all()
            )
            return tuple(_view(row) for row in rows)

    def update_notice(
        self,
        *,
        env: str,
        risk_check_id: str,
        row_version: int,
        workflow_json: str,
        notification_due_at: datetime | None,
    ) -> bool:
        """CAS notice state even after a record is no longer current."""
        with self._db.transactional_orm_session() as session:
            count = (
                session.query(RiskCheckModel)
                .filter_by(
                    env=env,
                    risk_check_id=risk_check_id,
                    row_version=row_version,
                )
                .update(
                    {
                        RiskCheckModel.workflow_json: workflow_json,
                        RiskCheckModel.notification_due_at: notification_due_at,
                        RiskCheckModel.row_version: RiskCheckModel.row_version + 1,
                    },
                    synchronize_session=False,
                )
            )
            return count == 1

    def list_notice_due(
        self, *, env: str, now: datetime, after_id: str = "", limit: int = 100
    ) -> tuple[RiskCheck, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("invalid page size")
        with self._db.orm_session() as session:
            rows = (
                session.query(RiskCheckModel)
                .filter(
                    RiskCheckModel.env == env,
                    RiskCheckModel.risk_check_id > after_id,
                    RiskCheckModel.notification_due_at.isnot(None),
                    RiskCheckModel.notification_due_at <= now,
                )
                .order_by(RiskCheckModel.risk_check_id)
                .limit(limit)
                .all()
            )
            return tuple(_view(row) for row in rows)

    def list_recovery_tenants(self, *, env: str) -> tuple[str, ...]:
        """Discover tenant identifiers only; each scan then enters that tenant's scope."""
        columns = RiskCheckModel.__table__.c
        with self._db.orm_session() as session:
            result = session.execute(
                select(columns.avernet_tenant)
                .where(columns.env == env)
                .distinct()
                .execution_options(skip_avernet_tenant_guard=True)
            )
            return tuple(sorted(row[0] for row in result))
