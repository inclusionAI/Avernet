"""Starlette compatibility adapter for bounded Space Skill multipart input."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from importlib.metadata import version
from typing import Any

from fastapi import HTTPException, Request
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.responses import mapped_error_response
from agentclaw.community.core.skill_center.skill_package import (
    MAX_EXPANDED_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PATH_LENGTH,
    SkillPackageTooLargeError,
)


MAX_MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024
MAX_DRAFT_JSON_OVERHEAD_BYTES = 64 * 1024
_SUPPORTED_STARLETTE_SERIES = (1, 3)


def _assert_starlette_compatibility() -> None:
    try:
        installed = tuple(int(part) for part in version("starlette").split(".")[:2])
    except ValueError as exc:  # pragma: no cover - invalid package metadata
        raise RuntimeError(
            "cannot determine Starlette multipart compatibility"
        ) from exc
    if installed != _SUPPORTED_STARLETTE_SERIES:
        raise RuntimeError(
            "Space Skill multipart limits require compatibility review for "
            f"Starlette {version('starlette')}"
        )


_assert_starlette_compatibility()


class _SkillUploadTooLarge(MultiPartException):
    """Internal parser signal translated to the public package error."""


class _BoundedSkillMultipartParser(MultiPartParser):
    """Enforce file limits before Starlette writes multipart data to disk.

    Starlette 1.3 exposes no public per-file receive limit. This adapter keeps
    the necessary private parser access isolated and fails closed when the
    pinned framework series changes.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._total_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            received = end - start
            self._current_file_bytes += received
            self._total_file_bytes += received
            if self._current_file_bytes > MAX_FILE_BYTES:
                raise _SkillUploadTooLarge("Space Skill file exceeds its size limit")
            if self._total_file_bytes > MAX_EXPANDED_BYTES:
                raise _SkillUploadTooLarge(
                    "Space Skill folder exceeds its aggregate size limit"
                )
        super().on_part_data(data, start, end)

    def on_headers_finished(self) -> None:
        try:
            super().on_headers_finished()
        except MultiPartException as exc:
            if self._current_files > self.max_files:
                raise _SkillUploadTooLarge(
                    "Space Skill folder exceeds its file-count limit"
                ) from exc
            raise


def _file_paths_field_limit() -> int:
    # JSON may escape each Unicode code point as ``\uXXXX``. Delimiters and
    # quotes add a small fixed amount per path.
    return MAX_FILES * (MAX_PATH_LENGTH * 6 + 4) + 2


def _multipart_body_limit() -> int:
    return MAX_EXPANDED_BYTES + _file_paths_field_limit() + MAX_MULTIPART_OVERHEAD_BYTES


def _draft_json_body_limit() -> int:
    # A JSON string may encode one content byte as a six-byte ``\uXXXX`` escape.
    return MAX_FILE_BYTES * 6 + MAX_DRAFT_JSON_OVERHEAD_BYTES


async def _bounded_request_stream(
    request: Request, *, limit: int
) -> AsyncIterator[bytes]:
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise _SkillUploadTooLarge(
                "Space Skill multipart body exceeds its size limit"
            )
        yield chunk


class SpaceSkillPublicAPIRoute(PublicAPIRoute):
    """Public route with receive-time bounds for its sole multipart command."""

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        methods = set(kwargs.get("methods") or ("GET",))
        self._limit_draft_file_json = "PUT" in methods and path.endswith(
            "/draft/files/{path:path}"
        )
        super().__init__(path, endpoint, **kwargs)

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def bounded_multipart_handler(request: Request) -> Response:
            if self._limit_draft_file_json:
                body_limit = _draft_json_body_limit()
                declared_length = request.headers.get("content-length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > body_limit:
                            response = mapped_error_response(
                                SkillPackageTooLargeError(), request
                            )
                            assert response is not None
                            return response
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                received = 0
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > body_limit:
                        response = mapped_error_response(
                            SkillPackageTooLargeError(), request
                        )
                        assert response is not None
                        return response
                    chunks.append(chunk)
                # Preserve FastAPI's generated JSON DTO and validation path by
                # seeding the Request body cache before its handler decodes it.
                request._body = b"".join(chunks)
            if request.headers.get("content-type", "").startswith(
                "multipart/form-data"
            ):
                body_limit = _multipart_body_limit()
                declared_length = request.headers.get("content-length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > body_limit:
                            response = mapped_error_response(
                                SkillPackageTooLargeError(), request
                            )
                            assert response is not None
                            return response
                    except ValueError:
                        pass
                parser = _BoundedSkillMultipartParser(
                    request.headers,
                    _bounded_request_stream(request, limit=body_limit),
                    max_files=MAX_FILES,
                    max_fields=1,
                    max_part_size=_file_paths_field_limit(),
                )
                try:
                    # FastAPI calls Request.form() before dependencies. Seeding
                    # its cache is the only way to preserve generated Form DTOs
                    # while using the bounded Starlette parser.
                    request._form = await parser.parse()
                except _SkillUploadTooLarge:
                    response = mapped_error_response(
                        SkillPackageTooLargeError(), request
                    )
                    assert response is not None
                    return response
                except MultiPartException as exc:
                    raise HTTPException(status_code=400, detail=exc.message) from exc
            return await original(request)

        return bounded_multipart_handler


__all__ = ["SpaceSkillPublicAPIRoute"]
