"""Persistence for general user feedback."""

from __future__ import annotations

from injector import inject
from sqlalchemy import desc

from agentclaw.community.core.feedback.models import (
    FeedbackCreateResult,
    FeedbackPage,
)
from agentclaw.community.core.feedback.repository.models import FeedbackModel
from agentclaw.community.core.repository.protocols.feedback import (
    FeedbackRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class FeedbackRepository(FeedbackRepositoryProtocol):
    """Persist feedback rows scoped to the current tenant and environment."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def create_feedback(
        self,
        *,
        reporter_id: str,
        module: str,
        content: str,
    ) -> FeedbackCreateResult:
        with self._transaction() as db:
            record = FeedbackModel(
                reporter_id=reporter_id,
                module=module,
                content=content,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(record)
            db.flush()
            return FeedbackCreateResult(feedback=record.to_record(), created=True)

    def list_feedback(
        self,
        *,
        module: str | None,
        reporter_id: str | None,
        offset: int,
        limit: int,
    ) -> FeedbackPage:
        with self._db.orm_session() as db:
            query = db.query(FeedbackModel).filter(
                FeedbackModel.env == get_current_env(),
                FeedbackModel.avernet_tenant == get_current_avernet_tenant(),
            )
            if module is not None:
                query = query.filter(FeedbackModel.module == module)
            if reporter_id is not None:
                query = query.filter(FeedbackModel.reporter_id == reporter_id)

            total = query.count()
            rows = (
                query.order_by(
                    desc(FeedbackModel.gmt_create),
                    desc(FeedbackModel.id),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return FeedbackPage(
                total=total, items=tuple(row.to_record() for row in rows)
            )
