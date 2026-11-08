"""Registered ApplyRunService with real capability services and SQLite repositories."""

from agentclaw.community.core.apply_engine.activation_delegates import DeviceActivation
from agentclaw.community.core.apply_engine.delivery import (
    ArcaDelivery,
    MaterialiserPorts,
    TeclawPlatformDelivery,
)
from agentclaw.community.core.apply_engine.run.service import ApplyRunService
from agentclaw.community.core.apply_engine.skill_set_apply_delegates import (
    DeviceSkillSetApply,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest_apply import (
    BotConfigManifestApplyRepository,
    BotConfigManifestApplyLockRepository,
)
from ._capabilities_world import CapabilityWorld
from ._empty_cli_tools import EmptyCliTools
from ._fakes import (
    FakeStartupScriptService,
    FakeIdentityService,
    FakeResourceFileService,
    resource_rows_for,
    real_validator,
    delivery_strategies,
    manifest_compilers,
    no_redeliver,
)
from .test_apply_service_lifecycle import _ManifestService
from .test_apply_service_uses_strategy import _InlineQueue


def registered_world(document, *, platform=False, resolver=None, world=None):
    world = world or CapabilityWorld()
    if platform:
        world.bots.update_by_owner("bot-1", "owner", {"active_engine": "teclaw"})
        world.bot["active_engine"] = "teclaw"
    port = world.activation
    sets = world.sets
    if not platform:
        port = DeviceActivation(world.activation._inner)
        sets = DeviceSkillSetApply(world.management)
    bundle = MaterialiserPorts(
        script_service=FakeStartupScriptService(),
        activation_service=port,
        mcp_auth_service=world.auth,
        mcp_config_service=world.config,
        identity_service=FakeIdentityService(),
        upload_service=world.uploads,
        skill_references=world.references,
        skill_sets=sets,
        package_validator=real_validator(),
        content=world.content,
        resource_service=FakeResourceFileService(),
        resource_rows=resource_rows_for()[0],
        cli_tool_service=EmptyCliTools(),
        shared_skills=world.query,
    )
    strategy = (
        TeclawPlatformDelivery(ports=lambda: bundle, redeliver=no_redeliver)
        if platform
        else ArcaDelivery(lambda: bundle)
    )
    manifests = _ManifestService(document)
    queue = _InlineQueue()
    service = ApplyRunService(
        capabilities=manifests,
        apply_repository=BotConfigManifestApplyRepository(world.db),
        lock_repository=BotConfigManifestApplyLockRepository(world.db),
        compilers=manifest_compilers(
            manifests=manifests, content=world.content, resolver=resolver
        ),
        completion_handlers={},
        task_queue_provider=lambda: queue,
        bot_repository=world.bots,
        delivery_strategies=delivery_strategies(
            **({"teclaw": strategy} if platform else {"arca": strategy}),
            is_teclaw=lambda engine: engine == "teclaw",
        ),
    )
    queue.service = service
    return world, service
