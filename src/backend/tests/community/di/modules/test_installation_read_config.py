"""Installation migration mode is YAML-owned and environment-isolated."""

from unittest.mock import Mock

import pytest
from injector import Injector, InstanceProvider

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import CapabilityDesiredStateRepositoryProtocol
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolSkillRepositoryProtocol
from agentclaw.community.core.skill_center.installation_read_config import InstallationReadConfig
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import BotCapabilityStateReader
from agentclaw.community.core.skill_center.version_resolution_contract import SkillVersionResolverProtocol
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.installation_read_config_module import (
    InstallationReadConfigModule,
)


@pytest.mark.parametrize("environment,expected", [("pre", True), ("prepub", True), ("prod", False), ("gray", False), ("dev", False)])
def test_environment_selection_and_reader_injection(monkeypatch, environment, expected):
    monkeypatch.setenv("SERVER_ENV", environment)
    monkeypatch.setenv("SC_INSTALLATION_DEFAULT_SYNC_ONLY", "true")
    monkeypatch.setattr(config_module, "read_user_config", lambda: {
        "skill_installation": {"default_sync_only": {"pre": True, "prod": False}}
    })
    injector = Injector([config_module.ConfigModule(), InstallationReadConfigModule()])
    repository = Mock()
    for protocol, value in [(CapabilityDesiredStateRepositoryProtocol, repository),
                            (BotRepository, Mock()), (SkillsPoolSkillRepositoryProtocol, Mock()),
                            (SkillVersionResolverProtocol, Mock())]:
        injector.binder.bind(protocol, to=InstanceProvider(value))
    reader = injector.get(BotCapabilityStateReader)
    reader.synchronize_installations(bot_id="default", owner_id="owner", bot={
        "bot_id": "default", "owner_id": "owner", "env": "pre" if expected else "prod",
        "active_engine": "openclaw",
    })
    assert repository.sync_default_installations.called is expected
    assert repository.flush_installations.called is not expected
    assert injector.get(InstallationReadConfig).default_sync_only is expected


@pytest.mark.parametrize("user_config", [{}, {"skill_installation": {}}, {"skill_installation": {"default_sync_only": {}}}])
def test_missing_settings_are_disabled(monkeypatch, user_config):
    monkeypatch.setenv("SC_INSTALLATION_DEFAULT_SYNC_ONLY", "true")
    monkeypatch.setattr(config_module, "read_user_config", lambda: user_config)
    assert InstallationReadConfigModule().installation_read() == InstallationReadConfig()


@pytest.mark.parametrize("environment,expected", [("pre", False), ("prod", True)])
def test_production_switch_does_not_enable_pre(monkeypatch, environment, expected):
    monkeypatch.setenv("SERVER_ENV", environment)
    monkeypatch.setattr(config_module, "read_user_config", lambda: {
        "skill_installation": {"default_sync_only": {"prod": True}}
    })
    assert InstallationReadConfigModule().installation_read().default_sync_only is expected


@pytest.mark.parametrize("block", [None, True, {"typo": True}, {"default_sync_only": None},
    {"default_sync_only": True}, {"default_sync_only": {"pre": "false"}},
    {"default_sync_only": {"prod": 1}}, {"default_sync_only": {"prd": True}}])
def test_invalid_settings_fail_explicitly(monkeypatch, block):
    monkeypatch.setattr(config_module, "read_user_config", lambda: {"skill_installation": block})
    with pytest.raises(ValueError, match="skill_installation"):
        InstallationReadConfigModule().installation_read()
