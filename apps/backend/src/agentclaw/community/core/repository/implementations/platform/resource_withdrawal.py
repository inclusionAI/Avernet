"""Dialect-neutral transactional outbox with optimistic claims and lease fencing."""

from datetime import timedelta
from uuid import uuid4

from injector import inject
from sqlalchemy import and_, func, or_, select, update

from agentclaw.community.core.repository.protocols.platform import (
    ResourceWithdrawalRepositoryProtocol,
)
from agentclaw.community.core.session_resources.withdrawal_models import (
    ResourceWithdrawalModel as Row,
)
from agentclaw.community.core.session_resources.withdrawal_types import (
    ResourceWithdrawal,
)
from agentclaw.community.plugin_api.database import DatabasePlugin


class ResourceWithdrawalRepository(ResourceWithdrawalRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get(self, event_id: str) -> ResourceWithdrawal | None:
        with self._db.orm_session() as session:
            row = session.get(Row, event_id)
            return row.to_record() if row else None

    def claim(self, *, tenant: str, lease_seconds: int) -> ResourceWithdrawal | None:
        with self._db.transactional_orm_session() as session:
            now = session.scalar(select(func.now()))
            eligible = and_(
                Row.tenant == tenant,
                or_(
                    and_(Row.status == "pending", Row.available_at <= now),
                    and_(Row.status == "processing", Row.lease_until <= now),
                ),
            )
            # Read only the key, never a stale ORM instance. Recheck eligibility in
            # the UPDATE: concurrent claimers cannot both win, on any dialect.
            key = session.scalar(
                select(Row.event_id)
                .where(eligible)
                .order_by(Row.created_at, Row.event_id)
                .limit(1)
            )
            if key is None:
                return None
            changed = session.execute(
                update(Row)
                .where(Row.event_id == key, eligible)
                .values(
                    status="processing",
                    lease_token=uuid4().hex,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    attempts=Row.attempts + 1,
                    retry_count=Row.retry_count + 1,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                return None
            return session.get(Row, key).to_record()

    def finish(
        self,
        record: ResourceWithdrawal,
        *,
        status: str,
        delay_seconds: int,
        error_code: str,
    ) -> bool:
        if status not in {"pending", "accepted", "blocked"}:
            raise ValueError("withdrawal_invalid_status")
        with self._db.transactional_orm_session() as session:
            now = session.scalar(select(func.now()))
            changed = session.execute(
                update(Row)
                .where(
                    Row.event_id == record.event_id,
                    Row.tenant == record.tenant,
                    Row.status == "processing",
                    Row.lease_token == record.lease_token,
                    Row.lease_until > now,
                )
                .values(
                    status=status,
                    lease_token=None,
                    lease_until=None,
                    available_at=now + timedelta(seconds=delay_seconds),
                    updated_at=now,
                    accepted_at=now if status == "accepted" else None,
                    last_error=error_code,
                )
            )
            return changed.rowcount == 1

    def replay(
        self,
        *,
        event_id: str,
        tenant: str,
        expected_attempts: int,
        actor: str,
        reason: str,
    ) -> bool:
        for value, limit in ((actor, 128), (reason, 512)):
            if (
                not value.strip()
                or len(value) > limit
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("withdrawal_replay_audit_required")
        with self._db.transactional_orm_session() as session:
            changed = session.execute(
                update(Row)
                .where(
                    Row.event_id == event_id,
                    Row.tenant == tenant,
                    Row.status == "blocked",
                    Row.attempts == expected_attempts,
                )
                .values(
                    status="pending",
                    available_at=func.now(),
                    updated_at=func.now(),
                    retry_count=0,
                    replay_actor=actor,
                    replay_reason=reason,
                )
            )
            return changed.rowcount == 1

    def stats(self, *, tenant: str) -> dict:
        with self._db.orm_session() as session:
            counts = dict(
                session.execute(
                    select(Row.status, func.count())
                    .where(Row.tenant == tenant)
                    .group_by(Row.status)
                ).all()
            )
            oldest = session.scalar(
                select(func.min(Row.created_at)).where(
                    Row.tenant == tenant, Row.status != "accepted"
                )
            )
            return {
                **{
                    s: counts.get(s, 0)
                    for s in ("pending", "processing", "blocked", "accepted")
                },
                "oldest_unaccepted_at": oldest.isoformat() if oldest else None,
            }
