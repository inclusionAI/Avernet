"""Manifest supply cleanup through management, SQLite and real runtime projection."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.base import Base
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.repository.implementations.bot.bot import BotRepository
from agentclaw.community.core.repository.implementations.bot.bot_mcp_config import (
    BotMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.bot.user_mcp_config import (
    UserMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skill_center.services.skill_set_service import (
    SkillSetService,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    DefaultExclusionChange,
    ResolvedSkillSetWrite,
)
from agentclaw.community.di.config import McpRuntimeCredentialsConfig
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from tests.community.core.skill_center._test_skill_set_management_service_support import (
    _Audit,
    _Authorization,
    _McpCenter,
    _registry,
    _RuntimeLayouts,
    _RuntimePool,
    _skill_service,
)
from tests.community.repository.skill_center.capability_desired_state_support import (
    _Database,
)
from tests.community.skill_version_fakes import PassthroughSkillVersionResolver


class _Center(_McpCenter):
    def get_mcp_detail(self, server_code: str) -> dict:
        return {**super().get_mcp_detail(server_code), "serverCode": server_code}


class _Device:
    """DeviceSync boundary: configuration survives a scope-only declaration."""

    def __init__(self):
        self.configured = set()
        self.removed = []
        self.declared = set()
        self.headers = {}

    def sync_single_mcp(self, detail, **kwargs):
        self.headers[detail["serverCode"]] = kwargs["custom_headers"]
        self.configured.add(detail["serverCode"])
        return True

    def sync_all_mcp_servers(self, entries):
        self.declared = {entry["server_code"] for entry in entries}
        return True

    def sync_remove_mcp(self, server_code):
        self.removed.append(server_code)
        self.configured.discard(server_code)
        return True


class _Passport:
    """External Passport boundary with a complete, overwrite-style scope."""

    def __init__(self):
        self.scope = {
            "mcps": [],
            "clis": [
                {
                    "cli_code": "kept-cli",
                    "identity_mode": "owner",
                    "cli_name": "Kept CLI",
                    "cli_desc": "Existing unrelated permission",
                }
            ],
        }
        self.updates = []

    def query_agent_passport(self, bot_id, owner_id):
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        return deepcopy(self.scope)

    def query_passport_clis(self, bot_id, owner_id):
        return self.query_agent_passport(bot_id, owner_id)["clis"]

    def update_passport(self, *, bot_id, user_id, engine_type, resource_scope):
        assert (bot_id, user_id, engine_type) == ("bot-1", "true-owner", "openclaw")
        assert resource_scope["mcp_codes"] == [
            item["mcp_code"] for item in resource_scope["mcp_items"]
        ]
        self.updates.append(deepcopy(resource_scope))
        self.scope = {
            "mcps": deepcopy(resource_scope["mcp_items"]),
            "clis": deepcopy(resource_scope["cli_items"]),
        }

    def assert_scope(self, codes):
        assert self.updates
        assert {item["mcp_code"] for item in self.scope["mcps"]} == codes
        assert self.scope["clis"] == [
            {
                "cli_code": "kept-cli",
                "identity_mode": "owner",
                "cli_name": "Kept CLI",
                "cli_desc": "Existing unrelated permission",
            }
        ]


@pytest.fixture
def runtime_cleanup(tmp_path):
    db = _Database()
    # MCP delivery reads the same SQLite database from its worker threads.
    db.engine.dispose()
    db.engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    db.sessions.configure(bind=db.engine)
    Base.metadata.create_all(db.engine)
    bots = BotRepository(db)
    bots.insert(
        {
            "bot_id": "bot-1",
            "entity_id": "true-owner",
            "owner_id": "true-owner",
            "creator_id": "true-owner",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "status": "ACTIVE",
            "env": "dev",
        }
    )
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                Skill(
                    id=1,
                    name="dependent",
                    git_path="git://dependent",
                    env="dev",
                    mcp_dependencies='["mcp.dependency"]',
                ),
                Skill(
                    id=2,
                    name="protector",
                    git_path="git://protector",
                    env="dev",
                    mcp_dependencies='["mcp.dependency"]',
                ),
            ]
        )
    repository = CapabilityDesiredStateRepository(db)
    skills = SkillRepository(db)
    reader = BotCapabilityStateReader(
        repository=repository,
        bot_repo=bots,
        pool_skills=skills,
        version_resolver=PassthroughSkillVersionResolver(),
    )
    center = _Center(allowed=True)
    config = MCPConfigService(
        user_mcp_config_repo=UserMCPConfigRepository(db),
        bot_mcp_config_repo=BotMCPConfigRepository(db),
        mcp_center=center,
        bot_repo=bots,
        capability_reader=reader,
        mcp_runtime_credentials=McpRuntimeCredentialsConfig(),
        secret_resolver=MagicMock(),
    )
    device = _Device()
    contexts = MagicMock()
    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = device
    passport = _Passport()
    identity = MagicMock()
    identity.list_draft_call_types.return_value = {}
    sync = MCPSyncService(
        mcp_provider_factory=MagicMock(),
        mcp_center=center,
        user_mcp_config_repo=UserMCPConfigRepository(db),
        passport_update=passport,
        mcp_config_service=config,
        bot_repository=bots,
        caller_identity_repository=identity,
        resolver_provider=lambda: contexts,
        device_sync_dispatcher_provider=lambda: dispatcher,
    )
    paths = MagicMock()
    for method in (
        "get_bot_skills_dir",
        "get_bot_skills_local_dir",
        "get_bot_skills_repo_dir",
    ):
        getattr(paths, method).return_value = tmp_path
    legacy = SkillSetService(
        skill_repo=skills,
        skill_set_repo=SkillSetRepository(db),
        mcp_center=center,
        mcp_config_service=config,
        skill_service=MagicMock(),
        bot_repo=bots,
        user_id="true-owner",
        entity_id="true-owner",
        bot_id="bot-1",
        engine_type="openclaw",
        resolver=contexts,
        device_sync_dispatcher=dispatcher,
        mcp_sync_service=sync,
        ext_info_provider=lambda _bot_id: None,
        path_factory=paths,
        reader=reader,
    )
    factory = MagicMock()
    factory.create.return_value = legacy
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=bots,
        repository=repository,
        reader=reader,
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=identity,
    )
    from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import (
        ScopedMCPConfigRepository,
    )
    from agentclaw.community.core.skill_center.policies.platform_default_mcp import (
        PlatformDefaultMcpPolicy,
    )
    from agentclaw.community.core.skill_center.services.direct_activation_service import (
        DirectActivationService,
    )

    management = _skill_service(repository, runtime)
    direct = DirectActivationService(
        repository=repository,
        bot_repo=bots,
        skill_query=MagicMock(),
        runtime=runtime,
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=center,
        reader=reader,
        platform_default_mcp_policy=PlatformDefaultMcpPolicy(lambda _bot: None),
        recovery=MagicMock(),
    )
    return SimpleNamespace(
        service=management,
        direct=direct,
        device=device,
        passport=passport,
        config=config,
        config_commands=ScopedMCPConfigRepository(db),
    )


async def _replace(service, sets):
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    return await service.replace_apply_skill_sets(
        scope=before.scope,
        actor_id="true-owner",
        sets=sets,
        remove_names=(),
        default_change=DefaultExclusionChange(),
        platform_default_codes=before.platform_default_codes,
        project=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_bulk_inactive_removes_only_mcp_configuration_without_final_supply(
    runtime_cleanup, protected
):
    service, device = runtime_cleanup.service, runtime_cleanup.device
    dependent = ResolvedSkillSetWrite("dependent", skill_ids=(1,))
    protector = ResolvedSkillSetWrite("protector", skill_ids=(2,))
    sets = (dependent, protector) if protected else (dependent,)
    initial = await _replace(service, sets)
    assert initial.runtime_projection["status"] == "CONVERGED"
    assert "mcp.dependency" in device.configured
    inactive = replace(dependent, is_active=False)
    result = await _replace(
        service, (inactive, protector) if protected else (inactive,)
    )
    assert result.runtime_projection["status"] == "CONVERGED"
    assert ("mcp.dependency" in device.configured) is protected
    assert ("mcp.dependency" in device.declared) is protected
    assert device.removed == ([] if protected else ["mcp.dependency"])
    # LOCAL hitl remains on the device, outside Passport's remote scope.
    runtime_cleanup.passport.assert_scope(device.declared - {"hitl"})
    after = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    member = next(item for item in after.sets if item.name == "dependent")
    assert member.skill_ids == (1,) and not member.is_active


@pytest.mark.asyncio
@pytest.mark.parametrize("derived", [False, True])
async def test_manifest_removes_override_only_configuration_or_refreshes_retained_dependency(
    runtime_cleanup, derived
):
    service, device = runtime_cleanup.service, runtime_cleanup.device
    runtime_cleanup.config_commands.replace(
        user_id="true-owner",
        server_code="mcp.dependency",
        headers={},
        bot_headers={"bot-1": {"X-Test": "override"}},
        owned_bot_ids={"bot-1"},
        endpoint_env="PROD",
        transport_protocol="STREAMABLE_HTTP",
    )
    await _replace(service, (ResolvedSkillSetWrite("dependent", skill_ids=(1,)),))
    if not derived:
        current = service.read_apply_snapshot(
            bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
        )
        await service.deactivate(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id=str(current.sets[0].id),
        )
    before = service.read_apply_snapshot(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )
    assert "mcp.dependency" not in before.installed_mcp_codes
    assert before.mcp_overrides["mcp.dependency"] == {"headers": {"X-Test": "override"}}
    assert device.headers["mcp.dependency"]["X-Test"] == "override"
    assert "mcp.dependency" in device.configured
    result = await runtime_cleanup.direct.remove_apply_mcp(
        server_code="mcp.dependency",
        bot_id="bot-1",
        owner_id="true-owner",
        actor_id="true-owner",
        apply_id="override-only-cleanup",
    )
    assert result["changed"] and result["runtime_projection"]["status"] == "CONVERGED"
    assert (
        runtime_cleanup.config.get_bot_override(
            bot_id="bot-1", owner_id="true-owner", server_code="mcp.dependency"
        )
        is None
    )
    assert device.removed == ([] if derived else ["mcp.dependency"])
    assert ("mcp.dependency" in device.configured) is derived
    runtime_cleanup.passport.assert_scope(device.declared - {"hitl"})
    if derived:
        # Clearing the user override retains the mandatory remote Bot identity.
        assert device.headers["mcp.dependency"] == {"X-TC-Bot-Id": "bot-1"}
