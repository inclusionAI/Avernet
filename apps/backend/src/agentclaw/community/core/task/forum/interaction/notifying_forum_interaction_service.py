"""Forum interaction decorator that emits human/Bot inbox notifications."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.repository.protocols.task.forum_interaction import (
    ForumInteractionRepositoryProtocol,
)
from agentclaw.community.core.task.forum.interaction.forum_author_stats_service import (
    ForumAuthorStatsService,
)
from agentclaw.community.core.task.forum.interaction.forum_interaction_service import (
    ForumInteractionService,
)
from agentclaw.community.core.task.forum.interaction.service_protocol import (
    ForumAuthorStatsServiceProtocol,
)
from agentclaw.community.core.task.forum.interaction.target_verifier_protocol import (
    ForumInteractionTargetVerifierProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    ForumInteractionActionType,
    ForumInteractionRecord,
    ForumInteractionTargetType,
)
from agentclaw.community.core.work_orders.models import WorkOrderEventType
from ..notifications.publisher_protocol import ForumNotificationPublisherProtocol


class NotifyingForumInteractionService(ForumInteractionService):
    """Emit notifications only for new active positive interactions.

    Cancellations and duplicate ``+1`` writes do not transition the active
    state to one and therefore never notify. The notification is created after
    the interaction transaction commits; a failed notification write
    propagates and the HTTP caller receives an error.
    """

    @inject
    def __init__(
        self,
        repository: ForumInteractionRepositoryProtocol,
        target_verifier: ForumInteractionTargetVerifierProtocol,
        author_stats_service: ForumAuthorStatsServiceProtocol,
        notifications: ForumNotificationPublisherProtocol,
    ) -> None:
        super().__init__(repository, target_verifier, author_stats_service)
        self._notifications = notifications

    def record_interaction(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
        active: int,
    ) -> ForumInteractionRecord:
        result = super().record_interaction(
            actor_type=actor_type,
            actor_id=actor_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            active=active,
        )
        if active != 1 or result.active != 1:
            return result

        action = ForumInteractionActionType(result.action_type.value)
        if action not in (
            ForumInteractionActionType.FOLLOW,
            ForumInteractionActionType.LIKE,
            ForumInteractionActionType.FAVORITE,
        ):
            return result

        event_type = {
            ForumInteractionActionType.FOLLOW: WorkOrderEventType.BBS_FOLLOWED,
            ForumInteractionActionType.LIKE: WorkOrderEventType.BBS_LIKED,
            ForumInteractionActionType.FAVORITE: (
                WorkOrderEventType.BBS_FAVORITED
            ),
        }[action]
        text = {
            ForumInteractionActionType.FOLLOW: "关注了你",
            ForumInteractionActionType.LIKE: "赞了你的内容",
            ForumInteractionActionType.FAVORITE: "收藏了你的内容",
        }[action]
        recipient_type, recipient_id, topic_id, post_id, topic_title = self._recipient(
            target=result.target_type,
            target_id=result.target_id,
        )
        self._notifications.notify(
            event_type=event_type,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_display_name=None,
            target_type=result.target_type.value,
            target_id=result.target_id,
            text=text,
            topic_id=topic_id,
            post_id=post_id,
            topic_title=topic_title,
        )
        return result

    def _recipient(
        self,
        *,
        target: ForumInteractionTargetType,
        target_id: str,
    ) -> tuple[str, str, str | None, str | None, str | None]:
        if target in (ForumInteractionTargetType.HUMAN, ForumInteractionTargetType.BOT):
            return target.value, target_id, None, None, None
        if target is ForumInteractionTargetType.POST:
            post = self._target_verifier.verify_post(target_id)
            if post is None:
                return target.value, target_id, None, target_id, None
            topic = self._target_verifier.verify_topic(post.topic_id)
            return (
                post.author_type,
                post.author_id,
                post.topic_id,
                post.post_id,
                topic.title if topic is not None else None,
            )
        topic = self._target_verifier.verify_topic(target_id)
        if topic is None:
            return target.value, target_id, target_id, None, None
        return topic.author_type, topic.author_id, target_id, None, topic.title
