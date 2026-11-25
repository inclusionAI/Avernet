"""Community binding for the service-Bot evaluation gate."""

from agentclaw.community.plugin_api.service_bot_eval import (
    ServiceBotEvalPlugin,
)
from agentclaw.community.plugins.community.service_bot_eval import (
    CommunityServiceBotEval,
)
from injector import Binder, Module, singleton


class CommunityServiceBotEvalModule(Module):
    """Bind the community no-op gate."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            ServiceBotEvalPlugin,
            to=CommunityServiceBotEval,
            scope=singleton,
        )
