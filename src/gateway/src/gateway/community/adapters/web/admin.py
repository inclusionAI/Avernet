"""Admin endpoints — issue access keys and register apps.

NOT FOR PRODUCTION: these endpoints are **unauthenticated** (single-box / dev
convenience). A production deployment must gate them behind an admin credential
(not in scope for this workstream).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gateway.community.logger import get_logger

logger = get_logger("admin")

router = APIRouter(tags=["admin"])


class AccessKeyRequest(BaseModel):
    access_key: str
    tenant: str
    expire_at: datetime
    creator: str = Field(min_length=1)


class AppRequest(BaseModel):
    app_name: str
    owners: str
    app_type: str = "UNKNOWN"
    tenant: str
    creator: str = Field(min_length=1)
    status: str = "ACTIVE"
    env: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


def _error(status: int, subcode: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": status * 1000 + subcode, "message": message, "data": None},
    )


@router.post("/access-keys", status_code=201)
async def issue_access_key(payload: AccessKeyRequest, request: Request) -> JSONResponse:
    issuer = request.app.state.access_key_issuer
    try:
        issued = await issuer.issue(
            payload.access_key,
            payload.tenant,
            payload.expire_at,
            creator=payload.creator,
        )
    except Exception:
        logger.exception("access key issuance failed")
        return _error(500, 1, "access key issuance failed")
    return JSONResponse(
        status_code=201,
        content={
            "access_key": issued.access_key,
            "tenant": issued.tenant,
            "expire_at": issued.expire_at.isoformat(),
            "token": issued.token,
        },
    )


@router.post("/apps", status_code=201)
async def register_app(payload: AppRequest, request: Request) -> JSONResponse:
    registrar = request.app.state.app_registrar
    try:
        issued = await registrar.register(
            payload.app_name,
            payload.owners,
            payload.app_type,
            payload.tenant,
            creator=payload.creator,
            status=payload.status,
            env=payload.env,
            config=payload.config,
        )
    except Exception:
        logger.exception("app registration failed")
        return _error(500, 1, "app registration failed")
    return JSONResponse(
        status_code=201,
        content={
            "id": issued.id,
            "app_name": issued.app_name,
            "owners": issued.owners,
            "app_type": issued.app_type,
            "tenant": issued.tenant,
            "status": payload.status,
            "env": payload.env,
            "token": issued.token,
        },
    )
