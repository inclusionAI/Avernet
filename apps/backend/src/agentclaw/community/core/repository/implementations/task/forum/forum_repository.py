"""Persistence for forum Topic and Post reads and writes."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

from injector import inject
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.errors import Conflict, NotFound
from agentclaw.community.core.task.forum.models import (
    TOPIC_STATUS_LOCKED,
    TOPIC_STATUS_OPEN,
    TOPIC_TYPE_DISCUSSION,
    TOPIC_TYPE_NOTICE,
    TOPIC_TYPE_POLL,
    ForumPollConfig,
    ForumPollResultOptionRecord,
    ForumPollResultRecord,
    ForumPollSelection,
    ForumPollProjectionCalculator,
    ForumPollProjectionInput,
    ForumPollVoteRecord,
    ForumTopicAnalysisRecord,
    ForumPostPage,
    ForumPostRecord,
    ForumReplyCreateResult,
    ForumTopicConfig,
    ForumTopicCreateResult,
    ForumTopicPage,
    ForumTopicRecord,
    ForumTopicViewerRecord,
)
from agentclaw.community.core.task.forum.repository.models import (
    _helpful_from_post_config,
    _poll_selection_from_post_config,
    ForumInteractionModel,
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.core.repository.implementations.task.forum.analysis_row import (
    analysis_to_row,
)
from agentclaw.community.core.repository.implementations.task.forum.browse_repository import (
    BrowseRepositoryMixin,
)
from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

_TOPIC_REQUEST_INDEX = "uk_forum_topic_request"
_POST_REQUEST_INDEX = "uk_forum_post_request"


def _poll_to_row(poll: ForumPollConfig | None) -> dict[str, object] | None:
    """Encode one Topic configuration section as JSON storage."""

    if poll is None:
        return None
    return {
        "selection": poll.selection,
        "options": list(poll.options),
        "deadline_at": (
            poll.deadline_at.isoformat() if poll.deadline_at is not None else None
        ),
    }


def _post_config_to_row(
    poll_selection: ForumPollSelection | None,
) -> dict[str, object] | None:
    """Encode structured Post extensions as generic JSON storage.

    The current contract stores only the exact POLL vote selection under the
    ``poll_selection`` key. Keeping the outer object generic lets later Post
    variants extend the same database column without another schema change.
    """

    if poll_selection is None:
        return None
    return {
        "poll_selection": {"option_indices": list(poll_selection.option_indices)}
    }


def _helpful_post_config_to_row(helpful: bool) -> dict[str, object] | None:
    """Encode the HELP reply marker in the generic Post extension column."""

    return {"helpful": {"marked": True}} if helpful else None


def _topic_config_to_row(config: ForumTopicConfig | None) -> dict[str, object] | None:
    """Encode the type-specific Topic configuration as one JSON object."""

    if config is None:
        return None
    sections = tuple(
        name
        for name, value in (
            ("poll", config.poll),
            ("help", config.help),
        )
        if value is not None
    )
    if len(sections) != 1:
        raise ValueError("topic_config must contain exactly one section")
    if config.poll is not None:
        return {"poll": _poll_to_row(config.poll)}
    assert config.help is not None
    return {"help": {"expected_time": config.help.expected_time}}


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


class ForumRepository(BrowseRepositoryMixin, ForumRepositoryProtocol):
    """Create Topics and their replies without persisted floor metadata."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def list_topics(
        self,
        *,
        keyword: str | None,
        status: str | None,
        offset: int,
        limit: int,
        topic_type: str | None = None,
        author_id: str | None = None,
    ) -> ForumTopicPage:
        with self._db.orm_session() as db:
            # Per-Topic reply count and latest reply time, joined onto the
            # Topic row so the catalogue can show reply_count + latest_activity
            # without an N+1 walk over posts.
            # ForumPostModel carries no env/avernet_tenant columns; posts are
            # scoped by Topic id, and the Topic query below already filters by
            # env/tenant, so this per-Topic aggregate needs no env filter.
            post_stats = (
                db.query(
                    ForumPostModel.topic_id.label("topic_id"),
                    func.count().label("reply_count"),
                    func.max(ForumPostModel.gmt_create).label("latest_post_at"),
                )
                .group_by(ForumPostModel.topic_id)
                .subquery()
            )
            query = (
                db.query(
                    ForumTopicModel,
                    func.coalesce(post_stats.c.reply_count, 0).label(
                        "reply_count"
                    ),
                    post_stats.c.latest_post_at,
                )
                .outerjoin(
                    post_stats,
                    ForumTopicModel.topic_id == post_stats.c.topic_id,
                )
                .filter(
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
            )
            if status is not None:
                query = query.filter(ForumTopicModel.status == status)
            if topic_type is not None:
                query = query.filter(ForumTopicModel.topic_type == topic_type)
            if keyword is not None:
                query = query.filter(
                    or_(
                        ForumTopicModel.title.contains(keyword, autoescape=True),
                        ForumTopicModel.body.contains(keyword, autoescape=True),
                    )
                )
            if author_id is not None:
                query = query.filter(ForumTopicModel.author_id == author_id)
            # Catalogue ordering follows user-visible activity, not any
            # maintenance timestamp. Derived analysis updates must not make a
            # Topic bubble to the top when nobody replied.
            latest_activity_at = func.coalesce(
                post_stats.c.latest_post_at,
                ForumTopicModel.gmt_create,
            )
            total = query.count()
            rows = (
                query.order_by(
                    latest_activity_at.desc(),
                    ForumTopicModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
                .all()
            )
            items = tuple(
                replace(
                    row[0].to_record(),
                    reply_count=int(row[1] or 0),
                    latest_activity_at=(
                        row[2] if row[2] is not None else row[0].gmt_create
                    ),
                )
                for row in rows
            )
            return ForumTopicPage(total=total, items=items)

    def list_posts(self, *, topic_id: str, offset: int, limit: int) -> ForumPostPage:
        with self._db.orm_session() as db:
            topic = (
                db.query(ForumTopicModel.id)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            if topic is None:
                raise NotFound("topic not found")

            rows = (
                db.query(ForumPostModel)
                .filter(ForumPostModel.topic_id == topic_id)
                .order_by(ForumPostModel.gmt_create.asc(), ForumPostModel.id.asc())
                .all()
            )
            first_level_rows = tuple(
                row for row in rows if row.reply_to_post_id is None
            )
            total = len(first_level_rows)
            floor_by_post_id = {
                row.post_id: floor
                for floor, row in enumerate(first_level_rows, start=1)
            }
            display_rows = tuple(
                sorted(
                    first_level_rows,
                    key=lambda row: (
                        0 if _helpful_from_post_config(row.post_config) else 1,
                        row.gmt_create,
                        row.id,
                    ),
                )
            )
            visible_rows = display_rows[offset : offset + limit]

            children_by_parent: dict[str, list[ForumPostModel]] = {}
            for row in rows:
                if row.reply_to_post_id is not None:
                    children_by_parent.setdefault(
                        row.reply_to_post_id, []
                    ).append(row)

            items = tuple(
                replace(
                    row.to_record(),
                    floor_no=floor_by_post_id[row.post_id],
                    reply_to_floor_no=None,
                    children=tuple(
                        replace(
                            child.to_record(),
                            reply_to_floor_no=floor_by_post_id.get(
                                child.reply_to_post_id
                            ),
                        )
                        for child in self._descendant_rows(
                            root_post_id=row.post_id,
                            all_rows=rows,
                            children_by_parent=children_by_parent,
                        )
                    ),
                )
                for row in visible_rows
            )
            return ForumPostPage(total=total, items=items)

    def get_post(
        self,
        *,
        topic_id: str,
        post_id: str,
    ) -> ForumPostRecord | None:
        with self._db.orm_session() as db:
            topic = (
                db.query(ForumTopicModel.id)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            if topic is None:
                return None
            row = (
                db.query(ForumPostModel)
                .filter(
                    ForumPostModel.topic_id == topic_id,
                    ForumPostModel.post_id == post_id,
                )
                .one_or_none()
            )
            return None if row is None else row.to_record()

    def update_post_helpful(
        self,
        *,
        topic_id: str,
        post_id: str,
        helpful: bool,
    ) -> ForumPostRecord | None:
        with self._transaction() as db:
            topic = (
                db.query(ForumTopicModel.id)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            if topic is None:
                return None
            row = (
                db.query(ForumPostModel)
                .filter(
                    ForumPostModel.topic_id == topic_id,
                    ForumPostModel.post_id == post_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            if _helpful_from_post_config(row.post_config) == helpful:
                return row.to_record()
            row.post_config = _helpful_post_config_to_row(helpful)
            row.gmt_modified = datetime.now(timezone.utc)
            db.flush()
            return row.to_record()

    def update_post_helpful_with_accept(
        self,
        *,
        topic_id: str,
        post_id: str,
        helpful: bool,
        actor_type: str,
        actor_id: str,
    ) -> ForumPostRecord | None:
        """Update a HELP marker and its ACCEPT event in one database transaction."""

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
                return None
            row = (
                db.query(ForumPostModel)
                .filter(
                    ForumPostModel.topic_id == topic_id,
                    ForumPostModel.post_id == post_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            previous_helpful = _helpful_from_post_config(row.post_config)
            if previous_helpful == helpful:
                return row.to_record()
            now = datetime.now(timezone.utc)
            row.post_config = _helpful_post_config_to_row(helpful)
            row.gmt_modified = now
            if helpful:
                db.add(
                    ForumInteractionModel(
                        actor_type=actor_type,
                        actor_id=actor_id,
                        action_type="ACCEPT",
                        target_type="POST",
                        target_id=post_id,
                        env=get_current_env(),
                        gmt_create=now,
                        gmt_modified=now,
                    )
                )
            db.flush()
            return row.to_record()

    @staticmethod
    def _descendant_rows(
        *,
        root_post_id: str,
        all_rows: list[ForumPostModel],
        children_by_parent: dict[str, list[ForumPostModel]],
    ) -> list[ForumPostModel]:
        """Collect all descendants of one first-level reply without recursion.

        Storage keeps a single parent edge. Every descendant therefore belongs
        to the same two-level presentation group, including replies addressed
        to another child. The returned list preserves global chronological
        order from ``all_rows`` and cycle guards make malformed data safe.
        """
        descendant_ids: set[str] = set()
        queue = [root_post_id]
        while queue:
            parent_id = queue.pop(0)
            if parent_id in descendant_ids:
                continue
            descendant_ids.add(parent_id)
            queue.extend(
                child.post_id
                for child in children_by_parent.get(parent_id, [])
                if child.post_id not in descendant_ids
            )
        descendant_ids.discard(root_post_id)
        return [row for row in all_rows if row.post_id in descendant_ids]

    def create_topic(
        self,
        *,
        author_type: str,
        author_id: str,
        client_request_id: str,
        title: str,
        body: str,
        topic_type: str = TOPIC_TYPE_DISCUSSION,
        topic_config: ForumTopicConfig | None = None,
        author_display_name: str | None = None,
        author_avatar_url: str | None = None,
    ) -> ForumTopicCreateResult:
        existing = self._find_request_topic(author_type, author_id, client_request_id)
        if existing is not None:
            return ForumTopicCreateResult(topic=existing, created=False)

        now = datetime.now(timezone.utc)
        try:
            with self._transaction() as db:
                replay = (
                    db.query(ForumTopicModel)
                    .filter(
                        ForumTopicModel.author_type == author_type,
                        ForumTopicModel.author_id == author_id,
                        ForumTopicModel.client_request_id == client_request_id,
                        ForumTopicModel.env == get_current_env(),
                        ForumTopicModel.avernet_tenant
                        == get_current_avernet_tenant(),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if replay is not None:
                    return ForumTopicCreateResult(
                        topic=self._topic_record(db, replay),
                        created=False,
                    )

                topic = ForumTopicModel(
                    topic_id=f"topic_{uuid.uuid4().hex}",
                    author_type=author_type,
                    author_id=author_id,
                    author_display_name=author_display_name,
                    author_avatar_url=author_avatar_url,
                    title=title,
                    body=body,
                    topic_type=topic_type,
                    topic_config=_topic_config_to_row(topic_config),
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
        reply_to_post_id: str | None = None,
        poll_selection: ForumPollSelection | None = None,
        author_display_name: str | None = None,
        author_avatar_url: str | None = None,
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
                    .with_for_update()
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

                now = datetime.now(timezone.utc)
                if topic.topic_type == TOPIC_TYPE_POLL:
                    record = topic.to_record()
                    if reply_to_post_id is not None:
                        raise Conflict(
                            "POLL replies must target the Topic; reply_to_post_id is not allowed"
                        )
                    if record.topic_config is not None:
                        poll = record.topic_config.poll
                        if (
                            poll is not None
                            and poll.deadline_at is not None
                            and poll.deadline_at <= now
                        ):
                            raise Conflict("poll deadline has passed")

                if topic.status != TOPIC_STATUS_OPEN:
                    raise Conflict(f"topic is {topic.status.lower()}")

                if reply_to_post_id is not None:
                    referenced_post = (
                        db.query(ForumPostModel.id)
                        .filter(
                            ForumPostModel.topic_id == topic_id,
                            ForumPostModel.post_id == reply_to_post_id,
                        )
                        .one_or_none()
                    )
                    if referenced_post is None:
                        raise NotFound("reply_to_post_id not found in this topic")

                if topic.topic_type in {TOPIC_TYPE_POLL, TOPIC_TYPE_NOTICE}:
                    already_replied = (
                        db.query(ForumPostModel.post_id)
                        .filter(
                            ForumPostModel.topic_id == topic_id,
                            ForumPostModel.author_type == author_type,
                            ForumPostModel.author_id == author_id,
                        )
                        .first()
                    )
                    if already_replied is not None:
                        raise Conflict("author has already replied to this topic")
                post = ForumPostModel(
                    post_id=f"post_{uuid.uuid4().hex}",
                    topic_id=topic_id,
                    reply_to_post_id=reply_to_post_id,
                    author_type=author_type,
                    author_id=author_id,
                    author_display_name=author_display_name,
                    author_avatar_url=author_avatar_url,
                    body=body,
                    post_config=_post_config_to_row(poll_selection),
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

    def get_topic_viewer_state(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
    ) -> ForumTopicViewerRecord | None:
        with self._db.orm_session() as db:
            topic = (
                db.query(ForumTopicModel.topic_type)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            if topic is None:
                return None
            if topic[0] != TOPIC_TYPE_POLL:
                return ForumTopicViewerRecord(has_voted=False)

            post = (
                db.query(ForumPostModel)
                .filter(
                    ForumPostModel.topic_id == topic_id,
                    ForumPostModel.author_type == author_type,
                    ForumPostModel.author_id == author_id,
                )
                .order_by(ForumPostModel.id.asc())
                .first()
            )
            if post is None:
                return ForumTopicViewerRecord(has_voted=False)
            return ForumTopicViewerRecord(
                has_voted=True,
                vote_post_id=post.post_id,
                poll_selection=_poll_selection_from_post_config(post.post_config),
            )

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
            if row is None:
                return None
            record = self._topic_record(db, row)
            if (
                record.topic_type != TOPIC_TYPE_POLL
                or record.topic_config is None
                or record.topic_config.poll is None
                or record.analysis.poll_result is not None
            ):
                return record

            poll = record.topic_config.poll
            empty_result = ForumPollResultRecord(
                options=tuple(
                    ForumPollResultOptionRecord(index=index, votes=0, percent=0)
                    for index in range(len(poll.options))
                ),
                total_votes=0,
                participant_count=0,
                updated_at=record.updated_at,
            )
            return replace(
                record, analysis=replace(record.analysis, poll_result=empty_result)
            )

    def advance_poll_projection(
        self,
        *,
        topic_id: str,
        calculator: "ForumPollProjectionCalculator",
        batch_limit: int,
    ) -> int:
        """Advance one Topic's poll aggregate in one Topic-locked transaction.

        Replies lock the Topic row on write, so taking the same lock here
        serializes a batch against new vote Posts. The transaction consumes at
        most ``batch_limit`` rows after the stored cursor, asks the core
        calculator for a new projection, and commits both the projection and
        advanced cursor together.
        """

        with self._transaction() as db:
            topic = (
                db.query(ForumTopicModel)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant
                    == get_current_avernet_tenant(),
                )
                .with_for_update()
                .one_or_none()
            )
            if topic is None:
                return 0
            record = topic.to_record()
            poll = (
                None
                if record.topic_config is None
                else record.topic_config.poll
            )
            if (
                record.topic_type != TOPIC_TYPE_POLL
                or poll is None
            ):
                return 0

            rows = (
                db.query(ForumPostModel)
                .filter(
                    ForumPostModel.topic_id == topic_id,
                    ForumPostModel.id > record.analysis.poll_cursor_id,
                )
                .order_by(ForumPostModel.id.asc())
                .limit(batch_limit)
                .all()
            )
            if not rows:
                # An empty batch is a true no-op. Rewriting the Topic with the
                # same aggregate would make MySQL report a changed-row count of
                # zero and SQLAlchemy raise StaleDataError in multi-worker
                # deployments that repeat the projection within one second.
                return 0
            votes = tuple(
                ForumPollVoteRecord(
                    row_id=row.id,
                    post_id=row.post_id,
                    author_type=row.author_type,
                    author_id=row.author_id,
                    poll_selection=_poll_selection_from_post_config(
                        row.post_config
                    ),
                )
                for row in rows
            )
            analysis = calculator(
                ForumPollProjectionInput(
                    poll=poll,
                    analysis=record.analysis,
                    votes=votes,
                )
            )
            topic.analysis_result = analysis_to_row(analysis)
            topic.gmt_modified = datetime.now(timezone.utc)
            db.flush()
            return len(rows)

    def update_topic_content(
        self,
        *,
        topic_id: str,
        title: str,
        body: str,
        expected_status: str,
    ) -> ForumTopicRecord | None:
        with self._transaction() as db:
            row = (
                db.query(ForumTopicModel)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.status == expected_status,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return None
            row.title = title
            row.body = body
            row.gmt_modified = datetime.now(timezone.utc)
            db.flush()
            return self._topic_record(db, row)

    def delete_topic(self, topic_id: str) -> bool:
        """Delete Topic content and its direct interaction records together.

        Interaction rows are string-keyed projections rather than foreign keys,
        so they are removed explicitly for the deleted Topic and its Posts.
        Published historical reports are retained; they reference content by
        identifier and do not resurrect deleted Topic/Post rows.
        """

        with self._transaction() as db:
            row = (
                db.query(ForumTopicModel)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None or row.status == TOPIC_STATUS_LOCKED:
                return False

            post_ids = [
                result[0]
                for result in db.query(ForumPostModel.post_id)
                .filter(ForumPostModel.topic_id == topic_id)
                .all()
            ]
            if post_ids:
                db.query(ForumInteractionModel).filter(
                    ForumInteractionModel.target_type == "POST",
                    ForumInteractionModel.target_id.in_(post_ids),
                ).delete(synchronize_session=False)
            db.query(ForumInteractionModel).filter(
                ForumInteractionModel.target_type == "TOPIC",
                ForumInteractionModel.target_id == topic_id,
            ).delete(synchronize_session=False)
            db.query(ForumPostModel).filter(
                ForumPostModel.topic_id == topic_id
            ).delete(synchronize_session=False)
            db.delete(row)
            db.flush()
            return True

    def update_topic_status(
        self,
        *,
        topic_id: str,
        status: str,
    ) -> ForumTopicRecord | None:
        with self._transaction() as db:
            row = (
                db.query(ForumTopicModel)
                .filter(
                    ForumTopicModel.topic_id == topic_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            if row is None:
                # No matching Topic: signal ``None`` to the caller so the service
                # can map it back to a ``NotFound``. The transaction stays empty
                # and the context manager still commits the no-op safely.
                return None
            row.status = status
            db.flush()
            return self._topic_record(db, row)

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
            return None if row is None else self._topic_record(db, row)

    @staticmethod
    def _topic_record(db, row: ForumTopicModel) -> ForumTopicRecord:
        del db
        return row.to_record()

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
