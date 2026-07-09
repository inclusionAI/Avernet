"""HTTP client for BCS user identity API (GET /auth/user)."""

from __future__ import annotations

import os

import httpx
from fastapi import HTTPException

from secbaas.config import ConfigLoader
from secbaas.logger import get_logger
from secbaas.spi.auth import AuthUser

logger = get_logger("plugin-auth-oauth")


class OAuthClient:
    """HTTP client that resolves the current user via BCS `GET /auth/user`.

    BCS authenticates by the `bcs_session` JWT cookie (issued after OAuth
    login). The caller forwards the inbound `Cookie` header verbatim; BCS
    extracts `bcs_session` itself.
    """

    def __init__(self) -> None:
        config = ConfigLoader.load()
        bcs_config = config.user_config.get("oauth", {})
        bcs_port = os.environ.get("BCS_PORT", "21000")
        self._base_url = bcs_config.get("base_url", f"http://127.0.0.1:{bcs_port}")
        self._user_path = bcs_config.get("user_path", "/auth/user")
        self._timeout = bcs_config.get("timeout", 10)

    async def get_login_user(
        self, cookie: str | None = None, referer: str | None = None
    ) -> AuthUser:
        """Resolve the current user from BCS `GET /auth/user`.

        Raises:
            HTTPException(401): cookie missing or BCS rejects the session.
            RuntimeError: 404 (OAuth not mounted) / other non-2xx / network error.
        """
        if not cookie:
            logger.warning("BCS auth failed: no cookie provided")
            raise HTTPException(status_code=401, detail="missing login cookie")

        url = f"{self._base_url}{self._user_path}"
        headers: dict[str, str] = {"Cookie": cookie}
        if referer:
            headers["referer"] = referer

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"bcs auth request error: {exc}") from exc

        if response.status_code == 401:
            logger.info("BCS auth: not authenticated (401)")
            raise HTTPException(status_code=401, detail="bcs: not authenticated")
        if response.status_code != 200:
            raise RuntimeError(
                f"bcs auth http error {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"bcs auth invalid json: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("bcs auth failed: response not an object")
        user_id = data.get("user_id")
        if not user_id:
            raise RuntimeError("bcs auth failed: response missing user_id")

        logger.info("BCS auth success: user_id=%s", user_id)
        return AuthUser(
            id=user_id,
            operatorName=user_id,
            staffId=user_id,
            nickName=data.get("name"),
        )
