"""Verify a short-lived command proof, without exposing keys or executing commands."""

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.core.system_config.dws_rollout import is_allowed, is_stop
from agentclaw.community.plugin_api.dws_identity import DwsRolloutIdentity
from agentclaw.community.di import Injected
from agentclaw.community.utils.env_utils import get_current_env
from starlette.concurrency import run_in_threadpool

from agentclaw.community.di.dws_management_config import (
    DwsManagementConfig,
    get_dws_management_config,
)

router = APIRouter(prefix="/internal/dws-hosting", tags=["dws-hosting-internal"])


class SignedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    payload: Annotated[str, Field(max_length=24000)]
    signature: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    bot_id: Annotated[str, Field(min_length=1, max_length=512)]
    actor_id: Annotated[str, Field(min_length=1, max_length=512)]
    operation: Annotated[str, Field(min_length=1, max_length=64)]
    timestamp_ms: int
    nonce: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    data: dict


@router.post("/verify-command", response_model=dict[str, bool])
async def verify_command(
    body: SignedCommand,
    config: Annotated[DwsManagementConfig, Depends(get_dws_management_config)],
    rollout: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
    identity: DwsRolloutIdentity = Injected(DwsRolloutIdentity),
):
    if not config.key:
        raise HTTPException(503, "management_verifier_unavailable")
    try:
        value = Command.model_validate_json(body.payload)
    except ValidationError:
        raise HTTPException(403, "invalid_management_proof") from None
    signature = hmac.new(
        config.bot_key(value.bot_id, value.actor_id),
        body.payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    # COSEC: this endpoint only verifies an existing, command-bound capability.
    # Only the authenticated Owner route can issue it. No keys, identities or
    # data are returned; Adapter must also enforce local scope and consume nonce.
    if (
        not hmac.compare_digest(signature, body.signature)
        or abs(int(time.time() * 1000) - value.timestamp_ms) > 30000
    ):
        raise HTTPException(403, "invalid_management_proof")
    allowed = is_stop(value.operation, value.data) or await run_in_threadpool(
        is_allowed, rollout, value.actor_id, get_current_env(), identity
    )
    return {"verified": allowed}


@router.post("/runtime-eligibility", response_model=dict[str, bool])
async def runtime_eligibility(
    body: SignedCommand,
    config: Annotated[DwsManagementConfig, Depends(get_dws_management_config)],
    rollout: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
    identity: DwsRolloutIdentity = Injected(DwsRolloutIdentity),
):
    # COSEC: a previously signed enable command is a read-only credential for
    # this one owner/Bot's rollout status, never a replayable management command.
    # Management still requires the original 30-second age and nonce checks.
    try:
        value = Command.model_validate_json(body.payload)
    except ValidationError:
        raise HTTPException(403, "invalid_runtime_proof") from None
    signature = hmac.new(
        config.bot_key(value.bot_id, value.actor_id),
        body.payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    if (
        not config.key
        or not hmac.compare_digest(signature, body.signature)
        or value.operation != "enabled"
        or value.data.get("enabled") is not True
    ):
        raise HTTPException(403, "invalid_runtime_proof")
    return {
        "allowed": await run_in_threadpool(
            is_allowed, rollout, value.actor_id, get_current_env(), identity
        )
    }
