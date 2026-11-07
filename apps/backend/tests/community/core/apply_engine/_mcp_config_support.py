"""A real Direct MCP/Apply/capture stack with external I/O stood in for."""

from datetime import datetime
from dataclasses import replace
from unittest.mock import MagicMock

from agentclaw.community.core.apply_engine.activation_delegates import DeviceActivation, PlatformActivation
from agentclaw.community.core.apply_engine.desired_state import DesiredState
from agentclaw.community.core.apply_engine.constructs import Category
from agentclaw.community.core.apply_engine.delivery import ArcaDelivery
from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities
from agentclaw.community.core.apply_engine.materialisers.mcp import McpMaterialiser
from agentclaw.community.core.apply_engine.orchestrator import ApplyOrchestrator
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.bot.bot_mcp_config import BotMCPConfigRepository
from agentclaw.community.core.repository.implementations.bot.user_mcp_config import UserMCPConfigRepository
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import CapabilityDesiredStateRepository
from agentclaw.community.core.repository.implementations.skill_center.skill import SkillRepository
from agentclaw.community.core.repository.implementations.skill_center.skill_version import SkillVersionRepository
from agentclaw.community.core.skill_center.policies.platform_default_mcp import PlatformDefaultMcpPolicy
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import BotCapabilityStateReader
from agentclaw.community.core.skill_center.services.direct_activation_service import DirectActivationService
from agentclaw.community.core.skill_center.services.skill_version_resolver import SkillVersionResolver
from agentclaw.community.di.config import McpRuntimeCredentialsConfig
from tests.community.core.bot_genome.test_bot_state import _reader

from ._fakes import FakeManifestContent, manifest_compiler
from .test_capability_full_replace_contract import _Audit, _Authorization, _Center, _Database, _Runtime, _context


class Center(_Center):
    def __init__(self):
        self.on_detail = None
        self.endpoint_envs = {}

    def get_mcp_detail(self, server_code):
        if self.on_detail is not None:
            callback, self.on_detail = self.on_detail, None
            callback()
        return {
            "serverCode": server_code, "runMode": "REMOTE",
            "endpoints": [
                {"env": env, "transportProtocol": "STREAMABLE_HTTP", "networkType": "INTERNET",
                 "url": f"https://center.example/{env.lower()}/{server_code}"}
                for env in self.endpoint_envs.get(server_code, ("PROD", "PRE"))
            ],
        }


class McpWorld:
    def __init__(self, *, platform=False, bot_type="personal", runtime=None):
        self.db = _Database(shared=True)
        self.bots = BotRepository(self.db)
        engine_type = "teclaw" if platform else "moltis"
        self.bots.insert({
            "bot_id": "bot-1", "bot_name": "Configuration contract", "owner_id": "owner",
            "owner_name": "Owner", "entity_id": "entity-1", "entity_type": "staff",
            "creator_id": "owner", "status": "ACTIVE", "active_engine": engine_type, "bot_type": bot_type,
        })
        self.bot = self.bots.get_by_id_and_owner("bot-1", "owner")
        self.ref = BotRef(entity_id="entity-1", bot_id="bot-1", owner_id="owner", engine_type=engine_type, bot_type=bot_type)
        self.context = replace(
            _context(self.bot, apply_id="apply"), engine_type=engine_type, bot_type=bot_type,
            capabilities=resolve_capabilities(active_engine=engine_type, bot_type=bot_type, is_teclaw=lambda e: e == "teclaw"),
        )
        self.desired = CapabilityDesiredStateRepository(self.db)
        self.skills = SkillRepository(self.db)
        self.capabilities = BotCapabilityStateReader(
            self.desired, self.bots, self.skills,
            SkillVersionResolver(SkillVersionRepository(self.db)),
        )
        self.overrides = BotMCPConfigRepository(self.db)
        self.center = Center()
        self.config = MCPConfigService(
            UserMCPConfigRepository(self.db), self.overrides, self.center, self.bots,
            MagicMock(), McpRuntimeCredentialsConfig(), MagicMock(),
        )
        self.policy = PlatformDefaultMcpPolicy(lambda _bot_id: {})
        self.direct = DirectActivationService(
            self.desired, self.bots, MagicMock(), runtime or _Runtime(), _Authorization(), _Audit(),
            self.center, self.capabilities, self.policy, MagicMock(),
        )
        self.activation = PlatformActivation(self.direct) if platform else DeviceActivation(self.direct)
        self.orchestrator = ApplyOrchestrator({
            Category.MCP: McpMaterialiser(self.activation, self.center, self.config, self.capabilities),
        }, steps=ArcaDelivery(lambda: None).steps_for)
        self.compiler = manifest_compiler(content=FakeManifestContent(), resolver=MagicMock())
        self.state_reader = _reader(activation=self.direct, mcp_config=self.config)

    def saved(self):
        return self.overrides.list_by_bot(bot_id="bot-1", owner_id="entity-1")

    async def apply_manifest(self, entries, **kwargs):
        compiled = await self.compiler.compile_document(
            self.context,
            {"schema_version": 1, "manifest": {"mcp": entries}},
        )
        return await self.apply_state(compiled.state, **kwargs)

    async def apply_state(self, state: DesiredState, **kwargs):
        return await self.orchestrator.apply(
            self.context, state.only({Category.MCP}),
            apply_id="apply", trigger="explicit", started_at=datetime.now(), **kwargs,
        )
