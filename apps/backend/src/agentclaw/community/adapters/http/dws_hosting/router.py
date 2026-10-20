"""Verify a short-lived command proof, without exposing keys or executing commands."""

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    return {"verified": True}
