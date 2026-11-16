"""Test/local binding for the service-Bot evaluation gate."""

from agentclaw.community.plugin_api.service_bot_eval import (
    ServiceBotEvalPlugin,
)
from agentclaw.community.plugins.local.service_bot_eval import LocalServiceBotEval
from injector import Binder, Module, singleton


class TestServiceBotEvalModule(Module):
    """Bind the local no-op gate so offline tests never reach SecurityAC."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            ServiceBotEvalPlugin,
            to=LocalServiceBotEval,
            scope=singleton,
        )
