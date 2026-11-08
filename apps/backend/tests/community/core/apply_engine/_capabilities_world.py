"""Real Apply, SkillCenter references, SQLite and commands; external delivery is in memory."""

from unittest.mock import MagicMock

from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.repository.implementations.bot.bot_mcp_config import (
    BotMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.bot.user_mcp_config import (
    UserMCPConfigRepository,
)
from agentclaw.community.di.config import McpRuntimeCredentialsConfig
from agentclaw.community.core.apply_engine.activation_delegates import (
    DeviceActivation,
    PlatformActivation,
)
from agentclaw.community.core.apply_engine.materialisers.capabilities import (
    CapabilitiesMaterialiser,
)
from agentclaw.community.core.apply_engine.skill_set_apply_delegates import (
    DeviceSkillSetApply,
    PlatformSkillSetApply,
)
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_center_reference import (
    SkillCenterReferenceRepository,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.skill_query_service import (
    SkillQueryService,
)
from agentclaw.community.core.skill_center.services.skill_reference_service import (
    SkillReferenceService,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SkillVersionMaterializer,
)
from agentclaw.community.core.skill_center.services.skill_version_resolver import (
    SkillVersionResolver,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)
from agentclaw.community.core.skill_center.services.track_latest import (
    TrackLatestService,
)
from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
    PlatformDefaultMcpPolicy,
)
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)
from tests.community.core.skill_center.test_skill_reference_service import (
    _PublicGateway,
    _TaskStore,
)
from tests.community.core.skill_center.test_skill_version_materializer import (
    _Http,
    _package,
)
from tests.community.core.skill_center.test_skill_query_service import _Collaborators
from .test_capability_full_replace_contract import (
    _Audit,
    _Authorization,
    _Center,
    _Database,
    _Runtime,
    _PersistedSkillPackages,
    _context,
)
from ._mcp_config_support import Center
from ._fakes import FakeManifestContent, real_validator


class CapabilityWorld:
    def __init__(self, *, store=None, auth=None, http=None, runtime=None, device=False, package=None):
        self.db = _Database(shared=True)
        self.bots = BotRepository(self.db)
        self.bots.insert(
            dict(
                bot_id="bot-1",
                bot_name="Capability contract",
                owner_id="owner",
                owner_name="Owner",
                entity_id="entity-1",
                entity_type="staff",
                creator_id="owner",
                status="ACTIVE",
                active_engine="moltis",
                bot_type="personal",
            )
        )
        self.bot = self.bots.get_by_id_and_owner("bot-1", "owner")
        self.ctx = _context(self.bot, apply_id="apply")
        self.repo = CapabilityDesiredStateRepository(self.db)
        self.skills = SkillRepository(self.db)
        self.versions = SkillVersionRepository(self.db)
        resolver = SkillVersionResolver(self.versions)
        self.reader = BotCapabilityStateReader(
            self.repo, self.bots, self.skills, resolver
        )
        self.store = store or LocalCanonicalCenterVersionStore()
        self.query = SkillQueryService(
            self.skills,
            self.bots,
            _Collaborators(),
            self.reader,
            object(),
            object(),
            lambda: object(),
            self.versions,
            resolver,
            self.store,
            object(),
        )
        self.center = Center()
        self.auth = auth or _Center()
        self.config = MCPConfigService(
            UserMCPConfigRepository(self.db),
            BotMCPConfigRepository(self.db),
            self.center,
            self.bots,
            MagicMock(),
            McpRuntimeCredentialsConfig(),
            MagicMock(),
        )
        runtime = runtime or _Runtime()
        self.runtime = runtime
        self.activation = (DeviceActivation if device else PlatformActivation)(
            DirectActivationService(
                self.repo,
                self.bots,
                self.query,
                runtime,
                _Authorization(),
                _Audit(),
                self.center,
                self.reader,
                PlatformDefaultMcpPolicy(lambda _: {}),
                MagicMock(),
            )
        )
        self.management = SkillSetManagementService(
            repository=self.repo,
            bot_repo=self.bots,
            runtime=runtime,
            legacy_factory=object(),
            passport=object(),
            authorization=_Authorization(),
            audit_log_repo=_Audit(),
            mcp_center=self.center,
            mcp_auth=self.auth,
            mcp_config=self.config,
            ext_info_provider=lambda _: {},
            recovery=MagicMock(),
        )
        self.sets = (DeviceSkillSetApply if device else PlatformSkillSetApply)(
            self.management
        )
        package = _package() if package is None else package
        self.gateway = _PublicGateway(package, mcp_services=())
        self.materializer = SkillVersionMaterializer(
            versions=self.versions,
            gateway=self.gateway,
            http=http or _Http(package),
            validator=real_validator(),
            store=self.store,
        )
        self.references = SkillReferenceService(
            skills=self.skills,
            query=self.query,
            versions=self.versions,
            resolver=resolver,
            store=self.store,
            references=SkillCenterReferenceRepository(self.db),
            gateway=self.gateway,
            materializer=self.materializer,
            track_latest=TrackLatestService(_TaskStore()),
        )
        self.uploads = _PersistedSkillPackages(self.skills)
        self.content = FakeManifestContent()
        self.materialiser = CapabilitiesMaterialiser(
            self.uploads,
            self.activation,
            real_validator(),
            self.content,
            self.query,
            self.references,
            self.sets,
            self.auth,
            self.config,
        )

    def snapshot(self):
        return self.sets.read_apply_snapshot(
            bot_id="bot-1", owner_id="entity-1", actor_id="owner"
        )

    def shared(self, name, path, *, dependencies=()):
        return self.skills.create(
            dict(
                name=name,
                description="Shared Skill",
                git_path="git://" + path,
                is_public=True,
                status="PUBLISHED",
                mcp_dependencies=list(dependencies),
            )
        )
