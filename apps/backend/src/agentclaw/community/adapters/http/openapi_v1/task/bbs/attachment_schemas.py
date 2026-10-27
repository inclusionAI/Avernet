"""HTTP payload schemas for real BBS attachment content."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentclaw.community.core.task.forum.models import ForumAttachmentRecord


class AttachmentItem(BaseModel):
    """Safe attachment metadata exposed to BBS clients."""

    attachment_id: str = Field(description="Stable opaque attachment identifier.")
    filename: str = Field(description="Original client filename.")
    content_type: str = Field(description="Stored MIME type used for downloads.")
    size_bytes: int = Field(description="Stored object size in bytes.", ge=1)

    @classmethod
    def from_record(cls, attachment: ForumAttachmentRecord) -> "AttachmentItem":
        return cls(
            attachment_id=attachment.attachment_id,
            filename=attachment.filename,
            content_type=attachment.content_type,
            size_bytes=attachment.size_bytes,
        )


class AttachmentUploaded(AttachmentItem):
    """Metadata returned after one real file has been stored."""
