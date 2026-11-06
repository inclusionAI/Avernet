"""Owned WebSocket connection, onboarding, heartbeat and reconnect lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .config import Settings
from .domain import Player
from .identity import IdentityStore
from .runtime import Runtime
from .tasks import PrivateSeat

log = logging.getLogger(__name__)


async def onboard(settings: Settings, token: str) -> None:
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        result = await client.post(
            settings.bcs_api_url + "/bots/onboard",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": settings.name,
                "summary": "PydanticAI 谁是卧底玩家",
                "skills": [],
                "domains": [],
                "scopes": [],
            },
        )
        if not result.is_success:
            raise RuntimeError(f"BCN onboarding failed with HTTP {result.status_code}")
        if result.json().get("onboarded") is not True:
            raise RuntimeError("BCN onboarding did not complete")


class Connection:
    def __init__(
        self,
        settings: Settings,
        player: Player,
        *,
        onboard_bot: Callable[[Settings, str], Awaitable[None]] = onboard,
    ):
        self.settings, self.player, self.onboard_bot = settings, player, onboard_bot
        self.identity = IdentityStore(settings.state_file, settings.bcs_ws_url)
        # Keep session secrets in memory across sockets, separate from cancellable runs.
        self.private_seats: dict[str, PrivateSeat] = {}

    async def once(self) -> None:
        async with connect(
            self.settings.bcs_ws_url,
            proxy=None,
            max_size=1_048_576,
            open_timeout=15,
            close_timeout=3,
        ) as ws:
            write_lock = asyncio.Lock()

            async def send(frame: dict) -> None:
                async with write_lock:
                    await ws.send(json.dumps(frame, ensure_ascii=False))

            token = self.identity.load_token()
            params = {"protocol_version": 3, "client_kind": "pydanticai-undercover"}
            if token:
                params["token"] = token
            await send({"type": "req", "id": "connect", "method": "bot.connect", "params": params})
            async with asyncio.timeout(15):
                response = json.loads(await ws.recv())
            if response.get("type") != "res" or response.get("id") != "connect":
                raise RuntimeError("Unexpected BCN handshake response")
            if not response.get("ok"):
                if response.get("error", {}).get("code") == "already_connected":
                    # The old socket may still be clearing its server-side registration.
                    raise ConnectionError("BCN previous connection is still active")
                # No raw server error text: it may include tokens or private context.
                raise RuntimeError("BCN handshake rejected")
            payload = response.get("payload", {})
            caps = payload.get("capabilities", {})
            if payload.get("protocol_version") != 3 or not all(
                caps.get(key) is True for key in ("unified_run_events", "canonical_session_id")
            ):
                raise RuntimeError("BCN V3 capabilities required")
            fresh_token, bot_uuid = payload.get("token"), payload.get("bot_uuid")
            if not isinstance(fresh_token, str) or not fresh_token or not isinstance(bot_uuid, str):
                raise RuntimeError("Incomplete BCN identity")
            if token and fresh_token != token:
                raise RuntimeError("BCN replaced a saved identity; refusing silent Bot replacement")
            self.identity.save(fresh_token, bot_uuid)
            await self.onboard_bot(self.settings, fresh_token)
            runtime = Runtime(self.player, send, private_seats=self.private_seats)
            heartbeat = asyncio.create_task(self.heartbeat(send))
            log.info("PydanticAI player connected; engine ready")
            try:
                async for raw in ws:
                    frame = json.loads(raw)
                    if not isinstance(frame, dict):
                        raise ValueError("Invalid BCN frame")
                    if frame.get("type") == "res" and frame.get("ok") is False:
                        raise RuntimeError("BCN control request rejected")
                    await runtime.dispatch(frame)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                await runtime.close()

    async def heartbeat(self, send) -> None:
        counter = 0
        while True:
            await asyncio.sleep(60)
            counter += 1
            await send(
                {"type": "req", "id": f"heartbeat-{counter}", "method": "bot.status", "params": {}}
            )

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                await self.once()
                delay = 1
            except (OSError, ConnectionClosed, TimeoutError):
                log.warning("BCN connection lost; cancelling in-flight work before reconnect")
            # Protocol, identity and onboarding errors deliberately fail visibly.
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
