from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi import Request, UploadFile
from starlette.datastructures import Headers

from agentclaw.community.adapters.http.openapi_v1.task.bbs.router import (
    download_attachment,
    upload_attachment,
)
from agentclaw.community.core.task.forum.models import (
    ForumAttachmentContent,
    ForumAttachmentRecord,
)


def _request(method: str, path: str) -> Request:
    request = Request(scope={"type": "http", "method": method, "path": path})
    request.state.trace_id = "trace-bbs-attachment"
    return request


def _record(filename: str = "设计 文档.pdf") -> ForumAttachmentRecord:
    return ForumAttachmentRecord(
        attachment_id="attachment_1",
        author_type="HUMAN",
        author_id="149844",
        topic_id=None,
        filename=filename,
        content_type="application/pdf",
        size_bytes=9,
        object_key="bbs/attachments/scope/attachment_1",
        created_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_upload_reads_real_multipart_file_and_returns_safe_metadata() -> None:
    class Service:
        def upload_attachment(self, **kwargs):
            assert kwargs == {
                "author_type": "HUMAN",
                "author_id": "149844",
                "filename": "design.pdf",
                "content_type": "application/pdf",
                "content": b"pdf-bytes",
            }
            return _record("design.pdf")

    file = UploadFile(
        file=BytesIO(b"pdf-bytes"),
        filename="design.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )

    result = await upload_attachment(
        request=_request(
            "POST", "/openapi/v1/collaboration/tasks/bbs/attachments"
        ),
        author_type="HUMAN",
        author_id="149844",
        file=file,
        service=Service(),
    )

    assert result.code == 201000
    assert result.data is not None
    assert result.data.model_dump() == {
        "attachment_id": "attachment_1",
        "filename": "design.pdf",
        "content_type": "application/pdf",
        "size_bytes": 9,
    }
    assert file.file.closed


@pytest.mark.asyncio
async def test_download_returns_real_bytes_mime_and_utf8_filename() -> None:
    class Service:
        def download_attachment(self, **kwargs):
            assert kwargs == {"attachment_id": "attachment_1"}
            return ForumAttachmentContent(
                attachment=_record(), content=b"pdf-bytes"
            )

    response = await download_attachment(
        attachment_id="attachment_1",
        request=_request(
            "GET",
            "/openapi/v1/collaboration/tasks/bbs/attachments/attachment_1/content",
        ),
        service=Service(),
    )

    assert response.body == b"pdf-bytes"
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''%E8%AE%BE%E8%AE%A1%20%E6%96%87%E6%A1%A3.pdf"
    )
