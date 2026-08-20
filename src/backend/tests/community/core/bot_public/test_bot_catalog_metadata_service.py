"""Catalog metadata port behavior."""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadataServiceProtocol,
    BotCatalogMetadataUnavailableError,
)
from agentclaw.community.core.bot_public.services.bot_catalog_metadata_service import (
    UnavailableBotCatalogMetadataService,
)
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile


@pytest.mark.parametrize("addresses", [(), (BotCatalogAddress("bot-1", "entity-1"),)])
def test_unavailable_catalog_metadata_service_fails_closed_for_every_candidate_shape(
    addresses: tuple[BotCatalogAddress, ...],
) -> None:
    """Catches a future fallback that silently treats an unconfigured BCS port as empty."""
    service = UnavailableBotCatalogMetadataService()
    assert isinstance(service, BotCatalogMetadataServiceProtocol)

    with pytest.raises(BotCatalogMetadataUnavailableError):
        service.query_public_bot_metadata(
            addresses=addresses,
            caller=BotCatalogCaller(tenant_id="tenant-1", user_id="user-1", app_id=9),
            request_id="trace-1",
        )


def test_test_profile_binds_catalog_metadata_protocol_to_unavailable_service() -> None:
    """Catches an accidental local/test fallback that would make catalog membership up."""
    port = build_injector(profile=DeployProfile.TEST).get(
        BotCatalogMetadataServiceProtocol
    )

    assert isinstance(port, UnavailableBotCatalogMetadataService)
