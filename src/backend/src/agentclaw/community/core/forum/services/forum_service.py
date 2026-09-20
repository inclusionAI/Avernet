"""Application service for phase-1 BBS content writes."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.forum.models import (
    AUTHOR_TYPES,
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_BODY_LENGTH,
    MAX_ID_LENGTH,
    MAX_TITLE_LENGTH,
    ForumReplyCreateResult,
    ForumTopicCreateResult,
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

    def create_topic(
        self,
        *,
        author_type: str,
        author_id: str,
        client_request_id: str,
        title: str,
        body: str,
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
        return self._repository.create_topic(
            author_type=normalized_author_type,
            author_id=normalized_author_id,
            client_request_id=request_id,
            title=normalized_title,
            body=normalized_body,
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
