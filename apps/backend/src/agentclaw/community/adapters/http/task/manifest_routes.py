"""Authenticated task asset configuration and discovery; the service owns configuration policy."""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope
from agentclaw.community.adapters.http.openapi_v1.response_envelope import error_response
from agentclaw.community.api.task.task_manifest_service import TaskPanelManifestServiceProtocol
from agentclaw.community.core.errors import InternalError
from agentclaw.community.di import Injected
from agentclaw.community.utils.env_utils import get_current_env


class PanelBundleDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    url: str


class TaskPanelManifestDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    schema_version: int
    env: str
    bundles: list[PanelBundleDTO]


router = APIRouter()


@router.get("/manifest", response_model=Envelope[TaskPanelManifestDTO])
async def get_task_manifest(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskPanelManifestServiceProtocol = Injected(TaskPanelManifestServiceProtocol),
):
    del user
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await run_in_threadpool(service.get_manifest, env=get_current_env())
    except InternalError:
        failure = error_response(503, "Task panel configuration is missing or unavailable", request)
        failure.headers["Cache-Control"] = "no-store"
        return failure
    return envelope(TaskPanelManifestDTO.model_validate(result), request)


@router.post("/manifest", response_model=Envelope[TaskPanelManifestDTO])
async def set_task_manifest(
    value: dict[str, object],
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(require_operator),
    service: TaskPanelManifestServiceProtocol = Injected(TaskPanelManifestServiceProtocol),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await run_in_threadpool(
            service.set_manifest, value=value, env=get_current_env(),
            operator=user.operatorName or user.staffId,
        )
    except ValueError:
        failure = error_response(422, "Invalid task panel configuration", request)
        failure.headers["Cache-Control"] = "no-store"
        return failure
    except InternalError:
        failure = error_response(503, "Task panel configuration could not be saved", request)
        failure.headers["Cache-Control"] = "no-store"
        return failure
    return envelope(TaskPanelManifestDTO.model_validate(result), request)
