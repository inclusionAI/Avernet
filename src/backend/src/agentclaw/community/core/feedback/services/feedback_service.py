"""Application service for general user feedback.

Validates public input, then delegates persistence to a repository. The reporter
is declared by the caller on this surface and is not re-verified here: like the
BBS unified writes, the feedback surface is a backend API reachable by humans,
Bots and application callers alike, so the service records the reporter the
caller declares.
"""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.feedback.models import (
    MAX_CONTENT_LENGTH,
    MAX_MODULE_LENGTH,
    MAX_REPORTER_ID_LENGTH,
    FeedbackCreateResult,
    FeedbackPage,
)
from agentclaw.community.core.feedback.service_protocol import FeedbackServiceProtocol
from agentclaw.community.core.repository.protocols.feedback import (
    FeedbackRepositoryProtocol,
)


class FeedbackService(FeedbackServiceProtocol):
    """Validate public input and delegate transactional persistence."""

    @inject
    def __init__(self, repository: FeedbackRepositoryProtocol) -> None:
        self._repository = repository

    def create_feedback(
        self,
        *,
        reporter_id: str,
        module: str,
        content: str,
    ) -> FeedbackCreateResult:
        normalized_reporter_id = self._required_text(
            reporter_id, "reporter_id", MAX_REPORTER_ID_LENGTH
        )
        normalized_module = self._required_text(module, "module", MAX_MODULE_LENGTH)
        normalized_content = self._required_text(content, "content", MAX_CONTENT_LENGTH)
        return self._repository.create_feedback(
            reporter_id=normalized_reporter_id,
            module=normalized_module,
            content=normalized_content,
        )

    def list_feedback(
        self,
        *,
        module: str | None,
        reporter_id: str | None,
        page: int,
        page_size: int,
    ) -> FeedbackPage:
        normalized_module = self._optional_text(module, "module", MAX_MODULE_LENGTH)
        normalized_reporter_id = self._optional_text(
            reporter_id, "reporter_id", MAX_REPORTER_ID_LENGTH
        )
        self._pagination(page, page_size)
        return self._repository.list_feedback(
            module=normalized_module,
            reporter_id=normalized_reporter_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    # ------------------------------------------------------------------
    # input normalization — lifted verbatim in shape from ForumService so the
    # validation rules match BBS exactly (trim, non-empty-after-trim, length),
    # keeping the public error contract (400 ValidationError) consistent.
    # ------------------------------------------------------------------
    @classmethod
    def _required_text(cls, value: str, field: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field} must not be empty")
        if len(normalized) > max_length:
            raise ValidationError(
                f"{field} must be at most {max_length} characters long"
            )
        return normalized

    @classmethod
    def _optional_text(
        cls, value: str | None, field: str, max_length: int
    ) -> str | None:
        if value is None:
            return None
        return cls._required_text(value, field, max_length)

    @staticmethod
    def _pagination(page: int, page_size: int) -> None:
        if not isinstance(page, int) or page < 1:
            raise ValidationError("page must be at least 1")
        if not isinstance(page_size, int) or page_size < 1 or page_size > 100:
            raise ValidationError("page_size must be between 1 and 100")
