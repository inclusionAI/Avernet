"""Stub BotService plugin — no-op implementation for tests / disabled mode."""

from __future__ import annotations

import os

from secbaas.community.spi.bot_service import (
    BotBindingData,
    BotServicePlugin,
    LogRelationPayload,
)


class StubBotServicePlugin(BotServicePlugin):
    """Stub BotService plugin.

    All methods are no-ops. Use when the bot metadata service is
    disabled or in test environments where no HTTP calls should
    be made.
    """

    async def report(self, payload: LogRelationPayload) -> None:
        """No-op: returns immediately without sending any request."""

    async def get_binding(
        self,
        bot_id: str,
        owner_id: str,
        stage: str,
    ) -> BotBindingData:
        """Return a deterministic stub BotBindingData.

        Args:
            bot_id: Bot identifier.
            owner_id: Owner entity identifier.
            stage: Lifecycle stage.

        Returns:
            BotBindingData with stub values.
        """
        return BotBindingData(
            bot_id=bot_id,
            owner_id=owner_id,
            bot_type=os.getenv("BAAS_STUB_BOT_TYPE", "service"),
            engine_type=os.getenv("BAAS_STUB_ENGINE_TYPE", "openclaw"),
            publish_id=None,
            publish_status=None,
            binding_id=0,
            device_provider=os.getenv("BAAS_STUB_DEVICE_PROVIDER", "stub"),
            device_id=os.getenv("BAAS_STUB_DEVICE_ID", "stub-device"),
        )

    async def close(self) -> None:
        """No-op: no resources to release."""
