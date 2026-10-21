"""Persistence for Topic-derived analysis tag updates."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from injector import inject

from agentclaw.community.core.repository.implementations.task.forum.analysis_row import (
    analysis_to_row,
)
from agentclaw.community.core.repository.protocols.task.forum_topic_tag import (
    ForumTopicTagRepositoryProtocol,
)
from agentclaw.community.core.task.forum.models import ForumTopicRecord
from agentclaw.community.core.task.forum.repository.models import ForumTopicModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class ForumTopicTagRepository(ForumTopicTagRepositoryProtocol):
    """Update the ``analysis.tags`` section in one Topic-locked transaction."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def update_topic_tags(
        self,
        *,
        topic_id: str,
        tags: tuple[str, ...],
    ) -> ForumTopicRecord | None:
        with self._transaction() as db:
            row = (
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
            if row is None:
                return None
            record = row.to_record()
            analysis = replace(record.analysis, tags=tags)
            row.analysis_result = analysis_to_row(analysis)
            row.gmt_modified = datetime.now(timezone.utc)
            db.flush()
            return row.to_record()


__all__ = ["ForumTopicTagRepository"]
