"""Narrow M2M read/disable API; existing CronRelayService owns runtime fan-out."""

from __future__ import annotations

from hashlib import sha256
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.cron.internal_auth import verify_cron_guard_token
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.di import Injected


router = APIRouter(
    prefix="/api/internal/cron-guard",
    tags=["cron-guard-internal"],
    dependencies=[Depends(verify_cron_guard_token)],
)
logger = logging.getLogger(__name__)


class DisableCronRequest(BaseModel):
    target_user_id: str = Field(min_length=1, max_length=128)
    target_bot_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    improvement_id: int = Field(gt=0)
    operator: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


def _task_id(item: dict[str, Any]) -> str:
    task_id = item.get("task_id") or item.get("id")
    return str(task_id) if task_id is not None else ""


def _fingerprint(item: dict[str, Any]) -> str:
    # Exact configuration compare without returning the possibly sensitive prompt.
    selected = {key: item.get(key) for key in ("name", "schedule", "payload", "command")}
    encoded = json.dumps(selected, sort_keys=True, ensure_ascii=False, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


async def _list_target(
    service: CronRelayServiceProtocol,
    user_id: str,
    bot_id: str,
) -> list[dict[str, Any]]:
    try:
        result = await service.list_all_crons(
            user_id=user_id, nick_name=user_id, bot_id=bot_id,
            runtime_stage="online",
        )
    except CronRelayError as error:
        raise HTTPException(status_code=error.error_code, detail=str(error)) from error
    if not result.get("success") or result.get("failed_targets"):
        raise HTTPException(status_code=503, detail="Cron target list is incomplete")
    return [item for item in result.get("data", []) if isinstance(item, dict)]


@router.get("")
async def list_target_crons(
    target_user_id: str = Query(min_length=1, max_length=128),
    target_bot_id: str = Query(min_length=1, max_length=128),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> dict[str, Any]:
    rows = await _list_target(service, target_user_id, target_bot_id)
    return {"success": True, "data": [
        {"task_id": _task_id(item), "name": item.get("name"),
         "enabled": item.get("enabled"), "runtime_stage": item.get("runtime_stage"),
         "configuration_fingerprint": _fingerprint(item)}
        for item in rows if _task_id(item)
    ]}


@router.get("/{task_id}/runs")
async def get_target_runs(
    task_id: str,
    target_user_id: str = Query(min_length=1, max_length=128),
    target_bot_id: str = Query(min_length=1, max_length=128),
    limit: int = Query(default=3000, ge=1, le=5000),
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> dict[str, Any]:
    if task_id not in {_task_id(row) for row in await _list_target(service, target_user_id, target_bot_id)}:
        raise HTTPException(status_code=404, detail="Cron task not found")
    try:
        result = await service.get_cron_runs(
            bot_id=target_bot_id, user_id=target_user_id, nick_name=target_user_id,
            task_id=task_id, limit=limit, runtime_stage="online",
        )
    except CronRelayError as error:
        raise HTTPException(status_code=error.error_code, detail=str(error)) from error
    if not result.get("success") or result.get("failed_targets"):
        raise HTTPException(status_code=503, detail="Cron run history is incomplete")
    data = result.get("data") or {}
    instances = data.get("results") if isinstance(data, dict) else None
    if instances is None:
        instances = [{"data": data, "device_uuid": None}]
    sanitized = []
    for instance in instances:
        payload = instance.get("data") or {}
        runs = payload.get("runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise HTTPException(status_code=503, detail="Invalid cron run history")
        sanitized.append({"device_uuid": instance.get("device_uuid"),
                          "truncated": len(runs) >= limit,
                          "runs": [{key: row.get(key) for key in
                                    ("job_id", "started_at_ms", "status")}
                                   for row in runs if isinstance(row, dict)]})
    return {"success": True, "data": sanitized}


@router.post("/disable")
async def disable_target_cron(
    body: DisableCronRequest,
    service: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> dict[str, Any]:
    rows = await _list_target(service, body.target_user_id, body.target_bot_id)
    matches = [row for row in rows if _task_id(row) == body.task_id]
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="Cron task is missing or ambiguous")
    current = matches[0]
    if _fingerprint(current) != body.expected_fingerprint:
        raise HTTPException(status_code=409, detail="Cron configuration changed")
    if current.get("enabled") is False:
        return {"success": True, "already_disabled": True, "data": None}
    if current.get("enabled") is not True:
        raise HTTPException(status_code=409, detail="Cron enabled state is unknown")
    try:
        result = await service.update_cron(
            bot_id=body.target_bot_id, user_id=body.target_user_id,
            nick_name=body.target_user_id, task_id=body.task_id,
            body={"enabled": False}, runtime_stage="online",
        )
    except CronRelayError as error:
        raise HTTPException(status_code=error.error_code, detail=str(error)) from error
    failed_targets = result.get("failed_targets", [])
    logger.info(
        "cron_guard_disable improvement_id=%s bot_id=%r task_id=%r operator=%r success=%s failed_targets=%s",
        body.improvement_id, body.target_bot_id, body.task_id, body.operator,
        bool(result.get("success")) and not failed_targets, len(failed_targets),
    )
    return {"success": bool(result.get("success")) and not failed_targets,
            "data": result.get("data"), "failed_targets": failed_targets,
            "message": result.get("message", "OK"),
            "improvement_id": body.improvement_id}
