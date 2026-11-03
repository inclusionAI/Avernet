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


class BuildStatus(BaseModel):
    build_id: str
    index_key: str
    embedding_model: str
    embedding_dimension: int
    status: str
    completed_count: int
    total_count: int
    error_code: str


def response(row):
    return BuildStatus(
        build_id=row.config.build_id,
        index_key=row.config.index_key,
        embedding_model=row.config.embedding_model,
        embedding_dimension=row.config.embedding_dimension,
        status=row.status,
        completed_count=row.completed_count,
        total_count=row.total_count,
        error_code=row.error_code,
    )


def create_index_build_router(
    service: IndexBuildManagement, authorize_operator: Authenticator
):
    router = APIRouter(prefix="/api/v1/index-builds", tags=["index-builds"])

    @router.put("/{build_id}", status_code=202, response_model=BuildStatus)
    async def create(
        build_id: BuildId,
        body: BuildRequest,
        identity: Annotated[VerifiedIdentity, Depends(authorize_operator)],
    ):
        try:
            row = await asyncio.to_thread(
                service.create, build_id, body.embedding_model, body.embedding_dimension
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

    @router.get("/{build_id}", response_model=BuildStatus)
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
