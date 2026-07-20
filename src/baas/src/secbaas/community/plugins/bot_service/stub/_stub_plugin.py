"""Stub BotService plugin — no-op implementation for tests / disabled mode."""

from __future__ import annotations

import os

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.spi.bot_service import (
    BotBindingData,
    BotServicePlugin,
    LogRelationPayload,
)


class StubBotServicePlugin(BotServicePlugin):
    """Stub BotService plugin.

    Most methods return deterministic stub values. Env vars enable
    error simulation for E2E failure-path tests:

    - ``BAAS_STUB_BOT_BINDING_ERROR=1`` — ``get_binding()`` raises ``PaasError``
    - ``BAAS_STUB_BOT_BINDING_NOT_FOUND=1`` — ``get_binding()`` returns ``None``
    """

    async def report(self, payload: LogRelationPayload) -> None:
        """No-op: returns immediately without sending any request."""

    async def get_binding(
        self,
        bot_id: str,
        owner_id: str,
        stage: str,
    ) -> BotBindingData:
        """Return deterministic stub BotBindingData, or simulate failures.

        Args:
            bot_id: Bot identifier.
            owner_id: Owner entity identifier.
            stage: Lifecycle stage.

        Returns:
            BotBindingData with stub values, or None when
            ``BAAS_STUB_BOT_BINDING_NOT_FOUND`` is set.

        Raises:
            PaasError: When ``BAAS_STUB_BOT_BINDING_ERROR`` is set.
        """
        if os.getenv("BAAS_STUB_BOT_BINDING_ERROR"):
            raise PaasError(
                code=ErrorCode.PAAS_ERROR,
                message="stub: simulated binding resolution failure",
            )
        if os.getenv("BAAS_STUB_BOT_BINDING_NOT_FOUND"):
            return None
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
