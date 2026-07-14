"""Local BotService plugin — lightweight local/singlebox implementation.

In local/singlebox mode there is no remote bot-metadata service to POST to.
This plugin simply logs at DEBUG level and returns immediately, providing
observability without network calls.

For get_binding(), the plugin raises PaasError(PLATFORM_UNAVAILABLE) because
there is no remote service to query — callers need a clear failure signal.

Future: may include local-specific metadata collection (e.g., writing
to a local file or in-memory store) for singlebox debugging.
"""

from __future__ import annotations

from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.logger import get_logger
from secbaas.community.spi.bot_service import (
    BotBindingData,
    BotServicePlugin,
    LogRelationPayload,
)

logger = get_logger("plugin-bot-service-local")


class LocalBotServicePlugin(BotServicePlugin):
    """Local BotService plugin for singlebox / offline development.

    ``report()`` logs the payload at DEBUG level and returns immediately.
    ``get_binding()`` raises PaasError(PLATFORM_UNAVAILABLE) because no
    remote service is available in local mode.
    ``close()`` is a no-op (no external resources to release).
    """

    async def report(self, payload: LogRelationPayload) -> None:
        """Log the payload at DEBUG level — no HTTP call.

        Args:
            payload: Log-relation request body (logged only).
        """
        logger.debug(
            "[bot-service-local] report: biz_task_id=%s biz_scene=%s "
            "engine=%s collector=%s",
            payload.biz_task_id,
            payload.biz_scene,
            payload.engine,
            payload.collector,
        )

    async def get_binding(
        self,
        bot_id: str,
        owner_id: str,
        stage: str,
    ) -> BotBindingData:
        """Not available in local mode — raises PaasError.

        In local/singlebox mode there is no remote bot-metadata service.

        Args:
            bot_id: Bot identifier.
            owner_id: Owner entity identifier.
            stage: Lifecycle stage.

        Raises:
            PaasError: Always, with PLATFORM_UNAVAILABLE.
        """
        logger.debug(
            "[bot-service-local] get_binding: bot_id=%s owner_id=%s stage=%s",
            bot_id,
            owner_id,
            stage,
        )
        raise PaasError(
            ErrorCode.PLATFORM_UNAVAILABLE,
            f"Bot binding lookup not available in local mode: "
            f"bot_id={bot_id}, owner_id={owner_id}, stage={stage}",
        )

    async def close(self) -> None:
        """No-op: no external resources to release."""
