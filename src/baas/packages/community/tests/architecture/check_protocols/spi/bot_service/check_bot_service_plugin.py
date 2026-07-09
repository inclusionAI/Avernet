from secbaas.plugins.bot_service import (
    AiohttpBotServicePlugin,
    LocalBotServicePlugin,
    StubBotServicePlugin,
)
from secbaas.spi.bot_service import BotServicePlugin as BotServicePluginProtocol

# Assign value, will trigger mypy type check
_aiohttp_plugin: BotServicePluginProtocol = AiohttpBotServicePlugin()
_local_plugin: BotServicePluginProtocol = LocalBotServicePlugin()
_stub_plugin: BotServicePluginProtocol = StubBotServicePlugin()
