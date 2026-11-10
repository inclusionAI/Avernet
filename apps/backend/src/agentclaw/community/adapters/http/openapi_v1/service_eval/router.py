"""Public OpenAPI v1 service-Bot evaluation API.

The bot-first addresses remain in the backend module. They delegate to
:protocol:`ServiceBotEvalServiceProtocol` and return the standard OpenAPI v1
envelope. Query identity follows the public-surface convention: ``user_id``
names the caller (injected by the frontend HTTP client), and ``owner_id`` is
the addressed-owner alias accepted by ``OwnerIdDep``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.service_bot.eval_files import read_upload_files
from .schemas import ServiceBotEvalSubmissionForm
from agentclaw.community.api.service_bot_eval import ServiceBotEvalServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/eval",
    tags=["service-bot-evaluation"],
    route_class=PublicAPIRoute,
)

BotVersionPath = Annotated[str, Path(min_length=1, description="服务Bot版本号")]
SubmissionIdPath = Annotated[str, Path(min_length=1, description="评测提交ID")]


@router.get(
    "/versions/{bot_version}",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def detail(
    bot_id: BotIdPath,
    bot_version: BotVersionPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """Read a service-Bot version's evaluation-mode visibility and certification state."""
    result = await service.detail(
        bot_id,
        bot_version,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return envelope(result, request)


@router.post(
    "/versions/{bot_version}/simple-cert/precheck",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def precheck(
    bot_id: BotIdPath,
    bot_version: BotVersionPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """Run the simple-cert precheck before draft-to-staging publication advances."""
    result = await service.precheck(
        bot_id,
        bot_version,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return envelope(result, request)


@router.post(
    "/versions/{bot_version}/simple-cert/submissions",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def submit(
    bot_id: BotIdPath,
    bot_version: BotVersionPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    upload: Annotated[
        ServiceBotEvalSubmissionForm,
        Form(media_type="multipart/form-data"),
    ],
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """Upload the sample and report archives used for simple certification."""
    files = await read_upload_files(upload.sample_files, upload.report_files)
    result = await service.submit(
        bot_id,
        bot_version,
        actor_id=actor_id,
        owner_id=owner_id,
        files=files,
        trigger_source=upload.trigger_source,
    )
    return envelope(result, request, message="评测数据已提交，等待认证结果")


@router.get(
    "/submissions/{submission_id}",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def get_submission(
    bot_id: BotIdPath,
    submission_id: SubmissionIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """Read one pollable simple-cert submission task."""
    result = await service.get_submission(
        bot_id,
        submission_id,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return envelope(result, request)


@router.get(
    "/versions/{bot_version}/history",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def history(
    bot_id: BotIdPath,
    bot_version: BotVersionPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    page_no: Annotated[int, Query(ge=1, le=100, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 10,
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """Read completed evaluation records and report links for one Bot version."""
    result = await service.history(
        bot_id,
        bot_version,
        actor_id=actor_id,
        owner_id=owner_id,
        page_no=page_no,
        page_size=page_size,
    )
    return envelope(result, request)


@router.post(
    "/versions/{bot_version}/simple-cert/sync",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def sync(
    bot_id: BotIdPath,
    bot_version: BotVersionPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """Refresh the certification decision cached by the evaluation service."""
    result = await service.sync(
        bot_id,
        bot_version,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return envelope(result, request)


__all__ = ["router"]
