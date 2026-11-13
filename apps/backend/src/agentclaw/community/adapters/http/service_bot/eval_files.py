"""Upload-file validation shared by the internal and OpenAPI eval routers."""

from __future__ import annotations

from typing import Sequence

from fastapi import UploadFile

from agentclaw.community.core.service_bot.errors import ServicePublicationConflictError
from agentclaw.community.plugin_api.service_bot_eval import ServiceBotEvalFile

MAX_FILES = 100
MAX_SINGLE_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 128 * 1024 * 1024


async def read_upload_files(
    sample_files: Sequence[UploadFile],
    report_files: Sequence[UploadFile],
) -> list[ServiceBotEvalFile]:
    """Read sample/report uploads into the protocol's bounded file records."""
    samples = await _files(
        sample_files,
        file_kind="sample",
        empty_message="评测样本材料不能为空",
    )
    reports = await _files(
        report_files,
        file_kind="report",
        empty_message="评测报告材料不能为空",
    )
    values = [*samples, *reports]
    if len(values) > MAX_FILES:
        raise ServicePublicationConflictError("样本和报告合计最多上传100个文件")
    if sum(len(file.content) for file in values) > MAX_TOTAL_FILE_BYTES:
        raise ServicePublicationConflictError("样本和报告合计不能超过128MiB")
    return values


async def _files(
    value: Sequence[UploadFile],
    *,
    file_kind: str,
    empty_message: str,
) -> list[ServiceBotEvalFile]:
    value = list(value)
    if not value:
        raise ServicePublicationConflictError(empty_message)
    files: list[ServiceBotEvalFile] = []
    for item in value:
        filename = item.filename or ""
        has_drive_letter = len(filename) >= 2 and filename[1] == ":" and filename[0].isalpha()
        if (
            not filename
            or "/" in filename
            or "\\" in filename
            or has_drive_letter
            or any(ord(ch) < 32 for ch in filename)
        ):
            raise ServicePublicationConflictError("评测材料文件名不合法")
        content = await item.read()
        if len(content) > MAX_SINGLE_FILE_BYTES:
            raise ServicePublicationConflictError("单个评测材料不能超过64MiB")
        files.append(
            ServiceBotEvalFile(
                filename=filename,
                content=content,
                content_type=item.content_type or "application/octet-stream",
                file_kind=file_kind,  # type: ignore[arg-type]
            )
        )
    return files
