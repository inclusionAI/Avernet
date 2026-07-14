"""Stub TeClaw bot plugin — in-memory implementation for testing.

Provides:
- StubTeClawBotPlugin: deterministic in-memory mock for TeClawBotPlugin Protocol.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot.teclaw import (
    TeClawBotPlugin,
    _BotCreateResult,
    _BotDestroyResult,
    _BotInfo,
    _BotRestartResult,
    _BotUpdateResult,
)

logger = get_logger("plugin-bot-teclaw")


class StubTeClawBotPlugin(TeClawBotPlugin):
    """In-memory mock implementation of the TeClawBotPlugin Protocol.

    Stores bots in an internal dict for deterministic create/get/update/
    destroy/restart lifecycle. All resolve_* methods return fixed
    stub-teclaw URLs. close() is a no-op.
    """

    def __init__(self) -> None:
        self._bots: dict[str, dict[str, Any]] = {}
        # Storage keys ("bot_config", "status", "outbound_rule") align with TeClaw API v2 response field names

    async def create_bot(self, bot_config: dict[str, Any]) -> _BotCreateResult:
        """Create a new bot in the in-memory store.

        Args:
            bot_config: Bot configuration dict (opaque passthrough).

        Returns:
            _BotCreateResult with generated stubbed bot_id and ONLINE status.
        """
        bot_id = f"stub-teclaw-{uuid.uuid4().hex[:12]}"
        self._bots[bot_id] = {
            "bot_config": bot_config,
            "status": "ONLINE",
            "outbound_rule": None,
        }
        logger.info("[stub-teclaw] bot created bot_id=%s", bot_id)
        return _BotCreateResult(
            teclaw_bot_id=bot_id,
            status="ONLINE",
            teclaw_bot_config=bot_config,
        )

    async def destroy_bot(self, bot_id: str) -> _BotDestroyResult:
        """Remove a bot from the in-memory store.

        Lenient — does not raise if bot_id is unknown.

        Args:
            bot_id: The teclaw_bot_id to destroy.

        Returns:
            _BotDestroyResult with DELETED status.
        """
        self._bots.pop(bot_id, None)
        logger.info("[stub-teclaw] bot destroyed bot_id=%s", bot_id)
        return _BotDestroyResult(teclaw_bot_id=bot_id, status="DELETED")

    async def update_bot(
        self, bot_id: str, bot_config: dict[str, Any]
    ) -> _BotUpdateResult:
        """Update a bot's config in the in-memory store.

        Uses ``setdefault`` to create the entry with all known keys when
        ``bot_id`` is new. Existing entries are updated in-place so any
        previously stored ``outbound_rule`` is preserved.

        Args:
            bot_id: The teclaw_bot_id to update.
            bot_config: New bot configuration dict.

        Returns:
            _BotUpdateResult with ONLINE status.
        """
        entry = self._bots.setdefault(
            bot_id,
            {"bot_config": {}, "status": "UNKNOWN", "outbound_rule": None},
        )
        entry["bot_config"] = bot_config
        entry["status"] = "ONLINE"
        logger.info("[stub-teclaw] bot updated bot_id=%s", bot_id)
        return _BotUpdateResult(
            teclaw_bot_id=bot_id,
            status="ONLINE",
            teclaw_bot_config=bot_config,
        )

    async def update_outbound_rule(self, bot_id: str, rules: dict[str, Any]) -> bool:
        """Store outbound operation rules for a bot in the in-memory store.

        Creates the bot entry with all known keys if ``bot_id`` does not
        already exist. Stores ``rules`` under the ``"outbound_rule"`` key
        without overwriting other fields.

        Args:
            bot_id: The teclaw_bot_id for the target device.
            rules: Dict in TeClaw API JSON format, e.g.
                ``{"header_operation_rules": [...]}``.

        Returns:
            True after successful storage.
        """
        self._bots.setdefault(
            bot_id,
            {"bot_config": {}, "status": "UNKNOWN", "outbound_rule": None},
        )
        self._bots[bot_id]["outbound_rule"] = rules
        logger.info("[stub-teclaw] outbound rule updated bot_id=%s", bot_id)
        return True

    async def restart_bot(self, bot_id: str) -> _BotRestartResult:
        """Restart a bot by re-applying its stored config.

        Delegates to update_bot internally with the stored bot_config.

        Args:
            bot_id: The teclaw_bot_id to restart.

        Returns:
            _BotRestartResult with ONLINE status.
        """
        stored = self._bots.get(bot_id, {})
        await self.update_bot(bot_id, stored.get("bot_config", {}))
        logger.info("[stub-teclaw] bot restarted bot_id=%s", bot_id)
        return _BotRestartResult(teclaw_bot_id=bot_id, status="ONLINE")

    async def get_bot(self, bot_id: str) -> _BotInfo:
        """Get current bot info from the in-memory store.

        Args:
            bot_id: The teclaw_bot_id to query.

        Returns:
            _BotInfo with stored data. UNKNOWN status for unknown IDs.
        """
        stored = self._bots.get(
            bot_id,
            {"bot_config": None, "status": "UNKNOWN", "outbound_rule": None},
        )
        logger.info("[stub-teclaw] bot queried bot_id=%s", bot_id)
        return _BotInfo(
            teclaw_bot_id=bot_id,
            status=stored["status"],
            teclaw_bot_config=stored["bot_config"],
            outbound_rule=stored.get("outbound_rule"),
        )

    async def resolve_http_conn_info(
        self, bot_id: str, port: int, path: str, template_id: int | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info — deterministic stub URL.

        Args:
            bot_id: The teclaw_bot_id for the target device.
            port: Target port on the device.
            path: HTTP path (e.g., "/api/openclaw/invoke").
            template_id: Optional template ID (int) for multi-tenant target format.

        Returns:
            HttpConnectionInfo with http://stub-teclaw:{port}{path} URL.
        """
        logger.info(
            "[stub-teclaw] resolve_http bot_id=%s port=%d path=%s",
            bot_id,
            port,
            path,
        )
        return HttpConnectionInfo(
            http_url=f"http://stub-teclaw:{port}{path}",
            token=f"stub-jwt-{bot_id}",
            target=(
                f"TECLAW_{bot_id}@{template_id}:{port}"
                if template_id is not None
                else f"TECLAW_{bot_id}:{port}"
            ),
        )

    async def resolve_ws_conn_info(
        self, bot_id: str, port: int, path: str, template_id: int | None = None
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info — deterministic stub URL.

        Args:
            bot_id: The teclaw_bot_id for the target device.
            port: Target port on the device.
            path: WebSocket path (e.g., "/api/openclaw/ws").
            template_id: Optional template ID (int) for multi-tenant target format.

        Returns:
            WsConnectionInfo with ws://stub-teclaw:{port}{path} URL.
        """
        logger.info(
            "[stub-teclaw] resolve_ws bot_id=%s port=%d path=%s",
            bot_id,
            port,
            path,
        )
        return WsConnectionInfo(
            ws_url=f"ws://stub-teclaw:{port}{path}",
            token=f"stub-jwt-{bot_id}",
            target=(
                f"TECLAW_{bot_id}@{template_id}:{port}"
                if template_id is not None
                else f"TECLAW_{bot_id}:{port}"
            ),
            expires_at=datetime.now(UTC) + timedelta(seconds=120),
        )

    async def close(self) -> None:
        """No-op — no resources to release."""
        pass
