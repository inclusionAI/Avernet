"""Community host binding for the agentcompute ``BotPlatform`` seam.

Constructs :class:`AvernetBotPlatform` (see that module for the contract)
from the ``agentcompute_platform`` ``user_config`` block and binds it to the
seam. Installed in the community column and the singlebox branch of
``profile_modules`` — never the base list, so profiles without a platform
side keep agentcompute's designed fail-clear mode (an unbound ``BotPlatform``
raises at ``injector.get`` and ``provide_runtime`` degrades to ``None``).

The config is validated here, at binding time: a profile that installs this
module without a completed block fails at boot naming the missing fields
(``AgentComputePlatformConfig.from_block``), which is the cheapest possible
moment to catch a half-bound platform — versus the first avernet-kind spawn
mysteriously failing in a running swarm.

This module sits at the heavy end of the DI graph on purpose: it is imported
function-local by ``profile_modules`` (the repo's pattern), which runs after
``di`` finishes initializing — so ``agentcompute_platform`` (the plugin)
stays independently importable and this module may freely import the
services it binds.
"""
from __future__ import annotations

from injector import Injector, Module, provider, singleton

from agentclaw.community.api.bot_config_manifest_apply_service import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.agentcompute.spi.bot import BotPlatform
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.grt_chat.services import GrtChatService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.skill_set_service_factory_protocol import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugins.community.agentcompute_platform import (
    AgentComputePlatformConfig, AvernetBotPlatform,
)


class CommunityAgentComputePlatformModule(Module):

    @singleton
    @provider
    def agentcompute_platform_config(self) -> AgentComputePlatformConfig:
        return AgentComputePlatformConfig.from_block(_block("agentcompute_platform") or {})

    @singleton
    @provider
    def bot_platform(self, injector: Injector) -> BotPlatform:
        return AvernetBotPlatform(
            config=injector.get(AgentComputePlatformConfig),
            manifest_seam=injector.get(ManifestCreationSeam),
            bot_service=injector.get(BotServiceProtocol),
            passport_plugin=injector.get(PassportPlugin),
            skill_set_factory=injector.get(SkillSetServiceFactoryProtocol),
            bot_repo=injector.get(BotRepository),
            apply_service=injector.get(BotConfigManifestApplyServiceProtocol),
            space_context=injector.get(BusinessSpaceContextProtocol),
            grt_chat=injector.get(GrtChatService),
        )
