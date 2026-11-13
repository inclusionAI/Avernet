"""Authenticated creation/retry and status; activation belongs to startup config."""

import asyncio
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from search_core.adapters.http.routes import Authenticator
from search_core.api.index_builds import (
    IndexBuildConflict,
    IndexBuildManagement,
    MappedIndexBuildManagement,
)
from search_core.api.models import VerifiedIdentity
from search_core.config.document_mapping import DocumentMapping, MappingError

BuildId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embedding_model: str = Field(min_length=1, max_length=128)
    embedding_dimension: int = Field(strict=True, ge=1, le=8192)
    filter_fields: tuple[str, ...] | None = None


class MappedBuildRequest(BuildRequest):
    document_mapping: DocumentMapping | None = None
    validation_card_ids: list[str] = Field(default_factory=list, max_length=5)


class BuildStatus(BaseModel):
    index_key: str
    embedding_model: str
    embedding_dimension: int
    conversion_version: str
    filter_fields: tuple[str, ...]
    status: str
    completed_count: int | None = Field(
        default=None, description="本次构建已完成的未删除 Card 数量；不是实时索引数量"
    )
    total_count: int | None = Field(
        default=None, description="本次构建的未删除 Card 总数；删除记录仍处理但不计数"
    )
    error_code: str


class MappedBuildStatus(BuildStatus):
    document_mapping: dict | None = None
    warnings: list[dict] | None = None


def response(row):
    return MappedBuildStatus(
        index_key=row.config.index_key,
        embedding_model=row.config.embedding_model,
        embedding_dimension=row.config.embedding_dimension,
        conversion_version=row.config.conversion_version,
        filter_fields=row.config.filter_fields,
        status=row.status,
        completed_count=row.completed_count if row.status != "configured" else None,
        total_count=row.total_count if row.status != "configured" else None,
        error_code=row.error_code,
        document_mapping=row.config.document_mapping.stored()
        if row.config.document_mapping
        else None,
        warnings=list(row.mapping_warnings) or None,
    )


def create_index_build_router(
    service: IndexBuildManagement | MappedIndexBuildManagement,
    authorize_operator: Authenticator,
    *,
    trigger: Callable[[str], None] | None = None,
    allow_document_mapping: bool = False,
):
    router = APIRouter(prefix="/api/v2/index-builds", tags=["index-builds"])

    Request = MappedBuildRequest if allow_document_mapping else BuildRequest
    Status = MappedBuildStatus if allow_document_mapping else BuildStatus

    @router.put(
        "/{build_id}",
        status_code=202,
        response_model=Status,
        response_model_exclude_none=True,
    )
    async def create(
        build_id: BuildId,
        body: Request,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        options = {}
        if {"document_mapping", "validation_card_ids"} & body.model_fields_set:
            if not allow_document_mapping:
                raise HTTPException(
                    422, "document_mapping is not enabled for this application"
                )
            if "document_mapping" in body.model_fields_set:
                options["document_mapping"] = (
                    body.document_mapping.stored() if body.document_mapping else None
                )
            options["validation_card_ids"] = body.validation_card_ids
        try:
            row = await asyncio.to_thread(
                service.create,
                build_id,
                body.embedding_model,
                body.embedding_dimension,
                filter_fields=body.filter_fields,
                **options,
            )
            if trigger is not None and row.status in {"pending", "running"}:
                trigger(build_id)
            return response(row)
        except MappingError as error:
            raise HTTPException(422, [error.detail()]) from error
        except IndexBuildConflict as error:
            raise HTTPException(
                409, "build ID already has different configuration"
            ) from error
        except ValueError as error:
            raise HTTPException(400, "invalid build configuration") from error
        except Exception as error:
            raise HTTPException(503, "index build store unavailable") from error

    @router.get("/{build_id}", response_model=Status, response_model_exclude_none=True)
    async def status(
        build_id: BuildId,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            return response(await asyncio.to_thread(service.status, build_id))
        except KeyError as error:
            raise HTTPException(404, "index build not found") from error
        except Exception as error:
            raise HTTPException(503, "index build status unavailable") from error

    return router
