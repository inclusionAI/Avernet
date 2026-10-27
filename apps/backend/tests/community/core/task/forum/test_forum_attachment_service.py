from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.errors import InternalError, NotFound, ValidationError
from agentclaw.community.core.task.forum.models import (
    MAX_ATTACHMENT_BYTES,
    ForumAttachmentRecord,
)
from agentclaw.community.core.task.forum.services.forum_attachment_service import (
    ForumAttachmentService,
)


class ObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_result = True
        self.delete_result = True
        self.deleted: list[str] = []

    def put_object(self, key: str, content: bytes | str) -> bool:
        if not self.put_result:
            return False
        self.objects[key] = content.encode() if isinstance(content, str) else content
        return True

    def get_object(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def delete_object(self, key: str) -> bool:
        self.deleted.append(key)
        if self.delete_result:
            self.objects.pop(key, None)
        return self.delete_result


class Repository:
    def __init__(self) -> None:
        self.attachments: dict[str, ForumAttachmentRecord] = {}
        self.create_error: Exception | None = None
        self.create_calls: list[dict[str, object]] = []

    def create_attachment(self, **kwargs) -> ForumAttachmentRecord:
        self.create_calls.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        record = ForumAttachmentRecord(
            **kwargs,
            topic_id=None,
            created_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
        )
        self.attachments[record.attachment_id] = record
        return record

    def get_attachment(self, attachment_id: str) -> ForumAttachmentRecord | None:
        return self.attachments.get(attachment_id)


def _service() -> tuple[ForumAttachmentService, Repository, ObjectStorage]:
    repository = Repository()
    storage = ObjectStorage()
    return ForumAttachmentService(repository, storage), repository, storage


def test_upload_stores_real_bytes_before_metadata() -> None:
    service, repository, storage = _service()

    result = service.upload_attachment(
        author_type="human",
        author_id="149844",
        filename=" design.pdf ",
        content_type="application/pdf",
        content=b"real-pdf-bytes",
    )

    assert result.attachment_id.startswith("attachment_")
    assert result.author_type == "HUMAN"
    assert result.filename == "design.pdf"
    assert result.size_bytes == len(b"real-pdf-bytes")
    assert result.object_key.startswith("bbs/attachments/")
    assert storage.objects[result.object_key] == b"real-pdf-bytes"
    assert repository.create_calls[0]["object_key"] == result.object_key


@pytest.mark.parametrize("content", [b"", bytearray()])
def test_upload_rejects_empty_file(content: bytes | bytearray) -> None:
    service, repository, storage = _service()

    with pytest.raises(ValidationError, match="must not be empty"):
        service.upload_attachment(
            author_type="HUMAN",
            author_id="149844",
            filename="empty.txt",
            content_type="text/plain",
            content=content,
        )

    assert repository.create_calls == []
    assert storage.objects == {}


def test_upload_rejects_file_above_twenty_mib() -> None:
    service, repository, storage = _service()

    with pytest.raises(ValidationError, match="must not exceed"):
        service.upload_attachment(
            author_type="HUMAN",
            author_id="149844",
            filename="large.bin",
            content_type=None,
            content=b"x" * (MAX_ATTACHMENT_BYTES + 1),
        )

    assert repository.create_calls == []
    assert storage.objects == {}


def test_upload_rejects_filename_above_utf8_byte_limit() -> None:
    service, _, _ = _service()

    with pytest.raises(ValidationError, match="UTF-8 bytes"):
        service.upload_attachment(
            author_type="HUMAN",
            author_id="149844",
            filename="测" * 86,
            content_type="text/plain",
            content=b"content",
        )


def test_upload_rejects_non_string_content_type_as_validation_error() -> None:
    service, _, _ = _service()

    with pytest.raises(ValidationError, match="content_type must be a string"):
        service.upload_attachment(
            author_type="HUMAN",
            author_id="149844",
            filename="file.txt",
            content_type=123,  # type: ignore[arg-type]
            content=b"content",
        )


def test_object_store_failure_does_not_write_metadata() -> None:
    service, repository, storage = _service()
    storage.put_result = False

    with pytest.raises(InternalError, match="object upload failed"):
        service.upload_attachment(
            author_type="HUMAN",
            author_id="149844",
            filename="file.txt",
            content_type="text/plain",
            content=b"content",
        )

    assert repository.create_calls == []


def test_metadata_failure_deletes_uploaded_object_and_preserves_error() -> None:
    service, repository, storage = _service()
    expected = RuntimeError("metadata unavailable")
    repository.create_error = expected

    with pytest.raises(RuntimeError) as raised:
        service.upload_attachment(
            author_type="HUMAN",
            author_id="149844",
            filename="file.txt",
            content_type="text/plain",
            content=b"content",
        )

    assert raised.value is expected
    assert len(storage.deleted) == 1
    assert storage.objects == {}


def test_cleanup_failure_still_preserves_metadata_error() -> None:
    service, repository, storage = _service()
    expected = RuntimeError("metadata unavailable")
    repository.create_error = expected
    storage.delete_result = False

    with pytest.raises(RuntimeError) as raised:
        service.upload_attachment(
            author_type="HUMAN",
            author_id="149844",
            filename="file.txt",
            content_type="text/plain",
            content=b"content",
        )

    assert raised.value is expected
    assert len(storage.deleted) == 1


def test_download_returns_metadata_and_real_bytes() -> None:
    service, repository, storage = _service()
    uploaded = service.upload_attachment(
        author_type="BOT",
        author_id="bot-a",
        filename="notes.txt",
        content_type="text/plain",
        content=b"download me",
    )

    result = service.download_attachment(attachment_id=uploaded.attachment_id)

    assert result.attachment == repository.attachments[uploaded.attachment_id]
    assert result.content == b"download me"
    assert storage.objects[uploaded.object_key] == result.content


def test_download_rejects_missing_metadata() -> None:
    service, _, _ = _service()

    with pytest.raises(NotFound, match="attachment not found"):
        service.download_attachment(attachment_id="attachment_missing")


def test_download_rejects_missing_object_content() -> None:
    service, _, storage = _service()
    uploaded = service.upload_attachment(
        author_type="HUMAN",
        author_id="149844",
        filename="gone.txt",
        content_type=None,
        content=b"temporary",
    )
    storage.objects.clear()

    with pytest.raises(NotFound, match="attachment content not found"):
        service.download_attachment(attachment_id=uploaded.attachment_id)
