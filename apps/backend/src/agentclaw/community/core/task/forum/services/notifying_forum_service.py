"""ForumService decorator that emits reply and help-response notifications."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.core.task.forum.models import (
    ForumReplyCreateResult,
    ForumTopicRecord,
    TOPIC_TYPE_HELP,
)
from agentclaw.community.core.task.forum.services.forum_service import ForumService
from agentclaw.community.core.work_orders.models import (
    WorkOrderEventType,
)
from ..notifications.publisher_protocol import ForumNotificationPublisherProtocol


class NotifyingForumService(ForumService):
    """Extend ``create_reply`` without changing the base service contract.

    Notifications are emitted only when the repository created the Post. An
    idempotent replay returns the original Post with ``created=False`` and is
    ignored. Notification persistence failures propagate after the reply has
    been committed; the caller receives the same failure status rather than a
    false success.
    """

    @inject
    def __init__(
        self,
        repository: ForumRepositoryProtocol,
        notifications: ForumNotificationPublisherProtocol,
    ) -> None:
        super().__init__(repository)
        self._notifications = notifications

    def create_reply(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
        client_request_id: str,
        body: str,
        reply_to_post_id: str | None = None,
        poll_selection: object | None = None,
        author_display_name: str | None = None,
        author_avatar_url: str | None = None,
    ) -> ForumReplyCreateResult:
        result = super().create_reply(
            topic_id=topic_id,
            author_type=author_type,
            author_id=author_id,
            client_request_id=client_request_id,
            body=body,
            reply_to_post_id=reply_to_post_id,
            poll_selection=poll_selection,
            author_display_name=author_display_name,
            author_avatar_url=author_avatar_url,
        )
        if not result.created:
            return result

        topic = self._get_topic(topic_id)
        if result.post.reply_to_post_id is None:
            direct_help_response = topic.topic_type == TOPIC_TYPE_HELP
            event_type = (
                WorkOrderEventType.BBS_HELP_RESPONDED
                if direct_help_response
                else WorkOrderEventType.BBS_REPLIED
            )
            recipient_type = topic.author_type
            recipient_id = topic.author_id
            actor_display = author_display_name or result.post.display_name
            text = (
                "响应了你的求助帖"
                if direct_help_response
                else "回复了你的帖子"
            )
        else:
            parent = self._repository.get_post(
                topic_id=result.post.topic_id,
                post_id=result.post.reply_to_post_id,
            )
            if parent is None:
                raise NotFound("reply target not found in this topic")
            event_type = WorkOrderEventType.BBS_REPLIED
            recipient_type = parent.author_type
            recipient_id = parent.author_id
            actor_display = author_display_name or result.post.display_name
            text = "回复了你的回复"

        self._notifications.notify(
            event_type=event_type,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            actor_type=author_type,
            actor_id=author_id,
            actor_display_name=actor_display,
            target_type="POST",
            target_id=result.post.post_id,
            text=text,
            topic_id=result.post.topic_id,
            post_id=result.post.post_id,
            topic_title=topic.title,
        )
        return result

    def _get_topic(self, topic_id: str) -> ForumTopicRecord:
        topic = self._repository.get_topic(topic_id)
        if topic is None:
            raise NotFound("topic not found")
        return topic
