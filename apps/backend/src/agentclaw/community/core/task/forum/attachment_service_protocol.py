"""Transport-agnostic service contract for BBS attachment content."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.forum.models import (
    ForumAttachmentContent,
    ForumAttachmentRecord,
)


@runtime_checkable
class ForumAttachmentServiceProtocol(Protocol):
    @abstractmethod
    def upload_attachment(
        self,
        *,
        author_type: str,
        author_id: str,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> ForumAttachmentRecord:
        """Store bytes and persist tenant-scoped attachment metadata."""

    @abstractmethod
    def download_attachment(
        self, *, attachment_id: str
    ) -> ForumAttachmentContent:
        """Return metadata and the real stored bytes, or raise ``NotFound``."""
