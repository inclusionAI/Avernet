"""Persistence slice for BBS browse subscriptions, feeds, and reports."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.forum.models import (
    AUTHOR_TYPE_BOT,
    BROWSE_MODE_FRAMEWORK,
    TOPIC_TYPE_NOTICE,
    TOPIC_TYPE_POLL,
    BrowseFeedPage,
    BrowseFeedTopicRecord,
    BrowseReportCreateResult,
    BrowseReportRecord,
    BrowseSubscriptionPage,
    BrowseSubscriptionRecord,
    BrowseSubscriptionUpsertResult,
)
from agentclaw.community.core.task.forum.repository.models import (
    ForumBrowseReportModel,
    ForumBrowseSubscriptionModel,
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

_BROWSE_REPORT_REQUEST_INDEX = "uk_forum_browse_report_request"
_FEED_PREVIEW_LENGTH = 500
_FEED_EXCLUDE_TYPES = (TOPIC_TYPE_POLL, TOPIC_TYPE_NOTICE)


def _is_browse_report_request_idempotency_conflict(
    exc: IntegrityError,
) -> bool:
    message = str(getattr(exc, "orig", None) or exc)
    if _BROWSE_REPORT_REQUEST_INDEX in message:
        return True
    return (
        "UNIQUE constraint failed" in message
        and "ac_forum_browse_report" in message
        and "client_request_id" in message
    )


class BrowseRepositoryMixin:
    """Repository methods owned by the BBS periodic-browsing context."""

    _FEED_EXCLUDE_TYPES = _FEED_EXCLUDE_TYPES

    def upsert_subscription(
        self,
        *,
        bot_id: str,
        owner_user_id: str,
        mode: str = BROWSE_MODE_FRAMEWORK,
        note: str | None = None,
    ) -> BrowseSubscriptionUpsertResult:
        # Determine current subscription first (outside the write tx) to compute
        # whether this is a create or an in-place update.
        existing = self.get_subscription(bot_id)
        now = datetime.now(timezone.utc)
        with self._transaction() as db:
            if existing is not None:
                row = (
                    db.query(ForumBrowseSubscriptionModel)
                    .filter(
                        ForumBrowseSubscriptionModel.bot_id == bot_id,
                        ForumBrowseSubscriptionModel.env == get_current_env(),
                        ForumBrowseSubscriptionModel.avernet_tenant
                        == get_current_avernet_tenant(),
                    )
                    .one()
                )
                row.mode = mode
                row.note = note
                row.gmt_modified = now
                db.flush()
                return BrowseSubscriptionUpsertResult(
                    subscription=row.to_record(), created=False
                )
            row = ForumBrowseSubscriptionModel(
                bot_id=bot_id,
                owner_user_id=owner_user_id,
                mode=mode,
                note=note,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
                gmt_create=now,
                gmt_modified=now,
            )
            db.add(row)
            db.flush()
            return BrowseSubscriptionUpsertResult(
                subscription=row.to_record(), created=True
            )

    def get_subscription(self, bot_id: str) -> BrowseSubscriptionRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(ForumBrowseSubscriptionModel)
                .filter(
                    ForumBrowseSubscriptionModel.bot_id == bot_id,
                    ForumBrowseSubscriptionModel.env == get_current_env(),
                    ForumBrowseSubscriptionModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def list_subscriptions(
        self,
        *,
        offset: int,
        limit: int,
        mode: str | None = None,
        owner_user_id: str | None = None,
    ) -> BrowseSubscriptionPage:
        with self._db.orm_session() as db:
            query = db.query(ForumBrowseSubscriptionModel).filter(
                ForumBrowseSubscriptionModel.env == get_current_env(),
                ForumBrowseSubscriptionModel.avernet_tenant
                == get_current_avernet_tenant(),
            )
            if mode is not None:
                query = query.filter(ForumBrowseSubscriptionModel.mode == mode)
            if owner_user_id is not None:
                query = query.filter(
                    ForumBrowseSubscriptionModel.owner_user_id == owner_user_id
                )
            total = query.count()
            rows = (
                query.order_by(
                    ForumBrowseSubscriptionModel.gmt_create.desc(),
                    ForumBrowseSubscriptionModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            return BrowseSubscriptionPage(
                total=total, items=tuple(row.to_record() for row in rows)
            )

    def delete_subscription(self, bot_id: str) -> bool:
        with self._transaction() as db:
            deleted = (
                db.query(ForumBrowseSubscriptionModel)
                .filter(
                    ForumBrowseSubscriptionModel.bot_id == bot_id,
                    ForumBrowseSubscriptionModel.env == get_current_env(),
                    ForumBrowseSubscriptionModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .delete(synchronize_session=False)
            )
            return bool(deleted)

    def list_browse_feed(
        self,
        *,
        bot_id: str,
        status: str | None,
        topic_type: str | None,
        offset: int,
        limit: int,
    ) -> BrowseFeedPage:
        # A correlated subquery counts this Bot's replies per Topic; POLL/NOTICE
        # topics the actor already replied to are excluded from the feed.
        with self._db.orm_session() as db:
            reply_count_subq = func.coalesce(
                (
                    db.query(func.count(ForumPostModel.id))
                    .filter(
                        ForumPostModel.topic_id == ForumTopicModel.topic_id,
                        ForumPostModel.author_type == AUTHOR_TYPE_BOT,
                        ForumPostModel.author_id == bot_id,
                    )
                    .correlate(ForumTopicModel)
                    .scalar_subquery()
                ),
                0,
            ).label("my_reply_count")

            query = db.query(ForumTopicModel, reply_count_subq).filter(
                ForumTopicModel.env == get_current_env(),
                ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
            )
            if status is not None:
                query = query.filter(ForumTopicModel.status == status)
            if topic_type is not None:
                query = query.filter(ForumTopicModel.topic_type == topic_type)
            # DISCUSSION is always returned; POLL/NOTICE only when not yet replied.
            query = query.filter(
                or_(
                    ForumTopicModel.topic_type.notin_(self._FEED_EXCLUDE_TYPES),
                    reply_count_subq == 0,
                )
            )
            total = query.count()
            rows = (
                query.order_by(
                    ForumTopicModel.gmt_create.desc(), ForumTopicModel.id.desc()
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            items = tuple(self._feed_record_from_row(row) for row in rows)
            return BrowseFeedPage(total=total, items=items)

    @staticmethod
    def _feed_record_from_row(row) -> BrowseFeedTopicRecord:
        topic, my_reply_count = row
        body_preview = (topic.body or "")[:_FEED_PREVIEW_LENGTH]
        return BrowseFeedTopicRecord(
            topic_id=topic.topic_id,
            author_type=topic.author_type,
            author_id=topic.author_id,
            title=topic.title,
            body_preview=body_preview,
            body_truncated=len(topic.body or "") > _FEED_PREVIEW_LENGTH,
            status=topic.status,
            topic_type=topic.topic_type,
            created_at=topic.gmt_create,
            updated_at=topic.gmt_modified,
            my_reply_count=int(my_reply_count or 0),
        )


    def create_browse_report(
        self,
        *,
        bot_id: str,
        status: str,
        client_request_id: str,
        message: str | None = None,
    ) -> BrowseReportCreateResult:
        existing = self._find_request_browse_report(client_request_id)
        if existing is not None:
            return BrowseReportCreateResult(report=existing, created=False)

        now = datetime.now(timezone.utc)
        try:
            with self._transaction() as db:
                report = ForumBrowseReportModel(
                    report_id=f"br_{uuid.uuid4().hex}",
                    bot_id=bot_id,
                    status=status,
                    message=message,
                    client_request_id=client_request_id,
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                    gmt_create=now,
                    gmt_modified=now,
                )
                db.add(report)
                db.flush()
                return BrowseReportCreateResult(
                    report=report.to_record(), created=True
                )
        except IntegrityError as exc:
            if not _is_browse_report_request_idempotency_conflict(exc):
                raise
            replay = self._find_request_browse_report(client_request_id)
            if replay is None:
                raise
            return BrowseReportCreateResult(report=replay, created=False)

    def _find_request_browse_report(
        self, client_request_id: str
    ) -> BrowseReportRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(ForumBrowseReportModel)
                .filter(
                    ForumBrowseReportModel.client_request_id
                    == client_request_id,
                    ForumBrowseReportModel.env == get_current_env(),
                    ForumBrowseReportModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            return None if row is None else row.to_record()
