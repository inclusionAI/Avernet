"""Backend-only signed management commands. Ordinary engine HTTP stays unchanged."""

import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from engine.community.core.dws_hosting.storage import durable_call
from engine.community.kernel.dws_connection import DwsError

router = APIRouter(prefix="/api/dws-hosting", tags=["dws-hosting"])


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


@router.post("/command")
async def command(body: SignedCommand, request: Request):
    endpoint = getattr(request.app.state, "dws_management", None)
    if endpoint is None:
        raise HTTPException(503, "dws_not_configured")
    try:
        value = Command.model_validate_json(body.payload)
        now = int(time.time() * 1000)
        if abs(now - value.timestamp_ms) > 30000 or (value.bot_id, value.actor_id) != (
            endpoint.bot_id,
            endpoint.owner_id,
        ):
            raise PermissionError("invalid_management_scope")
        # COSEC: Backend verifies exact bytes using its existing secret. Checking
        # a claimed actor, proxy access or a caller-supplied header is insufficient.
        if not await endpoint.verifier.verify(body.payload, body.signature):
            raise PermissionError("invalid_management_signature")
        # Recheck age after the network round trip before consuming/executing.
        now = int(time.time() * 1000)
        if abs(now - value.timestamp_ms) > 30000:
            raise PermissionError("expired_management_command")
        await durable_call(endpoint.store.consume_nonce, value.nonce, now)
        if value.operation == "enabled" and value.data.get("enabled") is True:
            rollout = endpoint.service.runtime.rollout
            if rollout is not None:
                await rollout.remember(body.payload, body.signature)
        data = await endpoint.service.command(
            value.actor_id, value.operation, value.data
        )
        return {"success": True, "data": data}
    except PermissionError:
        raise HTTPException(403, "management_forbidden") from None
    except (ValueError, KeyError, DwsError):
        # Return a safe code inside the envelope so Backend can preserve a conflict
        # without changing the shared relay's existing status handling.
        return {"success": True, "data": {"management_error": "operation_refused"}}
