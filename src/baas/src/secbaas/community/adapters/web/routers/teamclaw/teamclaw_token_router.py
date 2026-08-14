"""TeamClaw CLI token issuance API.

Provides ``POST /api/teamclaw/token?device_id=xxx`` which:
1. Authenticates the calling user via cookie-based auth
2. Looks up the device and validates its platform (ARCA/TeClaw only)
3. Verifies the user->bot->device ownership chain
4. Signs a 10-minute HS256 JWT using a dedicated Mist secret key

The JWT is consumed by the ``teamclaw`` CLI and proxypass infrastructure
to establish terminal WebSocket connections to bot device containers.
"""

import base64
import hashlib
import hmac
import json
import time

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse
from secbaas.community.api.device_manage import DeviceService
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.community.core.service.auth_service._auth_service import AuthService
from secbaas.community.logger import get_logger
from secbaas.community.spi.secret import SecretStorePlugin

logger = get_logger("router")

router = APIRouter(prefix="/api/teamclaw", tags=["TeamClaw CLI"])

# ── Platform allowlist for terminal access (per D-07) ─────────────────────
_SUPPORTED_TERMINAL_PLATFORMS: frozenset[str] = frozenset({"arca", "teclaw"})

# ── Token TTL (seconds) ────────────────────────────────────────────────────
_TOKEN_TTL_SECONDS: int = 600  # 10 minutes (per D-05)

# ── Mist secret name for the teamclaw terminal JWT signing key ─────────────
_TEAMCLAW_JWT_SECRET_NAME: str = "other_manual_teamclaw_terminal_jwt_secret"

# ── Response model ─────────────────────────────────────────────────────────


class TeamclawTokenResponse(BaseModel):
    """Response body for POST /api/teamclaw/token."""

    token: str = Field(description="HS256 JWT for terminal access")
    device_id: str = Field(description="Target device UUID")
    bot_uuid: str = Field(description="Bot UUID that owns this device")
    expires_at: int = Field(description="Unix timestamp when the token expires")


# ── JWT helpers ─────────────────────────────────────────────────────────────


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode bytes without padding (RFC 7515)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _generate_teamclaw_jwt(claims: dict[str, object], secret_key: str) -> str:
    """Sign a JWT with HS256.

    Follows the same pattern as ``secret_utils.generate_jwt_token()`` but
    accepts arbitrary claims instead of hardcoding a ``target`` claim.

    Args:
        claims: Payload claims dict (must contain ``exp``).
        secret_key: HMAC-SHA256 signing key.

    Returns:
        Compact JWT string (header.payload.signature).
    """
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}"
    signature = hmac.new(
        secret_key.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{header}.{payload}.{_b64url_encode(signature)}"


# ── Endpoint ────────────────────────────────────────────────────────────────


@router.post("/token", response_model=ApiResponse[TeamclawTokenResponse])
@inject
async def generate_teamclaw_token(
    device_id: str = Query(..., description="Target device UUID"),
    request: Request = None,
    device_service: DeviceService = Depends(
        Provide[ApplicationContainer.services.device_service]
    ),
    auth_service: AuthService = Depends(
        Provide[ApplicationContainer.services.auth_service]
    ),
    secret_plugin: SecretStorePlugin = Depends(
        Provide[ApplicationContainer.plugins.secret_plugin]
    ),
    bot_device_rel_repo: BotDeviceRelRepository = Depends(
        Provide[ApplicationContainer.repository.bot_device_rel_repository]
    ),
    bot_repo: BotRepository = Depends(
        Provide[ApplicationContainer.repository.bot_repository]
    ),
) -> ApiResponse[TeamclawTokenResponse]:
    """Issue a 10-minute HS256 JWT for CLI terminal access to a bot device.

    **Permission model (user -> bot -> device chain):**
    1. Authenticate the caller via cookie-based auth
    2. Look up the target device by ``device_id``
    3. Reject unsupported platforms (only ARCA + TeClaw)
    4. Find the bot that owns this device
    5. Verify the caller owns that bot (creator match)
    6. Sign a JWT with claims ``{sub, bot_uuid, device_id, exp}``
    """
    # ── Step 1: Authenticate user ───────────────────────────────────
    cookie = request.headers.get("cookie", "") if request else ""
    referer = request.headers.get("referer", "") if request else ""
    try:
        auth_user = await auth_service.authenticate_request(
            cookie=cookie, referer=referer
        )
    except Exception as exc:
        logger.warning(f"Teamclaw token auth failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "AUTHENTICATION_FAILED", "message": str(exc)},
        )

    user_id: str = auth_user.staffId

    # ── Step 2: Look up device ──────────────────────────────────────
    device_info = device_service.get_device_info(device_uuid=device_id)
    if device_info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "DEVICE_NOT_FOUND",
                "message": f"Device not found: {device_id}",
            },
        )

    # ── Step 3: Platform gate (D-07) ─────────────────────────────────
    platform = (device_info.provider_type or "").lower()
    if platform not in _SUPPORTED_TERMINAL_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error_code": "PLATFORM_NOT_SUPPORTED",
                "message": (
                    f"Terminal access is not supported on {platform.upper() if platform else 'unknown'} platform. "
                    "Only ARCA and TeClaw platforms are supported."
                ),
            },
        )

    # ── Step 4: Find the bot that owns this device ───────────────────
    tenant = device_info.tenant
    env = device_info.env
    rel = bot_device_rel_repo.get_by_device_uuid(
        device_uuid=device_id, tenant=tenant, env=env
    )
    if rel is None:
        logger.warning(
            f"Teamclaw token: device {device_id} not linked to any bot"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "ACCESS_DENIED",
                "message": "Device is not linked to any bot",
            },
        )

    bot_record = bot_repo.get_by_id(bot_id=rel.bot_id, tenant=tenant, env=env)
    if bot_record is None:
        logger.warning(
            f"Teamclaw token: bot {rel.bot_id} not found for device {device_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "ACCESS_DENIED",
                "message": "Bot not found for this device",
            },
        )

    # ── Step 5: Verify user owns the bot ─────────────────────────────
    if bot_record.creator != user_id:
        logger.warning(
            f"Teamclaw token access denied: user={user_id} bot_creator={bot_record.creator} "
            f"device={device_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "ACCESS_DENIED",
                "message": "You do not have access to this device",
            },
        )

    bot_uuid: str = bot_record.bot_uuid

    # ── Step 6: Fetch signing secret from Mist ───────────────────────
    try:
        secret_key = secret_plugin.get_secret(_TEAMCLAW_JWT_SECRET_NAME)
    except Exception as exc:
        logger.error(
            f"Teamclaw token: failed to fetch secret '{_TEAMCLAW_JWT_SECRET_NAME}': {exc}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "SECRET_FETCH_FAILED",
                "message": (
                    f"Failed to retrieve JWT signing key. Ensure "
                    f"'{_TEAMCLAW_JWT_SECRET_NAME}' is provisioned in Mist."
                ),
            },
        )

    # ── Step 7: Build and sign JWT ───────────────────────────────────
    now = int(time.time())
    expires_at = now + _TOKEN_TTL_SECONDS
    claims: dict[str, object] = {
        "sub": user_id,
        "bot_uuid": bot_uuid,
        "device_id": device_id,
        "exp": expires_at,
    }
    token = _generate_teamclaw_jwt(claims, secret_key)

    logger.info(
        f"Teamclaw token issued for device={device_id} bot={bot_uuid} user={user_id}"
    )

    return ApiResponse(
        data=TeamclawTokenResponse(
            token=token,
            device_id=device_id,
            bot_uuid=bot_uuid,
            expires_at=expires_at,
        )
    )