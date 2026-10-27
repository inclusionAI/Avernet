"""Application service for real BBS attachment upload and download."""

from __future__ import annotations

import hashlib
import uuid

from injector import inject

from agentclaw.community.core.errors import InternalError, NotFound, ValidationError
from agentclaw.community.core.repository.protocols.task.forum import (
    ForumRepositoryProtocol,
)
from agentclaw.community.core.task.forum.attachment_service_protocol import (
    ForumAttachmentServiceProtocol,
)
from agentclaw.community.core.task.forum.models import (
    AUTHOR_TYPES,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_CONTENT_TYPE_LENGTH,
    MAX_ATTACHMENT_FILENAME_UTF8_BYTES,
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_ID_LENGTH,
    ForumAttachmentContent,
    ForumAttachmentRecord,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


class ForumAttachmentService(ForumAttachmentServiceProtocol):
    """Validate attachment input and coordinate object + metadata persistence."""

    @inject
    def __init__(
        self,
        repository: ForumRepositoryProtocol,
        object_storage: ObjectStoragePlugin,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage

    def upload_attachment(
        self,
        *,
        author_type: str,
        author_id: str,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> ForumAttachmentRecord:
        normalized_author_type = self._author_type(author_type)
        normalized_author_id = self._required_text(
            author_id, "author_id", MAX_AUTHOR_ID_LENGTH
        )
        normalized_filename = self._filename(filename)
        normalized_content_type = self._content_type(content_type)
        normalized_content = self._content(content)

        attachment_id = f"attachment_{uuid.uuid4().hex}"
        scope = "\0".join(
            (
                get_current_avernet_tenant(),
                get_current_env(),
                normalized_author_type,
                normalized_author_id,
            )
        )
        scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]
        object_key = f"bbs/attachments/{scope_hash}/{attachment_id}"

        if not self._object_storage.put_object(object_key, normalized_content):
            raise InternalError("attachment object upload failed")

        try:
            return self._repository.create_attachment(
                attachment_id=attachment_id,
                author_type=normalized_author_type,
                author_id=normalized_author_id,
                filename=normalized_filename,
                content_type=normalized_content_type,
                size_bytes=len(normalized_content),
                object_key=object_key,
            )
        except Exception:
            if not self._object_storage.delete_object(object_key):
                logger.error(
                    "[bbs-attachment] metadata write failed and object cleanup "
                    "also failed attachment_id=%s key=%s",
                    attachment_id,
                    object_key,
                )
            raise

    def download_attachment(
        self, *, attachment_id: str
    ) -> ForumAttachmentContent:
        normalized_id = self._required_text(
            attachment_id, "attachment_id", MAX_ID_LENGTH
        )
        attachment = self._repository.get_attachment(normalized_id)
        if attachment is None:
            raise NotFound("attachment not found")
        content = self._object_storage.get_object(attachment.object_key)
        if content is None:
            raise NotFound("attachment content not found")
        return ForumAttachmentContent(attachment=attachment, content=content)

    @classmethod
    def _author_type(cls, value: str) -> str:
        normalized = cls._required_text(
            value, "author_type", MAX_AUTHOR_TYPE_LENGTH
        ).upper()
        if normalized not in AUTHOR_TYPES:
            raise ValidationError("author_type must be HUMAN or BOT")
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
                f"{field} must be at most {max_length} characters long"
            )
        return normalized

    @classmethod
    def _filename(cls, value: str) -> str:
        normalized = cls._required_text(
            value, "filename", MAX_ATTACHMENT_FILENAME_UTF8_BYTES
        )
        if len(normalized.encode("utf-8")) > MAX_ATTACHMENT_FILENAME_UTF8_BYTES:
            raise ValidationError(
                "filename must be at most "
                f"{MAX_ATTACHMENT_FILENAME_UTF8_BYTES} UTF-8 bytes long"
            )
        return normalized

    @classmethod
    def _content_type(cls, value: str | None) -> str:
        if value is None:
            return _DEFAULT_CONTENT_TYPE
        if not isinstance(value, str):
            raise ValidationError("content_type must be a string")
        if not value.strip():
            return _DEFAULT_CONTENT_TYPE
        return cls._required_text(
            value, "content_type", MAX_ATTACHMENT_CONTENT_TYPE_LENGTH
        )

    @staticmethod
    def _content(value: bytes) -> bytes:
        if not isinstance(value, (bytes, bytearray)):
            raise ValidationError("content must be bytes")
        content = bytes(value)
        if not content:
            raise ValidationError("attachment file must not be empty")
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ValidationError(
                f"attachment file must not exceed {MAX_ATTACHMENT_BYTES} bytes"
            )
        return content
