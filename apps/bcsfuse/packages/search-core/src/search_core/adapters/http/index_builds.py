"""Authenticated creation/retry and status; activation belongs to startup config."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from search_core.adapters.http.routes import Authenticator
from search_core.api.index_builds import IndexBuildConflict, IndexBuildManagement
from search_core.api.models import VerifiedIdentity

BuildId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embedding_model: str = Field(min_length=1, max_length=128)
    embedding_dimension: int = Field(strict=True, ge=1, le=8192)
    filter_fields: tuple[str, ...] | None = None


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


def response(row):
    return BuildStatus(
        index_key=row.config.index_key,
        embedding_model=row.config.embedding_model,
        embedding_dimension=row.config.embedding_dimension,
        conversion_version=row.config.conversion_version,
        filter_fields=row.config.filter_fields,
        status=row.status,
        completed_count=row.completed_count if row.status != "configured" else None,
        total_count=row.total_count if row.status != "configured" else None,
        error_code=row.error_code,
    )


def create_index_build_router(
    service: IndexBuildManagement, authorize_operator: Authenticator
):
    router = APIRouter(prefix="/api/v2/index-builds", tags=["index-builds"])

    @router.put(
        "/{build_id}",
        status_code=202,
        response_model=BuildStatus,
        response_model_exclude_none=True,
    )
    async def create(
        build_id: BuildId,
        body: BuildRequest,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            row = await asyncio.to_thread(
                service.create,
                build_id,
                body.embedding_model,
                body.embedding_dimension,
                filter_fields=body.filter_fields,
            )
            return response(row)
        except IndexBuildConflict as error:
            raise HTTPException(
                409, "build ID already has different configuration"
            ) from error
        except ValueError as error:
            raise HTTPException(400, "invalid build configuration") from error
        except Exception as error:
            raise HTTPException(503, "index build store unavailable") from error

    @router.get(
        "/{build_id}", response_model=BuildStatus, response_model_exclude_none=True
    )
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
