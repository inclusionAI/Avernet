"""OpenClaw HTTP management router — neutral delivery, deps via Injected.

Replaces corp ``openclaw/router.py``'s HTTP endpoints (test-connection /
disconnect / config). The WS proxy ``/client`` stays corp-only for now (后续).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from engine.community.di import Injected
from engine.community.plugin_api.openclaw.gateway_service import OpenClawGatewayService

router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])


@router.get("/test-connection")
async def test_connection(
    svc: OpenClawGatewayService = Injected(OpenClawGatewayService),
):
    return await svc.test_connection()


@router.post("/disconnect")
async def disconnect(
    svc: OpenClawGatewayService = Injected(OpenClawGatewayService),
):
    try:
        await svc.disconnect()
        return {"success": True, "message": "Disconnected from OpenClaw Gateway"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_openclaw_config(
    svc: OpenClawGatewayService = Injected(OpenClawGatewayService),
):
    return svc.get_config()
