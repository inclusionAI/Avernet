from injector import Binder, Module, provider, singleton

from agentclaw.community.core.bot_management.managed_bots import ManagedBotService
from agentclaw.community.core.bot_management.managed_bots_protocol import ManagedBotServiceProtocol
from agentclaw.community.core.repository.implementations.bot.template_bots import TemplateBotRepository
from agentclaw.community.core.repository.protocols.bot.template_bots import TemplateBotRepositoryProtocol
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.openapi_bot_operations_config import OpenApiBotOperationsConfig


class ManagedBotsModule(Module):
    def configure(self, binder: Binder) -> None:
        binder.bind(TemplateBotRepositoryProtocol, to=TemplateBotRepository, scope=singleton)
        binder.bind(ManagedBotServiceProtocol, to=ManagedBotService, scope=singleton)

    @singleton
    @provider
    def operations_config(self) -> OpenApiBotOperationsConfig:
        openapi = config_module.read_user_config().get('openapi', {})
        if not isinstance(openapi, dict):
            raise ValueError('openapi must be a mapping')
        block = openapi.get('bot_operations', {})
        if not isinstance(block, dict) or set(block) - {'allowed_apps'}:
            raise ValueError('openapi.bot_operations must contain only allowed_apps')
        entries = block.get('allowed_apps', [])
        if not isinstance(entries, list):
            raise ValueError('openapi.bot_operations.allowed_apps must be a list')
        allowed = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'env', 'tenant', 'app_name'}:
                raise ValueError('allowed application requires env, tenant and app_name')
            if not all(isinstance(value, str) and value and value.strip() == value for value in entry.values()):
                raise ValueError('allowed application fields must be nonblank trimmed strings')
            allowed.add((entry['env'], entry['tenant'], entry['app_name']))
        return OpenApiBotOperationsConfig(frozenset(allowed))
