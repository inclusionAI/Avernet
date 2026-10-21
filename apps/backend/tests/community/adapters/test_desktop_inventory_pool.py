"""Pool restart exercises the real Desktop -> BaaS POST transport path."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from agentclaw.community.core.desktop_bot.errors import DesktopBotServiceError
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import (
    DesktopBotService,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.di.modules.bot_inventory_module import BotInventoryModule


@pytest.mark.parametrize("failure", [503, 500, "timeout", "connection"])
def test_pool_restart_normalizes_real_baas_transport_failures(failure):
    requests = []

    def transport(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private timeout detail", request=request)
        if failure == "connection":
            raise httpx.ConnectError("private connection detail", request=request)
        return httpx.Response(failure, text="private upstream detail")

    with httpx.Client(
        base_url="https://baas.test", transport=httpx.MockTransport(transport)
    ) as client:
        baas = object.__new__(BaasService)
        baas._http = client
        baas._tenant = "default"
        desktop = object.__new__(DesktopBotService)
        desktop._baas = baas
        desktop._lookup_local = MagicMock(
            return_value=(
                SimpleNamespace(entity_id="u1", entity_type="staff"),
                {"env": "local", "ext": {}},
                "l1",
                "device1",
            )
        )
        desktop._desktop_layout_credentials_for_scope = MagicMock(
            return_value={
                "agentclaw_skills_layout": "pool",
            }
        )
        desktop._build_desktop_bot_payload = MagicMock(return_value={})
        port = BotInventoryModule().desktop_inventory_port(desktop)

        with pytest.raises(DesktopBotServiceError) as caught:
            port.restart(bot_id="l1", user_id="u1")

    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/bots/device1/update"
    assert isinstance(caught.value.__cause__, httpx.HTTPError)
    assert "private" not in str(caught.value)
