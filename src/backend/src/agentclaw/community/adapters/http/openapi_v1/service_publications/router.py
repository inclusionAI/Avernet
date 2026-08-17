"""Public service-Bot lifecycle and collaborative edit-lock endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    ErrorEnvelope,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    deleted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.service_publication_facade import (
    ServicePublicationFacadeProtocol,
)
from agentclaw.community.di import Injected

from .schemas import (
    EditLock,
    EditLockRelease,
    LifecycleAdvanceRequest,
    LifecycleRestartRequest,
    ServiceBotConfig,
    ServiceBotConfigUpdate,
    ServicePublication,
    ServicePublicationList,
    ServicePublicationOperation,
)


router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/lifecycle")
edit_lock_router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/edit-lock")


def _lock_payload(info: Any, *, acquired: bool | None = None) -> EditLock:
    lock = getattr(info, "lock", None)
    has_collaborators = bool(getattr(info, "has_collaborators", False))
    return EditLock(
        locked=lock is not None,
        acquired=acquired,
        holder_user_id=getattr(lock, "holder_user_id", None),
        holder_name=getattr(info, "holder_name", None),
        has_collaborators=has_collaborators,
        is_owner_holder=bool(getattr(info, "is_owner", False)),
        need_lock=has_collaborators,
    )


@router.post("/upgrade", response_model=Envelope[ServicePublication])
@envelope_errors
async def upgrade_to_service(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServicePublication]:
    """Irreversibly upgrade an eligible personal cloud Bot to a service Bot."""
    result = facade.convert_to_service(bot_id, actor_id=actor_id, owner_id=owner_id)
    return envelope(ServicePublication.model_validate(result), request)


@router.get("", response_model=Envelope[ServicePublicationList])
@envelope_errors
async def get_lifecycle(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServicePublicationList]:
    """Return the at-most-two visible lifecycle cards for a service Bot."""
    result = facade.list_publications(bot_id, actor_id=actor_id, owner_id=owner_id)
    return envelope(ServicePublicationList.model_validate(result), request)


@router.get("/approval", response_model=Envelope[ServiceBotConfig])
@envelope_errors
async def get_approval_config(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServiceBotConfig]:
    result = facade.get_service_config(bot_id, actor_id=actor_id, owner_id=owner_id)
    return envelope(ServiceBotConfig.model_validate(result), request)


@router.put("/approval", response_model=Envelope[ServiceBotConfig])
@envelope_errors
async def update_approval_config(
    bot_id: BotIdPath,
    body: ServiceBotConfigUpdate,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServiceBotConfig]:
    result = facade.update_service_config(
        bot_id,
        actor_id=actor_id,
        owner_id=owner_id,
        should_approval=body.should_approval,
    )
    return envelope(ServiceBotConfig.model_validate(result), request)


def _action_route(
    path: str,
    name: str,
    *,
    responses: dict[int | str, dict[str, object]] | None = None,
):
    def decorator(handler):
        return router.post(
            path,
            status_code=202,
            response_model=Envelope[ServicePublicationOperation],
            name=name,
            responses=responses,
        )(envelope_errors(handler))

    return decorator


@_action_route(
    "/advance",
    "advance_service_lifecycle",
    responses={
        423: {
            "model": ErrorEnvelope,
            "description": "A collaborator must hold the Bot edit lock before staging.",
            **error_example(423, "Edit lock required"),
        }
    },
)
async def advance_lifecycle(
    bot_id: BotIdPath,
    body: LifecycleAdvanceRequest,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServicePublicationOperation]:
    result = await facade.advance(
        bot_id,
        body.stage,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return accepted(ServicePublicationOperation.model_validate(result), request)


@_action_route("/restart", "restart_service_lifecycle")
async def restart_lifecycle(
    bot_id: BotIdPath,
    body: LifecycleRestartRequest,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServicePublicationOperation]:
    result = facade.restart(
        bot_id,
        body.stage,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return accepted(ServicePublicationOperation.model_validate(result), request)


@_action_route("/cancel-staging", "cancel_service_staging")
async def cancel_staging(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServicePublicationOperation]:
    result = await facade.cancel_staging(bot_id, actor_id=actor_id, owner_id=owner_id)
    return accepted(ServicePublicationOperation.model_validate(result), request)


@_action_route("/offline", "offline_service_lifecycle")
async def offline_lifecycle(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServicePublicationOperation]:
    result = await facade.offline(bot_id, actor_id=actor_id, owner_id=owner_id)
    return accepted(ServicePublicationOperation.model_validate(result), request)


@_action_route("/retry", "retry_service_lifecycle")
async def retry_lifecycle(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ServicePublicationOperation]:
    result = await facade.retry(bot_id, actor_id=actor_id, owner_id=owner_id)
    return accepted(ServicePublicationOperation.model_validate(result), request)


@router.delete("", response_model=Envelope[Deleted])
@envelope_errors
async def delete_initial_draft(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[Deleted]:
    facade.delete_initial_draft(bot_id, actor_id=actor_id, owner_id=owner_id)
    return deleted(request)


@edit_lock_router.get("", response_model=Envelope[EditLock])
@envelope_errors
async def get_edit_lock(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[EditLock]:
    info = facade.get_lock(bot_id, actor_id=actor_id, owner_id=owner_id)
    return envelope(_lock_payload(info), request)


@edit_lock_router.post("", response_model=Envelope[EditLock])
@envelope_errors
async def acquire_edit_lock(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[EditLock]:
    lock = facade.acquire_lock(bot_id, actor_id=actor_id, owner_id=owner_id)
    info = facade.get_lock(bot_id, actor_id=actor_id, owner_id=owner_id)
    return envelope(_lock_payload(info, acquired=lock is not None), request)


@edit_lock_router.delete("", response_model=Envelope[EditLockRelease])
@envelope_errors
async def release_edit_lock(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[EditLockRelease]:
    released = facade.release_lock(bot_id, actor_id=actor_id, owner_id=owner_id)
    return envelope(EditLockRelease(released=released), request)


@edit_lock_router.post("/steal", response_model=Envelope[EditLock])
@envelope_errors
async def steal_edit_lock(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[EditLock]:
    facade.steal_lock(bot_id, actor_id=actor_id, owner_id=owner_id)
    info = facade.get_lock(bot_id, actor_id=actor_id, owner_id=owner_id)
    return envelope(_lock_payload(info, acquired=True), request)
