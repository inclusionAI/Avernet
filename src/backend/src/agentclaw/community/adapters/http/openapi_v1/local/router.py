"""Public local Bot routes."""
from __future__ import annotations

from typing import Annotated, Any, Mapping

from fastapi import APIRouter, Depends, Header, Path as ApiPath, Query, Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    Page,
    PageParamsDep,
    BotIdPath,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    created,
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.api.local_bot_workflow_service import LocalBotWorkflowServiceProtocol
from agentclaw.community.core.bot_inventory.types import LocalBotCreateCommand
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from .schemas import (
    LocalBot,
    LocalBotAuthPending,
    LocalBotAuthStatus,
    LocalBotCreate,
    LocalDevice,
    LocalLifecycleResult,
    LocalOpenFolder,
    LocalOpenFolderResult,
)

logger = get_logger()

# COSEC: Local Bot workflows require an interactive end user and must not be
# reachable by an application-only principal, matching their REFUSED admission.
router = APIRouter(
    prefix="/openapi/v1/bots",
    tags=["local-bots"],
    dependencies=[Depends(refuse_app_only_caller)],
)

SpaceIdHeader = Annotated[
    str | None,
    Header(
        alias="X-Space-Id",
        description="Business-space context for the operation; omit to use the personal space.",
    ),
]
MachineIdPath = Annotated[
    str,
    ApiPath(description="Device whose filesystem is being listed."),
]


def _to_local_bot(row: Mapping[str, Any]) -> LocalBot:
    ext = row.get("ext") if isinstance(row.get("ext"), Mapping) else {}
    return LocalBot(
        bot_id=str(row.get("bot_id") or ""),
        bot_name=str(row.get("bot_name") or ""),
        bot_desc=str(row.get("bot_desc") or ""),
        engine=str(row.get("active_engine") or row.get("engine_type") or row.get("engine") or ""),
        status=str(row.get("status") or ""),
        owner_entity_id=str(row.get("owner_id") or row.get("entity_id") or ""),
        machine_id=_optional(row.get("machine_id") or ext.get("machine_id")),
        mount_path=_optional(row.get("mount_path") or ext.get("mount_path")),
        avatar_url=_optional(row.get("avatar_url") or ext.get("avatar_url")),
    )


def _optional(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _lifecycle_result(result: Mapping[str, Any], *, bot_id: str) -> LocalLifecycleResult:
    return LocalLifecycleResult(
        bot_id=str(result.get("bot_id") or bot_id),
        status=_optional(result.get("status")),
        result=dict(result),
    )


@router.get("/local/devices", response_model=Envelope[Page[LocalDevice]])
@envelope_errors
async def list_local_devices(
    page: PageParamsDep,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    status: Annotated[str | None, Query(description="Filter devices by their reported status.")] = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[Page[LocalDevice]]:
    """List local devices usable for personal local Bots."""
    total, items = service.list_devices(
        owner_id=owner_id,
        header_space_id=x_space_id,
        page=page.page,
        page_size=page.page_size,
        status=status,
    )
    return page_envelope(
        total,
        [
            LocalDevice(
                machine_id=str(item.get("machine_id") or ""),
                machine_name=str(item.get("machine_name") or ""),
                hostname=str(item.get("hostname") or ""),
                status=str(item.get("status") or ""),
                ip_address=str(item.get("ip_address") or ""),
                last_alive_at=_optional(item.get("last_online_at") or item.get("last_alive_at")),
                created_at=_optional(item.get("created_at")),
            )
            for item in items
        ],
        request,
    )


@router.get("/local/devices/{machine_id}/files", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def list_local_device_files(
    machine_id: MachineIdPath,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    dir: Annotated[str, Query(description="Directory to list on the device.")] = "~/Desktop",
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[dict[str, Any]]:
    """List a local device directory tree for mount-path selection."""
    return envelope(
        service.list_device_files(
            owner_id=owner_id,
            header_space_id=x_space_id,
            machine_id=machine_id,
            directory=dir,
        ),
        request,
    )


@router.post(
    "/local",
    status_code=201,
    response_model=Envelope[LocalBot],
    responses={202: {"model": Envelope[LocalBotAuthPending], "description": "Needs user authorization"}},
)
@envelope_errors
async def create_local_bot(
    body: LocalBotCreate,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[LocalBot] | JSONResponse:
    """Start creating a personal local Bot."""
    result = service.start_create(
        owner_id=owner_id,
        header_space_id=x_space_id,
        command=LocalBotCreateCommand(
            bot_name=body.bot_name,
            bot_desc=body.bot_desc,
            machine_id=body.machine_id,
            mount_path=body.mount_path,
            avatar_url=body.avatar_url,
            engine=body.engine,
        ),
    )
    if result.get("need_authorization"):
        body_out = accepted(
            LocalBotAuthPending(
                bot_id=str(result.get("bot_id") or ""),
                iframe_url=str(result.get("iframe_url") or ""),
                redirect_url=str(result.get("redirect_url") or ""),
            ),
            request,
        )
        return JSONResponse(status_code=202, content=body_out.model_dump())
    return created(_to_local_bot(result), request)


@router.get("/local", response_model=Envelope[Page[LocalBot]])
@envelope_errors
async def list_local_bots(
    page: PageParamsDep,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    keyword: Annotated[str | None, Query(description="Filter local bots whose name contains this text.")] = None,
    engine: Annotated[str | None, Query(description="Filter local bots by engine, matched exactly.")] = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[Page[LocalBot]]:
    """List personal local Bots."""
    total, rows = service.list_bots(
        owner_id=owner_id,
        header_space_id=x_space_id,
        keyword=keyword,
        engine=engine,
        page=page.page,
        page_size=page.page_size,
    )
    return page_envelope(
        total,
        [_to_local_bot(row) for row in rows],
        request,
    )


@router.get("/{bot_id}/local", response_model=Envelope[LocalBot])
@envelope_errors
async def get_local_bot(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[LocalBot]:
    """Get one personal local Bot."""
    return envelope(
        _to_local_bot(
            service.get_bot(owner_id=owner_id, header_space_id=x_space_id, bot_id=bot_id)
        ),
        request,
    )


@router.get("/{bot_id}/local/auth-status", response_model=Envelope[LocalBotAuthStatus])
@envelope_errors
async def local_bot_auth_status(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    bot_name: Annotated[str, Query(description="Display name to apply when authorization completes.")],
    machine_id: Annotated[str, Query(description="Device on which the authorized bot will run.")],
    bot_desc: Annotated[str | None, Query(description="Optional description to apply to the bot.")] = None,
    mount_path: Annotated[str | None, Query(description="Optional workspace path to mount for the bot.")] = None,
    avatar_url: Annotated[str | None, Query(description="Optional avatar URL to apply to the bot.")] = None,
    engine: Annotated[str, Query(description="Engine to run after authorization completes.")] = "openclaw",
    x_space_id: SpaceIdHeader = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[LocalBotAuthStatus] | JSONResponse:
    """Poll Passport authorization and complete local Bot creation once issued."""
    result = service.poll_auth_status(
        owner_id=owner_id,
        header_space_id=x_space_id,
        bot_id=bot_id,
        command=LocalBotCreateCommand(
            bot_name=bot_name,
            bot_desc=bot_desc,
            machine_id=machine_id,
            mount_path=mount_path,
            avatar_url=avatar_url,
            engine=engine,
        ),
    )
    if result.client_error:
        body_out = envelope(
            LocalBotAuthStatus(status=result.status, message=result.message, bot=None),
            request,
            code=400000,
            message=result.message or "Authorization did not complete",
        )
        return JSONResponse(status_code=400, content=body_out.model_dump())
    return envelope(
        LocalBotAuthStatus(
            status=result.status,
            message=result.message,
            bot=_to_local_bot(result.bot) if result.bot is not None else None,
        ),
        request,
    )


@router.post("/{bot_id}/local/restart", response_model=Envelope[LocalLifecycleResult])
@envelope_errors
async def restart_local_bot(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[LocalLifecycleResult]:
    """Restart a personal local Bot."""
    result = service.restart(owner_id=owner_id, header_space_id=x_space_id, bot_id=bot_id)
    return envelope(_lifecycle_result(result, bot_id=bot_id), request)


@router.delete("/{bot_id}/local", response_model=Envelope[Deleted])
@envelope_errors
async def delete_local_bot(
    bot_id: BotIdPath,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[Deleted]:
    """Delete a personal local Bot."""
    service.delete(owner_id=owner_id, header_space_id=x_space_id, bot_id=bot_id)
    return deleted_envelope(request)


@router.post("/{bot_id}/local/open-folder", response_model=Envelope[LocalOpenFolderResult])
@envelope_errors
async def open_local_bot_folder(
    bot_id: BotIdPath,
    body: LocalOpenFolder | None,
    owner_id: UserIdDep,
    request: Request,
    x_space_id: SpaceIdHeader = None,
    service: LocalBotWorkflowServiceProtocol = Injected(LocalBotWorkflowServiceProtocol),
) -> Envelope[LocalOpenFolderResult]:
    """Open a personal local Bot folder on the host device."""
    result = service.open_folder(
        owner_id=owner_id,
        header_space_id=x_space_id,
        bot_id=bot_id,
        folder_path=body.folder_path if body else None,
    )
    return envelope(LocalOpenFolderResult(bot_id=str(result.get("bot_id") or bot_id)), request)
