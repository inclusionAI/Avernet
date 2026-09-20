"""Authenticated internal endpoint for ECB resource-context resolution."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.core.tc_file_upload_integrations.resource_context import (
    TcResourceContextService,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.config import TcFileServiceToken

router = APIRouter(
    prefix="/api/internal/tc-resource-context",
    tags=["tc-file-upload-integrations-internal"],
)


class TcResourceContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    res_id: str = Field(min_length=1, max_length=128)


def _verify_bearer(authorization: str | None, configured: str) -> None:
    if not configured or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    presented = authorization[7:]
    if not presented or not hmac.compare_digest(presented, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


@router.post("")
def resolve_tc_resource_context(
    body: TcResourceContextRequest,
    authorization: str | None = Header(default=None),
    service: TcResourceContextService = Injected(TcResourceContextService),
    auth_config: TcFileServiceToken = Injected(TcFileServiceToken),
) -> dict[str, object]:
    _verify_bearer(authorization, auth_config.value)
    try:
        snapshot = service.resolve(body.res_id)
    except ValueError as exc:
        code = str(exc)
        if code == "resource_not_found":
            raise HTTPException(status_code=404, detail=code) from exc
        if code in {
            "resource_context_incomplete",
            "resource_context_unsupported_scope",
        }:
            raise HTTPException(status_code=409, detail=code) from exc
        raise HTTPException(status_code=400, detail=code) from exc
    return {"code": 0, "data": snapshot.as_payload()}
