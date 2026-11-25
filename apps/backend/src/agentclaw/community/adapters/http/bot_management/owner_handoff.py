"""Authenticated, feature-gated handoff commands; no implicit environment input."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.owner_handoff_service import OwnerHandoffServiceProtocol
from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.di import Injected

router = APIRouter(prefix="/owner-handoffs", tags=["bot-owner-handoff"])


class CreateHandoff(BaseModel):
    bot_pk: int = Field(gt=0)
    new_owner_id: str = Field(min_length=1, max_length=1024)
    request_key: str = Field(min_length=1, max_length=64)


def _call(method, **kwargs):
    try:
        return {"success": True, "data": method(**kwargs)}
    except HandoffError as exc:
        code = str(exc)
        status = 403 if code in {"OWNER_REQUIRED", "HANDOFF_ACCESS_DENIED"} else 409
        if code == "OWNER_HANDOFF_DISABLED":
            status = 404
        raise HTTPException(status_code=status, detail=code) from exc


@router.post("")
def create_handoff(
    body: CreateHandoff,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OwnerHandoffServiceProtocol = Injected(OwnerHandoffServiceProtocol),
):
    return _call(service.create, **body.model_dump(), actor_id=user.staffId)


@router.get("/{operation_id}")
def get_handoff(
    operation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OwnerHandoffServiceProtocol = Injected(OwnerHandoffServiceProtocol),
):
    return _call(service.get, operation_id=operation_id, actor_id=user.staffId)


@router.post("/{operation_id}/retry")
def retry_handoff(
    operation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OwnerHandoffServiceProtocol = Injected(OwnerHandoffServiceProtocol),
):
    return _call(service.retry, operation_id=operation_id, actor_id=user.staffId)


@router.post("/{operation_id}/cancel")
def cancel_handoff(
    operation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OwnerHandoffServiceProtocol = Injected(OwnerHandoffServiceProtocol),
):
    return _call(service.cancel, operation_id=operation_id, actor_id=user.staffId)


@router.get("/{operation_id}/authorization")
def handoff_authorization(
    operation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: OwnerHandoffServiceProtocol = Injected(OwnerHandoffServiceProtocol),
):
    return _call(
        service.authorization, operation_id=operation_id, actor_id=user.staffId
    )
