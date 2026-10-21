"""The production desktop port normalizes restart failures at its boundary."""

from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.bot_inventory.adapters.desktop_inventory import (
    DesktopInventoryAdapter,
)
from agentclaw.community.core.desktop_bot.errors import DesktopBotServiceError
from agentclaw.community.core.service_bot.baas_service_errors import (
    BaasServiceError,
    BaasTransientServiceError,
)
from agentclaw.community.di.modules.bot_inventory_module import BotInventoryModule


@pytest.mark.unit
@pytest.mark.parametrize("error_type", [BaasServiceError, BaasTransientServiceError])
def test_production_port_translates_restart_error(error_type):
    desktop = MagicMock()
    upstream_error = error_type("private upstream detail")
    desktop.restart.side_effect = upstream_error
    port = BotInventoryModule().desktop_inventory_port(desktop)

    assert isinstance(port, DesktopInventoryAdapter)
    with pytest.raises(DesktopBotServiceError) as caught:
        port.restart(bot_id="l1", user_id="u1")

    assert caught.value.__cause__ is upstream_error
    assert "private upstream detail" not in str(caught.value)
    desktop.restart.assert_called_once_with(bot_id="l1", user_id="u1")


@pytest.mark.unit
def test_production_port_preserves_restart_result():
    desktop = MagicMock()
    result = {"bot_id": "l1", "status": "PENDING"}
    desktop.restart.return_value = result

    port = BotInventoryModule().desktop_inventory_port(desktop)

    assert port.restart(bot_id="l1", user_id="u1") is result


@pytest.mark.unit
def test_production_port_preserves_unexpected_errors():
    desktop = MagicMock()
    error = RuntimeError("unexpected implementation error")
    desktop.restart.side_effect = error
    port = BotInventoryModule().desktop_inventory_port(desktop)

    with pytest.raises(RuntimeError) as caught:
        port.restart(bot_id="l1", user_id="u1")

    assert caught.value is error
