"""Default BBS interaction target verifier selected by the DI composition root."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.core.task.domain.identity import (
    is_bare_default_bot_identity,
    parse_scoped_bot_identity,
)
from agentclaw.community.core.task.forum.interaction.target_verifier_protocol import (
    ForumInteractionTargetVerifierProtocol,
)
from agentclaw.community.core.task.forum.models import ForumPostRecord, ForumTopicRecord
from agentclaw.community.core.task.forum.repository.models import (
    ForumPostModel,
    ForumTopicModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class DatabaseForumInteractionTargetVerifier(ForumInteractionTargetVerifierProtocol):
    """Verify stable content and identity targets without falling back to text."""

    @inject
    def __init__(
        self,
        forum_repository: ForumRepositoryProtocol,
        db: DatabasePlugin,
    ) -> None:
        self._forum_repository = forum_repository
        self._db = db

    def verify_topic(self, topic_id: str) -> ForumTopicRecord | None:
        return self._forum_repository.get_topic(topic_id)

    def verify_post(self, post_id: str) -> ForumPostRecord | None:
        with self._db.orm_session() as db:
            row = (
                db.query(ForumPostModel)
                .join(
                    ForumTopicModel,
                    ForumTopicModel.topic_id == ForumPostModel.topic_id,
                )
                .filter(
                    ForumPostModel.post_id == post_id,
                    ForumTopicModel.env == get_current_env(),
                    ForumTopicModel.avernet_tenant == get_current_avernet_tenant(),
                )
                .one_or_none()
            )
            return None if row is None else row.to_record()

    def verify_bot(self, bot_id: str) -> bool:
        if is_bare_default_bot_identity(bot_id):
            return False
        scoped = parse_scoped_bot_identity(bot_id)
        if scoped is None:
            return False
        bare_bot_id, owner_id = scoped
        with self._db.orm_session() as db:
            query = db.query(BotModel.id).filter(
                BotModel.bot_id == bare_bot_id,
                BotModel.env == get_current_env(),
                BotModel.avernet_tenant == get_current_avernet_tenant(),
                BotModel.is_delete == 0,
            )
            query = query.filter(BotModel.owner_id == owner_id)
            row = query.one_or_none()
            return row is not None
