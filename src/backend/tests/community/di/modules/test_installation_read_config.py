"""Installation migration mode is DB-owned and environment-isolated."""

from unittest.mock import Mock

import pytest
from injector import Injector, InstanceProvider

from agentclaw.community.api.common_config_service import CommonConfigServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.capability_desired_state import (
    CapabilityDesiredStateRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolSkillRepositoryProtocol,
)
from agentclaw.community.core.skill_center.installation_read_config import (
    INSTALLATION_READ_BUSINESS_CODE,
    INSTALLATION_READ_PARAM_CODE,
    InstallationReadConfig,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from agentclaw.community.core.skill_center.version_resolution_contract import (
    SkillVersionResolverProtocol,
)
from agentclaw.community.di.modules.installation_read_config_module import (
    InstallationReadConfigModule,
)


def _injector(*, common_config: Mock) -> Injector:
    injector = Injector([InstallationReadConfigModule()])
    repository = Mock()
    for protocol, value in [
        (CommonConfigServiceProtocol, common_config),
        (CapabilityDesiredStateRepositoryProtocol, repository),
        (BotRepository, Mock()),
        (SkillsPoolSkillRepositoryProtocol, Mock()),
        (SkillVersionResolverProtocol, Mock()),
    ]:
        injector.binder.bind(protocol, to=InstanceProvider(value))
    return injector


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("pre", True),
        ("prepub", True),
        ("prod", False),
        ("gray", False),
        ("dev", False),
    ],
)
def test_environment_scoped_common_config_drives_reader(monkeypatch, environment, expected):
    monkeypatch.setenv("SERVER_ENV", environment)
    common_config = Mock()
    common_config.get_value.return_value = expected
    injector = _injector(common_config=common_config)
    repository = injector.get(CapabilityDesiredStateRepositoryProtocol)

    reader = injector.get(BotCapabilityStateReader)
    reader.synchronize_installations(
        bot_id="default",
        owner_id="owner",
        bot={
            "bot_id": "default",
            "owner_id": "owner",
            "env": "pre" if expected else "prod",
            "active_engine": "openclaw",
        },
    )

    assert repository.sync_default_installations.called is expected
    assert repository.flush_installations.called is not expected
    common_config.get_value.assert_called_with(
        business_code=INSTALLATION_READ_BUSINESS_CODE,
        param_code=INSTALLATION_READ_PARAM_CODE,
        env={"prepub": "pre", "gray": "prod"}.get(environment, environment),
        default=False,
        only_enabled=True,
    )


@pytest.mark.parametrize("value", [None, "true", 1, {}, []])
def test_missing_disabled_or_invalid_config_retains_complete_repair(value):
    common_config = Mock()
    common_config.get_value.return_value = value
    config = InstallationReadConfig(common_config_service=common_config, env="pre")

    assert config.default_sync_only is False


def test_common_config_read_error_retains_complete_repair():
    common_config = Mock()
    common_config.get_value.side_effect = RuntimeError("database unavailable")
    config = InstallationReadConfig(common_config_service=common_config, env="prod")

    assert config.default_sync_only is False


def test_db_value_is_read_dynamically_for_emergency_rollback():
    common_config = Mock()
    common_config.get_value.side_effect = [True, False]
    config = InstallationReadConfig(common_config_service=common_config, env="pre")

    assert config.default_sync_only is True
    assert config.default_sync_only is False
    assert common_config.get_value.call_count == 2


def test_pre_and_prod_read_independent_common_config_records():
    common_config = Mock()
    common_config.get_value.side_effect = lambda **kwargs: {
        "pre": True,
        "prod": False,
    }[kwargs["env"]]

    assert InstallationReadConfig(
        common_config_service=common_config, env="pre"
    ).default_sync_only is True
    assert InstallationReadConfig(
        common_config_service=common_config, env="prod"
    ).default_sync_only is False
