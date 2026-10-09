from __future__ import annotations

from fastapi import APIRouter

from engine.community.di import Injected
from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from pydantic import BaseModel

class ZeroCheckSwitchRequest(BaseModel):
    enabled: bool


class ZeroCheckSwitchResponse(BaseModel):
    enabled: bool


router = APIRouter(prefix="/api/openclaw/zero-check", tags=["openclaw"])


@router.get("", response_model=ZeroCheckSwitchResponse)
async def get_zero_check_switch(
    auth_gate_service: AuthGateService = Injected(AuthGateService),
) -> ZeroCheckSwitchResponse:
    return ZeroCheckSwitchResponse(enabled=await auth_gate_service.get_switch())


@router.post("", response_model=ZeroCheckSwitchResponse)
async def set_zero_check_switch(
    payload: ZeroCheckSwitchRequest,
    auth_gate_service: AuthGateService = Injected(AuthGateService),
) -> ZeroCheckSwitchResponse:
    await auth_gate_service.set_switch(payload.enabled)
    return ZeroCheckSwitchResponse(enabled=await auth_gate_service.get_switch())
