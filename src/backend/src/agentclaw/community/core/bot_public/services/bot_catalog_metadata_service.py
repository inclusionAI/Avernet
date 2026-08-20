"""Fail-closed implementation of the catalog metadata port."""

from __future__ import annotations

from collections.abc import Sequence

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataUnavailableError,
)
from agentclaw.community.log import get_logger

logger = get_logger()


class UnavailableBotCatalogMetadataService:
    """Temporary binding used until the BCS metadata protocol is configured."""

    def query_public_bot_metadata(
        self,
        *,
        addresses: Sequence[BotCatalogAddress],
        caller: BotCatalogCaller,
        request_id: str,
    ) -> Sequence[BotCatalogMetadata]:
        del caller
        logger.warning(
            "[BotCatalogMetadata] request_id=%s candidate_count=%s failure=unconfigured",
            request_id,
            len(addresses),
        )
        raise BotCatalogMetadataUnavailableError()
