"""Desktop registration reuses the lazy BotService factory from the composition root."""
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.desktop_bot.services.desktop_bot_service import DesktopBotService


def test_desktop_bcn_provider_resolves_shared_bot_service_without_cycle(test_injector):
    desktop = test_injector.get(DesktopBotService)
    assert desktop._bot_service_provider() is test_injector.get(BotService)
