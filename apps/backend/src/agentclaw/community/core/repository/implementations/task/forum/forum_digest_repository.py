"""Persistence for batch-level BBS digest reports."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.errors import Conflict
from agentclaw.community.core.repository.protocols.task.forum_digest import (
    ForumDigestReportRepositoryProtocol,
)
from agentclaw.community.core.task.forum.digest.models import (
    DIGEST_NOTIFICATION_STATUS_SUBMITTED,
    DIGEST_REPORT_STATUS_CONFIRMED,
    DIGEST_REPORT_STATUS_FAILED,
    DIGEST_REPORT_STATUS_PENDING_CONFIRMATION,
    DIGEST_REPORT_STATUS_REJECTED,
    ForumDigestReportBrief,
    ForumDigestReportCreateResult,
    ForumDigestReportDecisionResult,
    ForumDigestReportItem,
    ForumDigestReportRecord,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumDigestReportModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

_DIGEST_REPORT_REQUEST_INDEX = "uk_forum_digest_report_request"


def _is_digest_request_idempotency_conflict(exc: IntegrityError) -> bool:
    """Recognize the named MySQL/OceanBase or column-based SQLite conflict."""
    message = str(getattr(exc, "orig", None) or exc)
    if _DIGEST_REPORT_REQUEST_INDEX in message:
        return True
    return (
        "UNIQUE constraint failed" in message
        and "ac_forum_digest_report" in message
        and all(
            column in message
            for column in (
                "avernet_tenant",
                "env",
                "bot_id",
                "client_request_id",
            )
        )
    )


class ForumDigestReportRepository(ForumDigestReportRepositoryProtocol):
    """Store an entire confirmation batch as one row and one JSON projection."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def create_report(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        client_request_id: str,
        brief: ForumDigestReportBrief,
        items: tuple[ForumDigestReportItem, ...],
    ) -> ForumDigestReportCreateResult:
        existing = self._find_request_report(bot_id, client_request_id)
        if existing is not None:
            return ForumDigestReportCreateResult(digest=existing, created=False)

        now = datetime.now(timezone.utc)
        try:
            with self._transaction() as db:
                report = ForumDigestReportModel(
                    digest_id=f"bbsd_{uuid.uuid4().hex}",
                    bot_id=bot_id,
                    owner_user_id=owner_user_id,
                    client_request_id=client_request_id,
                    brief_json={
                        "summary": brief.summary,
                        "highlights": list(brief.highlights),
                    },
                    items_json=[
                        {**asdict(item), "tags": list(item.tags)}
                        for item in items
                    ],
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                    gmt_create=now,
                    gmt_modified=now,
                )
                db.add(report)
                db.flush()
                return ForumDigestReportCreateResult(
                    digest=report.to_record(), created=True
                )
        except IntegrityError as exc:
            if not _is_digest_request_idempotency_conflict(exc):
                raise
            replay = self._find_request_report(bot_id, client_request_id)
            if replay is None:
                raise
            return ForumDigestReportCreateResult(digest=replay, created=False)

    def get_report(self, digest_id: str) -> ForumDigestReportRecord | None:
        with self._db.orm_session() as db:
            row = self._find_row(db, digest_id)
            return None if row is None else row.to_record()

    def get_report_by_request(
        self, bot_id: str, client_request_id: str
    ) -> ForumDigestReportRecord | None:
        return self._find_request_report(bot_id, client_request_id)

    def update_delivery(
        self,
        *,
        digest_id: str,
        session_id: str,
        session_url: str,
        notification_status: str,
    ) -> ForumDigestReportRecord | None:
        with self._transaction() as db:
            row = self._find_row(db, digest_id)
            if row is None:
                return None
            if row.status != DIGEST_REPORT_STATUS_PENDING_CONFIRMATION:
                return row.to_record()
            row.session_id = session_id
            row.session_url = session_url
            row.notification_status = notification_status
            row.status = DIGEST_REPORT_STATUS_PENDING_CONFIRMATION
            db.flush()
            return row.to_record()

    def mark_failed(self, digest_id: str) -> ForumDigestReportRecord | None:
        with self._transaction() as db:
            row = self._find_row(db, digest_id)
            if row is None:
                return None
            if row.status == DIGEST_REPORT_STATUS_PENDING_CONFIRMATION:
                row.status = DIGEST_REPORT_STATUS_FAILED
                db.flush()
            return row.to_record()

    def apply_decision(
        self,
        *,
        digest_id: str,
        decision: str,
        decision_client_request_id: str,
    ) -> ForumDigestReportDecisionResult:
        if decision not in {DIGEST_REPORT_STATUS_CONFIRMED, DIGEST_REPORT_STATUS_REJECTED}:
            raise Conflict(f"unsupported digest decision: {decision}")

        with self._transaction() as db:
            row = self._find_row(db, digest_id, lock=True)
            if row is None:
                raise Conflict("digest report not found")
            if row.status == decision:
                return ForumDigestReportDecisionResult(
                    digest=row.to_record(), changed=False
                )
            if row.status != DIGEST_REPORT_STATUS_PENDING_CONFIRMATION:
                raise Conflict(f"digest report is already {row.status.lower()}")

            now = datetime.now(timezone.utc)
            row.status = decision
            row.decision_client_request_id = decision_client_request_id
            row.decided_at = now
            row.gmt_modified = now
            row.notification_status = (
                row.notification_status or DIGEST_NOTIFICATION_STATUS_SUBMITTED
            )
            db.flush()
            return ForumDigestReportDecisionResult(
                digest=row.to_record(), changed=True
            )

    @staticmethod
    def _find_row(db, digest_id: str, *, lock: bool = False):
        query = db.query(ForumDigestReportModel).filter(
            ForumDigestReportModel.digest_id == digest_id,
            ForumDigestReportModel.env == get_current_env(),
            ForumDigestReportModel.avernet_tenant
            == get_current_avernet_tenant(),
        )
        if lock:
            query = query.with_for_update()
        return query.one_or_none()

    def _find_request_report(
        self, bot_id: str, client_request_id: str
    ) -> ForumDigestReportRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(ForumDigestReportModel)
                .filter(
                    ForumDigestReportModel.bot_id == bot_id,
                    ForumDigestReportModel.client_request_id
                    == client_request_id,
                    ForumDigestReportModel.env == get_current_env(),
                    ForumDigestReportModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            return None if row is None else row.to_record()


__all__ = ["ForumDigestReportRepository"]
