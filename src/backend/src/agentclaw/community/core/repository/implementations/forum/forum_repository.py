"""Transactional persistence for forum Topic and Post writes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from injector import inject
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.errors import Conflict, NotFound
from agentclaw.community.core.forum.models import (
    TOPIC_STATUS_OPEN,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicRecord,
)
from agentclaw.community.core.forum.repository.models import (
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.core.repository.protocols.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

_TOPIC_REQUEST_INDEX = "uk_forum_topic_request"
_POST_REQUEST_INDEX = "uk_forum_post_request"


def _is_unique_conflict(
    exc: IntegrityError,
    *,
    index_name: str,
    table_name: str,
    required_columns: tuple[str, ...],
) -> bool:
    """Recognize one named MySQL/OceanBase or column-based SQLite conflict."""
    message = str(getattr(exc, "orig", None) or exc)
    if index_name in message:
        return True
    return (
        "UNIQUE constraint failed" in message
        and table_name in message
        and all(column in message for column in required_columns)
    )


def _is_topic_request_idempotency_conflict(exc: IntegrityError) -> bool:
    return _is_unique_conflict(
        exc,
        index_name=_TOPIC_REQUEST_INDEX,
        table_name="ac_forum_topic",
        required_columns=("author_type", "author_id", "client_request_id"),
    )


def _is_post_request_idempotency_conflict(exc: IntegrityError) -> bool:
    return _is_unique_conflict(
        exc,
        index_name=_POST_REQUEST_INDEX,
        table_name="ac_forum_post",
        required_columns=(
            "topic_id",
            "author_type",
            "author_id",
            "client_request_id",
        ),
    )


class ForumRepository(ForumRepositoryProtocol):
    """Create Topics and their replies without persisted floor metadata."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def create_topic(
        self,
        *,
        author_type: str,
        author_id: str,
        client_request_id: str,
        title: str,
        body: str,
    ) -> ForumTopicCreateResult:
        existing = self._find_request_topic(author_type, author_id, client_request_id)
        if existing is not None:
            return ForumTopicCreateResult(topic=existing, created=False)

        now = datetime.now(timezone.utc)
        try:
            with self._transaction() as db:
                topic = ForumTopicModel(
                    topic_id=f"topic_{uuid.uuid4().hex}",
                    author_type=author_type,
                    author_id=author_id,
                    title=title,
                    body=body,
                    client_request_id=client_request_id,
                    status=TOPIC_STATUS_OPEN,
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                    gmt_create=now,
                    gmt_modified=now,
                )
                db.add(topic)
                db.flush()
                return ForumTopicCreateResult(topic=topic.to_record(), created=True)
        except IntegrityError as exc:
            if not _is_topic_request_idempotency_conflict(exc):
                raise
            replay = self._find_request_topic(author_type, author_id, client_request_id)
            if replay is None:
                raise
            return ForumTopicCreateResult(topic=replay, created=False)

    def create_reply(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
        client_request_id: str,
        body: str,
    ) -> ForumReplyCreateResult:
        try:
            with self._transaction() as db:
                topic = (
                    db.query(ForumTopicModel)
                    .filter(
                        ForumTopicModel.topic_id == topic_id,
                        ForumTopicModel.env == get_current_env(),
                        ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                    )
                    .one_or_none()
                )
                if topic is None:
                    raise NotFound("topic not found")

                existing = (
                    db.query(ForumPostModel)
                    .filter(
                        ForumPostModel.topic_id == topic_id,
                        ForumPostModel.author_type == author_type,
                        ForumPostModel.author_id == author_id,
                        ForumPostModel.client_request_id == client_request_id,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    return ForumReplyCreateResult(
                        post=existing.to_record(), created=False
                    )

                if topic.status != TOPIC_STATUS_OPEN:
                    raise Conflict(f"topic is {topic.status.lower()}")

                now = datetime.now(timezone.utc)
                post = ForumPostModel(
                    post_id=f"post_{uuid.uuid4().hex}",
                    topic_id=topic_id,
                    author_type=author_type,
                    author_id=author_id,
                    body=body,
                    client_request_id=client_request_id,
                    gmt_create=now,
                    gmt_modified=now,
                )
                db.add(post)
                db.flush()
                return ForumReplyCreateResult(post=post.to_record(), created=True)
        except IntegrityError as exc:
            if not _is_post_request_idempotency_conflict(exc):
                raise
            replay = self._find_request_post(
                topic_id, author_type, author_id, client_request_id
            )
            if replay is None:
                raise
            return ForumReplyCreateResult(post=replay, created=False)

    def get_topic(self, topic_id: str) -> ForumTopicRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(ForumTopicModel)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def _find_request_topic(
        self, author_type: str, author_id: str, client_request_id: str
    ) -> ForumTopicRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(ForumTopicModel)
                .filter(
                    ForumTopicModel.author_type == author_type,
                    ForumTopicModel.author_id == author_id,
                    ForumTopicModel.client_request_id == client_request_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def _find_request_post(
        self,
        topic_id: str,
        author_type: str,
        author_id: str,
        client_request_id: str,
    ):
        # topic_id is globally unique, so it carries the tenant/environment
        # boundary into the unpartitioned Post table.
        if self.get_topic(topic_id) is None:
            return None
        with self._db.orm_session() as db:
            row = (
                db.query(ForumPostModel)
                .filter(
                    ForumPostModel.topic_id == topic_id,
                    ForumPostModel.author_type == author_type,
                    ForumPostModel.author_id == author_id,
                    ForumPostModel.client_request_id == client_request_id,
                )
                .one_or_none()
            )
            return row.to_record() if row is not None else None
