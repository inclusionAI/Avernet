"""``PlatformBotStateReader``: a device-delivered bot's state through the services that own it.

Each category is read through the same service the Manifest materialisers
and the compose collector read through, so capture sees what apply sees:

- persona: ``IdentityFilePort`` (``list_bot_files`` / ``read_identity_file``);
- skills: ``BotCapabilityStateReaderProtocol.active_skill_assets``, classified
  by locator prefix as the skill set service records them (``local://`` files
  via the factory's package storage, ``center://`` by pinned version,
  ``git://<path>`` as a shared-repository skill);
- resources: the bot's ``ac_resource`` file rows (what the compose collector
  lists), at ``data/<row path>`` in the workspace, bytes through
  ``ResourceFileService.read_file``; files Manifest apply wrote outside those
  rows are not captured (noted for RSI-04, which fixes the resources road);
- MCP: ``DirectActivationServiceProtocol.list_installed_mcps`` minus the
  platform defaults, the bot override from ``MCPConfigServiceProtocol``;
- script: ``BotStartupScriptServiceProtocol.get_body`` with the ``${BOT_*}``
  substitution apply performs.

A platform-managed teclaw bot (a teclaw engine on a deployment whose
teclaw delivery mode is ``platform``) has no device to read: its state is
composed from platform rows (``managed_files``), and capturing it is
RSI-04's to define, so the reader refuses it by name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from agentclaw.community.core.bot_config_manifest.schema import placeholders
from agentclaw.community.core.bot_genome.capture.capture import (
    BotStateReader,
    CapturedFile,
    CapturedMcpServer,
    CapturedPersonaFile,
    CapturedResource,
    CapturedSkill,
    SkillOrigin,
)
from agentclaw.community.core.bot_genome.errors import CaptureRefused, Reason
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.mcp.mcp_config_service_protocol import MCPConfigServiceProtocol
from agentclaw.community.core.ports.identity_file_port import IdentityFilePort
from agentclaw.community.core.resources.resource_service_protocol import ResourceServiceFactoryProtocol
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.direct_activation_service_protocol import (
    DirectActivationServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_service_factory_protocol import (
    SkillServiceFactoryProtocol,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:  # the module reaches the device dispatcher at import time
    from agentclaw.community.core.services.resource_file_service import ResourceFileService

__all__ = ["PlatformBotStateReader"]

LOCAL_PREFIX = "local://"
#: Where ``ac_resource`` file rows live, workspace-relative (``ResourceService.data_dir``).
RESOURCE_DATA_DIR = "data/"
CENTER_PREFIX = "center://"
REPO_PREFIX = "git://"


class PlatformBotStateReader(BotStateReader):
    def __init__(
        self,
        *,
        identity: IdentityFilePort,
        capabilities: BotCapabilityStateReaderProtocol,
        skill_factory: SkillServiceFactoryProtocol,
        resource_services: ResourceServiceFactoryProtocol,
        resource_files: "ResourceFileService",
        activation: DirectActivationServiceProtocol,
        mcp_config: MCPConfigServiceProtocol,
        scripts: BotStartupScriptServiceProtocol,
        is_teclaw: Callable[[str | None], bool],
        teclaw_platform_managed: bool,
    ) -> None:
        self._identity = identity
        self._capabilities = capabilities
        self._skill_factory = skill_factory
        self._resource_services = resource_services
        self._resource_files = resource_files
        self._activation = activation
        self._mcp_config = mcp_config
        self._scripts = scripts
        self._is_teclaw = is_teclaw  # teclaw is an engine family, not a bot type (bot_service.is_teclaw_bot)
        self._teclaw_platform_managed = teclaw_platform_managed

    async def refusal(self, bot: BotRef) -> str | None:
        if self._teclaw_platform_managed and self._is_teclaw(bot.engine_type):
            return (
                "a platform-managed teclaw bot has no device to read; its state is composed from platform rows (RSI-04)"
            )
        return None

    async def persona_files(self, bot: BotRef) -> tuple[CapturedPersonaFile, ...]:
        listed = await self._identity.list_bot_files(
            bot.entity_type, bot.entity_id, bot.bot_id, bot.owner_id, engine_type=bot.engine_type
        )
        out: list[CapturedPersonaFile] = []
        for file_type, exists in listed:
            if not exists:
                continue
            text = await self._identity.read_identity_file(
                bot.entity_type, bot.entity_id, bot.bot_id, file_type, bot.owner_id, engine_type=bot.engine_type
            )
            out.append(CapturedPersonaFile(file_type, text))
        return tuple(out)

    async def skills(self, bot: BotRef) -> tuple[CapturedSkill, ...]:
        captured: list[CapturedSkill] = []
        for asset in self._capabilities.active_skill_assets(bot_id=bot.bot_id, owner_id=bot.owner_id):
            locator = str(asset.git_path or "")
            if locator.startswith(LOCAL_PREFIX):
                _, storage = self._skill_factory.local_skill_package_storage_for_locator(
                    entity_id=bot.entity_id,
                    owner_id=bot.owner_id,
                    bot_id=bot.bot_id,
                    engine_type=bot.engine_type,
                    entity_type=bot.entity_type,
                    is_desktop=bot.bot_type == "desktop",
                    is_teclaw=self._is_teclaw(bot.engine_type),
                    locator=locator,
                    skill_name=asset.name,
                )
                files = await storage.read_package_files()
                captured.append(
                    CapturedSkill(
                        asset.name, SkillOrigin.LOCAL, files=tuple(CapturedFile(path, data) for path, data in files)
                    )
                )
            elif locator.startswith(CENTER_PREFIX):
                captured.append(CapturedSkill(asset.name, SkillOrigin.CENTER, center_version=asset.sc_version_number))
            elif locator.startswith(REPO_PREFIX):
                captured.append(CapturedSkill(asset.name, SkillOrigin.REPO, repo_path=locator[len(REPO_PREFIX) :]))
            else:
                raise CaptureRefused(
                    [Reason("skill_package", f"skills/{asset.name}", f"unknown skill locator {locator!r}")]
                )
        return tuple(captured)

    async def resources(self, bot: BotRef) -> tuple[CapturedResource, ...]:
        service = self._resource_services.create(bot_id=bot.bot_id)
        captured: list[CapturedResource] = []
        for resource in service.list_resources(user_id=bot.owner_id):
            if not resource.path or resource.is_directory:
                continue  # URL, link and node resources are not workspace files; a folder row has no bytes
            # A row's path is relative to the bot's data directory (``workspace/data``, the compose
            # collector's join); the workspace-relative path apply writes and a revision names has it in front.
            path = RESOURCE_DATA_DIR + resource.path.lstrip("/")
            data = await self._resource_files.read_file(
                entity_type=bot.entity_type,
                entity_id=bot.entity_id,
                bot_id=bot.bot_id,
                engine_type=bot.engine_type,
                path=path,
            )
            if data is None:
                raise CaptureRefused(
                    [
                        Reason(
                            "resource_unreadable",
                            f"resources/{path}",
                            f"{path!r} is listed but cannot be read from the bot",
                        )
                    ]
                )
            captured.append(CapturedResource(path, (CapturedFile(path.rsplit("/", 1)[-1], data),)))
        return tuple(captured)

    async def mcp_servers(self, bot: BotRef) -> tuple[CapturedMcpServer, ...]:
        who = dict(bot_id=bot.bot_id, owner_id=bot.owner_id, actor_id=bot.owner_id)
        codes = set(self._activation.list_installed_mcps(**who)) - set(
            self._activation.platform_default_mcp_codes(**who)
        )
        servers = []
        for code in sorted(codes):
            override = self._mcp_config.get_bot_override(bot_id=bot.bot_id, owner_id=bot.owner_id, server_code=code)
            servers.append(CapturedMcpServer(code, dict(override) if override else None))
        return tuple(servers)

    async def script_body(self, bot: BotRef) -> str | None:
        body = self._scripts.get_body(entity_id=bot.entity_id, bot_id=bot.bot_id)
        if not body:
            return None
        return placeholders.resolve(
            body, engine_type=bot.engine_type, env=get_current_env(), tenant=get_current_avernet_tenant()
        )
