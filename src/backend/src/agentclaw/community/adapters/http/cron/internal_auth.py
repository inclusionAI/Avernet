"""Fail-closed Bearer guard for Cron Guard's machine-to-machine API."""

from hmac import compare_digest

from fastapi import Header, HTTPException

from agentclaw.community.di import Injected
from agentclaw.community.di.config import CronGuardInternalToken


async def verify_cron_guard_token(
    authorization: str = Header(default=""),
    configured: CronGuardInternalToken = Injected(CronGuardInternalToken),
) -> None:
    scheme, _, supplied = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not supplied
        or not configured.value
        or not compare_digest(supplied, configured.value)
    ):
        raise HTTPException(status_code=401, detail="Invalid token")
