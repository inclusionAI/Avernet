"""Application service for BBS Topic and Post content."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.errors import NotFound, ValidationError
from agentclaw.community.core.forum.models import (
    AUTHOR_TYPES,
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_BODY_LENGTH,
    MAX_ID_LENGTH,
    MAX_SEARCH_KEYWORD_LENGTH,
    MAX_TITLE_LENGTH,
    TOPIC_STATUSES,
    TOPIC_TYPES,
    ForumPostPage,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
    ForumTopicPage,
    ForumTopicRecord,
)
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.core.repository.protocols.forum import (
    ForumRepositoryProtocol,
)


class ForumService(ForumServiceProtocol):
    """Validate public input and delegate transactional persistence.

    Adapters derive the author from the authenticated principal or an authorized
    represented identity. Author identity is never accepted from the body.
    """

    @inject
    def __init__(self, repository: ForumRepositoryProtocol) -> None:
        self._repository = repository

    def list_topics(
        self,
        *,
        keyword: str | None,
        status: str | None,
        page: int,
        page_size: int,
        topic_type: str | None = None,
    ) -> ForumTopicPage:
        normalized_keyword = self._optional_text(
            keyword, "keyword", MAX_SEARCH_KEYWORD_LENGTH
        )
        normalized_status = self._topic_status(status)
        normalized_topic_type = self._topic_type(topic_type)
        self._pagination(page, page_size)
        return self._repository.list_topics(
            keyword=normalized_keyword,
            status=normalized_status,
            topic_type=normalized_topic_type,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    def get_topic(self, *, topic_id: str) -> ForumTopicRecord:
        normalized_topic_id = self._required_text(topic_id, "topic_id", MAX_ID_LENGTH)
        topic = self._repository.get_topic(normalized_topic_id)
        if topic is None:
            raise NotFound("topic not found")
        return topic

    def list_posts(self, *, topic_id: str, page: int, page_size: int) -> ForumPostPage:
        normalized_topic_id = self._required_text(topic_id, "topic_id", MAX_ID_LENGTH)
        self._pagination(page, page_size)
        return self._repository.list_posts(
            topic_id=normalized_topic_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    def create_topic(
        self,
        *,
        author_type: str,
        author_id: str,
        client_request_id: str,
        title: str,
        body: str,
        topic_type: str | None = None,
    ) -> ForumTopicCreateResult:
        normalized_author_type = self._author_type(author_type)
        normalized_author_id = self._required_text(
            author_id, "author_id", MAX_AUTHOR_ID_LENGTH
        )
        request_id = self._required_text(
            client_request_id, "client_request_id", MAX_ID_LENGTH
        )
        normalized_title = self._required_text(title, "title", MAX_TITLE_LENGTH)
        normalized_body = self._required_text(body, "body", MAX_BODY_LENGTH)
        normalized_topic_type = self._topic_type(topic_type) or "DISCUSSION"
        return self._repository.create_topic(
            author_type=normalized_author_type,
            author_id=normalized_author_id,
            client_request_id=request_id,
            title=normalized_title,
            body=normalized_body,
            topic_type=normalized_topic_type,
        )

    def create_reply(
        self,
        *,
        topic_id: str,
        author_type: str,
        author_id: str,
        client_request_id: str,
        body: str,
    ) -> ForumReplyCreateResult:
        normalized_topic_id = self._required_text(topic_id, "topic_id", MAX_ID_LENGTH)
        normalized_author_type = self._author_type(author_type)
        normalized_author_id = self._required_text(
            author_id, "author_id", MAX_AUTHOR_ID_LENGTH
        )
        request_id = self._required_text(
            client_request_id, "client_request_id", MAX_ID_LENGTH
        )
        normalized_body = self._required_text(body, "body", MAX_BODY_LENGTH)
        return self._repository.create_reply(
            topic_id=normalized_topic_id,
            author_type=normalized_author_type,
            author_id=normalized_author_id,
            client_request_id=request_id,
            body=normalized_body,
        )

    @classmethod
    def _author_type(cls, value: str) -> str:
        normalized = cls._required_text(
            value, "author_type", MAX_AUTHOR_TYPE_LENGTH
        ).upper()
        if normalized not in AUTHOR_TYPES:
            allowed = ", ".join(sorted(AUTHOR_TYPES))
            raise ValidationError(f"author_type must be one of: {allowed}")
        return normalized

    @staticmethod
    def _required_text(value: str, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(
                f"{field} must contain no more than {max_length} characters"
            )
        return normalized

    @classmethod
    def _topic_type(cls, value: str | None) -> str | None:
        normalized = cls._optional_text(value, "topic_type", MAX_AUTHOR_TYPE_LENGTH)
        if normalized is None:
            return None
        normalized = normalized.upper()
        if normalized not in TOPIC_TYPES:
            allowed = ", ".join(sorted(TOPIC_TYPES))
            raise ValidationError(f"topic_type must be one of: {allowed}")
        return normalized

    @classmethod
    def _topic_status(cls, value: str | None) -> str | None:
        normalized = cls._optional_text(value, "status", MAX_AUTHOR_TYPE_LENGTH)
        if normalized is None:
            return None
        normalized = normalized.upper()
        if normalized not in TOPIC_STATUSES:
            allowed = ", ".join(sorted(TOPIC_STATUSES))
            raise ValidationError(f"status must be one of: {allowed}")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, field: str, max_length: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValidationError(
                f"{field} must contain no more than {max_length} characters"
            )
        return normalized

    @staticmethod
    def _pagination(page: int, page_size: int) -> None:
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ValidationError("page must be at least 1")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or page_size < 1
            or page_size > 100
        ):
            raise ValidationError("page_size must be between 1 and 100")
